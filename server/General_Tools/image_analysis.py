"""只提取上传图片中的文字，不推断图片表达的其他内容。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError
from rapidocr_onnxruntime import RapidOCR

from server.General_Tools.pdf_analysis import OCR_CONFIDENCE_THRESHOLD, _different_numbers, _enhance, _ocr

from . import CONSTANT


MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


def read_image_text(source: str | Path | bytes | BinaryIO, output_path: str | Path | None = None) -> dict:
    """OCR 读取 JPG、PNG 等图片的文字，并标出低可信或数字冲突结果。"""
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError(CONSTANT.IMAGE_ANALYSIS_ERROR_IMAGE_EXCEEDS_MB_UPLOAD_LIMIT)
        image_source = path
    else:
        content = source if isinstance(source, bytes) else source.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError(CONSTANT.IMAGE_ANALYSIS_ERROR_IMAGE_EXCEEDS_MB_UPLOAD_LIMIT)
        image_source = io.BytesIO(content)
    try:
        with Image.open(image_source) as opened:
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise ValueError(CONSTANT.IMAGE_ANALYSIS_ERROR_IMAGE_EXCEEDS_PIXEL_LIMIT)
            image_format = opened.format
            image = opened.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(CONSTANT.IMAGE_ANALYSIS_ERROR_INPUT_NOT_READABLE_IMAGE) from exc

    try:
        engine = RapidOCR()
        original = _ocr(image, engine)
        enhanced = _ocr(_enhance(image), engine)
    except Exception as exc:
        raise ValueError(CONSTANT.IMAGE_ANALYSIS_ERROR_IMAGE_OCR_FAILED) from exc
    best = max((original, enhanced), key=lambda result: result["mean_confidence"])
    numbers_disagree = _different_numbers(original["text"], enhanced["text"])
    result = {
        "image_format": image_format,
        "width": image.width,
        "height": image.height,
        "text": best["text"],
        "ocr_mean_confidence": best["mean_confidence"],
        "ocr_words": best["words"],
        "ocr_original": original["text"],
        "ocr_enhanced": enhanced["text"],
        "ocr_variants_disagree_on_numbers": numbers_disagree,
        "needs_review": not best["text"] or best["mean_confidence"] < OCR_CONFIDENCE_THRESHOLD or numbers_disagree,
    }
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
