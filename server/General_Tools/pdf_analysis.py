"""从上传的 PDF 文档中提取页面文字和视觉证据。"""

from __future__ import annotations

import io
import json
import mmap
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

import pymupdf
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from rapidocr_onnxruntime import RapidOCR

if __package__:
    from . import CONSTANT
else:
    import CONSTANT


MAX_INPUT_BYTES = 120 * 1024 * 1024
MAX_RENDER_PIXELS = 25_000_000
OCR_CONFIDENCE_THRESHOLD = 70
NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")


def _ocr(image: Image.Image, engine: RapidOCR) -> dict:
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    detections, _ = engine(encoded.getvalue())
    words = []
    confidences = []
    for polygon, word, confidence in detections or []:
        confidence = float(confidence)
        confidences.append(confidence)
        words.append({
            "text": word,
            "confidence": round(confidence * 100, 1),
            "polygon": [[round(coordinate) for coordinate in point] for point in polygon],
        })
    return {
        "text": "\n".join(word["text"] for word in words),
        "mean_confidence": round(100 * sum(confidences) / len(confidences), 1) if confidences else 0,
        "words": words,
    }


def _enhance(image: Image.Image) -> Image.Image:
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    return ImageEnhance.Contrast(gray.filter(ImageFilter.MedianFilter(3))).enhance(1.3)


def _different_numbers(first: str, second: str) -> bool:
    return NUMBER_PATTERN.findall(first) != NUMBER_PATTERN.findall(second)


def _read_source(source: bytes | BinaryIO) -> bytes:
    if isinstance(source, bytes):
        content = source
    else:
        content = source.read(MAX_INPUT_BYTES + 1)
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_PDF_EXCEEDS_MB_UPLOAD_LIMIT)
    if not content.startswith(b"%PDF-"):
        raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_INPUT_NOT_PDF)
    return content


@contextmanager
def _open_document(source: str | Path | bytes | BinaryIO):
    with ExitStack() as resources:
        if isinstance(source, (str, Path)):
            path = Path(source)
            size = path.stat().st_size
            if size > MAX_INPUT_BYTES:
                raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_PDF_EXCEEDS_MB_UPLOAD_LIMIT)
            input_file = resources.enter_context(path.open("rb"))
            if input_file.read(5) != b"%PDF-":
                raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_INPUT_NOT_PDF)
            mapped = resources.enter_context(mmap.mmap(input_file.fileno(), 0, access=mmap.ACCESS_READ))
            view = memoryview(mapped)
            resources.callback(view.release)
            content = view
        else:
            content = _read_source(source)
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_PDF_COULD_NOT_BE_OPENED) from exc
        resources.enter_context(document)
        yield document


def analyze_pdf(
    source: str | Path | bytes | BinaryIO,
    output_dir: str | Path,
    *,
    dpi: int = 300,
) -> dict:
    """保存页面证据并返回兼容 JSON 的页面级分析。

    ``source`` 接受已保存的前端上传路径、其字节，或 ``UploadFile.file``。该函数是同步的；在处理异步 FastAPI 请求时，请在工作线程中调用它。
    """
    if not 72 <= dpi <= 400:
        raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_DPI_MUST_BE_BETWEEN)
    output_dir = Path(output_dir)
    with _open_document(source) as document:
        if document.needs_pass:
            raise ValueError(CONSTANT.PDF_ANALYSIS_ERROR_PASSWORD_PROTECTED_PDF_NOT_SUPPORTED)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_dir = output_dir / "pages"
        image_dir.mkdir(exist_ok=True)
        extracted_dir = output_dir / "embedded_images"
        extracted_dir.mkdir(exist_ok=True)
        pages = []
        ocr_error = None
        try:
            engine = RapidOCR()
        except Exception as exc:
            engine = None
            ocr_error = str(exc)
        for page_index, page in enumerate(document):
            number = page_index + 1
            native_text = page.get_text("text", sort=True).strip()
            source_image_dpi = None
            page_images = page.get_images(full=True)
            for item in page_images:
                for rectangle in page.get_image_rects(item[0]):
                    if rectangle.get_area() >= page.rect.get_area() * 0.8:
                        image_dpi = min(item[2] / rectangle.width, item[3] / rectangle.height) * 72
                        source_image_dpi = round(image_dpi) if source_image_dpi is None else min(source_image_dpi, round(image_dpi))
            scale = dpi / 72
            if page.rect.width * page.rect.height * scale * scale > MAX_RENDER_PIXELS:
                scale = (MAX_RENDER_PIXELS / (page.rect.width * page.rect.height)) ** 0.5
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                     colorspace=pymupdf.csRGB, alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            image_path = image_dir / f"page-{number:03}.png"
            image.save(image_path)
            enhanced = _enhance(image)
            enhanced_path = image_dir / f"page-{number:03}-enhanced.png"
            enhanced.save(enhanced_path)

            embedded_images = []
            for image_index, item in enumerate(page_images, start=1):
                image_data = document.extract_image(item[0])
                extension = image_data["ext"]
                target = extracted_dir / f"page-{number:03}-image-{image_index:03}.{extension}"
                target.write_bytes(image_data["image"])
                embedded_images.append(str(target))

            attempts = []
            if engine is not None:
                try:
                    attempts = [_ocr(image, engine), _ocr(enhanced, engine)]
                except Exception as exc:
                    ocr_error = str(exc)
                    engine = None
            best = max(attempts, key=lambda item: item["mean_confidence"], default=None)
            numbers_disagree = bool(attempts) and _different_numbers(attempts[0]["text"], attempts[1]["text"])
            if best and native_text:
                numbers_disagree |= _different_numbers(native_text, best["text"])
            needs_review = (
                ocr_error is not None or not native_text and not best
                or bool(best and best["mean_confidence"] < OCR_CONFIDENCE_THRESHOLD)
                or bool(source_image_dpi is not None and source_image_dpi < 150)
                or numbers_disagree
            )
            pages.append({
                "page": number,
                "native_text": native_text,
                "ocr_text": best["text"] if best else "",
                "ocr_mean_confidence": best["mean_confidence"] if best else None,
                "ocr_words": best["words"] if best else [],
                "ocr_original": attempts[0]["text"] if attempts else "",
                "ocr_enhanced": attempts[1]["text"] if attempts else "",
                "ocr_variants_disagree_on_numbers": numbers_disagree,
                "needs_review": needs_review,
                "page_image": str(image_path),
                "enhanced_image": str(enhanced_path),
                "embedded_images": embedded_images,
                "has_vector_drawings": bool(page.get_drawings()),
                "source_image_dpi": source_image_dpi,
                "rendered_dpi": round(scale * 72),
            })
        result = {
            "page_count": document.page_count,
            "ocr_engine": "rapidocr-onnxruntime",
            "ocr_error": ocr_error,
            "pages": pages,
        }
        (output_dir / "analysis.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result


async def analyze_upload(upload, output_dir: str | Path, **kwargs) -> dict:
    """在线程池中分析 FastAPI 上传文件，避免阻塞事件循环。"""
    from starlette.concurrency import run_in_threadpool

    await upload.seek(0)
    return await run_in_threadpool(analyze_pdf, upload.file, output_dir, **kwargs)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=CONSTANT.PDF_ANALYSIS_DESCRIPTION_EXTRACT_PDF_TEXT_IMAGES_OCR_EVIDENCE)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()
    result = analyze_pdf(args.pdf, args.output_dir, dpi=args.dpi)
    print(json.dumps({"page_count": result["page_count"], "ocr_error": result["ocr_error"],
                      "needs_review": [p["page"] for p in result["pages"] if p["needs_review"]],
                      "output": str(args.output_dir / "analysis.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
