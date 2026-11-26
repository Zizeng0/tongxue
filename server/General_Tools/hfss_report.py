"""从实验记录与 HFSS 截图生成可核查的 Word 实验报告。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image as PillowImage

if __package__:
    from . import CONSTANT
else:
    import CONSTANT


SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
CAPTURE_METHODS = {"HFSS ExportModelImageToFile", "HFSS window capture"}
MIN_SCREENSHOTS = 10


@dataclass(frozen=True)
class Screenshot:
    step: str
    action: str
    observation: str
    caption: str
    path: Path
    capture_method: str


@dataclass(frozen=True)
class ReportSpec:
    title: str
    introduction: str
    scope: str
    source_file: Path
    source_guide: str
    outcome: str
    limitation: str
    evidence_files: tuple[Path, ...]
    screenshots: tuple[Screenshot, ...]


def _resolve_existing(base: Path, value: str, *, label: str) -> Path:
    path = Path(value)
    resolved = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(CONSTANT.HFSS_REPORT_ERROR_DOES_NOT_EXIST.format(label=label, resolved=resolved))
    return resolved


def parse_report_spec(data: dict[str, Any], base: Path, *, min_screenshots: int = MIN_SCREENSHOTS) -> ReportSpec:
    """校验实验来源与截图；缺少实证时阻止报告生成。"""
    for field in ("title", "introduction", "scope", "source_guide", "outcome", "limitation"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(CONSTANT.HFSS_REPORT_ERROR_REPORT_FIELD_MUST_BE_NONEMPTY.format(field=field))
    source_file = _resolve_existing(base, data["source_file"], label="HFSS source")
    if source_file.suffix.lower() != ".hfss":
        raise ValueError(CONSTANT.HFSS_REPORT_ERROR_EXPERIMENT_SOURCE_MUST_BE_HFSS_FILE)
    raw_screenshots = data.get("screenshots")
    if not isinstance(raw_screenshots, list) or len(raw_screenshots) < min_screenshots:
        raise ValueError(CONSTANT.HFSS_REPORT_ERROR_EACH_REPORT_NEEDS_AT_LEAST_HFSS.format(min_screenshots=min_screenshots))

    screenshots = []
    seen_hashes = set()
    for index, item in enumerate(raw_screenshots, 1):
        for field in ("step", "action", "observation", "caption"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(CONSTANT.HFSS_REPORT_ERROR_SCREENSHOT_NEEDS.format(index=index, field=field))
        if item.get("capture_method") not in CAPTURE_METHODS:
            raise ValueError(CONSTANT.HFSS_REPORT_ERROR_SCREENSHOT_NEEDS_HFSS_CAPTURE_METHOD.format(index=index))
        path = _resolve_existing(base, item["path"], label=f"Screenshot {index}")
        if path.suffix.lower() not in SUPPORTED_IMAGES:
            raise ValueError(CONSTANT.HFSS_REPORT_ERROR_UNSUPPORTED_SCREENSHOT_FORMAT.format(suffix=path.suffix))
        with PillowImage.open(path) as image:
            image.verify()
        digest = hashlib.sha256(path.read_bytes()).digest()
        if digest in seen_hashes:
            raise ValueError(CONSTANT.HFSS_REPORT_ERROR_REPORT_SCREENSHOTS_MUST_CONTAIN_DIFFERENT_IMAGE)
        seen_hashes.add(digest)
        screenshots.append(Screenshot(
            step=item["step"].strip(),
            action=item["action"].strip(),
            observation=item["observation"].strip(),
            caption=item["caption"].strip(),
            path=path,
            capture_method=item["capture_method"],
        ))

    evidence_files = tuple(_resolve_existing(base, value, label="Evidence file") for value in data.get("evidence_files", []))
    return ReportSpec(
        title=data["title"].strip(),
        introduction=data["introduction"].strip(),
        scope=data["scope"].strip(),
        source_file=source_file,
        source_guide=data["source_guide"].strip(),
        outcome=data["outcome"].strip(),
        limitation=data["limitation"].strip(),
        evidence_files=evidence_files,
        screenshots=tuple(screenshots),
    )


def _set_font(style, size: int, *, bold: bool = False) -> None:
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)


def _add_summary(document: Document, spec: ReportSpec) -> None:
    document.add_paragraph(spec.title, style="Title")
    document.add_paragraph(spec.introduction)
    document.add_heading("实验范围与文件", level=1)
    document.add_paragraph(spec.scope)
    table = document.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in (
        ("指导文件", spec.source_guide),
        ("HFSS 源工程", spec.source_file.name),
        ("操作截图", f"{len(spec.screenshots)} 张"),
    ):
        cells = table.add_row().cells
        cells[0].text, cells[1].text = label, value
        for cell in cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    table.columns[0].width = Cm(3.4)
    table.columns[1].width = Cm(13.7)
    document.add_heading("实验结果", level=1)
    document.add_paragraph(spec.outcome)
    document.add_heading("结果限制", level=1)
    document.add_paragraph(spec.limitation)


def _add_screenshot(document: Document, screenshot: Screenshot, number: int) -> None:
    for paragraph in (
        document.add_heading(f"操作画面 {number}", level=2),
        document.add_paragraph(f"对应指导书步骤：{screenshot.step}"),
        document.add_paragraph(screenshot.action),
        document.add_paragraph(screenshot.observation),
    ):
        paragraph.paragraph_format.keep_with_next = True
    with PillowImage.open(screenshot.path) as image:
        width, height = image.size
    width_cm = min(13.2, 8.5 * width / height)
    picture = document.add_paragraph()
    picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
    picture.paragraph_format.keep_with_next = True
    picture.add_run().add_picture(str(screenshot.path), width=Cm(width_cm))
    caption = document.add_paragraph(f"图 {number}  {screenshot.caption}")
    caption.style = "Caption"
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER


def build_report(spec: ReportSpec, output_path: str | Path) -> Path:
    """按实验步骤写入文字和原始截图。"""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21.59)
    section.page_height = Cm(27.94)
    section.top_margin = Cm(1.75)
    section.bottom_margin = Cm(1.75)
    section.left_margin = Cm(2.25)
    section.right_margin = Cm(2.25)
    _set_font(document.styles["Normal"], 10)
    _set_font(document.styles["Title"], 17, bold=True)
    title_properties = document.styles["Title"]._element.get_or_add_pPr()
    existing_borders = title_properties.find(qn("w:pBdr"))
    if existing_borders is not None:
        title_properties.remove(existing_borders)
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "nil")
    borders.append(bottom)
    title_properties.append(borders)
    _set_font(document.styles["Heading 1"], 13, bold=True)
    _set_font(document.styles["Heading 2"], 11, bold=True)
    _set_font(document.styles["Caption"], 9)
    document.styles["Normal"].paragraph_format.space_after = Pt(8)
    document.styles["Normal"].paragraph_format.line_spacing = 1.3
    _add_summary(document, spec)
    document.add_page_break()
    document.add_heading("操作步骤与截图", level=1)
    for index, screenshot in enumerate(spec.screenshots, 1):
        _add_screenshot(document, screenshot, index)
    document.add_heading("证据文件", level=1)
    if spec.evidence_files:
        paths = (
            str(path.relative_to(spec.source_file.parent) if path.is_relative_to(spec.source_file.parent) else path)
            for path in spec.evidence_files
        )
        document.add_paragraph("；".join(paths), style="Normal")
    document.save(output)
    return output


def build_reports(manifest_path: str | Path, output_dir: str | Path) -> tuple[Path, Path]:
    """一次校验两份报告，防止只生成部分成果。"""
    manifest = Path(manifest_path).resolve()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if set(data) != {"tee_22_23", "modeling_41_48"}:
        raise ValueError(CONSTANT.HFSS_REPORT_ERROR_MANIFEST_MUST_CONTAIN_TEE_MODELING)
    specs = []
    for key in ("tee_22_23", "modeling_41_48"):
        entry = data[key]
        base = manifest.parent
        if isinstance(entry, str):
            spec_path = _resolve_existing(base, entry, label=f"{key} report spec")
            entry = json.loads(spec_path.read_text(encoding="utf-8"))
            base = spec_path.parent
        specs.append(parse_report_spec(entry, base))
    output = Path(output_dir).resolve()
    names = ("HFSS_2_2_2_3_实验报告.docx", "HFSS_4_1_4_8_实验报告.docx")
    return tuple(build_report(spec, output / name) for spec, name in zip(specs, names))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=CONSTANT.HFSS_REPORT_DESCRIPTION_CREATE_TWO_HFSS_WORD_REPORTS_FROM)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    arguments = parser.parse_args()
    for path in build_reports(arguments.manifest, arguments.output_dir):
        print(path)


if __name__ == "__main__":
    main()
