"""Register MCP tools and authenticate workbench requests."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import HTTPException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.utilities.types import Image
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from server.conversations import find_conversation
from server.features_services.jwt_validation import bearer, require_user
from server.features_services.project_service import data_dir, project_detail
from server.General_Tools.pdf_on_demand import MAX_TEXT_RANGE_PAGES, describe_pdf, read_pdf_text_range, render_pdf_page

from . import CONSTANT, artifact_tools, experiment_tools, literature

mcp = FastMCP(CONSTANT.SERVER_NAME)
artifact_annotations = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
mcp.tool(artifact_tools.render_presentation, description=CONSTANT.RENDER_PRESENTATION, annotations=artifact_annotations)
mcp.tool(artifact_tools.render_thesis, description=CONSTANT.RENDER_THESIS, annotations=artifact_annotations)
mcp.tool(artifact_tools.plot_discrete_sequence, description=CONSTANT.PLOT_DISCRETE_SEQUENCE, annotations=artifact_annotations)
for function, description in (
    (experiment_tools.experiment_capabilities, CONSTANT.EXPERIMENT_CAPABILITIES),
    (experiment_tools.plan_experiment, CONSTANT.PLAN_EXPERIMENT),
    (experiment_tools.describe_hfss_spec, CONSTANT.HFSS_SPEC),
    (experiment_tools.extract_hfss_parameters, CONSTANT.EXTRACT_HFSS_PARAMETERS),
    (experiment_tools.read_experiment_input, CONSTANT.READ_EXPERIMENT_INPUT),
    (experiment_tools.inspect_experiment_template, CONSTANT.INSPECT_EXPERIMENT_TEMPLATE),
    (experiment_tools.get_experiment_task, CONSTANT.EXPERIMENT_STATUS),
):
    mcp.tool(function, description=description, annotations=artifact_annotations)
mcp.tool(experiment_tools.submit_experiment_task, description=CONSTANT.SUBMIT_EXPERIMENT,
         annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))


def project_pdf(owner_id: int, conversation_id: str, file_id: str) -> Path:
    try:
        project = project_detail(conversation_id, owner_id)
        allowed = [item["path"] for turn in project["messages"] for item in turn["inputs"]]
        if project["guide"]:
            allowed.append(project["guide"]["path"])
        if file_id not in allowed or Path(file_id).suffix.lower() != ".pdf":
            raise ToolError(CONSTANT.FILE_NOT_FOUND)
        root, _ = find_conversation(data_dir(), conversation_id, owner_id)
        path = (root / file_id).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ToolError(CONSTANT.FILE_NOT_FOUND)
        return path
    except HTTPException as exc:
        if exc.status_code == 404:
            raise ToolError(CONSTANT.FILE_NOT_FOUND) from None
        raise


@mcp.tool(description=CONSTANT.SEARCH_PAPERS, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def search_papers(query: Annotated[str, Field(min_length=1, max_length=250)],
                  limit: Annotated[int, Field(ge=1, le=5)] = 5) -> dict:
    query = query.strip()
    if not query:
        raise ToolError(CONSTANT.QUERY_REQUIRED)
    papers, failed = literature.search_literature([query], limit)
    if failed and not papers:
        raise ToolError(CONSTANT.CROSSREF_UNAVAILABLE)
    return {"papers": [{**asdict(paper), "url": paper.url} for paper in papers]}


@mcp.tool(description=CONSTANT.READ_PDF_PAGES.format(max_pages=MAX_TEXT_RANGE_PAGES),
          annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def read_pdf_pages(conversation_id: Annotated[str, Field(min_length=1, max_length=32)],
                   file_id: Annotated[str, Field(min_length=1, max_length=512)],
                   start_page: Annotated[int, Field(ge=1)],
                   end_page: Annotated[int, Field(ge=1)]) -> dict:
    owner_id = get_http_request().state.tongxue_owner_id
    if end_page - start_page >= MAX_TEXT_RANGE_PAGES:
        raise ToolError(CONSTANT.PDF_PAGE_LIMIT.format(max_pages=MAX_TEXT_RANGE_PAGES))
    path = project_pdf(owner_id, conversation_id, file_id)
    try:
        return read_pdf_text_range(path, start_page, end_page)
    except (OSError, ValueError, RuntimeError):
        raise ToolError(CONSTANT.PDF_READ_FAILED) from None


@mcp.tool(description=CONSTANT.RENDER_PDF_PAGE, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def preview_pdf_page(conversation_id: Annotated[str, Field(min_length=1, max_length=32)],
                     file_id: Annotated[str, Field(min_length=1, max_length=512)],
                     page_number: Annotated[int, Field(ge=1)]) -> Image:
    path = project_pdf(get_http_request().state.tongxue_owner_id, conversation_id, file_id)
    try:
        return Image(data=render_pdf_page(path, page_number), format="jpeg")
    except (OSError, ValueError, RuntimeError):
        raise ToolError(CONSTANT.PDF_PREVIEW_FAILED) from None


@mcp.tool(description=CONSTANT.LIST_BOOKMARKS, annotations=artifact_annotations)
def list_project_pdf_bookmarks(conversation_id: Annotated[str, Field(min_length=1, max_length=32)],
                               file_id: Annotated[str, Field(min_length=1, max_length=512)],
                               start: Annotated[int, Field(ge=0)] = 0) -> dict:
    path = project_pdf(get_http_request().state.tongxue_owner_id, conversation_id, file_id)
    try:
        return describe_pdf(path, start)
    except (OSError, ValueError, RuntimeError):
        raise ToolError(CONSTANT.PDF_READ_FAILED) from None


async def authenticate(request, call_next):
    try:
        user = await run_in_threadpool(require_user, await bearer(request))
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    request.state.tongxue_owner_id = user.id
    request.state.tongxue_user = user
    return await call_next(request)


mcp_app = mcp.http_app(
    path="/", transport="streamable-http", stateless_http=True, json_response=True,
    middleware=[Middleware(BaseHTTPMiddleware, dispatch=authenticate)],
)
