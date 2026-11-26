"""读取 DOCX 模板并生成带实验步骤和图片的 Word 报告。"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from . import CONSTANT


PLACEHOLDER = re.compile(r"\{\{([a-zA-Z_][a-zA-Z_0-9]*)\}\}")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}


def convert_doc_template(path: Path) -> Path:
    if path.suffix.lower() != ".doc" or path.read_bytes()[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError(CONSTANT.DOC_TEMPLATE_INVALID)
    output = path.with_suffix(".docx")
    office = shutil.which("soffice") or shutil.which("libreoffice")
    with tempfile.TemporaryDirectory() as profile:
        if office:
            command = [office, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "docx", "--outdir", str(path.parent), str(path)]
        elif shutil.which("powershell"):
            command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                       str(Path(__file__).with_name("convert_doc.ps1")), str(path.resolve()), str(output.resolve())]
        else:
            raise ValueError(CONSTANT.DOC_CONVERTER_REQUIRED)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(CONSTANT.DOC_CONVERSION_ERROR) from exc
    if result.returncode != 0 or not output.is_file():
        raise ValueError(CONSTANT.DOC_CONVERSION_FAILED)
    inspect_report_template(output)
    return output


def _open_template(path: Path | None):
    try:
        return Document(path) if path else Document()
    except Exception as exc:
        raise ValueError(CONSTANT.DOCX_TEMPLATE_INVALID) from exc


def _paragraphs(document):
    yield from document.paragraphs
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def _add_styled_paragraph(document, text: str, style_name: str, size: int):
    paragraph = document.add_paragraph(text)
    if style_name in document.styles:
        paragraph.style = style_name
    else:
        for run in paragraph.runs:
            run.bold = style_name != "Caption"
            run.italic = style_name == "Caption"
            run.font.size = Pt(size)
    return paragraph


def _section_anchor(paragraphs, heading: str, used: set[int]):
    for paragraph in paragraphs:
        if id(paragraph._p) not in used and paragraph.text.strip() == heading.strip():
            used.add(id(paragraph._p))
            return paragraph
    return None


def _clean_template_cells(document) -> None:
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs = list(cell.paragraphs)
                index = 0
                while index < len(paragraphs):
                    text = paragraphs[index].text.strip()
                    if text.startswith("{") and not text.startswith("{{"):
                        closing = next((end for end in range(index, len(paragraphs))
                                        if paragraphs[end].text.strip().endswith("}")), None)
                        if closing is not None:
                            for paragraph in paragraphs[index:closing + 1]:
                                paragraph._p.getparent().remove(paragraph._p)
                            index = closing + 1
                            continue
                    index += 1
                for paragraph in list(cell.paragraphs):
                    if (not paragraph.text.strip() and len(cell.paragraphs) > 1
                            and not paragraph._p.xpath(".//w:drawing | .//w:pict | .//w:object | .//w:br")):
                        paragraph._p.getparent().remove(paragraph._p)
                if not cell.paragraphs:
                    cell.add_paragraph()


def _add_code(document, source: str):
    paragraphs = []
    previous_empty = False
    for line in source.rstrip("\n").split("\n"):
        if not line.strip() and previous_empty:
            continue
        previous_empty = not line.strip()
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.left_indent = Cm(0.2)
        paragraph.paragraph_format.line_spacing = 1.0
        run = paragraph.add_run(line or " ")
        run.font.name = "Consolas"
        run.font.size = Pt(8)
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), "F2F4F6")
        paragraph._p.get_or_add_pPr().append(shading)
        paragraphs.append(paragraph)
    return paragraphs


def _add_body_paragraph(document, text: str):
    paragraph = document.add_paragraph(text.strip())
    paragraph.paragraph_format.space_after = Pt(5)
    paragraph.paragraph_format.line_spacing = 1.2
    return paragraph


def inspect_report_template(path: Path) -> dict[str, Any]:
    document = _open_template(path)
    _clean_template_cells(document)
    paragraphs = [paragraph.text.strip() for paragraph in _paragraphs(document) if paragraph.text.strip()]
    preview = paragraphs[:80]
    return {
        "paragraphs": preview,
        "placeholders": sorted(set(PLACEHOLDER.findall("\n".join(paragraphs)))),
        "truncated": len(paragraphs) > len(preview),
        "content_marker": "content" in set(PLACEHOLDER.findall("\n".join(paragraphs))),
    }


def _replace_placeholders(document, values: dict[str, str]) -> None:
    for paragraph in _paragraphs(document):
        matches = list(PLACEHOLDER.finditer(paragraph.text))
        for match in reversed(matches):
            replacement = values.get(match.group(1))
            if replacement is None:
                continue
            if not paragraph.runs:
                paragraph.text = paragraph.text[:match.start()] + replacement + paragraph.text[match.end():]
                continue
            position = 0
            first = last = None
            for index, run in enumerate(paragraph.runs):
                next_position = position + len(run.text)
                if first is None and position <= match.start() < next_position:
                    first = (index, match.start() - position)
                if position <= match.end() - 1 < next_position:
                    last = (index, match.end() - position)
                    break
                position = next_position
            if first is None or last is None:
                raise ValueError(CONSTANT.TEMPLATE_PLACEHOLDER_INVALID)
            first_index, first_offset = first
            last_index, last_offset = last
            if first_index == last_index:
                run = paragraph.runs[first_index]
                run.text = run.text[:first_offset] + replacement + run.text[last_offset:]
            else:
                paragraph.runs[first_index].text = paragraph.runs[first_index].text[:first_offset] + replacement
                for run in paragraph.runs[first_index + 1:last_index]:
                    run.text = ""
                paragraph.runs[last_index].text = paragraph.runs[last_index].text[last_offset:]


def write_experiment_report(
    task_dir: Path,
    output_path: Path,
    title: str,
    sections: list[dict[str, Any]],
    template_path: Path | None = None,
    replacements: dict[str, str] | None = None,
) -> Path:
    root = task_dir.resolve()
    output = output_path.resolve()
    if not output.is_relative_to(root) or output.suffix.lower() != ".docx":
        raise ValueError(CONSTANT.REPORT_OUTPUT_INVALID)
    if not title.strip() or not sections or len(sections) > 30:
        raise ValueError(CONSTANT.REPORT_TITLE_OR_SECTION_COUNT_INVALID)
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError(CONSTANT.SECTION_TITLE_OR_BODY_MISSING)
        heading = section.get("heading")
        paragraphs = section.get("paragraphs", [])
        images = section.get("images", [])
        code_files = section.get("code_files", [])
        code_blocks = section.get("code_blocks", [])
        if (not isinstance(heading, str) or not heading.strip() or not isinstance(paragraphs, list)
                or not isinstance(images, list) or not isinstance(code_files, list) or not isinstance(code_blocks, list)):
            raise ValueError(CONSTANT.SECTION_FIELDS_MISSING)
        if not any(isinstance(paragraph, str) and paragraph.strip() for paragraph in paragraphs) and not images and not code_files and not code_blocks:
            raise ValueError(CONSTANT.SECTION_CONTENT_MISSING.format(heading=heading))
    document = _open_template(template_path)
    template_paragraphs = list(_paragraphs(document)) if template_path else []
    marker = next((paragraph for paragraph in _paragraphs(document) if "{{content}}" in paragraph.text), None)
    preview = inspect_report_template(template_path) if template_path else {"placeholders": []}
    if template_path:
        _clean_template_cells(document)
    values = {**(replacements or {}), "title": title}
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
        raise ValueError(CONSTANT.TEMPLATE_PLACEHOLDER_VALUE_INVALID)
    _replace_placeholders(document, values)
    if "title" not in preview["placeholders"]:
        first_paragraph = document.paragraphs[0] if document.paragraphs else None
        title_paragraph = _add_styled_paragraph(document, title, "Title", 20)
        if first_paragraph is not None:
            first_paragraph._p.addprevious(title_paragraph._p)
    image_count = 0
    used_anchors: set[int] = set()
    for section in sections:
        heading = section.get("heading")
        paragraphs = section.get("paragraphs", [])
        images = section.get("images", [])
        anchor = _section_anchor(template_paragraphs, heading, used_anchors) if marker is None else None
        image_width = Cm(14)
        container = anchor or marker
        if container is not None and getattr(container._parent, "width", None) and container._parent.width > Cm(1):
            image_width = min(image_width, container._parent.width - Cm(0.5))
        added = [] if anchor is not None else [_add_styled_paragraph(document, heading, "Heading 1", 16)]
        for paragraph in paragraphs:
            if not isinstance(paragraph, str) or len(paragraph) > 10000:
                raise ValueError(CONSTANT.SECTION_PARAGRAPH_INVALID)
            if paragraph.strip():
                added.append(_add_body_paragraph(document, paragraph))
        for code_file in section.get("code_files", []):
            if not isinstance(code_file, str):
                raise ValueError(CONSTANT.CODE_FILE_PATH_INVALID)
            source_path = (root / code_file).resolve()
            if not source_path.is_relative_to(root) or source_path.suffix.lower() != ".py" or not source_path.is_file() or source_path.stat().st_size > 100_000:
                raise ValueError(CONSTANT.REPORT_CODE_FILE_INVALID)
            added.extend(_add_code(document, source_path.read_text(encoding="utf-8")))
        for code_block in section.get("code_blocks", []):
            if not isinstance(code_block, str) or not code_block.strip() or len(code_block) > 10000:
                raise ValueError(CONSTANT.CODE_BLOCK_INVALID)
            added.extend(_add_code(document, code_block))
        for image in images:
            image_count += 1
            if image_count > 40 or not isinstance(image, dict):
                raise ValueError(CONSTANT.TOO_MANY_REPORT_IMAGES)
            image_path = Path(str(image.get("path", "")))
            source = (root / image_path).resolve() if not image_path.is_absolute() else image_path.resolve()
            if not source.is_relative_to(root) or source.suffix.lower() not in IMAGE_EXTENSIONS or not source.is_file():
                raise ValueError(CONSTANT.REPORT_IMAGE_INVALID)
            picture = document.add_paragraph()
            picture.add_run().add_picture(str(source), width=image_width)
            picture.paragraph_format.space_after = Pt(0)
            added.append(picture)
            caption = image.get("caption")
            if caption:
                added.append(_add_styled_paragraph(document, str(caption), "Caption", 9))
        if marker is not None:
            for paragraph in added:
                marker._p.addprevious(paragraph._p)
        elif anchor is not None:
            position = anchor._p
            for paragraph in added:
                position.addnext(paragraph._p)
                position = paragraph._p
    if template_path and marker is None and not used_anchors and len([paragraph for paragraph in template_paragraphs if paragraph.text.strip()]) > 3:
        raise ValueError(CONSTANT.REPORT_SECTION_TEMPLATE_MISMATCH)
    if marker is not None:
        marker._p.getparent().remove(marker._p)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    return output
