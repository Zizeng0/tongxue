"""Render validated slide content as editable PowerPoint slides."""

from pathlib import Path
from typing import Annotated, BinaryIO, Literal
from unicodedata import east_asian_width
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.exc import PackageNotFoundError
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt
from lxml.etree import XMLSyntaxError

from .templates import Category, PALETTES, TEMPLATES, Theme

from . import CONSTANT


PPTX_FILENAME = "演示文稿.pptx"
MAX_IMAGES = 12
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=70)]
Point = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=95)]
ImageNumber = Annotated[int, Field(ge=1, le=MAX_IMAGES)]
BODY_TYPES = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT}
MetricValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
MetricLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=35)]
TableCell = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=42)]
ModelLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=28)]


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: MetricValue
    label: MetricLabel


class ComparisonTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headers: list[TableCell] = Field(min_length=2, max_length=4)
    rows: list[list[TableCell]] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_width(self):
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError(CONSTANT.TABLE_COLUMNS_INVALID)
        return self


class ModelBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: ModelLabel
    relation: ModelLabel


class StructureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    center: ModelLabel
    branches: list[ModelBranch] = Field(min_length=1, max_length=3)


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: ModelLabel
    text: Point


class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_number: ImageNumber
    caption: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=55)]


class SlideContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    points: list[Point] = Field(default_factory=list, max_length=5)
    notes: str = Field(default="", max_length=3600)
    image_number: ImageNumber | None = None
    layout: Literal["list", "columns", "steps", "focus", "table", "metrics", "diagram", "image_left", "model",
                    "editorial", "gallery", "section"] = "list"
    table: ComparisonTable | None = None
    metrics: list[Metric] = Field(default_factory=list, max_length=3)
    model: StructureModel | None = None
    lead: str = Field(default="", max_length=105)
    blocks: list[ContentBlock] = Field(default_factory=list, max_length=4)
    figures: list[Figure] = Field(default_factory=list, max_length=2)

    def image_numbers(self) -> list[int]:
        return [self.image_number] if self.image_number is not None else [figure.image_number for figure in self.figures]

    @model_validator(mode="before")
    @classmethod
    def move_dense_points_to_notes(cls, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("points"), list):
            return payload
        points = payload["points"]
        notes = payload.get("notes", "")
        if not all(isinstance(point, str) for point in points) or not isinstance(notes, str):
            return payload
        limit = {"table": 1, "metrics": 2, "diagram": 4, "model": 1}.get(str(payload.get("layout")), 5)
        extra = [point for index, point in enumerate(points) if index >= limit or len(point.strip()) > 95]
        compact = []
        for point in points[:limit]:
            point = point.strip()
            if len(point) > 95:
                end = max(point.rfind(mark, 25, 94) for mark in ("。", "！", "？", "；", " ", "，"))
                point = point[:end + 1] if end >= 25 else point[:94].rstrip() + "…"
            if point:
                compact.append(point)
        if extra:
            notes = "\n\n".join(text for text in ("\n".join(extra), notes) if text)
        return {**payload, "points": compact, "notes": notes[:3600]}

    @model_validator(mode="after")
    def validate_content(self):
        if self.layout == "gallery":
            if (len(self.figures) != 2 or len({figure.image_number for figure in self.figures}) != 2
                    or self.image_number is not None or self.blocks or self.points):
                raise ValueError(CONSTANT.GALLERY_INVALID)
            return self
        if self.figures:
            raise ValueError(CONSTANT.GALLERY_LAYOUT_REQUIRED)
        if self.layout == "editorial":
            if not self.blocks or self.points or (self.image_number is not None and len(self.blocks) > 3):
                raise ValueError(CONSTANT.EDITORIAL_INVALID)
            return self
        if self.blocks:
            raise ValueError(CONSTANT.EDITORIAL_LAYOUT_REQUIRED)
        if self.layout == "section":
            if self.points or self.image_number is not None:
                raise ValueError(CONSTANT.SECTION_LAYOUT_INVALID)
            return self
        if self.layout == "table" and self.table and self.image_number is None and len(self.points) <= 1:
            return self
        if self.layout == "metrics" and self.metrics and self.image_number is None and len(self.points) <= 2:
            return self
        if self.layout == "model" and self.model and self.image_number is None and len(self.points) <= 1:
            return self
        if self.layout in {"table", "metrics", "model"} or not self.points:
            raise ValueError(CONSTANT.LAYOUT_CONTENT_MISSING)
        return self

    def template_points(self) -> list[str]:
        if self.layout == "editorial":
            return [f"{block.heading}｜{block.text}" for block in self.blocks]
        if self.layout == "table" and self.table:
            return ["  /  ".join(row) for row in [self.table.headers, *self.table.rows]] + self.points
        if self.layout == "metrics":
            return [f"{item.label}：{item.value}" for item in self.metrics] + self.points
        return self.points


class DeckContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    subtitle: str = Field(default="", max_length=110)
    category: Category | None = None
    theme: Theme | None = None
    accent: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    cover_image_number: ImageNumber | None = None
    slides: list[SlideContent] = Field(min_length=1, max_length=19)

    def image_numbers(self) -> list[int]:
        cover = [self.cover_image_number] if self.cover_image_number is not None else []
        return cover + [number for page in self.slides for number in page.image_numbers()]

    @model_validator(mode="before")
    @classmethod
    def remove_repeated_cover(cls, payload):
        if not isinstance(payload, dict):
            return payload
        pages = payload.get("slides")
        if not isinstance(pages, list) or len(pages) < 2 or not isinstance(pages[0], dict):
            return payload
        first = pages[0]
        if first.get("title") == payload.get("title") or first.get("title") == "封面":
            return {**payload, "subtitle": payload.get("subtitle") or first.get("subtitle") or "", "slides": pages[1:]}
        return payload


def load_template(path: Path | BinaryIO):
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(entry.file_size for entry in entries) > 150 * 1024 * 1024:
                raise ValueError(CONSTANT.TEMPLATE_SIZE_EXCEEDED)
            if any(entry.filename.lower().startswith(("ppt/embeddings/", "ppt/activex/"))
                   or "vbaproject" in entry.filename.lower() for entry in entries):
                raise ValueError(CONSTANT.TEMPLATE_FEATURE_UNSUPPORTED)
        deck = Presentation(str(path) if isinstance(path, Path) else path)
    except (BadZipFile, PackageNotFoundError, KeyError, XMLSyntaxError, OSError, AttributeError) as exc:
        raise ValueError(CONSTANT.TEMPLATE_INVALID) from exc
    for slide_id in list(deck.slides._sldIdLst):
        deck.part.drop_rel(slide_id.rId)
        deck.slides._sldIdLst.remove(slide_id)
    return deck


def save_slide_image(slide, path: Path, *, placeholder=None, bounds=None, credit=None):
    from io import BytesIO
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((1800, 1600))
        encoded = BytesIO()
        image.convert("RGBA").save(encoded, format="PNG")
    encoded.seek(0)
    if placeholder is not None:
        picture = placeholder.insert_picture(encoded)
    else:
        left, top, available_width, available_height = bounds
        scale = min(available_width / image.width, available_height / image.height)
        width, height = image.width * scale, image.height * scale
        picture = slide.shapes.add_picture(encoded, Inches(left + (available_width - width) / 2),
                                           Inches(top + (available_height - height) / 2), Inches(width), Inches(height))
    if source := image_source(credit):
        picture.click_action.hyperlink.address = source
    return picture


def text_slot(shapes, kinds):
    return next((shape for shape in shapes if shape.has_text_frame and shape.placeholder_format.type in kinds), None)


def template_slides(deck, content: DeckContent, images: list[Path], credits: dict[int, str]) -> set[int]:
    layouts = [layout for master in deck.slide_masters for layout in master.slide_layouts if any(
        shape.placeholder_format.type in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}
        for shape in layout.placeholders)]
    cover = next((layout for layout in layouts if text_slot(layout.placeholders, {PP_PLACEHOLDER.SUBTITLE}) is not None), None)
    body = next((layout for layout in layouts if text_slot(layout.placeholders, BODY_TYPES) is not None), None)
    illustrated = next((layout for layout in layouts if text_slot(layout.placeholders, BODY_TYPES) is not None and any(
        shape.placeholder_format.type == PP_PLACEHOLDER.PICTURE for shape in layout.placeholders)), None)
    if body is None:
        raise ValueError(CONSTANT.TEMPLATE_LAYOUT_MISSING)
    cover_slide = deck.slides.add_slide(cover if cover is not None else body)
    cover_slide.shapes.title.text = content.title
    subtitle = text_slot(cover_slide.placeholders, {PP_PLACEHOLDER.SUBTITLE})
    if subtitle is None:
        subtitle = text_slot(cover_slide.placeholders, BODY_TYPES)
    subtitle.text = content.subtitle.strip()
    picture = next((shape for shape in cover_slide.placeholders
                    if shape.placeholder_format.type == PP_PLACEHOLDER.PICTURE), None)
    embedded = {number for page in content.slides for number in page.image_numbers()}
    if (number := content.cover_image_number) and picture is not None:
        save_slide_image(cover_slide, images[number - 1], placeholder=picture, credit=credits.get(number))
        write_notes(cover_slide, credits.get(number, ""))
        embedded.add(number)
    for page in content.slides:
        layout = body
        if page.image_number is not None and illustrated is not None:
            layout = illustrated
        slide = deck.slides.add_slide(layout)
        slide.shapes.title.text = page.title
        slot = text_slot(slide.placeholders, BODY_TYPES)
        bounds = tuple(getattr(slot, dimension) / Inches(1) for dimension in ("left", "top", "width", "height"))
        if page.layout == "gallery":
            render_gallery(slide, page, images, credits, bounds, None, None)
            write_notes(slide, slide_notes(page, credits))
            continue
        if page.layout == "model":
            render_model(slide, page, bounds, None, None)
            write_notes(slide, page.notes)
            continue
        slot.text_frame.word_wrap = True
        slot.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        visible = ([page.lead] if page.lead else []) + page.template_points()
        for index, point in enumerate(visible):
            paragraph = slot.text_frame.add_paragraph() if index else slot.text_frame.paragraphs[0]
            paragraph.text = point
        if page.image_number is not None and illustrated is not None:
            picture = next(shape for shape in slide.placeholders if shape.placeholder_format.type == PP_PLACEHOLDER.PICTURE)
            picture = save_slide_image(slide, images[page.image_number - 1], placeholder=picture, credit=credits.get(page.image_number))
        elif page.image_number is not None:
            original_width = slot.width
            slot.width = int(original_width * 0.55)
            available = (slot.left + int(original_width * 0.60), slot.top, int(original_width * 0.40), slot.height)
            picture = save_slide_image(slide, images[page.image_number - 1], bounds=tuple(value / Inches(1) for value in available),
                                       credit=credits.get(page.image_number))
        if page.image_number is not None:
            left, top, width, height = (getattr(picture, dimension) / Inches(1)
                                        for dimension in ("left", "top", "width", "height"))
            bottom = deck.slide_height / Inches(1)
            if top + height + .28 <= bottom and not image_source(credits.get(page.image_number)):
                write_image_credit(slide, credits.get(page.image_number), (left, top + height, width, .28), None)
        write_notes(slide, slide_notes(page, credits))
    return embedded


def fitting_size(text: str, bounds: tuple, max_size: int, min_size: int) -> int:
    width, height = bounds[2:]
    for size in range(max_size, min_size - 1, -1):
        lines = 0
        for line in text.split("\n"):
            line_count = 1
            used = 0
            for character in line:
                advance = size if east_asian_width(character) in {"W", "F"} else size * 0.65
                if used + advance > width * 72 * 0.96:
                    used = 0
                    line_count += 1
                used += advance
            lines += line_count
        if lines * size * 1.3 <= height * 72:
            return size
    return min_size


def add_text(slide, text, bounds, size, color, *, bold=False, min_size=17, anchor=MSO_ANCHOR.MIDDLE,
             align=PP_ALIGN.LEFT):
    if not text.strip():
        return
    box = slide.shapes.add_textbox(*map(Inches, bounds))
    frame = box.text_frame
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    frame.vertical_anchor = anchor
    frame.text = text
    font_size = fitting_size(text, bounds, size, min_size)
    for paragraph in frame.paragraphs:
        paragraph.alignment = align
        paragraph.line_spacing = 1.12
        font = paragraph.font
        font.name = "Microsoft YaHei"
        east_asian = OxmlElement("a:ea")
        east_asian.set("typeface", font.name)
        font._element.append(east_asian)
        font.size = Pt(font_size)
        font.bold = bold
        if color is None:
            font.color.theme_color = MSO_THEME_COLOR.TEXT_1
        else:
            font.color.rgb = RGBColor.from_string(color)
    return frame


def draw_rule(slide, left, top, width, height, color):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, *map(Inches, (left, top, width, height)))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(color)
    shape.line.fill.background()
    shape.shadow.inherit = False


def accent_color(custom: str | None, background: str, default: str) -> str:
    def luminance(hex_color):
        channels = [int(hex_color[index:index + 2], 16) / 255 for index in (0, 2, 4)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))

    if custom:
        candidate = custom.lstrip("#")
        low, high = sorted([luminance(candidate), luminance(background)])
        if (high + 0.05) / (low + 0.05) >= 4.5:
            return candidate
    return default


def write_notes(slide, text: str) -> None:
    if text.strip():
        slide.notes_slide.notes_text_frame.text = text


def slide_notes(page, credits):
    return "\n\n".join(filter(None, [page.notes, *(credits.get(number) for number in page.image_numbers())]))


def image_source(credit: str | None) -> str:
    if not credit:
        return ""
    source = next((line.removeprefix("来源：").strip() for line in credit.splitlines()
                   if line.startswith("来源：")), "")
    return source if source.startswith(("https://", "http://")) else ""


def write_image_credit(slide, credit: str | None, bounds, color) -> None:
    if not credit:
        return
    source = image_source(credit)
    pdf_credit = next((line for line in credit.splitlines() if line.startswith("图片来源：")), "")
    if source or pdf_credit:
        frame = add_text(slide, pdf_credit or "图片来源", bounds, 11, color, min_size=9)
    if source:
        run = frame.paragraphs[0].runs[0]
        run.hyperlink.address = source
        if color:
            run.font.color.rgb = RGBColor.from_string(color)
        run.font.underline = True


def cover_contours(slide, left, top, size, color):
    for inset in (0, .34, .68):
        diameter = size - inset * 2
        ring = slide.shapes.add_shape(MSO_SHAPE.OVAL, *map(Inches, (left + inset, top + inset, diameter, diameter)))
        ring.fill.background()
        ring.line.color.rgb = RGBColor.from_string(color)
        ring.line.width = Pt(1)
        ring.shadow.inherit = False


def render_cover(slide, content, title_color, body_color, accent, surface):
    category = content.category
    motif = tint(surface, accent, .24)
    if category == "speech":
        draw_rule(slide, 0, 5.92, 13.333, 1.58, surface)
        cover_contours(slide, 10.55, .34, 1.91, motif)
        draw_rule(slide, 5.92, 2.10, 1.48, 0.055, accent)
        add_text(slide, content.title, (1.1, 2.38, 11.13, 2.04), 41, title_color, bold=True,
                 min_size=31, align=PP_ALIGN.CENTER)
        add_text(slide, content.subtitle, (1.1, 4.62, 11.13, 0.92), 19, body_color, align=PP_ALIGN.CENTER)
        return
    if category == "teaching":
        draw_rule(slide, 0, 0, 0.18, 7.5, accent)
        draw_rule(slide, 9.42, 0, 3.91, 7.50, surface)
        cover_contours(slide, 9.77, 1.62, 3.18, motif)
        draw_rule(slide, .94, 1.45, .88, .06, accent)
        add_text(slide, content.title, (0.92, 1.64, 8.34, 2.43), 40, title_color, bold=True, min_size=26)
        add_text(slide, content.subtitle, (0.94, 4.32, 8.14, 1.23), 19, body_color, anchor=MSO_ANCHOR.TOP)
        return
    if category in {"technical", "research_report"}:
        draw_rule(slide, 0, 4.17, 13.333, 3.33, surface)
        cover_contours(slide, 9.75, 4.55, 2.50, motif)
        draw_rule(slide, 0, 0, 13.333, 0.14, accent)
        draw_rule(slide, 0.94, 4.18, 11.45, 0.035, accent)
        add_text(slide, content.title, (0.94, 1.66, 11.44, 2.32), 39, title_color, bold=True, min_size=26)
        add_text(slide, content.subtitle, (0.94, 4.57, 8.34, 1.81), 19, body_color, min_size=15, anchor=MSO_ANCHOR.TOP)
        return
    if category == "competition":
        draw_rule(slide, 9.54, 4.38, 3.79, 3.12, surface)
        for index, width in enumerate((3.75, 2.58, 1.48)):
            draw_rule(slide, 13.333 - width, 5.45 + index * 0.65, width, 0.16, accent)
        draw_rule(slide, 0.95, 1.05, 0.065, 4.52, accent)
        add_text(slide, content.title, (1.29, 1.46, 10.85, 2.65), 41, title_color, bold=True, min_size=26)
        add_text(slide, content.subtitle, (1.29, 4.28, 9.40, 0.98), 19, body_color, anchor=MSO_ANCHOR.TOP)
        return
    draw_rule(slide, 0, 4.37, 13.333, 3.13, surface)
    cover_contours(slide, 9.78, 4.59, 2.57, motif)
    draw_rule(slide, 0.95, 1.54, 1.15, 0.07, accent)
    add_text(slide, content.title, (0.95, 1.84, 11.2, 2.48), 39, title_color, bold=True, min_size=25)
    if content.subtitle.strip():
        add_text(slide, content.subtitle, (0.98, 4.71, 8.30, 1.66), 19, body_color, min_size=15, anchor=MSO_ANCHOR.TOP)


def render_illustrated_cover(slide, content, path, credit, title_color, body_color, accent, surface):
    draw_rule(slide, 6.76, 0, 6.57, 7.50, surface)
    draw_rule(slide, 0, 0, .17, 7.50, accent)
    draw_rule(slide, .90, 1.24, 1.10, .065, accent)
    add_text(slide, content.title, (.90, 1.57, 5.51, 2.20), 39, title_color, bold=True, min_size=24)
    draw_rule(slide, .92, 4.09, 5.24, .016, tint(surface, accent, .32))
    add_text(slide, content.subtitle, (.92, 4.35, 5.30, 1.66), 19, body_color, min_size=15, anchor=MSO_ANCHOR.TOP)
    draw_rule(slide, 7.13, .72, 5.83, 5.66, tint(surface, accent, .13))
    save_slide_image(slide, path, bounds=(7.19, .78, 5.71, 5.54), credit=credit)
    write_notes(slide, credit or "")
    write_image_credit(slide, credit, (7.19, 6.52, 5.71, .36), body_color)


def render_comparison(slide, page, background, body_color, accent):
    data = page.table
    values = [data.headers, *data.rows]
    row_count, column_count = len(values), len(data.headers)
    height = min(3.55, 0.81 * row_count)
    table = slide.shapes.add_table(row_count, column_count, *map(Inches, (0.92, 2.22, 11.45, height))).table
    table.first_row = table.horz_banding = False
    for row_idx, row in enumerate(values):
        for col_idx, value in enumerate(row):
            cell = table.cell(row_idx, col_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(accent if row_idx == 0 else background)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.text_frame.word_wrap = True
            cell.text = value
            font = cell.text_frame.paragraphs[0].font
            font.name = "Microsoft YaHei"
            font.bold = row_idx == 0
            font.size = Pt(fitting_size(value, (0, 0, 11.45 / column_count - 0.24, height / row_count - 0.1), 19, 12))
            font.color.rgb = RGBColor.from_string(background if row_idx == 0 else body_color)
    if page.points:
        add_points(slide, page.points[:1], 0.92, 2.41 + height, 11.45, 0.96, accent, body_color)


def render_metrics(slide, page, title_color, body_color, accent):
    count = len(page.metrics)
    width = 11.45 / count
    for index, item in enumerate(page.metrics):
        left = 0.92 + index * width
        if index:
            draw_rule(slide, left - 0.17, 2.63, 0.025, 1.80, accent)
        add_text(slide, item.value, (left, 2.48, width - 0.33, 1.17), 39, accent, bold=True, min_size=23)
        add_text(slide, item.label, (left, 3.81, width - 0.33, 0.72), 18, title_color, bold=True, min_size=14)
    if page.points:
        add_points(slide, page.points[:2], 0.92, 5.01, 11.45, 1.65, accent, body_color, bullets=True)


def render_diagram(slide, points, title_color, accent):
    cell_width = 11.45 / len(points)
    draw_rule(slide, 0.95, 3.06, 11.34, 0.033, accent)
    for index, point in enumerate(points):
        left = 0.95 + index * cell_width
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(left), Inches(2.96), Inches(.23), Inches(.23))
        dot.fill.solid()
        dot.fill.fore_color.rgb = RGBColor.from_string(accent)
        dot.line.fill.background()
        add_text(slide, str(index + 1), (left, 2.38, cell_width - .25, .46), 21, accent, bold=True)
        add_text(slide, point, (left, 3.38, cell_width - .32, 2.50), 20, title_color,
                 bold=True, min_size=16, anchor=MSO_ANCHOR.TOP)


def render_model(slide, page, bounds, text_color, accent):
    left, top, width, height = bounds
    diagram_height = height * (0.78 if page.points else 1)
    center_y = top + diagram_height / 2
    start = left + width * 0.27
    junction = left + width * 0.34
    end = left + width * 0.71
    row_height = diagram_height / len(page.model.branches)
    node_height = min(0.86, row_height * 0.78)

    def link(x1, y1, x2, y2):
        line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, *map(Inches, (x1, y1, x2, y2))).line
        line.width = Pt(1.5)
        if accent:
            line.color.rgb = RGBColor.from_string(accent)
        else:
            line.color.theme_color = MSO_THEME_COLOR.ACCENT_1

    add_text(slide, page.model.center, (left, center_y - node_height / 2, width * .26, node_height),
             22, accent, bold=True, min_size=15, align=PP_ALIGN.CENTER)
    link(start, center_y, junction, center_y)
    centers = [top + (index + .5) * row_height for index in range(len(page.model.branches))]
    if len(centers) > 1:
        link(junction, centers[0], junction, centers[-1])
    for branch, y in zip(page.model.branches, centers):
        link(junction, y, end, y)
        add_text(slide, branch.relation, (junction + width * .02, y - row_height * .44, width * .33, row_height * .42),
                 16, text_color, min_size=11, anchor=MSO_ANCHOR.BOTTOM, align=PP_ALIGN.CENTER)
        add_text(slide, branch.label, (end + width * .015, y - node_height / 2, width * .27, node_height),
                 20, text_color, bold=True, min_size=14)
    if page.points:
        add_text(slide, page.points[0], (left, top + diagram_height, width, height - diagram_height),
                 18, text_color, min_size=14)


def tint(background, accent, ratio):
    base = RGBColor.from_string(background)
    tone = RGBColor.from_string(accent)
    return "".join(f"{round(a + (b - a) * ratio):02X}" for a, b in zip(base, tone))


def render_editorial(slide, page, bounds, title_color, body_color, accent, surface):
    left, top, width, height = bounds
    columns = 2 if width > 8 and len(page.blocks) > 1 else 1
    field_height = min(height, (1.5 if columns == 2 else 1.35) * ((len(page.blocks) + columns - 1) // columns) + .45)
    draw_rule(slide, left, top, width, field_height, surface)
    gutter = .32
    cell_width = (width - gutter * (columns + 1)) / columns
    rows = (len(page.blocks) + columns - 1) // columns
    row_height = (field_height - .44) / rows
    for index, block in enumerate(page.blocks):
        row, column = divmod(index, columns)
        x = left + gutter + column * (cell_width + gutter)
        y = top + .22 + row * row_height
        if row:
            draw_rule(slide, x, y - .07, cell_width, .013, tint(surface, accent, .28))
        add_text(slide, block.heading, (x, y, cell_width, .42), 18, title_color, bold=True, min_size=13)
        add_text(slide, block.text, (x, y + .49, cell_width, row_height - .53), 16, body_color,
                 min_size=12, anchor=MSO_ANCHOR.TOP)


def render_gallery(slide, page, images, credits, bounds, body_color, surface):
    left, top, width, height = bounds
    if page.lead:
        add_text(slide, page.lead, (left, top, width, .50), 16, body_color, min_size=12)
        top += .56
        height -= .56
    gap = width * .034
    panel_width = (width - gap) / 2
    image_height = height - 1.0
    for index, figure in enumerate(page.figures):
        x = left + index * (panel_width + gap)
        number = figure.image_number
        credit = credits.get(number)
        if surface:
            draw_rule(slide, x, top, panel_width, image_height, surface)
        save_slide_image(slide, images[number - 1], bounds=(x, top, panel_width, image_height), credit=credit)
        add_text(slide, figure.caption, (x, top + image_height + .11, panel_width, .52),
                 14, body_color, bold=True, min_size=11)
        write_image_credit(slide, credit, (x, top + height - .28, panel_width, .26), body_color)


def default_slides(deck, content: DeckContent, images: list[Path], credits: dict[int, str]) -> None:
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    blank = next(layout for layout in deck.slide_layouts if not len(layout.placeholders) or layout.name == "Blank")
    theme = content.theme or (TEMPLATES[content.category].theme if content.category else "academic")
    background, title_color, body_color, default_accent = PALETTES[theme]
    rule_color = content.accent[1:] if content.accent else default_accent
    accent = accent_color(content.accent, background, default_accent)
    surface = tint(background, accent, .13)
    cover = deck.slides.add_slide(blank)
    cover.background.fill.solid()
    cover.background.fill.fore_color.rgb = RGBColor.from_string(background)
    if number := content.cover_image_number:
        render_illustrated_cover(cover, content, images[number - 1], credits.get(number),
                                 title_color, body_color, accent, surface)
    else:
        render_cover(cover, content, title_color, body_color, rule_color, surface)
    for page in content.slides:
        slide = deck.slides.add_slide(blank)
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string(background)
        draw_rule(slide, 0, 0, 13.333, 1.37 if page.lead else 1.77, surface)
        draw_rule(slide, 0, 0, .16, 1.37 if page.lead else 1.77, rule_color)
        add_text(slide, page.title, (0.90, .25, 11.5, 1.02), 29, title_color, bold=True, min_size=21)
        if page.lead and page.layout != "gallery":
            add_text(slide, page.lead, (.92, 1.52, 11.45, .58), 16, body_color, min_size=13)
        if page.layout == "gallery":
            render_gallery(slide, page, images, credits, (.92, 1.65 if page.lead else 2.12, 11.45,
                                                           5.26 if page.lead else 4.79), body_color, surface)
        elif page.layout == "editorial":
            if page.image_number is None:
                render_editorial(slide, page, (.92, 2.20, 11.45, 4.64), title_color, body_color, accent, surface)
            else:
                render_editorial(slide, page, (.92, 2.20, 5.35, 4.45), title_color, body_color, accent, surface)
                render_illustration(slide, page.image_number, images, credits, 6.58, body_color, surface)
        elif page.layout == "table":
            render_comparison(slide, page, background, body_color, accent)
        elif page.layout == "metrics":
            render_metrics(slide, page, title_color, body_color, accent)
        elif page.layout == "model":
            draw_rule(slide, .92, 2.20, 11.45, 4.51, surface)
            render_model(slide, page, (1.12, 2.38, 11.05, 4.12), title_color, accent)
        elif page.image_number is not None:
            image_left = page.layout == "image_left"
            image_x = 0.92 if image_left else 6.37
            add_points(slide, page.points, 7.27 if image_left else 0.92, 2.28, 5.10, 4.50, accent, body_color, bullets=True)
            render_illustration(slide, page.image_number, images, credits, image_x, body_color, surface)
        elif page.layout == "diagram" and len(page.points) >= 2:
            render_diagram(slide, page.points, title_color, accent)
        elif page.layout == "columns" and len(page.points) >= 2:
            middle = (len(page.points) + 1) // 2
            draw_rule(slide, 6.64, 2.28, 0.025, 4.15, accent)
            add_points(slide, page.points[:middle], 0.92, 2.28, 5.36, 4.2, accent, body_color, bullets=True)
            add_points(slide, page.points[middle:], 6.99, 2.28, 5.36, 4.2, accent, body_color, bullets=True)
        elif page.layout == "focus":
            draw_rule(slide, .92, 2.24, 11.45, 1.85, surface)
            add_text(slide, page.points[0], (1.19, 2.43, 10.90, 1.46), 31 if content.category == "speech" else 27,
                     accent, bold=True, min_size=20, align=PP_ALIGN.CENTER if content.category == "speech" else PP_ALIGN.LEFT)
            add_points(slide, page.points[1:], 0.92, 4.50, 11.45, 2.27, accent, body_color)
        else:
            add_points(slide, page.points, 0.92, 2.28, 11.45, 4.50, accent, body_color,
                       bullets=True, numbered=page.layout == "steps" and len(page.points) >= 2)
        write_notes(slide, slide_notes(page, credits))


def render_illustration(slide, number, images, credits, x, color, surface):
    credit = credits.get(number)
    bounds = (x, 2.24, 5.79, 4.37)
    draw_rule(slide, *bounds, surface)
    save_slide_image(slide, images[number - 1], bounds=bounds, credit=credit)
    write_image_credit(slide, credit, (x, 6.68, bounds[2], 0.30), color)


def add_points(slide, points, left, top, width, height, accent, color, *, bullets=False, numbered=False):
    if not points:
        return
    row = min(height / len(points), 1.05)
    offset = 0.62 if numbered else 0.30 if bullets else 0
    for index, point in enumerate(points):
        position = top + index * row
        if offset:
            marker = str(index + 1) if numbered else "•"
            add_text(slide, marker, (left, position, offset, row - 0.09), 17, accent, bold=True,
                     min_size=13, anchor=MSO_ANCHOR.TOP)
        add_text(slide, point, (left + offset, position, width - offset, row - 0.09), 18, color,
                 min_size=13, anchor=MSO_ANCHOR.TOP)


def write_presentation(output: Path | BinaryIO, content: DeckContent, template: Path | None, images: list[Path],
                       credits: dict[int, str] | None = None) -> set[int]:
    if any(number > len(images) for number in content.image_numbers()):
        raise ValueError(CONSTANT.IMAGE_REFERENCE_INVALID)
    deck = load_template(template) if template else Presentation()
    deck.core_properties.title = content.title
    if template:
        embedded = template_slides(deck, content, images, credits or {})
    else:
        default_slides(deck, content, images, credits or {})
        embedded = set(content.image_numbers())
    if not isinstance(output, Path):
        deck.save(output)
        return embedded
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{uuid4().hex}.pptx")
    try:
        deck.save(staging)
        staging.replace(output)
    finally:
        staging.unlink(missing_ok=True)
    return embedded
