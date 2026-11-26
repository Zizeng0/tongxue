"""精确查询教材习题，并记录已解答的图片习题。"""

from dataclasses import dataclass
from hashlib import sha256
import logging
import re
import unicodedata

import psycopg
from psycopg.types.json import Jsonb

from . import CONSTANT
from .theory_retrieval import requirement_terms


logger = logging.getLogger("uvicorn.error")
DSP_4E_SHA256 = "cb91c82c8c44d3597f67a7e1398ccd1ce1bf3705918647ef2e04b152b1d75b09"
REFERENCE = re.compile(r"(?:(?:请\s*)?(?:解答|做|给出))?\s*《?数字信号处理》?\s*(?:[(]?\s*(?:第4版|第四版)\s*[)]?\s*)?"
                       r"第\s*(\d{1,2})\s*章\s*(?:第|习题\s*)\s*(\d{1,3})\s*[题.]?\s*(?:的解答)?[。？?]?\s*")
LOOKUP_ACTION = re.compile(r"(?:请\s*)?(?:帮我|麻烦)?\s*(?:先\s*)?(?:在(?:本地)?(?:知识库|题库)(?:里|中)?\s*)?"
                           r"(?:查找|查询|检索|搜索|找出|查)\s*(?:一下\s*)?")
LOOKUP_TARGET = re.compile(r"(?:(?:这张图(?:片)?(?:中|里|上)?(?:的)?|图中(?:的)?|这道题的|这道|这份|这几道)\s*)?"
                           r"(?:教材原题|原题|题目|习题|题)\s*")
LOOKUP_AND_SOLVE = re.compile(r"^[,、]?\s*(?:并|再|然后)\s*(?:给出(?:题解|解答|答案)|解答|解题|求解|做题)[。.!?]*$")
NUMBERED_LOOKUP = re.compile(r"第?\s*(\d{1,3})\s*(?:题目|题)\s*")
NUMBERED_ACTION = re.compile(r"(?:请\s*)?(?:解答|完成|做)\s*")
PRESENTATION = re.compile(r"请|和|与|并|再|然后|给出|提供|展示|写出|详细|完整|解题流程|解题步骤|解答过程|解题过程|步骤|流程|"
                          r"题解|答案|解答|解题|求解|做题|绘制图像|画出对应的图片|画出图像|绘图|画图|图像|[ ,。、.!?]")
NUMBERED = re.compile(r"(?m)^[^\S\r\n]*(\d{1,3})[^\S\r\n]*[*＊]?[^\S\r\n]*[.．、。](?!\d)\s*")
WIDE = str.maketrans({code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)})
HAN = r"[\u3400-\u9fff]"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(WIDE).replace("−", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(rf"(?<={HAN}) (?={HAN})", "", text)


def question_hash(question: str) -> bytes:
    key = normalize(question).rstrip("。")
    acronyms = r"DFT|IDFT|FFT|IFFT|LTI|FIR|IIR|DTFT"
    key = re.sub(rf"(?<={HAN}) (?=(?:{acronyms})(?![A-Za-z\d]))", "", key)
    key = re.sub(rf"(?<![A-Za-z\d])({acronyms}) (?={HAN})", r"\1", key)
    return sha256(key.encode("utf-8")).digest()


def parse_lookup_request(text: str) -> tuple[str, bool, bool]:
    """返回检索词，以及用户是否要求查题和解题。"""
    action = LOOKUP_ACTION.match(text)
    if not action:
        if action := NUMBERED_ACTION.match(text):
            numbered = NUMBERED_LOOKUP.match(text[action.end():])
            if numbered:
                tail = normalize(text[action.end() + numbered.end():]).strip(",。.!? ")
                if not tail or is_presentation(tail):
                    return "", True, True
        return text, False, True
    target = text[action.end():].strip()
    if numbered := NUMBERED_LOOKUP.match(target):
        tail = normalize(target[numbered.end():]).strip(",。.!? ")
        if not tail or is_presentation(tail):
            return "", True, bool(tail)
    generic = LOOKUP_TARGET.match(target)
    if generic:
        rest = target[generic.end():].lstrip()
        if not rest.strip("。.!?？！ "):
            return "", True, False
        if LOOKUP_AND_SOLVE.fullmatch(normalize(rest)) or is_presentation(rest):
            return "", True, True
        target = rest
    if target.startswith(("：", ":")):
        return target[1:].strip(), True, False
    if REFERENCE.fullmatch(normalize(target)):
        return target, True, False
    return text, False, True


def is_presentation(text: str) -> bool:
    text = normalize(text)
    return bool(text) and not PRESENTATION.sub("", text)


def requested_number(text: str) -> int | None:
    action = LOOKUP_ACTION.match(text) or NUMBERED_ACTION.match(text)
    number = NUMBERED_LOOKUP.match(text[action.end():]) if action else None
    return int(number[1]) if number else None


@dataclass(frozen=True)
class ImageExercise:
    transcription: str
    request_text: str
    answer: str
    reusable_answer: bool
    figure_source: dict | None = None
    has_image_template: bool = False

    @property
    def uncertain(self) -> bool:
        return "[不确定]" in normalize(self.transcription)


def find_image(database_url: str, owner_id: int, image_hash: bytes) -> ImageExercise | None:
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        row = connection.execute(
            "SELECT transcription, request_text, answer, reusable_answer, to_jsonb(kb_image_exercises)->'figure_source', "
            "to_jsonb(kb_image_exercises)->>'image_height' IS NOT NULL "
            "FROM kb_image_exercises "
            "WHERE owner_id = %s AND image_hash = %s", (owner_id, image_hash),
        ).fetchone()
    return ImageExercise(*row) if row else None


def image_candidates(database_url: str, owner_id: int, lower: int, upper: int):
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        rows = connection.execute(
            "SELECT image_template, transcription, request_text, answer, reusable_answer, figure_source "
            "FROM kb_image_exercises WHERE owner_id = %s AND image_height BETWEEN %s AND %s "
            "AND image_template IS NOT NULL ORDER BY image_hash", (owner_id, lower, upper),
        )
        for template, *exercise in rows:
            candidate = ImageExercise(*exercise, has_image_template=True)
            if candidate.transcription and not candidate.uncertain:
                yield template, candidate


def save_image_template(database_url: str, owner_id: int, image_hash: bytes, descriptor: tuple[int, bytes]) -> None:
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute(
                "UPDATE kb_image_exercises SET image_height = %s, image_template = %s "
                "WHERE owner_id = %s AND image_hash = %s AND image_height IS NULL", (*descriptor, owner_id, image_hash),
            )
    except psycopg.Error as exc:
        logger.warning(CONSTANT.EXERCISE_BANK_LOG_COULD_NOT_INDEX_IMAGE_EXERCISE, type(exc).__name__)


def save_image(database_url: str, owner_id: int, image_hash: bytes, exercise: ImageExercise,
               descriptor: tuple[int, bytes] | None = None) -> None:
    height, template = descriptor or (None, None)
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute(
                "INSERT INTO kb_image_exercises "
                "(owner_id, image_hash, transcription, request_text, answer, reusable_answer, figure_source, image_height, image_template) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (owner_id, image_hash) DO UPDATE SET "
                "transcription = EXCLUDED.transcription, request_text = EXCLUDED.request_text, "
                "answer = EXCLUDED.answer, reusable_answer = EXCLUDED.reusable_answer, figure_source = EXCLUDED.figure_source, "
                "image_height = EXCLUDED.image_height, image_template = EXCLUDED.image_template "
                "WHERE NOT kb_image_exercises.reusable_answer",
                (owner_id, image_hash, exercise.transcription, exercise.request_text,
                 exercise.answer, exercise.reusable_answer, Jsonb(exercise.figure_source), height, template),
            )
    except psycopg.Error as exc:
        logger.warning(CONSTANT.EXERCISE_BANK_LOG_COULD_NOT_STORE_IMAGE_EXERCISE_IN, type(exc).__name__)


def find_textbook(database_url: str, text: str, *, from_image: bool = False, question_only: bool = False) -> str | None:
    reference = None if from_image else REFERENCE.fullmatch(normalize(text))
    if not reference and not text.strip():
        return None
    content_column = "e.question" if question_only else "e.answer"
    has_answer = "" if question_only else " AND e.answer <> ''"
    columns = (
        "SELECT d.title, d.edition, e.chapter, e.exercise_number, e.first_book_page, e.last_book_page, "
        f"e.first_pdf_page, e.last_pdf_page, {content_column} FROM kb_exercises e "
        "JOIN kb_documents d ON e.document_id = d.id "
    )
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        if reference:
            rows = connection.execute(
                columns + "WHERE d.sha256 = %s AND d.edition = %s AND e.chapter = %s AND e.exercise_number = %s LIMIT 2",
                (DSP_4E_SHA256, "第四版", *map(int, reference.groups())),
            ).fetchall()
            return format_answer(rows[0], question_only) if len(rows) == 1 else None
        markers = list(NUMBERED.finditer(text))
        has_numbering = markers and not text[:markers[0].start()].strip()
        parts = ([(int(marker[1]), text[marker.end():end])
                  for marker, end in zip(markers, [*(item.start() for item in markers[1:]), len(text)])]
                 if has_numbering else [(None, text)])
        answers = []
        for number, part in parts:
            rows = connection.execute(
                columns + "WHERE NOT e.has_input_figure AND e.question_hash = %s "
                "AND (%s::integer IS NULL OR e.exercise_number = %s)" + has_answer + " LIMIT 2",
                (question_hash(part), number, number),
            ).fetchall()
            if len(rows) != 1:
                return None
            answers.append(format_answer(rows[0], question_only))
    return "\n\n---\n\n".join(answers)


def format_answer(row: tuple, question_only: bool) -> str:
    title, edition, chapter, number, book_start, book_end, pdf_start, pdf_end, content = row
    book_pages = str(book_start) if book_start == book_end else f"{book_start}–{book_end}"
    pdf_pages = str(pdf_start) if pdf_start == pdf_end else f"{pdf_start}–{pdf_end}"
    heading = f"**《{title}》{edition} · 第 {chapter} 章第 {number} 题**（书页 {book_pages}，PDF 页 {pdf_pages}）"
    label = "**题库收录题干**\n\n" if question_only else ""
    return f"{heading}\n\n{label}{content}"


def search_keywords(database_url: str, question: str, terms: list[str], *, subject: str = "") -> list[str]:
    """按题干包含的关键词排序，只返回题干和出处供对照。"""
    keywords = list(dict.fromkeys(normalize(term).lower() for term in [*terms, *requirement_terms(question)]
                                  if 2 <= len(term.strip()) <= 40))[:8]
    if not keywords:
        return []
    with psycopg.connect(database_url, connect_timeout=2, options="-c statement_timeout=3000") as connection:
        rows = connection.execute(
            "SELECT d.title, d.edition, e.chapter, e.exercise_number, e.first_book_page, e.last_book_page, "
            "e.first_pdf_page, e.last_pdf_page, e.question FROM kb_exercises e "
            "JOIN kb_documents d ON e.document_id = d.id "
            "CROSS JOIN LATERAL (SELECT count(*) AS hits FROM unnest(%s::text[]) AS k(term) "
            "WHERE strpos(lower(e.question), k.term) > 0) AS matched "
            "WHERE matched.hits > 0 ORDER BY (lower(d.subject) = lower(%s)) DESC, "
            "matched.hits DESC, d.id, e.chapter, e.exercise_number LIMIT 3",
            (keywords, subject),
        ).fetchall()
    return [format_answer(row, True) for row in rows]


@dataclass(frozen=True)
class BookExercise:
    title: str
    edition: str
    chapter: int
    number: int


def reviewed_image_reference(chapter: int, number: int) -> BookExercise:
    return BookExercise("数字信号处理", "第四版", chapter, number)


def find_by_reference(database_url: str, reference: BookExercise, *, question_only: bool = False,
                      document_hash: str = "") -> str | None:
    content = "e.question" if question_only else "e.answer"
    has_answer = "" if question_only else " AND e.answer <> ''"
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        rows = connection.execute(
            "SELECT d.title, d.edition, e.chapter, e.exercise_number, e.first_book_page, e.last_book_page, "
            f"e.first_pdf_page, e.last_pdf_page, {content} FROM kb_exercises e "
            "JOIN kb_documents d ON e.document_id = d.id WHERE lower(d.title) = lower(%s) "
            "AND (%s = '' OR d.edition = %s) AND e.chapter = %s AND e.exercise_number = %s "
            "AND (%s = '' OR d.sha256 = %s)" + has_answer + " LIMIT 2",
            (reference.title, reference.edition, reference.edition, reference.chapter, reference.number,
             document_hash, document_hash),
        ).fetchall()
    return format_answer(rows[0], question_only) if len(rows) == 1 else None
