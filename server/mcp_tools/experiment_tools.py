"""Expose account-scoped experiment metadata and the shared workbench queue."""

import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from pydantic import Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from server import agent_pool, task_progress
from server import CONSTANT as WORKBENCH
from server.agent import file_tools, hfss, modelsim, packet_tracer, quartus
from server.agent.thesis.word_format import read_word_standard
from server.contracts.requests.conversation import ConversationRequest
from server.conversations import find_conversation, read_conversation
from server.features_services.jwt_validation import require_admin
from server.features_services.project_service import data_dir, project_detail
from server.General_Tools.hfss_tools import find_hfss_executable
from server.General_Tools.report_writer import inspect_report_template

from . import CONSTANT

Software = Literal["modelsim", "quartus", "packet-tracer", "hfss", "pycharm"]
Deliverable = Literal["report", "paper", "code"]
ProjectId = Annotated[str, Field(min_length=1, max_length=32)]
Requirements = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]
PLANNERS = {
    "modelsim": modelsim.plan_modelsim_simulation, "quartus": quartus.plan_quartus_project,
    "packet-tracer": packet_tracer.plan_packet_tracer_network, "hfss": hfss.plan_hfss_simulation,
}


def experiment_context(owner_id: int, conversation_id: str):
    try:
        root, metadata = find_conversation(data_dir(), conversation_id, owner_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        raise ToolError(CONSTANT.EXPERIMENT_NOT_FOUND) from None
    if metadata.get("kind", "task") != "task":
        raise ToolError(CONSTANT.EXPERIMENT_NOT_FOUND)
    return root.resolve(), read_conversation(metadata)["messages"]


def attachment_path(root: Path, turn: dict, item: dict, *, as_docx: bool = False) -> Path:
    folder = root / "messages" / turn["id"]
    path = folder / Path(item["stored_name"]).name
    if as_docx and path.suffix.lower() == ".doc":
        path = path.with_suffix(".docx")
    if path.resolve() != path or folder.parent != root / "messages" or not path.is_file():
        raise ToolError(CONSTANT.EXPERIMENT_INPUT_MISSING)
    return path


def owner_id() -> int:
    return get_http_request().state.tongxue_owner_id


def experiment_capabilities() -> dict:
    return {
        "workflows": [
            {"software_id": software, "mode": "planning_and_report"} for software in PLANNERS if software != "hfss"
        ] + [
            {"software_id": "hfss", "mode": "local_solver", "executable_available": find_hfss_executable() is not None},
            {"software_id": "pycharm", "mode": "source_and_report"},
        ],
        "deliverables": ["report", "paper", "code"],
        "submission_requires_admin": True,
    }


def plan_experiment(software_id: Software, goal: Requirements) -> dict:
    planner = PLANNERS.get(software_id)
    return {"plan": planner.invoke({"goal": goal}) if planner else CONSTANT.EXPERIMENT_PYTHON_PLAN.format(goal=goal)}


def describe_hfss_spec(topic: Literal["all", "spec", "geometry", "materials", "simulation", "raw_methods"] = "all") -> dict:
    return json.loads(hfss.describe_hfss_experiment_spec.invoke({"topic": topic}))


def extract_hfss_parameters(guide_excerpt: Requirements) -> dict:
    return json.loads(hfss.extract_hfss_guide_parameters.invoke({"guide_excerpt": guide_excerpt}))


def inspect_experiment_template(conversation_id: ProjectId) -> dict:
    root, turns = experiment_context(owner_id(), conversation_id)
    turn = next((item for item in reversed(turns) if item.get("report_template")), None)
    if turn is None:
        return {"template": None}
    path = attachment_path(root, turn, turn["report_template"], as_docx=True)
    try:
        return inspect_report_template(path)
    except (OSError, ValueError):
        raise ToolError(CONSTANT.EXPERIMENT_TEMPLATE_FAILED) from None


def read_experiment_input(conversation_id: ProjectId, message_id: ProjectId,
                          stored_name: Annotated[str, Field(min_length=1, max_length=128)]) -> dict:
    root, turns = experiment_context(owner_id(), conversation_id)
    turn = next((item for item in turns if item["id"] == message_id), None)
    entry = next((item for item in turn.get("other_files", []) if item["stored_name"] == stored_name), None) if turn else None
    if entry is None or Path(stored_name).suffix.lower() not in file_tools.TEXT_EXTENSIONS | {".docx"}:
        raise ToolError(CONSTANT.EXPERIMENT_INPUT_MISSING)
    path = attachment_path(root, turn, entry)
    try:
        if path.suffix.lower() == ".docx":
            text, truncated = read_word_standard(path, file_tools.MAX_PREVIEW_BYTES)
            return {"name": entry["name"], "text": text, "truncated": truncated}
        return file_tools.experiment_file_preview(path, entry["name"])
    except (OSError, ValueError):
        raise ToolError(CONSTANT.EXPERIMENT_INPUT_FAILED) from None


def task_state(user_id: int, conversation_id: str, request_id: str, turn: dict) -> dict:
    state = {"conversation_id": conversation_id, "request_id": request_id, "message_id": turn["id"],
             "software_id": turn["software_id"], "status": turn.get("status", "draft"), "reply": turn.get("reply")}
    try:
        progress = task_progress.get_progress(user_id, request_id)
        if progress.get("conversation_id") == conversation_id:
            state["stage"] = progress["stage"]
    except HTTPException:
        pass
    if state["status"] == "processing":
        if not agent_pool.is_active(conversation_id):
            _, turns = experiment_context(user_id, conversation_id)
            latest = find_task(turns, request_id)
            if latest and latest.get("status") in {"answered", "error"}:
                state["status"], state["reply"] = latest["status"], latest.get("reply")
            else:
                state["status"] = "interrupted"
        else:
            state["status"] = "queued" if state.get("stage") == WORKBENCH.MESSAGE_FILES_SAVED else "running"
    if state["status"] in {"answered", "error", "interrupted"}:
        project = project_detail(conversation_id, user_id)
        row = next((item for item in project["messages"] if item["message_id"] == turn["id"]), {})
        state["outputs"] = row.get("outputs", [])
    return state


def find_task(turns, request_id: str):
    return next((turn for turn in turns if turn.get("request_id") == request_id), None)


async def submit_experiment_task(
    conversation_id: ProjectId, software_id: Software, requirements: Requirements,
    deliverables: Annotated[list[Deliverable], Field(min_length=1, max_length=3)],
    request_id: UUID, confirm_execution: Literal[True],
) -> dict:
    from server.app import requested_deliverables, start_experiment_task

    user = get_http_request().state.tongxue_user
    try:
        require_admin(user)
    except HTTPException as exc:
        raise ToolError(exc.detail) from None
    request_key = str(request_id)
    root, turns = await run_in_threadpool(experiment_context, user.id, conversation_id)
    deliverables = list(dict.fromkeys(requested_deliverables(requirements, deliverables)))
    if existing := find_task(turns, request_key):
        return await run_in_threadpool(reuse_task, user.id, conversation_id, request_key, existing,
                                       software_id, requirements, deliverables)
    previous_software = turns[-1].get("software_id") if turns else None
    if previous_software and previous_software != software_id:
        raise ToolError(CONSTANT.EXPERIMENT_SOFTWARE_MISMATCH)
    guide = next((turn for turn in turns if turn.get("guide")), None)
    if guide is None:
        raise ToolError(CONSTANT.EXPERIMENT_GUIDE_REQUIRED)
    try:
        path = await run_in_threadpool(attachment_path, root, guide, guide["guide"])
        with path.open("rb") as source:
            if path.suffix.lower() != ".pdf" or source.read(5) != b"%PDF-":
                raise ToolError(CONSTANT.EXPERIMENT_GUIDE_REQUIRED)
    except (ToolError, OSError):
        raise ToolError(CONSTANT.EXPERIMENT_GUIDE_REQUIRED) from None
    if software_id == "hfss" and not await run_in_threadpool(find_hfss_executable):
        raise ToolError(CONSTANT.EXPERIMENT_HFSS_UNAVAILABLE)
    request = ConversationRequest(
        requirements=requirements, software_id=software_id, deliverables=deliverables, conversation_id=conversation_id,
        request_id=request_key, execution_scope="mcp",
    )
    try:
        await start_experiment_task(request, user.id, request_key)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise ToolError(exc.detail) from None
        _, latest = await run_in_threadpool(experiment_context, user.id, conversation_id)
        if not find_task(latest, request_key):
            raise ToolError(exc.detail) from None
    _, latest = await run_in_threadpool(experiment_context, user.id, conversation_id)
    if not (saved := find_task(latest, request_key)):
        raise ToolError(CONSTANT.EXPERIMENT_TASK_NOT_FOUND)
    return await run_in_threadpool(reuse_task, user.id, conversation_id, request_key, saved,
                                   software_id, requirements, deliverables)


def reuse_task(user_id, conversation_id, request_id, turn, software_id, requirements, deliverables):
    if (turn.get("execution_scope") != "mcp" or turn.get("software_id") != software_id
            or turn.get("requirements") != requirements or set(turn.get("deliverables", [])) != set(deliverables)):
        raise ToolError(CONSTANT.EXPERIMENT_REQUEST_CONFLICT)
    return task_state(user_id, conversation_id, request_id, turn)


def get_experiment_task(conversation_id: ProjectId, request_id: UUID) -> dict:
    user_id = owner_id()
    _, turns = experiment_context(user_id, conversation_id)
    if not (turn := find_task(turns, str(request_id))) or turn.get("execution_scope") != "mcp":
        raise ToolError(CONSTANT.EXPERIMENT_TASK_NOT_FOUND)
    return task_state(user_id, conversation_id, str(request_id), turn)
