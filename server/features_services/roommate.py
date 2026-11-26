"""室友会话及上传下载接口。"""

import logging
from io import BytesIO
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from server import task_progress
from server.agent.roommate_ledger import mirror_owner
from server.agent.roommate import RoommateError, run_roommate
from server.agent.CONSTANT import ATTACHMENT_REQUEST, ROOMMATE_IDENTIFYING_TASK
from server.General_Tools.pdf_on_demand import validate_pdf_material
from server.General_Tools.presentation_writer import load_template
from server.agent.thesis.word_format import validate_thesis
from server.contracts.requests.conversation import ConversationRequest
from server.contracts.responses.conversation import ConversationListResponse, ConversationResponse
from server.conversations import MAX_IMAGE_SIZE, append_message, delete_conversation, find_conversation, list_conversations, public_conversation, save_reply
from server.data_model import User
from server.features_services.jwt_validation import require_user
from server.features_services.project_service import IMAGE_EXTENSIONS, data_dir

from . import CONSTANT

router = APIRouter(prefix="/api/roommate", tags=["roommate"])
logger = logging.getLogger("uvicorn.error")
KIND = "roommate"
MAX_IMAGES = 4
MAX_FILES = 4
FILE_LIMITS = {".pdf": MAX_IMAGE_SIZE, ".pptx": MAX_IMAGE_SIZE, ".docx": MAX_IMAGE_SIZE,
               ".txt": 256 * 1024, ".md": 256 * 1024}


def find_roommate(conversation_id: str, owner_id: int) -> tuple[Path, dict]:
    directory, metadata = find_conversation(data_dir(), conversation_id, owner_id)
    if metadata.get("kind") != KIND:
        raise HTTPException(status_code=404, detail=CONSTANT.ROOMMATE_NOT_FOUND)
    return directory, metadata


def check_image(content: bytes) -> None:
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP", "GIF", "BMP"} or image.width * image.height > 25_000_000:
                raise ValueError(CONSTANT.ROOMMATE_IMAGE_FORMAT_INVALID)
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError) as exc:
        raise HTTPException(status_code=422, detail=CONSTANT.ROOMMATE_IMAGE_INVALID) from exc


@router.get("", response_model=ConversationListResponse)
def list_roommate(user: User = Depends(require_user)):
    return ConversationListResponse(items=list_conversations(user.id, KIND))


@router.delete("/{conversation_id}", status_code=204)
def delete_roommate(conversation_id: str, user: User = Depends(require_user)):
    delete_conversation(data_dir(), conversation_id, user.id, KIND)


@router.post("", status_code=201, response_model=ConversationResponse)
async def create_roommate(
    background_tasks: BackgroundTasks,
    question: str = Form("", max_length=16000),
    images: list[UploadFile] = File(default_factory=list),
    files: list[UploadFile] = File(default_factory=list),
    conversation_id: str | None = Form(None, max_length=32),
    request_id: str | None = Form(None, max_length=36),
    user: User = Depends(require_user),
):
    question = question.strip()
    if not question and not images and not files:
        raise HTTPException(status_code=422, detail=CONSTANT.ROOMMATE_INPUT_REQUIRED)
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=422, detail=CONSTANT.ROOMMATE_TOO_MANY_IMAGES.format(limit=MAX_IMAGES))
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=422, detail=CONSTANT.ROOMMATE_TOO_MANY_FILES.format(limit=MAX_FILES))
    template_count = 0
    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in FILE_LIMITS:
            raise HTTPException(status_code=422, detail=CONSTANT.ROOMMATE_FILE_TYPE_UNSUPPORTED)
        if suffix == ".pptx":
            template_count += 1
            if template_count > 1:
                raise HTTPException(status_code=422, detail=CONSTANT.ROOMMATE_TOO_MANY_TEMPLATES)
        limit = FILE_LIMITS[suffix]
        content = await upload.read(limit + 1)
        if len(content) > limit:
            raise HTTPException(status_code=413, detail=CONSTANT.ROOMMATE_FILE_SIZE_EXCEEDED)
        try:
            if suffix == ".pptx":
                await run_in_threadpool(load_template, BytesIO(content))
            elif suffix == ".pdf":
                await run_in_threadpool(validate_pdf_material, content)
            elif suffix == ".docx":
                await run_in_threadpool(validate_thesis, content)
            else:
                content.decode("utf-8-sig")
        except ValueError as exc:
            detail = str(exc) if suffix not in {".txt", ".md"} else CONSTANT.ROOMMATE_TEXT_ENCODING_INVALID
            raise HTTPException(status_code=422, detail=detail) from exc
        await upload.seek(0)
    for upload in images:
        if not upload.filename or Path(upload.filename).suffix.lower() not in IMAGE_EXTENSIONS:
            raise HTTPException(status_code=422, detail=CONSTANT.ROOMMATE_IMAGE_TYPE_UNSUPPORTED)
        content = await upload.read(MAX_IMAGE_SIZE + 1)
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=413, detail=CONSTANT.ROOMMATE_IMAGE_SIZE_EXCEEDED)
        await run_in_threadpool(check_image, content)
        await upload.seek(0)
    started_at = perf_counter()
    finished = CONSTANT.ROOMMATE_TASK_ERROR

    def stage(message: str) -> None:
        task_progress.update_progress(user.id, request_id, message)

    try:
        task_progress.start_progress(user.id, request_id)
        stage(CONSTANT.ROOMMATE_SAVING_TASK)
        request = ConversationRequest(requirements=question or ATTACHMENT_REQUEST, software_id=KIND,
                                      deliverables=[], conversation_id=conversation_id, request_id=request_id,
                                      attachments=images, other_files=files)
        task_dir, metadata = await append_message(data_dir(), request, user.id, kind=KIND, initial_status="processing")
        task_progress.update_progress(user.id, request_id, ROOMMATE_IDENTIFYING_TASK, conversation_id=metadata["id"])
        message = metadata["messages"][-1]
        try:
            result = await run_roommate(user.id, task_dir, metadata, stage)
        except RoommateError as exc:
            reply, status = str(exc), "error"
        except Exception as exc:
            logger.error(CONSTANT.ROOMMATE_TASK_FAILED_LOG, type(exc).__name__)
            reply, status = CONSTANT.ROOMMATE_TASK_FAILED, "error"
        else:
            message.update(roommate_result=result.exportable, roommate_evidence=result.evidence,
                           output_images=result.figures, artifact_files=result.files)
            if result.action:
                message["roommate_action"] = result.action
            if result.reused_inputs:
                message["roommate_reused_inputs"] = result.reused_inputs
            if result.reference_message_id:
                message["roommate_reference_id"] = result.reference_message_id
            if result.presentation:
                message["roommate_presentation"] = result.presentation
            if result.needs_clarification:
                message["roommate_clarification"] = True
                if result.clarification_action:
                    message["roommate_clarification_action"] = result.clarification_action
            reply, status = result.reply, "answered"
        stage(CONSTANT.ROOMMATE_SAVING_REPLY)
        await run_in_threadpool(save_reply, task_dir, metadata, user.id, reply, status, perf_counter() - started_at)
        background_tasks.add_task(mirror_owner, user.id)
        finished = CONSTANT.ROOMMATE_TASK_FINISHED if status == "answered" else CONSTANT.ROOMMATE_TASK_ERROR
        return public_conversation(metadata)
    finally:
        task_progress.update_progress(user.id, request_id, finished, done=True)


@router.get("/{conversation_id}/images/{message_id}/{filename}")
def generated_image(conversation_id: str, message_id: str, filename: str, user: User = Depends(require_user)):
    directory, metadata = find_roommate(conversation_id, user.id)
    relative = f"solutions/{message_id}/{filename}"
    if not any(relative in message.get("output_images", []) for message in metadata["messages"]):
        raise HTTPException(status_code=404, detail=CONSTANT.ROOMMATE_OUTPUT_IMAGE_MISSING)
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve() / "solutions") or not path.is_file():
        raise HTTPException(status_code=404, detail=CONSTANT.ROOMMATE_OUTPUT_IMAGE_MISSING)
    return FileResponse(path, media_type="image/png")


@router.get("/{conversation_id}/input-images/{message_id}/{index}")
def input_image(conversation_id: str, message_id: str, index: int, user: User = Depends(require_user)):
    directory, metadata = find_roommate(conversation_id, user.id)
    message = next((turn for turn in metadata["messages"] if turn["id"] == message_id), None)
    attachments = message["attachments"] if message else []
    if not 0 <= index < len(attachments):
        raise HTTPException(status_code=404, detail=CONSTANT.ROOMMATE_INPUT_IMAGE_MISSING)
    path = (directory / "messages" / message_id / attachments[index]["stored_name"]).resolve()
    if not path.is_relative_to(directory.resolve() / "messages") or not path.is_file():
        raise HTTPException(status_code=404, detail=CONSTANT.ROOMMATE_INPUT_IMAGE_MISSING)
    return FileResponse(path)
