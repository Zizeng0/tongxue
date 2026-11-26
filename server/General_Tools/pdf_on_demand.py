"""使用 PyMuPDF 按页读取 PDF；仅在模型选择页面后执行 OCR。"""

import json
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from server.General_Tools.pdf_analysis import MAX_INPUT_BYTES, MAX_RENDER_PIXELS, _ocr, _open_document

from . import CONSTANT

MAX_PAGES_PER_READ = 5
MAX_TOC_ENTRIES = 80
MAX_TEXT_CHARS = 12000
MAX_MATERIAL_PAGES = 20
MAX_TEXT_RANGE_PAGES = 50
MAX_TEXT_RANGE_CHARS = 120000
MAX_PREVIEW_EDGE = 1600


@dataclass(frozen=True)
class MaterialPage:
    number: int
    text: str = ""
    image: bytes | None = None


def validate_pdf_material(content: bytes) -> None:
    with _open_document(content) as document:
        if document.needs_pass:
            raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_PASSWORD_PROTECTED_PDF_NOT_SUPPORTED)
        if not document.page_count:
            raise ValueError(CONSTANT.PDF_ON_DEMAND_NO_PAGES)


def read_pdf_material(path: Path, max_chars: int, scan_limit: int, *, start_page: int = 1,
                      page_limit: int = MAX_MATERIAL_PAGES, end_page: int | None = None) -> tuple[list[MaterialPage], bool]:
    """Extract bounded text and images of scanned pages for the Roommate model."""
    pages = []
    with _open_pdf(path) as document:
        if end_page is not None:
            _validate_page_range(document, start_page, end_page, min(page_limit, MAX_MATERIAL_PAGES))
            end = end_page
            truncated = False
        else:
            end = min(document.page_count, start_page - 1 + page_limit)
            truncated = document.page_count > end and start_page == 1
        for index in range(start_page - 1, end):
            if max_chars <= 0:
                truncated = True
                break
            page = document[index]
            text = _page_text(page)
            if text:
                pages.append(MaterialPage(index + 1, text=text[:max_chars]))
                truncated |= len(text) > max_chars
                max_chars -= len(text)
            elif scan_limit > 0:
                scale = min(2, 1800 / max(page.rect.width, page.rect.height))
                image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
                pages.append(MaterialPage(index + 1, image=image.tobytes("png")))
                scan_limit -= 1
            else:
                truncated = True
    return pages, truncated


def _open_pdf(path: Path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_PDF_EXCEEDS_MB_UPLOAD_LIMIT)
    document = pymupdf.open(path)
    if document.needs_pass:
        document.close()
        raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_PASSWORD_PROTECTED_PDF_NOT_SUPPORTED)
    return document


def describe_pdf(path: Path, start: int = 0) -> dict:
    """只读取页数和书签，不渲染页面。"""
    with _open_pdf(path) as document:
        chapters = document.get_toc(simple=True)
        if start < 0 or start > len(chapters):
            raise ValueError(CONSTANT.PDF_ON_DEMAND_ERROR_BOOKMARK_OFFSET_OUTSIDE_PDF)
        return {
            "page_count": document.page_count,
            "bookmark_count": len(chapters),
            "chapters": [
                {"page": page, "title": title}
                for level, title, page in chapters[start:start + MAX_TOC_ENTRIES]
            ],
        }


def _validate_page_range(document, start_page: int, end_page: int, max_pages: int) -> None:
    if start_page < 1 or end_page < start_page or end_page > document.page_count:
        raise ValueError(CONSTANT.PDF_ON_DEMAND_ERROR_PAGE_RANGE_OUTSIDE_PDF)
    if end_page - start_page + 1 > max_pages:
        raise ValueError(CONSTANT.PDF_ON_DEMAND_ERROR_READ_AT_MOST_PAGES_PER_CALL.format(MAX_PAGES_PER_READ=max_pages))


def _page_text(page) -> str:
    return page.get_text("text", sort=True).strip()


def read_pdf_text_range(path: Path, start_page: int, end_page: int) -> dict:
    """Keep every requested page in the response, with a bounded text budget per page."""
    with _open_pdf(path) as document:
        _validate_page_range(document, start_page, end_page, MAX_TEXT_RANGE_PAGES)
        page_budget = min(MAX_TEXT_CHARS, MAX_TEXT_RANGE_CHARS // (end_page - start_page + 1))
        pages = []
        truncated = False
        for number in range(start_page, end_page + 1):
            text = _page_text(document[number - 1])
            pages.append({"page": number, "text": text[:page_budget], "source": "native" if text else "no_text_layer"})
            truncated |= len(text) > page_budget
        return {"pages": pages, "truncated": truncated}


def render_pdf_page(path: Path, page_number: int) -> bytes:
    """Render the complete page as a bounded JPEG without creating a cache."""
    with _open_pdf(path) as document:
        _validate_page_range(document, page_number, page_number, 1)
        page = document[page_number - 1]
        scale = min(2, MAX_PREVIEW_EDGE / max(page.rect.width, page.rect.height))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
        return pixmap.tobytes("jpeg", jpg_quality=88)


def read_pdf_pages(path: Path, start_page: int, end_page: int, max_chars: int = MAX_TEXT_CHARS, *, ocr: bool = True) -> dict:
    """读取指定页的文字层，并可对空白文字层运行 OCR。"""
    with _open_pdf(path) as document:
        _validate_page_range(document, start_page, end_page, MAX_PAGES_PER_READ)
        if max_chars < 1 or max_chars > MAX_TEXT_CHARS:
            raise ValueError(CONSTANT.PDF_ON_DEMAND_ERROR_MAX_CHARS_MUST_BE_BETWEEN.format(MAX_TEXT_CHARS=MAX_TEXT_CHARS))
        pages = []
        remaining = max_chars
        ocr_engine = None
        for number in range(start_page, end_page + 1):
            page = document[number - 1]
            content = _page_text(page)
            source = "native"
            if not content and not ocr:
                source = "no_text_layer"
            elif not content:
                cache_path = path.parent / f"{path.stem}_analysis" / f"page-{number:04d}.json"
                if cache_path.is_file():
                    content = json.loads(cache_path.read_text(encoding="utf-8"))["text"]
                else:
                    if ocr_engine is None:
                        ocr_engine = RapidOCR()
                    scale = min(2, (MAX_RENDER_PIXELS / (page.rect.width * page.rect.height)) ** 0.5)
                    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
                    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    content = _ocr(image, ocr_engine)["text"]
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps({"text": content}, ensure_ascii=False), encoding="utf-8")
                source = "ocr"
            pages.append({"page": number, "source": source, "text": content[:remaining]})
            remaining -= min(len(content), remaining)
            if remaining == 0:
                break
        return {"pages": pages, "truncated": remaining == 0 or pages[-1]["page"] < end_page}
