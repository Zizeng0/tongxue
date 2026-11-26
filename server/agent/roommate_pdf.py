"""Select PDF attachments and explicit PDF page ranges for Roommate tasks."""

import re
from pathlib import Path

from server.General_Tools.pdf_on_demand import MAX_MATERIAL_PAGES

from . import CONSTANT
from .roommate_types import RoommateError

PAGE_REFERENCE = re.compile(
    r"(?:第\s*|(?:PDF|附件|讲义|资料)\s*(?:的\s*)?(?:第\s*)?)"
    r"([0-9]{1,9})\s*(?:[页頁]?\s*[-–—~～至到]\s*(?:第\s*)?([0-9]{1,9})\s*)?[页頁]"
    r"|\bpages?\s+([0-9]{1,9})(?:\s*(?:[-–—]|to)\s*([0-9]{1,9}))?\b",
    re.I,
)
PDF_FILENAME_REFERENCE = re.compile(r"[\w)\]]\.pdf(?![a-z0-9_])", re.I)


def pdf_page_range(request: str) -> tuple[int, int] | None:
    matches = PAGE_REFERENCE.finditer(request)
    match = next(matches, None)
    if not match:
        return None
    first, last, english_first, english_last = match.groups()
    start = int(first or english_first)
    end = int(last or english_last or start)
    if next(matches, None) or not 1 <= start <= end or end - start + 1 > MAX_MATERIAL_PAGES:
        raise RoommateError(CONSTANT.ROOMMATE_PDF_PAGE_RANGE.format(limit=MAX_MATERIAL_PAGES))
    return start, end


def select_pdf_files(files: list[dict], request: str, page_range: tuple[int, int] | None) -> list[dict]:
    if page_range is None:
        return files
    pdfs = [file for file in files if Path(file["stored_name"]).suffix.lower() == ".pdf"]
    request = request.casefold()
    named = [file for file in pdfs if file["name"].casefold() in request]
    if not named and PDF_FILENAME_REFERENCE.search(request):
        raise RoommateError(CONSTANT.ROOMMATE_PDF_OUTSIDE_TASK)
    if len(named or pdfs) > 1:
        raise RoommateError(CONSTANT.ROOMMATE_PDF_ASK_FILENAME)
    if not named:
        return files
    return [file for file in files if file not in pdfs or file in named]


def task_pdf_path(task_dir: Path, source_id: str, stored_name: str) -> Path:
    if Path(source_id).name != source_id or Path(stored_name).name != stored_name:
        raise RoommateError(CONSTANT.ROOMMATE_PDF_OUTSIDE_TASK)
    root = task_dir.resolve() / "messages"
    directory = root / source_id
    path = directory / stored_name
    if directory.parent != root or path.resolve() != path or path.suffix.lower() != ".pdf" or not path.is_file():
        raise RoommateError(CONSTANT.ROOMMATE_PDF_OUTSIDE_TASK)
    return path
