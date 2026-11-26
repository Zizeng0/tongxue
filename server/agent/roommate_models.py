"""Account-scoped API clients shared by Roommate workflows."""

import logging

import httpx
from starlette.concurrency import run_in_threadpool

from server import solutions
from server.agent import runner
from server.agent.roommate_types import RoommateError
from server.features_services.model_settings import SavedModel, effective_model

from . import CONSTANT

logger = logging.getLogger("uvicorn.error")


def text_api(owner_id: int, saved: SavedModel):
    """Create a client for the request's event loop."""
    name, key, connection = runner.selected_text_model(saved)
    if connection.provider == "ollama" or not connection.base_url or not key:
        return None
    return runner.initialize_user_model.__wrapped__(owner_id, name, key, connection)


async def vision_text(content: bytes, saved: SavedModel, *, as_material: bool = False) -> str:
    model, url, key = solutions.vision_model_settings()
    connection = None
    if saved.connection:
        model, connection = effective_model("vision", saved)
        url = connection.base_url
        key = connection.api_key.get_secret_value() if connection.api_key else ""
    elif saved.model:
        model = saved.model
    if not url or not key:
        raise RoommateError(CONSTANT.ROOMMATE_VISION_MODEL_REQUIRED)
    try:
        options = {"prompt": CONSTANT.ROOMMATE_VISION_MATERIAL_PROMPT} if as_material else {}
        transcript = await run_in_threadpool(solutions.transcribe_with_vision_api, content, url, key, model, connection, **options)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise RoommateError(CONSTANT.ROOMMATE_VISION_REQUEST_FAILED) from exc
    if not transcript:
        raise RoommateError(CONSTANT.ROOMMATE_EMPTY_VISION)
    return transcript



async def close_text_api(model) -> None:
    try:
        if async_client := getattr(model, "root_async_client", None):
            await async_client.close()
        if sync_client := getattr(model, "root_client", None):
            await run_in_threadpool(sync_client.close)
    except Exception as exc:
        logger.warning(CONSTANT.ROOMMATE_MODEL_CLOSE_FAILED_LOG, type(exc).__name__)
