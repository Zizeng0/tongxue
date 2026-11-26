"""比对教材扫描区域，定位已登记的习题。"""

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from .exercise_bank import DSP_4E_SHA256, image_candidates, normalize


SOURCE = (Path(__file__).resolve().parents[3] / "knowledge-base" / "data" / "documents"
          / DSP_4E_SHA256 / "source.pdf")
REGIONS = {
    # 2-23 的第二个版本兼容裁掉末尾句号的图片。
    (2, 23): (89, ((92, 257, 644, 375), (92, 257, 628, 375))),
    (2, 31): (90, ((120, 104, 585, 215),)),
    (3, 25): (125, ((80, 714, 680, 810),)),
}
WIDTH = 640
ASPECT_TOLERANCE = .045
LATEX = re.compile(r"\\\(.*?\\\)|\\\[.*?\\\]", re.DOTALL)


def ink_bounds(gray):
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    left, top, width, height = cv2.boundingRect(ink)
    return ink[top:top + height, left:left + width]


def normalized_ink(ink, height):
    resized = cv2.resize(ink, (WIDTH, height), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(resized, (5, 5), 1)


@lru_cache(maxsize=4)
def textbook_templates(path: Path, modified: int):
    with pymupdf.open(path) as document:
        templates = {}
        for identity, (page_number, regions) in REGIONS.items():
            page = document[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), colorspace=pymupdf.csGRAY)
            pixels = np.frombuffer(page.samples, dtype=np.uint8).reshape(page.height, page.width)
            variants = []
            for left, top, right, bottom in regions:
                ink = ink_bounds(pixels[top:bottom, left:right])
                height = round(WIDTH * ink.shape[0] / ink.shape[1])
                variants.append(normalized_ink(ink, height))
            templates[identity] = variants
    return templates


def same_ink(view, template) -> bool:
    if float(cv2.matchTemplate(view, template, cv2.TM_CCOEFF_NORMED)[0, 0]) < .90:
        return False
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    delta = cv2.max(cv2.subtract(view, cv2.dilate(template, kernel)),
                    cv2.subtract(template, cv2.dilate(view, kernel)))
    windows = cv2.boxFilter(delta.astype(np.float32), -1, (11, 11))
    return float(windows.max()) <= 55


def matches_template(ink, template) -> bool:
    height, width = template.shape
    if abs(ink.shape[0] / ink.shape[1] * width / height - 1) > ASPECT_TOLERANCE:
        return False
    view = normalized_ink(ink, height)
    if same_ink(view, template):
        return True
    # 保留边框，防止对齐时把有差异的符号移出比对区域。
    fixed = cv2.copyMakeBorder(template, 12, 12, 12, 12, cv2.BORDER_CONSTANT)
    moving = cv2.copyMakeBorder(view, 12, 12, 12, 12, cv2.BORDER_CONSTANT)
    try:
        _, transform = cv2.findTransformECC(
            fixed, moving, np.eye(2, 3, dtype=np.float32), cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 22, 1e-4),
        )
    except cv2.error:
        return False
    if (np.max(np.abs(np.diag(transform) - 1)) > .035 or
            max(abs(transform[0, 1]), abs(transform[1, 0])) > .012 or
            np.max(np.abs(transform[:, 2])) > 6):
        return False
    aligned = cv2.warpAffine(moving, transform, (fixed.shape[1], fixed.shape[0]),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    return same_ink(aligned, fixed)


def image_ink(content: bytes):
    try:
        with Image.open(BytesIO(content)) as image:
            if image.width * image.height > 25_000_000:
                return None
            gray = np.asarray(ImageOps.exif_transpose(image).convert("L"))
    except (OSError, Image.DecompressionBombError):
        return None
    ink = ink_bounds(gray)
    return ink if ink.size and min(ink.shape) >= 25 else None


def encode_template(ink):
    height = round(WIDTH * ink.shape[0] / ink.shape[1])
    if not 25 <= height <= 960:
        return None
    _, encoded = cv2.imencode(".png", normalized_ink(ink, height))
    return height, encoded.tobytes()


def find_similar(database_url, owner_id, ink):
    height = WIDTH * ink.shape[0] / ink.shape[1]
    lower = max(25, int(height / (1 + ASPECT_TOLERANCE)))
    upper = min(960, int(height / (1 - ASPECT_TOLERANCE)) + 1)
    hits = {}
    for encoded, exercise in image_candidates(database_url, owner_id, lower, upper):
        template = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_GRAYSCALE)
        if template is None or not matches_template(ink, template):
            continue
        key = LATEX.sub(lambda formula: re.sub(r"\s*=\s*", "=", formula[0]), normalize(exercise.transcription))
        if key not in hits or not hits[key].reusable_answer:
            hits[key] = exercise
        if len(hits) > 1:
            return None
    return next(iter(hits.values()), None)


def find_image_reference(ink) -> tuple[int, int] | None:
    if ink is None or not SOURCE.is_file():
        return None
    try:
        templates = textbook_templates(SOURCE, SOURCE.stat().st_mtime_ns)
    except (OSError, RuntimeError, ValueError):
        return None
    hits = [identity for identity, variants in templates.items() if any(matches_template(ink, template) for template in variants)]
    return hits[0] if len(hits) == 1 else None
