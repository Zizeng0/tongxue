"""将室友的题解和配图写入可编辑的 Word 文档。"""

import re
from pathlib import Path
from uuid import uuid4

from docx import Document
from docx.shared import RGBColor

from .report_writer import write_experiment_report


IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+)$")
INLINE_FORMAT = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
WORD_FILENAME = "题解.docx"


def write_solution_document(task_dir: Path, message_id: str, reply: str, figures: list[str], *, title: str = "题解") -> Path:
    root = task_dir.resolve()
    output = root / "reports" / message_id / (WORD_FILENAME if title == "题解" else "文档.docx")
    image_paths = {relative: (root / relative).resolve() for relative in figures}
    images = {relative: path for relative, path in image_paths.items()
              if path.is_relative_to(root / "solutions") and path.is_file() and path.suffix.lower() == ".png"}

    sections = []
    section = {"heading": "解答", "paragraphs": [], "images": []}
    for line in INVALID_XML.sub("", reply).splitlines():
        line = line.strip()
        if not line or line in {"---", "***", "```"}:
            continue
        if heading := HEADING.fullmatch(line):
            if section["paragraphs"] or section["images"]:
                sections.append(section)
            section = {"heading": heading[1], "paragraphs": [], "images": []}
            continue
        for link in IMAGE_LINK.finditer(line):
            if path := images.pop(link[2], None):
                section["images"].append({"path": str(path), "caption": link[1]})
        line = INLINE_FORMAT.sub(lambda match: match[1] or match[2], IMAGE_LINK.sub("", line)).strip()
        section["paragraphs"].extend(line[index:index + 10000] for index in range(0, len(line), 10000))
    section["images"].extend({"path": str(path)} for path in images.values())
    if section["paragraphs"] or section["images"]:
        sections.append(section)
    if not sections:
        raise ValueError("题解没有可导出的内容。")
    if len(sections) > 30:
        last = sections[29]
        for extra in sections[30:]:
            last["paragraphs"].extend([extra["heading"], *extra["paragraphs"]])
            last["images"].extend(extra["images"])
        sections = sections[:30]
    staging = output.with_name(f".{uuid4().hex}.docx")
    try:
        write_experiment_report(root, staging, title, sections)
        document = Document(staging)
        title_style = document.styles["Title"]
        title_style.font.color.rgb = RGBColor(0, 0, 0)
        for border in title_style.element.xpath("./w:pPr/w:pBdr"):
            border.getparent().remove(border)
        document.save(staging)
        staging.replace(output)
    finally:
        staging.unlink(missing_ok=True)
    return output
