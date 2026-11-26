"""Locate a requested PDF section and extract figures with their source page."""

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf


CAPTION = re.compile(r"^\s*(?:图\s*\d+(?:[.．-]\d+)*|(?:figure|fig\.)\s*\d+\b)", re.I)
SECTION_LIMIT = 4


@dataclass(frozen=True)
class PdfFigure:
    path: Path
    caption: str
    credit: str
    context: str


def compact(text: str) -> str:
    return re.sub(r"[^\w]", "", text).casefold()


def page_lines(page):
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
        for line in block["lines"]:
            spans = line["spans"]
            if spans:
                yield ("".join(span["text"] for span in spans).strip(), pymupdf.Rect(line["bbox"]),
                       max(span["size"] for span in spans))


def find_section(path: Path, request: str) -> int | None:
    question = compact(request)
    best = (0, 0)
    with pymupdf.open(path) as document:
        for index in range(min(document.page_count, 180)):
            page = document[index]
            for title, bounds, size in page_lines(page):
                cleaned = compact(title)
                if (len(cleaned) >= 5 and len(cleaned) > best[0] and cleaned in question
                        and size >= 16 and bounds.y0 < page.rect.height * .4):
                    best = (len(cleaned), index + 1)
    return best[1] or None


def extract_pdf_figures(path: Path, filename: str, pages: list[int], target: Path, limit: int) -> list[PdfFigure]:
    figures = []
    with pymupdf.open(path) as document:
        for number in pages:
            page = document[number - 1]
            lines = list(page_lines(page))
            captions = [(text, box) for text, box, _ in lines if CAPTION.match(text)]
            if not captions:
                continue
            regions = [pymupdf.Rect(item["bbox"]) for item in page.get_image_info()]
            regions.extend(page.cluster_drawings(x_tolerance=8, y_tolerance=8))
            for caption, label in captions:
                candidates = [
                    box for box in regions
                    if 30 <= box.width <= page.rect.width * .72 and 30 <= box.height <= page.rect.height * .48
                    and -8 <= label.y0 - box.y1 <= 55
                    and box.x0 - 48 <= label.x0 <= box.x1 + 12
                ]
                if not candidates:
                    continue
                box = min(candidates, key=lambda rect: abs(label.y0 - rect.y1))
                # Include nearby diagram labels while keeping the crop above the printed caption.
                clip = pymupdf.Rect(box.x0 - 12, box.y0 - 10, box.x1 + 20, min(box.y1 + 7, label.y0)) & page.rect
                for text, nearby, size in lines:
                    if (len(text) <= 20 and size <= 13 and nearby.y1 <= label.y0
                            and nearby.intersects(clip)):
                        clip |= nearby
                clip &= page.rect
                target.mkdir(parents=True, exist_ok=True)
                output = target / f"{path.stem}-p{number}-{len(figures) + 1}.png"
                scale = min(3.2, 1400 / max(clip.width, clip.height))
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False).save(output)
                context = [text for text, nearby, _ in lines if text and abs(nearby.y0 - label.y0) < 90
                           and nearby.x0 <= label.x1 + page.rect.width * .32
                           and nearby.x1 >= label.x0 - page.rect.width * .32]
                source = f"《{filename}》PDF 第 {number} 页"
                figures.append(PdfFigure(output, caption, f"图片来源：{source}\n原图标注：{caption}",
                                         " ".join(context)[:350]))
                if len(figures) >= limit:
                    return figures
    return figures
