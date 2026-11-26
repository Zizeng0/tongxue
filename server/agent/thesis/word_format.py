"""Extract and apply thesis formatting to an existing Word document."""

import re
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError
from pydantic import BaseModel, ConfigDict, Field

from . import CONSTANT


Role = Literal["title", "heading1", "heading2", "heading3", "section_heading", "body", "caption", "table", "references"]
ROLE_LABELS: dict[Role, str] = {
    "title": "论文题目", "heading1": "一级标题", "heading2": "二级标题", "heading3": "三级标题",
    "section_heading": "摘要等独立章节标题", "body": "正文", "caption": "图表题注", "table": "表格文字",
    "references": "参考文献条目",
}
ALIGNMENTS = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
              "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
MARGINS = {"top_cm": "top_margin", "bottom_cm": "bottom_margin", "left_cm": "left_margin", "right_cm": "right_margin"}
PAPER_SIZES = {"a4": (21, 29.7), "letter": (21.59, 27.94)}
SECTION_HEADINGS = {"摘要", "abstract", "目录", "参考文献", "references", "致谢", "附录", "bibliography"}


class ParagraphFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chinese_font: str | None = Field(None, min_length=1, max_length=80)
    latin_font: str | None = Field(None, min_length=1, max_length=80)
    size_pt: float | None = Field(None, ge=5, le=96)
    bold: bool | None = None
    color_hex: str | None = Field(None, pattern=r"^[0-9a-fA-F]{6}$")
    remove_border: bool | None = None
    alignment: Literal["left", "center", "right", "justify"] | None = None
    line_multiple: float | None = Field(None, ge=0.5, le=4)
    line_exact_pt: float | None = Field(None, ge=5, le=100)
    first_line_chars: float | None = Field(None, ge=-8, le=8)
    before_pt: float | None = Field(None, ge=0, le=100)
    after_pt: float | None = Field(None, ge=0, le=100)


class PageMargins(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_cm: float | None = Field(None, ge=0, le=8)
    bottom_cm: float | None = Field(None, ge=0, le=8)
    left_cm: float | None = Field(None, ge=0, le=8)
    right_cm: float | None = Field(None, ge=0, le=8)


class ThesisFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper: Literal["a4", "letter"] | None = None
    margins: PageMargins = Field(default_factory=PageMargins)
    paragraphs: dict[Role, ParagraphFormat] = Field(default_factory=dict)

    def with_overrides(self, overrides: "ThesisFormat") -> "ThesisFormat":
        margins = {**self.margins.model_dump(exclude_none=True), **overrides.margins.model_dump(exclude_none=True)}
        styles = {role: style.model_dump(exclude_none=True) for role, style in self.paragraphs.items()}
        for role, style in overrides.paragraphs.items():
            fields = style.model_dump(exclude_none=True)
            base = styles.setdefault(role, {})
            if "line_multiple" in fields:
                base.pop("line_exact_pt", None)
            if "line_exact_pt" in fields:
                base.pop("line_multiple", None)
            base.update(fields)
        return ThesisFormat(paper=overrides.paper or self.paper, margins=margins, paragraphs=styles)


# Based on the generic defaults in Chinese Thesis Workbench, not a school template.
DEFAULT_FORMAT = ThesisFormat(paragraphs={
    "title": ParagraphFormat(chinese_font="黑体", size_pt=18, bold=True, alignment="center", color_hex="000000", remove_border=True),
    "heading1": ParagraphFormat(chinese_font="黑体", size_pt=18, bold=True, color_hex="000000"),
    "heading2": ParagraphFormat(chinese_font="黑体", size_pt=15, bold=True, color_hex="000000"),
    "heading3": ParagraphFormat(chinese_font="黑体", size_pt=12, bold=True, color_hex="000000"),
    "section_heading": ParagraphFormat(chinese_font="黑体", size_pt=18, bold=True, alignment="center", before_pt=12, color_hex="000000"),
    "body": ParagraphFormat(chinese_font="宋体", latin_font="Times New Roman", size_pt=12, line_multiple=1.25, first_line_chars=2),
    "caption": ParagraphFormat(chinese_font="宋体", size_pt=10.5, alignment="center", line_multiple=1),
    "table": ParagraphFormat(chinese_font="宋体", size_pt=10.5, alignment="center"),
    "references": ParagraphFormat(chinese_font="宋体", size_pt=10.5, first_line_chars=-2),
})


def open_thesis(source):
    """Validate the ZIP before loading the Word package; never run Office on uploads."""
    try:
        with ZipFile(source) as archive:
            entries = archive.infolist()
            names = {entry.filename.lower() for entry in entries}
            if (len(entries) != len(names) or len(entries) > 4000
                    or sum(entry.file_size for entry in entries) > 150 * 1024 * 1024
                    or any(entry.flag_bits & 1 for entry in entries)
                    or any(name.endswith("vbaproject.bin") or name.startswith(("word/activex/", "word/embeddings/"))
                           for name in names)):
                raise ValueError(CONSTANT.WORD_UNSUPPORTED)
        return Document(source)
    except (BadZipFile, PackageNotFoundError, KeyError, TypeError, XMLSyntaxError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(CONSTANT.WORD_INVALID) from exc


def validate_thesis(content: bytes) -> None:
    open_thesis(BytesIO(content))


def read_word_standard(source: Path, limit: int) -> tuple[str, bool]:
    document = open_thesis(source)
    parts = []
    remaining = limit
    for _, paragraph in thesis_paragraphs(document):
        text = paragraph.text.strip()
        if text:
            if len(text) + 1 > remaining:
                parts.append(text[:remaining])
                return "\n".join(parts), True
            parts.append(text)
            remaining -= len(text) + 1
    return "\n".join(parts), False


def paragraph_role(paragraph: Paragraph, text: str, in_references: bool) -> Role:
    heading = re.sub(r"\s+", "", text).casefold()
    style = paragraph.style
    style_name = (style.name or "") if style else ""
    if heading in SECTION_HEADINGS or re.fullmatch(r"附录[一二三四五六七八九十A-Za-z0-9]", heading):
        return "section_heading"
    if style_name in {"Title", "标题", "论文题目"}:
        return "title"
    if heading_style := re.fullmatch(r"(?:Heading|标题)\s*([1-3])", style_name, re.I):
        return f"heading{heading_style[1]}"
    if style_name in {"Caption", "题注"} or re.match(r"^(?:图|表|Fig\.?|Table)\s*\d", text, re.I):
        return "caption"
    if style_name in {"Bibliography", "References", "参考文献正文"} or in_references:
        return "references"
    if re.match(r"^第\s*[一二三四五六七八九十百零\d]+\s*章", text):
        return "heading1"
    if len(text) <= 80 and (number := re.match(r"^(\d+(?:\.\d+){1,2})\s+\S", text)):
        return "heading3" if number[1].count(".") == 2 else "heading2"
    return "body"


def thesis_paragraphs(document):
    in_references = False
    for element in document.element.body.xpath(".//w:p[not(ancestor::w:txbxContent)]"):
        paragraph = Paragraph(element, document._body)
        text = "".join(node.text or "" for node in element.xpath(".//w:t[not(ancestor::w:txbxContent)]")).strip()
        if not text:
            continue
        if element.xpath("ancestor::w:tc"):
            yield "table", paragraph
            continue
        role = paragraph_role(paragraph, text, in_references)
        if role == "section_heading" or role.startswith("heading"):
            in_references = re.sub(r"\s+", "", text).casefold() in {"参考文献", "references", "bibliography"}
        yield role, paragraph


def property_sources(paragraph, run=None):
    if run is not None:
        yield run.rPr
    if paragraph._p.pPr is not None:
        yield paragraph._p.pPr.rPr if run is not None else paragraph._p.pPr
    style = paragraph.style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        yield style.element.rPr if run is not None else style.element.pPr
        style = style.base_style


def style_value(sources, tag, attr="val"):
    for source in sources:
        if source is not None and (child := source.find(qn(f"w:{tag}"))) is not None:
            if (value := child.get(qn(f"w:{attr}"))) is not None:
                return value
    return None


def text_runs(paragraph):
    return paragraph._p.xpath(".//w:r[w:t and not(ancestor::w:txbxContent)]")


def observed_format(paragraph: Paragraph) -> ParagraphFormat:
    run = next(iter(text_runs(paragraph)), None)
    rpr = list(property_sources(paragraph, run)) if run is not None else []
    ppr = list(property_sources(paragraph))
    fields = {}
    for field, tag, attribute, scale in (
        ("size_pt", "sz", "val", 2), ("before_pt", "spacing", "before", 20),
        ("after_pt", "spacing", "after", 20), ("first_line_chars", "ind", "firstLineChars", 100),
    ):
        if (value := style_value(rpr if tag == "sz" else ppr, tag, attribute)) is not None:
            fields[field] = int(value) / scale
    if (hanging := style_value(ppr, "ind", "hangingChars")) is not None:
        fields["first_line_chars"] = -int(hanging) / 100
    for field, attribute in (("chinese_font", "eastAsia"), ("latin_font", "ascii")):
        if value := style_value(rpr, "rFonts", attribute):
            fields[field] = value
    if (color := style_value(rpr, "color")) and re.fullmatch(r"[0-9a-fA-F]{6}", color):
        fields["color_hex"] = color
    for source in rpr:
        if source is not None and (bold := source.find(qn("w:b"))) is not None:
            fields["bold"] = bold.get(qn("w:val"), "1") not in {"0", "false", "off"}
            break
    if (alignment := style_value(ppr, "jc")) in {"left", "center", "right", "both"}:
        fields["alignment"] = "justify" if alignment == "both" else alignment
    if spacing := style_value(ppr, "spacing", "line"):
        rule = style_value(ppr, "spacing", "lineRule") or "auto"
        if rule == "auto":
            fields["line_multiple"] = int(spacing) / 240
        elif rule == "exact":
            fields["line_exact_pt"] = int(spacing) / 20
    return ParagraphFormat.model_validate(fields)


def sample_thesis_format(source: Path) -> ThesisFormat:
    document = open_thesis(source)
    observations = {}
    for role, paragraph in thesis_paragraphs(document):
        fields = observations.setdefault(role, {})
        for field, value in observed_format(paragraph).model_dump(exclude_none=True).items():
            fields.setdefault(field, []).append(value)
    margins = {}
    for key, attr in MARGINS.items():
        values = [round(value.cm, 3) for section in document.sections if (value := getattr(section, attr)) is not None]
        if values and len(set(values)) == 1:
            margins[key] = values[0]
    styles = {}
    for role, fields in observations.items():
        style = {field: values[0] for field, values in fields.items() if len(set(values)) == 1}
        if "line_multiple" in style and "line_exact_pt" in style:
            style.pop("line_multiple")
            style.pop("line_exact_pt")
        if style:
            styles[role] = style
    paper = next((name for name, size in PAPER_SIZES.items()
                  if all(section.page_width is not None and section.page_height is not None
                         and all(abs(observed - expected) < 0.03 for observed, expected in
                                 zip(sorted((section.page_width.cm, section.page_height.cm)), size))
                         for section in document.sections)), None)
    return ThesisFormat(paper=paper, margins=margins, paragraphs=styles)


def apply_paragraph_format(paragraph: Paragraph, style: ParagraphFormat) -> None:
    formatting = paragraph.paragraph_format
    if style.alignment is not None:
        formatting.alignment = ALIGNMENTS[style.alignment]
    if style.line_multiple is not None:
        formatting.line_spacing = style.line_multiple
    elif style.line_exact_pt is not None:
        formatting.line_spacing = Pt(style.line_exact_pt)
        formatting.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    if style.before_pt is not None:
        formatting.space_before = Pt(style.before_pt)
    if style.after_pt is not None:
        formatting.space_after = Pt(style.after_pt)
    if style.first_line_chars is not None:
        indent = paragraph._p.get_or_add_pPr().get_or_add_ind()
        for attr in ("firstLine", "hanging", "firstLineChars", "hangingChars"):
            indent.attrib.pop(qn(f"w:{attr}"), None)
        attr = "hangingChars" if style.first_line_chars < 0 else "firstLineChars"
        indent.set(qn(f"w:{attr}"), str(round(abs(style.first_line_chars) * 100)))
        if style.first_line_chars < 0 and not any(key in indent.attrib for key in (qn("w:left"), qn("w:leftChars"))):
            indent.set(qn("w:leftChars"), str(round(-style.first_line_chars * 100)))
    if style.remove_border:
        properties = paragraph._p.get_or_add_pPr()
        if (border := properties.find(qn("w:pBdr"))) is not None:
            properties.remove(border)
        border = parse_xml(f'<w:pBdr {nsdecls("w")}><w:top w:val="nil"/><w:left w:val="nil"/>'
                           '<w:bottom w:val="nil"/><w:right w:val="nil"/><w:between w:val="nil"/></w:pBdr>')
        properties.insert_element_before(border, "w:shd", "w:tabs", "w:spacing", "w:ind", "w:jc", "w:rPr", "w:sectPr")
    if not any(value is not None for value in (style.chinese_font, style.latin_font, style.size_pt, style.bold, style.color_hex)):
        return
    for run in text_runs(paragraph):
        properties = run.get_or_add_rPr()
        if style.chinese_font or style.latin_font:
            fonts = properties.get_or_add_rFonts()
            for attr, value in (("eastAsia", style.chinese_font), ("ascii", style.latin_font), ("hAnsi", style.latin_font)):
                if value:
                    theme_attr = "eastAsiaTheme" if attr == "eastAsia" else f"{attr}Theme"
                    fonts.attrib.pop(qn(f"w:{theme_attr}"), None)
                    fonts.set(qn(f"w:{attr}"), value)
        if style.size_pt is not None:
            properties.get_or_add_sz().val = Pt(style.size_pt)
        if style.bold is not None:
            properties.get_or_add_b().val = style.bold
        if style.color_hex is not None:
            color = properties.get_or_add_color()
            for attribute in ("themeColor", "themeTint", "themeShade"):
                color.attrib.pop(qn(f"w:{attribute}"), None)
            color.set(qn("w:val"), style.color_hex.upper())


def format_thesis(source: Path, target: Path, spec: ThesisFormat) -> list[str]:
    document = open_thesis(source)
    changed = []
    margins = spec.margins.model_dump(exclude_none=True)
    if spec.paper or margins:
        for section in document.sections:
            if spec.paper:
                width, height = PAPER_SIZES[spec.paper]
                if (section.page_width or 0) > (section.page_height or 0):
                    width, height = height, width
                section.page_width, section.page_height = Cm(width), Cm(height)
            for field, value in margins.items():
                setattr(section, MARGINS[field], Cm(value))
            if ((section.page_width and (section.left_margin or 0) + (section.right_margin or 0) >= section.page_width)
                    or (section.page_height and (section.top_margin or 0) + (section.bottom_margin or 0) >= section.page_height)):
                raise ValueError(CONSTANT.MARGINS_INVALID)
        if spec.paper:
            changed.append("纸张尺寸")
        if margins:
            changed.append("页边距")
    for role, paragraph in thesis_paragraphs(document):
        style = spec.paragraphs.get(role)
        if style and style.model_dump(exclude_none=True):
            apply_paragraph_format(paragraph, style)
            if ROLE_LABELS[role] not in changed:
                changed.append(ROLE_LABELS[role])
    if not changed:
        return []
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        document.save(staging)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return changed
