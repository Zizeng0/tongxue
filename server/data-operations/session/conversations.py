"""会话元数据存入数据库，上传文件保留在本地目录。"""

import re
import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from server import conversation_memory, database
from server.General_Tools.report_writer import convert_doc_template, inspect_report_template
from server.contracts.requests.conversation import ConversationRequest
from server.contracts.responses.conversation import ConversationResponse
from server.data_model import Conversation, SolutionConversation

from . import CONSTANT


MAX_PDF_SIZE = 100 * 1024 * 1024
MAX_GUIDE_SIZE = 120 * 1024 * 1024
MAX_IMAGE_SIZE = 25 * 1024 * 1024
MAX_REPORT_TEMPLATE_SIZE = 10 * 1024 * 1024
MAX_OTHER_FILE_SIZE = 100 * 1024 * 1024
_table_lock = Lock()


def conversation_title(requirements: str) -> str:
    """取首句作为会话名，并移除 Windows 文件夹名不允许的字符。"""
    first_sentence = re.split(r"[。！？!?\r\n]|(?<!\d)\.(?!\d)", requirements.strip(), maxsplit=1)[0]
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", first_sentence).strip(" .")[:60].rstrip(" .")
    if not title or title.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)), *(f"LPT{index}" for index in range(1, 10))}:
        return "新建会话"
    return title


def read_conversation(metadata: dict) -> dict:
    """把旧版单任务草稿转换为会话结构，保证已有记录仍可读取。"""
    if "messages" in metadata:
        return metadata
    return {
        "id": metadata["id"],
        "title": conversation_title(metadata["requirements"]),
        "created_at": metadata["created_at"],
        "messages": [{
            "id": metadata["id"],
            "created_at": metadata["created_at"],
            "requirements": metadata["requirements"],
            "guide": metadata["guide"],
            "report_template": metadata.get("report_template"),
            "attachments": metadata["attachments"],
            "other_files": metadata.get("other_files", []),
        }],
    }


def public_conversation(metadata: dict) -> ConversationResponse:
    """只返回界面需要的文字和原始文件名，不暴露服务端存储路径。"""
    conversation = read_conversation(metadata)
    return ConversationResponse.model_validate({
        "id": conversation["id"],
        "title": conversation["title"],
        "created_at": conversation["created_at"],
        "messages": [{
            "id": message["id"],
            "created_at": message["created_at"],
            "requirements": message["requirements"],
            "guide_name": message["guide"]["name"] if message["guide"] else None,
            "software_id": message.get("software_id"),
            "report_template_name": message.get("report_template", {}).get("name") if message.get("report_template") else None,
            "report_files": message.get("report_files", []),
            "artifact_files": message.get("artifact_files", []),
            "processing_seconds": message.get("processing_seconds"),
            "stage_seconds": message.get("stage_seconds", {}),
            "attachment_names": [attachment["name"] for attachment in message["attachments"]],
            "output_images": message.get("output_images", []),
            "other_file_names": [file["name"] for file in message.get("other_files", [])],
            "reply": message.get("reply"),
            "exercise_candidates": message.get("exercise_candidates"),
            "vision_status": message.get("vision_status"),
            "status": message.get("status", "draft"),
            "request_id": message.get("request_id"),
        } for message in conversation["messages"]],
    })


def ensure_table() -> None:
    with _table_lock:
        engine = database.get_engine()
        Conversation.__table__.create(engine, checkfirst=True)
        SolutionConversation.__table__.create(engine, checkfirst=True)
        conversation_memory.ensure_tables()


def conversation_rows(session, owner_id: int, kind: str | None = None):
    models = (Conversation, SolutionConversation) if kind != "task" else (Conversation,)
    for model in models:
        for row in session.scalars(select(model).where(
            model.owner_id == owner_id, model.conversation_id.not_in(conversation_memory.archived_ids(session, owner_id)),
        )):
            if kind is None or row.metadata_json.get("kind", "task") == kind:
                yield row


def conversation_row(session, conversation_id: str, owner_id: int, kind: str | None = None, *, for_update=False):
    models = (Conversation, SolutionConversation) if kind != "solution" else (SolutionConversation, Conversation)
    for model in models:
        query = select(model).where(
            model.conversation_id == conversation_id, model.owner_id == owner_id,
        )
        if for_update:
            query = query.with_for_update()
        else:
            query = query.where(model.conversation_id.not_in(conversation_memory.archived_ids(session, owner_id)))
        row = session.scalar(query)
        if row is not None and (kind is None or row.metadata_json.get("kind", "task") == kind):
            return row
    return None


def list_conversations(owner_id: int, kind: str = "task") -> list[ConversationResponse]:
    ensure_table()
    with database.get_session() as session:
        items = [public_conversation(row.metadata_json) for row in conversation_rows(session, owner_id, kind)]
    items.sort(key=lambda item: item.created_at)
    return items


def find_conversation(data_dir: Path, conversation_id: str, owner_id: int) -> tuple[Path, dict]:
    ensure_table()
    with database.get_session() as session:
        row = conversation_row(session, conversation_id, owner_id)
        if row is None:
            raise HTTPException(status_code=404, detail=CONSTANT.CONVERSATION_NOT_FOUND)
        return data_dir / "tasks" / row.directory_name, row.metadata_json


def delete_conversation(data_dir: Path, conversation_id: str, owner_id: int, kind: str = "task") -> None:
    ensure_table()
    with database.get_session() as session:
        row = conversation_row(session, conversation_id, owner_id, kind, for_update=True)
        if row is None or row.metadata_json.get("kind", "task") != kind:
            raise HTTPException(status_code=404, detail=CONSTANT.CONVERSATION_NOT_FOUND)
        metadata = read_conversation(row.metadata_json)
        view = conversation_memory.archive_view(session, metadata, owner_id)
        if view.archived:
            raise HTTPException(status_code=404, detail=CONSTANT.CONVERSATION_NOT_FOUND)
        view.archived = True
        records = conversation_memory.append_events(session, metadata, owner_id)
        payloads = [(record.event_key, record.event) for record in records]
        session.commit()
        mirror_ledger(data_dir / "tasks" / row.directory_name, payloads)


def mirror_ledger(task_dir: Path, payloads: list[tuple[str, dict]]) -> None:
    try:
        conversation_memory.mirror_events(task_dir, payloads)
    except OSError:
        logging.getLogger("uvicorn.error").exception("Could not mirror conversation ledger to task directory")


def write_metadata(task_dir: Path, metadata: dict, owner_id: int) -> None:
    ensure_table()
    with database.get_session() as session:
        row = conversation_row(session, metadata["id"], owner_id, for_update=True)
        if row is None:
            model = SolutionConversation if metadata.get("kind") == "solution" else Conversation
            row = model(conversation_id=metadata["id"], owner_id=owner_id, directory_name=task_dir.name, metadata_json=metadata)
            session.add(row)
        else:
            saved = read_conversation(row.metadata_json)
            current = metadata["messages"][-1]
            messages = {message["id"]: message for message in saved["messages"]}
            messages[current["id"]] = current
            row.metadata_json = {**saved, "messages": list(messages.values())}
        conversation_memory.archive_view(session, metadata, owner_id)
        records = conversation_memory.append_events(session, row.metadata_json, owner_id)
        payloads = [(record.event_key, record.event) for record in records]
        new_requests = [record.event["payload"]["text"] for record in records
                        if record.id is None and record.role == "user"]
        if new_requests:
            conversation_memory.store_preferences(session, owner_id, "\n".join(new_requests))
        session.commit()
    mirror_ledger(task_dir, payloads)


def backfill_conversation_memory(data_dir: Path) -> int:
    ensure_table()
    with database.get_session() as session:
        sources = [(row.owner_id, row.directory_name, row.conversation_id)
                   for model in (Conversation, SolutionConversation) for row in session.scalars(select(model))]
    for owner_id, directory, conversation_id in sources:
        with database.get_session() as session:
            row = conversation_row(session, conversation_id, owner_id, for_update=True)
            metadata = read_conversation(row.metadata_json)
            conversation_memory.archive_view(session, metadata, owner_id)
            records = conversation_memory.append_events(session, metadata, owner_id)
            requests = [record.event["payload"]["text"] for record in records
                        if record.id is None and record.role == "user"]
            if requests:
                conversation_memory.store_preferences(session, owner_id, "\n".join(requests))
            payloads = [(record.event_key, record.event) for record in records]
            session.commit()
        mirror_ledger(data_dir / "tasks" / directory, payloads)
    return len(sources)


async def save_upload(upload: UploadFile, target: Path, limit: int | None = None) -> dict:
    """分块写入上传文件，同时限制单文件大小，避免一次读入全部内容。"""
    size = 0
    if limit is None:
        limit = MAX_PDF_SIZE if target.suffix.lower() == ".pdf" else MAX_IMAGE_SIZE
    with target.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail=CONSTANT.UPLOAD_TOO_LARGE.format(value1=limit // (1024 * 1024), filename=upload.filename))
            output.write(chunk)
    return {"name": upload.filename, "stored_name": target.name, "size": size}


async def append_message(
    data_dir: Path,
    request: ConversationRequest,
    owner_id: int,
    kind: str = "task",
    *,
    initial_status: str = "draft",
) -> tuple[Path, dict]:
    """新建会话或追加消息，返回会话目录和已写入的元数据。"""
    tasks_dir = data_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    if request.conversation_id:
        task_dir, metadata = find_conversation(data_dir, request.conversation_id, owner_id)
        if metadata.get("kind", "task") != kind:
            raise HTTPException(status_code=404, detail=CONSTANT.CONVERSATION_NOT_FOUND)
        if request.guide is not None:
            raise HTTPException(status_code=409, detail=CONSTANT.CONVERSATION_GUIDE_ALREADY_SET)
    else:
        base_title = conversation_title(request.requirements)
        for index in range(1, 10000):
            title = base_title if index == 1 else f"{base_title} ({index})"
            task_dir = tasks_dir / title
            try:
                task_dir.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise HTTPException(status_code=409, detail=CONSTANT.TOO_MANY_SAME_NAME_CONVERSATIONS)
        metadata = {"id": uuid4().hex, "kind": kind, "title": title,
                    "created_at": datetime.now(timezone.utc).isoformat(), "messages": []}

    message_id = uuid4().hex
    message_dir = task_dir / "messages" / message_id
    message_dir.mkdir(parents=True)
    try:
        guide_file = await save_upload(request.guide, message_dir / "guide.pdf", MAX_GUIDE_SIZE) if request.guide else None
        template_extension = Path(request.report_template.filename).suffix.lower() if request.report_template else None
        report_template_file = await save_upload(request.report_template, message_dir / f"report_template{template_extension}", MAX_REPORT_TEMPLATE_SIZE) if request.report_template else None
        if report_template_file:
            try:
                template_path = message_dir / report_template_file["stored_name"]
                if template_extension == ".doc":
                    await run_in_threadpool(convert_doc_template, template_path)
                else:
                    inspect_report_template(template_path)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        attachment_files = []
        for index, attachment in enumerate(request.attachments):
            extension = Path(attachment.filename).suffix.lower()
            attachment_files.append(await save_upload(attachment, message_dir / f"attachment-{index + 1}{extension}"))
        other_files = []
        for index, upload in enumerate(request.other_files):
            extension = Path(upload.filename).suffix.lower()
            limit = MAX_IMAGE_SIZE if extension in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"} else MAX_OTHER_FILE_SIZE
            other_files.append(await save_upload(upload, message_dir / f"other-{index + 1}{extension}", limit))
        metadata["messages"].append({
            "id": message_id,
            "status": initial_status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requirements": request.requirements.strip(),
            "software_id": request.software_id,
            "deliverables": request.deliverables,
            "guide": guide_file,
            "report_template": report_template_file,
            "attachments": attachment_files,
            "other_files": other_files,
            "reply": None,
            **({"request_id": request.request_id} if request.request_id else {}),
            **({"execution_scope": request.execution_scope} if request.execution_scope else {}),
        })
        write_metadata(task_dir, metadata, owner_id)
    except Exception:
        shutil.rmtree(task_dir if not request.conversation_id else message_dir)
        raise
    return task_dir, metadata


def save_reply(task_dir: Path, metadata: dict, owner_id: int, reply: str, status: str = "answered", processing_seconds: float | None = None) -> None:
    metadata["messages"][-1]["reply"] = reply
    metadata["messages"][-1]["replied_at"] = datetime.now(timezone.utc).isoformat()
    metadata["messages"][-1]["status"] = status
    if processing_seconds is not None:
        metadata["messages"][-1]["processing_seconds"] = round(processing_seconds, 1)
    reports_dir = task_dir / "reports" / metadata["messages"][-1]["id"]
    metadata["messages"][-1]["report_files"] = [path.name for path in reports_dir.glob("*.docx") if path.is_file()]
    write_metadata(task_dir, metadata, owner_id)
