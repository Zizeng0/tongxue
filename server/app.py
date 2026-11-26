"""本地 FastAPI 路由：校验输入并调用会话存储与软件 Agent。"""

import logging
from copy import deepcopy
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from langchain_core.exceptions import ModelTimeoutError
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from server.General_Tools.image_analysis import read_image_text
from server import agent_pool, conversation_memory
from server.agent.runner import DEFAULT_DEEPSEEK_MODEL, DEFAULT_OPENAI_MODEL, IncompleteDeliverableError, generate_reply, model_requires_key, selected_text_model
from server.contracts.requests.conversation import ConversationRequest
from server.contracts.responses.conversation import ConversationListResponse, ConversationResponse
from server.conversations import MAX_IMAGE_SIZE, MAX_OTHER_FILE_SIZE, MAX_REPORT_TEMPLATE_SIZE, append_message, delete_conversation, find_conversation, list_conversations, public_conversation, save_reply, write_metadata
from server.features_services.jwt_validation import require_user
from server.features_services.model_settings import ModelConnection, load_models
from server.features_services.output_settings import output_instruction
from server.mcp_tools.readonly import mcp_app
from server.data_model import User
from server.routes import register_routes
from server.task_progress import get_progress, start_progress, update_progress

from . import CONSTANT


app = FastAPI(title="同学实验工作台", lifespan=mcp_app.lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    allow_private_network=True,
)
app.state.selected_software = "hfss"
workbench_router = APIRouter(dependencies=[Depends(require_user)])
logger = logging.getLogger("uvicorn.error")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SOFTWARE = (
    {"id": "modelsim", "name": "Modelsim SE-64 10.1c"},
    {"id": "quartus", "name": "Quartus II 13.1 (64-bit)"},
    {"id": "packet-tracer", "name": "Cisco Packet Tracer"},
    {"id": "hfss", "name": "Ansys HFSS"},
    {"id": "pycharm", "name": "PyCharm"},
)
DELIVERABLES = {"report", "paper", "code"}
ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
OTHER_FILE_EXTENSIONS = ATTACHMENT_EXTENSIONS | {".txt", ".csv", ".tsv", ".json", ".py", ".xlsx", ".xls", ".npy", ".doc", ".docx"}
DELIVERABLE_KEYWORDS = {"report": ("实验报告",), "paper": ("论文",), "code": ("实验源文件", "源文件")}


class SoftwareSelection(BaseModel):
    software_id: str


def require_supported_software(software_id: str) -> None:
    if not any(item["id"] == software_id for item in SOFTWARE):
        raise HTTPException(status_code=422, detail=CONSTANT.UNSUPPORTED_SOFTWARE)


def requested_deliverables(requirements: str, selected: list[str] | None) -> list[str]:
    mentioned = []
    for deliverable, keywords in DELIVERABLE_KEYWORDS.items():
        if any(keyword in requirements for keyword in keywords):
            mentioned.append(deliverable)
    return mentioned or list(selected or [])


@workbench_router.get("/api/software")
def list_software():
    return {
        "items": SOFTWARE,
        "selected_id": app.state.selected_software,
    }


@workbench_router.post("/api/software/selection")
def select_software(selection: SoftwareSelection):
    require_supported_software(selection.software_id)
    app.state.selected_software = selection.software_id
    return {"selected_id": selection.software_id}


@workbench_router.get("/api/tasks", response_model=ConversationListResponse)
def list_tasks(user: User = Depends(require_user)):
    return {"items": list_conversations(user.id)}


@workbench_router.get("/api/task-progress/{request_id}")
def task_progress(request_id: str, user: User = Depends(require_user)):
    return get_progress(user.id, request_id)


@workbench_router.delete("/api/tasks/{conversation_id}", status_code=204)
def delete_task(conversation_id: str, user: User = Depends(require_user)):
    find_conversation(DATA_DIR, conversation_id, user.id)
    if agent_pool.is_active(conversation_id):
        raise HTTPException(status_code=409, detail=CONSTANT.CONVERSATION_BUSY)
    delete_conversation(DATA_DIR, conversation_id, user.id)


@workbench_router.get("/api/tasks/{conversation_id}/reports/{message_id}/{filename}")
def download_report(conversation_id: str, message_id: str, filename: str, user: User = Depends(require_user)):
    task_dir, metadata = find_conversation(DATA_DIR, conversation_id, user.id)
    if Path(filename).name != filename or not filename.endswith(".docx"):
        raise HTTPException(status_code=404, detail=CONSTANT.REPORT_NOT_FOUND)
    for message in metadata["messages"]:
        if message["id"] == message_id and filename in message.get("report_files", []):
            path = task_dir / "reports" / message["id"] / filename
            if path.is_file():
                return FileResponse(path, filename=filename)
    raise HTTPException(status_code=404, detail=CONSTANT.REPORT_NOT_FOUND)


async def analyze_files(task_dir: Path, metadata: dict, owner_id: int, request_id: str | None) -> None:
    current = metadata["messages"][-1]
    message_dir = task_dir / "messages" / current["id"]
    for file_info in [current["guide"], *current["attachments"], *current["other_files"]]:
        if not file_info:
            continue
        source = message_dir / Path(file_info["stored_name"]).name
        try:
            update_progress(owner_id, request_id, CONSTANT.ANALYZING_FILE.format(name=source.name))
            logger.info(CONSTANT.FILE_ANALYSIS_STARTED_LOG, metadata["id"], source.suffix.lower())
            if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                await run_in_threadpool(read_image_text, source, message_dir / f"{source.stem}_analysis.json")
                logger.info(CONSTANT.IMAGE_ANALYSIS_FINISHED_LOG, metadata["id"])
        except ValueError as exc:
            logger.warning(CONSTANT.FILE_ANALYSIS_SKIPPED_LOG, metadata["id"], exc)


async def process_task(task_dir: Path, metadata: dict, software_id: str, owner_id: int,
                       request_id: str | None, started_at: float, model_name: str, api_key: str | None,
                       connection: ModelConnection | None = None) -> None:
    logger.info(CONSTANT.TASK_PROCESSING_STARTED_LOG, software_id,
                agent_pool.current_job_number(), threading.get_native_id(), metadata["id"])
    try:
        await analyze_files(task_dir, metadata, owner_id, request_id)

        update_progress(owner_id, request_id, CONSTANT.INITIALIZING_MODEL.format(model_name=model_name))
        logger.info(CONSTANT.MODEL_INITIALIZATION_LOG, metadata["id"], model_name)
        model = agent_pool.create_agent(owner_id, model_name, api_key, connection)
        update_progress(owner_id, request_id, CONSTANT.ANALYZING_TASK)
        logger.info(CONSTANT.REPLY_GENERATION_STARTED_LOG, metadata["id"])
        workflow_metadata = await run_in_threadpool(conversation_memory.model_context, metadata, owner_id)
        workflow_metadata["output_instruction"] = await run_in_threadpool(output_instruction, owner_id)
        for attempt in range(2 if software_id in {"hfss", "pycharm"} else 1):
            try:
                if request_id is None:
                    reply = await generate_reply(task_dir, workflow_metadata, software_id, model_name, model)
                else:
                    reply = await generate_reply(task_dir, workflow_metadata, software_id, model_name, model,
                                                 progress=lambda stage: update_progress(owner_id, request_id, stage))
                break
            except GraphRecursionError:
                if attempt or software_id not in {"hfss", "pycharm"}:
                    raise
                logger.info(CONSTANT.WORKFLOW_RESUMED_LOG,
                            software_id, agent_pool.current_job_number(), threading.get_native_id(), metadata["id"])
                update_progress(owner_id, request_id, CONSTANT.RESUMING_WORKFLOW)
                workflow_metadata = deepcopy(workflow_metadata)
                current = workflow_metadata["messages"][-1]
                current["requirements"] += ("\n\n当前执行已达到步骤上限。请先检查本会话已生成的文件和验证结果，"
                                            "只补全原始任务剩余步骤；不得重复已验证的操作或改变原始任务范围。")
        if not reply.strip():
            raise ValueError(CONSTANT.MODEL_EMPTY_REPLY)
        save_reply(task_dir, metadata, owner_id, reply, processing_seconds=time.monotonic() - started_at)
        update_progress(owner_id, request_id, CONSTANT.REPLY_GENERATED, done=True)
        logger.info(CONSTANT.REPLY_SAVED_LOG, metadata["id"])
    except Exception as exc:
        if isinstance(exc, GraphRecursionError):
            logger.warning(CONSTANT.WORKFLOW_LIMIT_REACHED_LOG, metadata["id"], type(exc).__name__)
        elif isinstance(exc, IncompleteDeliverableError):
            logger.warning(CONSTANT.DELIVERABLE_INCOMPLETE_LOG, metadata["id"], exc)
        elif isinstance(exc, ModelTimeoutError):
            logger.warning(CONSTANT.MODEL_TIMEOUT_LOG, metadata["id"], type(exc).__name__)
        elif status_code := getattr(exc, "status_code", None):
            logger.error(CONSTANT.MODEL_REQUEST_FAILED_LOG, metadata["id"], type(exc).__name__, status_code)
        elif connection and connection.custom:
            logger.error(CONSTANT.CUSTOM_MODEL_REQUEST_FAILED_LOG, metadata["id"], type(exc).__name__)
        else:
            logger.exception(CONSTANT.AGENT_FAILURE_LOG, metadata["id"])
        if isinstance(exc, GraphRecursionError):
            error_reply = CONSTANT.WORKFLOW_LIMIT_REACHED
        elif isinstance(exc, IncompleteDeliverableError):
            error_reply = str(exc)
        elif isinstance(exc, ModelTimeoutError):
            error_reply = CONSTANT.MODEL_TIMEOUT
        elif connection:
            error_reply = CONSTANT.CONFIGURED_MODEL_UNAVAILABLE
        elif model_name == DEFAULT_OPENAI_MODEL:
            error_reply = CONSTANT.OPENAI_UNAVAILABLE
        elif model_name != DEFAULT_DEEPSEEK_MODEL and ":" in model_name:
            error_reply = CONSTANT.PROVIDER_UNAVAILABLE
        elif model_name == DEFAULT_DEEPSEEK_MODEL:
            error_reply = CONSTANT.DEEPSEEK_UNAVAILABLE
        else:
            error_reply = CONSTANT.OLLAMA_UNAVAILABLE
        save_reply(task_dir, metadata, owner_id, error_reply, "error", processing_seconds=time.monotonic() - started_at)
        update_progress(owner_id, request_id, CONSTANT.TASK_FAILED, done=True)


@workbench_router.post("/api/tasks", status_code=201, response_model=ConversationResponse)
async def create_task(
    guide: UploadFile | None = File(None),
    report_template: UploadFile | None = File(None),
    requirements: str = Form(...),
    software_id: str = Form(...),
    deliverables: list[str] | None = Form(None),
    attachments: list[UploadFile] | None = File(None),
    other_files: list[UploadFile] | None = File(None),
    conversation_id: str | None = Form(None),
    request_id: str | None = Form(None),
    user: User = Depends(require_user),
):
    """先保存消息；配置模型后再路由到所选软件的工作流。"""
    deliverables = requested_deliverables(requirements, deliverables)
    if not conversation_id and guide is None:
        raise HTTPException(status_code=422, detail=CONSTANT.GUIDE_REQUIRED)
    if guide is not None:
        if not guide.filename or Path(guide.filename).suffix.lower() != ".pdf" or await guide.read(5) != b"%PDF-":
            raise HTTPException(status_code=422, detail=CONSTANT.GUIDE_PDF_INVALID)
        await guide.seek(0)
    if report_template is not None:
        if "report" not in (deliverables or []) or not report_template.filename or Path(report_template.filename).suffix.lower() not in {".doc", ".docx"}:
            raise HTTPException(status_code=422, detail=CONSTANT.REPORT_TEMPLATE_INVALID)
        if report_template.size is not None and report_template.size > MAX_REPORT_TEMPLATE_SIZE:
            raise HTTPException(status_code=413, detail=CONSTANT.REPORT_TEMPLATE_TOO_LARGE)
    if not requirements.strip():
        raise HTTPException(status_code=422, detail=CONSTANT.REQUIREMENTS_REQUIRED)
    if not deliverables or not set(deliverables).issubset(DELIVERABLES):
        raise HTTPException(status_code=422, detail=CONSTANT.DELIVERABLES_REQUIRED)
    require_supported_software(software_id)

    for attachment in attachments or []:
        if not attachment.filename or Path(attachment.filename).suffix.lower() not in ATTACHMENT_EXTENSIONS:
            raise HTTPException(status_code=422, detail=CONSTANT.ATTACHMENT_TYPE_UNSUPPORTED)
    for upload in other_files or []:
        if not upload.filename or Path(upload.filename).suffix.lower() not in OTHER_FILE_EXTENSIONS:
            raise HTTPException(status_code=422, detail=CONSTANT.OTHER_FILE_TYPE_UNSUPPORTED)
        if upload.size is not None and upload.size > (MAX_IMAGE_SIZE if Path(upload.filename).suffix.lower() in ATTACHMENT_EXTENSIONS - {".pdf"} else MAX_OTHER_FILE_SIZE):
            raise HTTPException(status_code=413, detail=CONSTANT.OTHER_FILE_TOO_LARGE)
    request = ConversationRequest(
        requirements=requirements,
        software_id=software_id,
        deliverables=deliverables,
        conversation_id=conversation_id,
        guide=guide,
        report_template=report_template,
        attachments=attachments or [],
        other_files=other_files or [],
    )
    return await start_experiment_task(request, user.id, request_id)


async def start_experiment_task(request: ConversationRequest, owner_id: int, request_id: str | None) -> ConversationResponse:
    software_id, conversation_id = request.software_id, request.conversation_id
    saved_models = await run_in_threadpool(load_models, owner_id)
    connection = None
    if saved_models.text.model or saved_models.text.connection:
        model_name, api_key, connection = selected_text_model(saved_models.text)
    else:
        model_name, api_key = agent_pool.agent_settings()
    logger.info(CONSTANT.TASK_REQUEST_VALIDATED_LOG,
                software_id, threading.get_native_id(), owner_id, conversation_id or "新建")
    started_at = time.monotonic()
    if request.execution_scope is None:
        start_progress(owner_id, request_id)
    if conversation_id:
        find_conversation(DATA_DIR, conversation_id, owner_id)
        agent_pool.reserve(conversation_id)
    try:
        if request.execution_scope == "mcp":
            _, existing = find_conversation(DATA_DIR, conversation_id, owner_id)
            if any(message.get("request_id") == request_id for message in existing["messages"]):
                agent_pool.release(conversation_id)
                return public_conversation(existing)
            start_progress(owner_id, request_id)
        task_dir, metadata = await append_message(DATA_DIR, request, owner_id)
    except Exception:
        if conversation_id:
            agent_pool.release(conversation_id)
        update_progress(owner_id, request_id, CONSTANT.FILE_SAVE_FAILED, done=True)
        raise
    update_progress(owner_id, request_id, CONSTANT.MESSAGE_FILES_SAVED,
                    conversation_id=metadata["id"] if request.execution_scope == "mcp" else None)
    logger.info(CONSTANT.MESSAGE_FILES_SAVED_LOG, metadata["id"])
    if not conversation_id:
        agent_pool.reserve(metadata["id"])
    if not model_requires_key(model_name) or api_key or agent_pool.AGENT_MODEL_FACTORY is not None:
        if request_id:
            metadata["messages"][-1].update(status="processing", request_id=request_id)
            try:
                write_metadata(task_dir, metadata, owner_id)
                response = public_conversation(metadata)
                agent_pool.submit(metadata["id"], lambda: process_task(
                    task_dir, metadata, software_id, owner_id, request_id, started_at, model_name, api_key, connection), software_id)
                return response
            except Exception:
                agent_pool.release(metadata["id"])
                logger.exception(CONSTANT.AGENT_START_FAILED_LOG, metadata["id"])
                save_reply(task_dir, metadata, owner_id, CONSTANT.TASK_START_FAILED, "error")
                update_progress(owner_id, request_id, CONSTANT.TASK_FAILED, done=True)
                return public_conversation(metadata)
        try:
            await process_task(task_dir, metadata, software_id, owner_id, None, started_at, model_name, api_key, connection)
        finally:
            agent_pool.release(metadata["id"])
    else:
        try:
            await analyze_files(task_dir, metadata, owner_id, request_id)
        finally:
            agent_pool.release(metadata["id"])
        update_progress(owner_id, request_id, CONSTANT.MODEL_UNCONFIGURED_DRAFT_SAVED, done=True)
        key_name = "API_KEY" if model_name.startswith("openai:") else "DEEPSEEK_API_KEY"
        logger.info(CONSTANT.MODEL_UNCONFIGURED_LOG, metadata["id"], key_name)
    return public_conversation(metadata)


register_routes(app, workbench_router)
app.mount("/mcp", mcp_app)
