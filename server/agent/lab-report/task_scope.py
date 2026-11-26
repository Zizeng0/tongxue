"""识别最近一次明确的 HFSS 请求中指定的章节。"""

import re
from pathlib import Path


_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHAPTER = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*章|(?<!\d)(\d{1,2})\s*\.\s*\d+")
_OUTPUT_CHAPTER = re.compile(r"(?:^|[^\d])(\d{1,2})[._-]\d+(?:[._-]|$)")


def _chapter_number(label: str) -> int | None:
    if label.isdecimal():
        return int(label)
    if label == "十":
        return 10
    if "十" in label:
        tens, _, ones = label.partition("十")
        if (tens and tens not in _CHINESE_DIGITS) or (ones and ones not in _CHINESE_DIGITS):
            return None
        return _CHINESE_DIGITS.get(tens, 1) * 10 + _CHINESE_DIGITS.get(ones, 0)
    return _CHINESE_DIGITS.get(label)


def active_hfss_chapters(messages: list[dict]) -> tuple[int, frozenset[int]] | None:
    """返回最近一次明确的请求和其中指定的全部章节。"""
    for index in range(len(messages) - 1, -1, -1):
        requirements = messages[index].get("requirements", "")
        chapters = set()
        for match in _CHAPTER.finditer(requirements):
            if re.search(r"(?:不要|不做|排除|不是|避开)\s*$", requirements[max(0, match.start() - 6):match.start()]):
                continue
            chapter = _chapter_number(match.group(1)) if match.group(1) else int(match.group(2))
            if chapter is not None:
                chapters.add(chapter)
        if chapters:
            return index, frozenset(chapters)
    return None


def hfss_output_matches_chapter(path: Path, chapters: frozenset[int]) -> bool:
    """自动复用带有章节标记的实验目录。"""
    return any(int(match.group(1)) in chapters for part in path.parts
               for match in _OUTPUT_CHAPTER.finditer(part))
