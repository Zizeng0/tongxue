"""把 PDF 解析结果组织成可按任务要求检索的页级逻辑片段。"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import pymupdf

from . import CONSTANT


MAX_SEGMENT_CHARS = 2400
HEADING_PATTERN = re.compile(
    r"^(?:第[一二三四五六七八九十百千万零〇两0-9]+[章节篇]|实验\s*[一二三四五六七八九十0-9]+|附件\s*[一二三四五六七八九十0-9]+|chapter\s+\d+|\d+\.\d+(?:\.\d+)?(?:\s|$))",
    re.IGNORECASE,
)
SECTION_RANGE_PATTERN = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\s*(?:-|–|—|~|至|到)\s*(?:(\d+)\.)?(\d+)(?!\d)"
)
SECTION_NUMBER_PATTERN = re.compile(r"(?<!\d)(\d+)\.(\d+)(?!\d)")
MIN_SECTION_EXCERPT_CHARS = 64


def _page_heading(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines()[:8] if line.strip()]
    for index, line in enumerate(lines):
        heading = line
        if len(heading) <= 80 and HEADING_PATTERN.match(heading):
            if heading.startswith("附件") and index + 1 < len(lines) and len(lines[index + 1]) <= 50:
                return f"{heading} {lines[index + 1]}"
            return heading
    return None


def _chapters(analysis: dict, pdf_path: Path) -> list[dict]:
    page_count = analysis["page_count"]
    starts = {1: "开篇"}
    with pymupdf.open(pdf_path) as document:
        for level, title, page in document.get_toc(simple=True):
            if level <= 2 and 1 <= page <= page_count and title.strip():
                starts[page] = title.strip()
    if len(starts) == 1:
        for page in analysis["pages"]:
            heading = _page_heading(page["native_text"] or page["ocr_text"])
            if heading:
                starts[page["page"]] = heading
    ordered = sorted(starts.items())
    return [
        {"title": title, "start_page": page,
         "end_page": ordered[index + 1][0] - 1 if index + 1 < len(ordered) else page_count}
        for index, (page, title) in enumerate(ordered)
    ]


def _split_page(text: str, max_chars: int) -> list[str]:
    if not text.strip():
        return ["[本页没有可读取的文字，请核对页面图像]"]
    pieces = []
    position = 0
    while position < len(text):
        end = min(position + max_chars, len(text))
        if end < len(text):
            line_end = text.rfind("\n", position + max_chars // 2, end)
            if line_end > position:
                end = line_end + 1
        piece = text[position:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(text):
            break
        position = max(position + 1, end - 120)
    return pieces


def build_pdf_segments(analysis: dict, pdf_path: str | Path, output_dir: str | Path) -> dict:
    """建立按页切分、按章节标注的索引；原 PDF 不会被修改。"""
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    if len(analysis["pages"]) != analysis["page_count"]:
        raise ValueError(CONSTANT.PDF_SEGMENTS_ERROR_PDF_ANALYSIS_HAS_MISSING_PAGES)
    chapters = _chapters(analysis, pdf_path)
    segments = []
    for page in analysis["pages"]:
        page_number = page["page"]
        chapter = next(item["title"] for item in reversed(chapters) if item["start_page"] <= page_number)
        source = "native" if page["native_text"].strip() else "ocr"
        text = page["native_text"] if source == "native" else page["ocr_text"]
        for part, piece in enumerate(_split_page(text, MAX_SEGMENT_CHARS), start=1):
            segments.append({
                "id": f"p{page_number:04d}-{part:02d}",
                "page": page_number,
                "chapter": chapter,
                "source": source,
                "text": piece,
                "needs_review": page["needs_review"],
            })
    index = {"source_pdf": pdf_path.name, "page_count": analysis["page_count"],
             "chapters": chapters, "segments": segments}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "segments.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def _terms(text: str) -> set[str]:
    terms = set(re.findall(r"[a-z0-9]+", text.casefold()))
    for group in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(group[index:index + 2] for index in range(len(group) - 1))
    return terms


def _requested_sections(requirement: str) -> list[str]:
    match = SECTION_RANGE_PATTERN.search(requirement)
    if not match:
        return []
    major, start, end_major, end = match.groups()
    if end_major and end_major != major:
        return []
    first, last = int(start), int(end)
    if first > last or last - first > 50:
        return []
    return [f"{major}.{minor}" for minor in range(first, last + 1)]


def _segment_section(segment: dict) -> str | None:
    match = SECTION_NUMBER_PATTERN.search(segment["chapter"])
    if not match:
        match = SECTION_NUMBER_PATTERN.search(segment["text"][:120])
    return f"{match.group(1)}.{int(match.group(2))}" if match else None


def _select_range_segments(ranked: list[tuple[float, dict]], requested: list[str],
                           max_chars: int, max_segments: int) -> dict:
    by_section: dict[str, list[tuple[float, dict]]] = {section: [] for section in requested}
    for item in ranked:
        section = _segment_section(item[1])
        if section in by_section:
            by_section[section].append(item)

    first_pass = []
    minimum_chars = 0
    for section in requested:
        if not by_section[section] or len(first_pass) >= max_segments:
            continue
        candidate = by_section[section][0][1]
        required = min(MIN_SECTION_EXCERPT_CHARS, len(candidate["text"]))
        if first_pass and minimum_chars + required > max_chars:
            continue
        first_pass.append((section, candidate))
        minimum_chars += required

    selected = []
    remaining = max_chars
    for index, (_, candidate) in enumerate(first_pass):
        quota = max(1, remaining // (len(first_pass) - index))
        excerpt = {**candidate, "text": candidate["text"][:quota]}
        selected.append(excerpt)
        remaining -= len(excerpt["text"])

    selected_sections = {section for section, _ in first_pass}
    used_ids = {segment["id"] for segment in selected}
    for _, candidate in ranked:
        if remaining <= 0 or len(selected) >= max_segments:
            break
        if _segment_section(candidate) not in selected_sections or candidate["id"] in used_ids:
            continue
        excerpt = {**candidate, "text": candidate["text"][:remaining]}
        selected.append(excerpt)
        used_ids.add(candidate["id"])
        remaining -= len(excerpt["text"])

    omitted = [section for section in requested if section not in selected_sections]
    original_lengths = {segment["id"]: len(segment["text"]) for _, segment in ranked}
    unselected_in_range = any(candidate["id"] not in used_ids
                              for section in requested for _, candidate in by_section[section])
    selected.sort(key=lambda item: (item["page"], item["id"]))
    return {"weak_match": not selected, "segments": selected,
            "requested_sections": requested, "omitted_sections": omitted,
            "truncated": unselected_in_range or any(len(item["text"]) < original_lengths[item["id"]]
                                                   for item in selected)}


def select_task_segments(index: dict, requirement: str, *, max_chars: int = 12000,
                         max_segments: int = 8) -> dict:
    """按任务相关度选择有页码和章节来源的片段；无匹配时取开头并标记。"""
    if max_chars < 1 or max_segments < 1:
        raise ValueError(CONSTANT.PDF_SEGMENTS_ERROR_SELECTION_LIMITS_MUST_BE_POSITIVE)
    segments = index["segments"]
    query_terms = _terms(requirement)
    document_frequency = Counter(term for segment in segments for term in _terms(segment["text"] + " " + segment["chapter"]))
    ranked = []
    for segment in segments:
        body_matches = query_terms & _terms(segment["text"])
        title_matches = query_terms & _terms(segment["chapter"])
        score = sum(math.log1p((len(segments) + 1) / (document_frequency[term] + 1)) for term in body_matches)
        score += 3 * len(title_matches)
        if segment["chapter"] and segment["chapter"] in requirement:
            score += 10
        ranked.append((score, segment))
    ranked.sort(key=lambda item: (-item[0], item[1]["page"], item[1]["id"]))
    requested = _requested_sections(requirement)
    if requested:
        return {"source_pdf": index["source_pdf"],
                **_select_range_segments(ranked, requested, max_chars, max_segments)}
    weak_match = not ranked or ranked[0][0] == 0
    chapter_matches = {chapter["title"]: len(query_terms & _terms(chapter["title"])) for chapter in index.get("chapters", [])}
    strongest_chapter_match = max(chapter_matches.values(), default=0)
    if strongest_chapter_match > 0:
        ranked = [item for item in ranked if chapter_matches.get(item[1]["chapter"], 0) == strongest_chapter_match]
    selected = []
    remaining = max_chars
    for score, segment in ranked:
        if len(selected) >= max_segments or remaining <= 0:
            break
        if not weak_match and score == 0:
            break
        excerpt = {**segment, "text": segment["text"][:remaining]}
        selected.append(excerpt)
        remaining -= len(excerpt["text"])
    selected.sort(key=lambda item: (item["page"], item["id"]))
    return {"source_pdf": index["source_pdf"], "weak_match": weak_match, "segments": selected}


def format_task_segments(selection: dict) -> str:
    """将检索结果格式化为带来源标识的模型输入。"""
    omitted = selection.get("omitted_sections", [])
    coverage_warning = f"未覆盖章节：{', '.join(omitted)}；请按章节继续检索。\n" if omitted else ""
    if selection.get("truncated") and not omitted:
        coverage_warning = "部分章节还有未返回的内容；请按页继续读取。\n"
    if not selection["segments"]:
        return coverage_warning + "未找到可读取的 PDF 片段，请查看原文件。"
    heading = "未定位到明确匹配的章节，以下是文档开头片段；请核对任务范围。\n" if selection["weak_match"] else ""
    pieces = [
        f"[第{segment['page']}页 · {segment['chapter']} · {segment['source']}"
        f"{' · 待复核' if segment['needs_review'] else ''}]\n{segment['text']}"
        for segment in selection["segments"]
    ]
    return coverage_warning + heading + "\n\n".join(pieces)
