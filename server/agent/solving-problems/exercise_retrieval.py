"""检索完整的习题记录，按题目条件给候选排序。"""

from dataclasses import dataclass
import math
import os
import re
import unicodedata

import httpx
import psycopg

from .exercise_bank import DSP_4E_SHA256, format_answer

from . import CONSTANT


TOP_K = 20
TOP_N = 3
HIGH_CONFIDENCE_SIMILARITY = 0.74
HIGH_CONFIDENCE_MARGIN = 0.035
HIGH_CONFIDENCE_TEXT_OVERLAP = 0.22
NUMBERED = re.compile(r"(?m)^[^\S\r\n]*(\d{1,3})[^\S\r\n]*[*＊]?[^\S\r\n]*[.．、。，,](?!\d)\s*")
NUMBER = re.compile(r"(?<![\d.])[-+]?(?:\d+\.\d+|\d+|\.\d+)")
SUBQUESTION = re.compile(r"(?m)^\s*\(\d{1,2}\)\s*")

# 用人工核验的完整题干比对。在归一化时允许格式差异。
REVIEWED_PROMPTS = {
    (2, 23): (
        "设系统由下面差分方程描述：y(n)=y(n-1)+y(n-2)+x(n-1)"
        "求系统的系统函数H(z)，并画出极零点分布图；"
        "限定系统是因果的，写出H(z)的收敛域，并求出其单位脉冲响应h(n)；"
        "限定系统是稳定性的，写出H(z)的收敛域，并求出其单位脉冲响应h(n)。"
    ),
    (2, 31): (
        r"假设系统函数如下式：H(z)=\frac{z^2+5z-50}{2z^4-2.98z^3+0.17z^2+2.3418z-1.5147}"
        "画出极、零点分布图，并判断系统是否稳定；"
        "求出输入单位阶跃序列u(n)检查系统是否稳定。"
    ),
}


def canonical_prompt(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("−", "-").lower()
    text = NUMBERED.sub("", text, count=1)
    text = SUBQUESTION.sub("", text)
    text = re.sub(r"\\(?:left|right)(?![a-z])|\\[\[\]()]", "", text)
    return re.sub(r"[\s。。，,、：:；;！!？?$]", "", text).replace("是稳定性的", "是稳定的")


@dataclass(frozen=True)
class Candidate:
    row: tuple
    question: str
    similarity: float
    source_hash: str
    has_input_figure: bool
    source_path: str = ""

    @property
    def identity(self) -> tuple[int, int]:
        return self.row[2], self.row[3]

    def reply(self, question_only: bool) -> str:
        row = (*self.row[:8], self.question) if question_only else self.row
        reply = format_answer(row, question_only)
        if self.source_path.startswith("https://github.com/"):
            reply += CONSTANT.EXERCISE_EXTERNAL_SOURCE.format(url=self.source_path, page=self.row[6])
        return reply

    def confirms(self, text: str, number: int | None) -> bool:
        if self.source_hash != DSP_4E_SHA256 or self.has_input_figure or "[不确定]" in text:
            return False
        reviewed = REVIEWED_PROMPTS.get(self.identity)
        return bool(reviewed and (number is None or self.identity[1] == number)
                    and canonical_prompt(text) == canonical_prompt(reviewed))


@dataclass(frozen=True)
class MatchEvidence:
    candidate: Candidate
    margin: float
    text_overlap: float
    numbers_match: bool

    def summary(self) -> str:
        number_status = "，数字条件一致" if self.numbers_match else ""
        return (f"题库高置信命中（相似度 {self.candidate.similarity:.3f}，"
                f"Top1-Top2 差值 {self.margin:.3f}，条件重合率 {self.text_overlap:.3f}"
                f"{number_status}），未调用视觉")


def topic_parts(text: str) -> list[tuple[int | None, str]]:
    markers = list(NUMBERED.finditer(text))
    if not markers or text[:markers[0].start()].strip():
        return [(None, text)]
    ends = [*(marker.start() for marker in markers[1:]), len(text)]
    return [(int(marker[1]), text[marker.end():end]) for marker, end in zip(markers, ends)]


def _terms(text: str) -> set[str]:
    normalized = canonical_prompt(text)
    terms = set(re.findall(r"[A-Za-z]{2,}|\d+(?:\.\d+)?", normalized))
    for phrase in re.findall(r"[\u3400-\u9fff]+", normalized):
        terms.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return terms


def _text_overlap(query: str, candidate: str) -> float:
    query_terms = _terms(query)
    candidate_terms = _terms(candidate)
    if not query_terms or not candidate_terms:
        return 0.0
    return len(query_terms & candidate_terms) / min(len(query_terms), len(candidate_terms))


def _numbers_match(query: str, candidate: str) -> bool:
    return set(NUMBER.findall(canonical_prompt(query))) == set(NUMBER.findall(canonical_prompt(candidate)))


def high_confidence(results) -> MatchEvidence | None:
    """向量结果达到唯一命中条件时，返回可直接使用的习题证据。

    人工核验题仍需与归一化后的完整题干一致。其他题目结合最高分、前两名分差、
    题号及文本和数字的一致性判断。题干看起来相似但数字不同的题仍进入候选和视觉识别流程。
    """
    if len(results) != 1:
        return None
    text, number, candidates = results[0]
    if not candidates:
        return None
    candidate = candidates[0]
    if candidate.source_hash != DSP_4E_SHA256 or candidate.has_input_figure or "[不确定]" in text:
        return None
    if number is not None and candidate.identity[1] != number:
        return None
    if candidate.confirms(text, number):
        second = candidates[1].similarity if len(candidates) > 1 else 0.0
        return MatchEvidence(candidate, candidate.similarity - second, 1.0, True)
    second = candidates[1].similarity if len(candidates) > 1 else 0.0
    margin = candidate.similarity - second
    overlap = _text_overlap(text, candidate.question)
    numbers_match = _numbers_match(text, candidate.question)
    if (candidate.similarity < HIGH_CONFIDENCE_SIMILARITY
            or margin < HIGH_CONFIDENCE_MARGIN
            or overlap < HIGH_CONFIDENCE_TEXT_OVERLAP
            or not numbers_match):
        return None
    return MatchEvidence(candidate, margin, overlap, numbers_match)


def rerank(candidates: list[Candidate], query: str, number: int | None) -> list[Candidate]:
    query_numbers = set(NUMBER.findall(canonical_prompt(query)))

    def score(candidate: Candidate) -> tuple[float, float]:
        conditions = set(NUMBER.findall(canonical_prompt(candidate.question)))
        overlap = len(conditions & query_numbers) / len(conditions) if conditions else 0.0
        return (candidate.similarity + .15 * (candidate.identity[1] == number) + .06 * overlap,
                candidate.similarity)

    return sorted(candidates, key=score, reverse=True)[:TOP_N]


def search(database_url: str, text: str, expected_number: int | None = None) -> list[tuple[str, int | None, list[Candidate]]]:
    parts = [(number, part[:4000]) for number, part in topic_parts(text) if len(part.strip()) >= 8]
    if not parts:
        return []
    model = os.environ.get("KB_EMBEDDING_MODEL", "bge-m3")
    url = os.environ.get("KB_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    response = httpx.post(f"{url}/api/embed", json={"model": model, "input": [part for _, part in parts]},
                          timeout=5, trust_env=False)
    response.raise_for_status()
    payload = response.json()
    vectors = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(parts):
        raise ValueError(CONSTANT.EXERCISE_RETRIEVAL_ERROR_UNEXPECTED_EXERCISE_EMBEDDING_COUNT)
    results = []
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        for (number, part), values in zip(parts, vectors):
            number = number if number is not None else expected_number
            if (not isinstance(values, list) or len(values) != 1024
                    or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)):
                raise ValueError(CONSTANT.EXERCISE_RETRIEVAL_ERROR_INVALID_EXERCISE_EMBEDDING)
            vector = "[" + ",".join(map(str, values)) + "]"
            rows = connection.execute(
                "SELECT d.title, d.edition, e.chapter, e.exercise_number, e.first_book_page, e.last_book_page, "
                "e.first_pdf_page, e.last_pdf_page, e.answer, e.question, "
                "1 - (e.embedding <=> %s::vector), d.sha256, e.has_input_figure, d.source_path "
                "FROM kb_exercises e JOIN kb_documents d ON e.document_id = d.id "
                "WHERE e.embedding_model = %s AND e.embedding IS NOT NULL "
                "ORDER BY e.embedding <=> %s::vector, e.document_id, e.chapter, e.exercise_number LIMIT %s",
                (vector, model, vector, TOP_K),
            ).fetchall()
            candidates = [Candidate(row[:9], *row[9:]) for row in rows]
            results.append((part, number, rerank(candidates, part, number)))
    return results


def describe(results) -> str:
    blocks = []
    for _, number, candidates in results:
        if candidates:
            heading = f"第 {number} 题的相似候选" if number is not None else "相似候选"
            lines = [item.reply(question_only=True) for item in candidates]
            blocks.append(f"**{heading}**\n\n" + "\n\n".join(lines))
    return "\n\n".join(blocks)


def confirmed(results) -> Candidate | None:
    # 在逐一识别题号之前，保留同一请求中的多道习题。
    if len(results) != 1:
        return None
    text, number, candidates = results[0]
    matches = [item for item in candidates if item.confirms(text, number)]
    return matches[0] if len(matches) == 1 else None
