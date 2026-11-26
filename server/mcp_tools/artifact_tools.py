"""Render validated artifacts without modifying any project or conversation."""

from base64 import b64encode
from importlib import import_module
from io import BytesIO
from typing import Annotated

from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import BlobResourceContents, EmbeddedResource
from pydantic import Field, FiniteFloat

from server.agent.thesis.generation import Manuscript, validate_references, write_manuscript
from server.General_Tools.presentation_writer import DeckContent, write_presentation

from . import CONSTANT

drawing = import_module("server.agent.solving-problems.drawing")
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
Reference = Annotated[str, Field(min_length=1, max_length=1200)]
ReferenceId = Annotated[str, Field(pattern=r"^[LM][1-9][0-9]{0,2}$")]


def document_result(output: BytesIO, filename: str, mime_type: str) -> ToolResult:
    resource = BlobResourceContents(
        uri=f"urn:tongxue:mcp:{filename}", mimeType=mime_type, blob=b64encode(output.getbuffer()).decode("ascii"),
    )
    return ToolResult(
        content=[EmbeddedResource(type="resource", resource=resource)], structured_content={"filename": filename},
    )


def render_presentation(deck: DeckContent) -> ToolResult:
    if deck.image_numbers():
        raise ToolError(CONSTANT.PRESENTATION_IMAGES_UNAVAILABLE)
    try:
        with BytesIO() as output:
            write_presentation(output, deck, None, [])
            return document_result(output, "presentation.pptx", PPTX_MIME)
    except (ValueError, OSError) as exc:
        raise ToolError(CONSTANT.PRESENTATION_RENDER_FAILED) from exc


def render_thesis(manuscript: Manuscript,
                  sources: Annotated[dict[ReferenceId, Reference] | None, Field(max_length=13)] = None,
                  disclosure: Annotated[str, Field(max_length=2500)] = "") -> ToolResult:
    references = sources or {}
    try:
        validate_references(manuscript, set(references))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    try:
        with BytesIO() as output:
            write_manuscript(output, manuscript, references, disclosure)
            return document_result(output, "manuscript.docx", DOCX_MIME)
    except (ValueError, OSError) as exc:
        raise ToolError(CONSTANT.THESIS_RENDER_FAILED) from exc


def plot_discrete_sequence(
    samples: Annotated[list[FiniteFloat], Field(min_length=1, max_length=drawing.MAX_SEQUENCE_SAMPLES)],
    start_index: Annotated[int, Field(ge=-drawing.MAX_SEQUENCE_START, le=drawing.MAX_SEQUENCE_START)] = 0,
    title: Annotated[str, Field(max_length=80)] = CONSTANT.SEQUENCE_TITLE,
) -> Image:
    try:
        return Image(data=drawing.discrete_sequence_png(samples, start_index, title), format="png")
    except (ValueError, OverflowError) as exc:
        raise ToolError(CONSTANT.SEQUENCE_RENDER_FAILED) from exc
