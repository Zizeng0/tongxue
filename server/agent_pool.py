"""同一会话一次运行一个 Agent 任务，多个会话可以并行处理。"""

import asyncio
import itertools
import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Lock, get_native_id
from typing import Awaitable, Callable

from fastapi import HTTPException

from server.agent.runner import initialize_user_model, model_api_key, model_settings
from server.features_services.model_settings import ModelConnection

from . import CONSTANT


# 设置任务 Agent 的模型名称，可覆盖 CHAT_MODEL 或 OLLAMA_MODEL。
AGENT_MODEL_NAME: str | None = None
AGENT_MAX_WORKERS = 4
# 自定义工厂接收 owner_id、model_name 和 api_key，并返回模型实例。
AGENT_MODEL_FACTORY: Callable[[int, str, str | None], object] | None = None

_executor = ThreadPoolExecutor(max_workers=AGENT_MAX_WORKERS, thread_name_prefix="task-agent")
_lock = Lock()
_active: set[str] = set()
_job_numbers = itertools.count(1)
_current_job_number: ContextVar[int | None] = ContextVar("agent_job_number", default=None)
logger = logging.getLogger("uvicorn.error")


def agent_settings() -> tuple[str, str | None]:
    configured_name, api_key = model_settings()
    if AGENT_MODEL_NAME is not None:
        return AGENT_MODEL_NAME, model_api_key(AGENT_MODEL_NAME)
    return configured_name, api_key


def reserve(conversation_id: str) -> None:
    with _lock:
        if conversation_id in _active:
            raise HTTPException(status_code=409, detail=CONSTANT.CONVERSATION_BUSY)
        _active.add(conversation_id)


def release(conversation_id: str) -> None:
    with _lock:
        _active.discard(conversation_id)


def is_active(conversation_id: str) -> bool:
    with _lock:
        return conversation_id in _active


def current_job_number() -> int | None:
    return _current_job_number.get()


def create_agent(owner_id: int, model_name: str, api_key: str | None, connection: ModelConnection | None = None):
    if AGENT_MODEL_FACTORY is not None:
        return AGENT_MODEL_FACTORY(owner_id, model_name, api_key)
    # 旧调用方可复用缓存的模型，任务 Agent 每次创建独立实例。
    factory = getattr(initialize_user_model, "__wrapped__", initialize_user_model)
    if connection:
        return factory(owner_id, model_name, api_key, connection)
    return factory(owner_id, model_name, api_key)


def submit(conversation_id: str, job: Callable[[], Awaitable[None]], module_name: str = "experiment") -> None:
    """调用方在保存消息之前预留该会话的执行位置。"""
    with _lock:
        job_number = next(_job_numbers)

    def run() -> None:
        thread_id = get_native_id()
        token = _current_job_number.set(job_number)
        logger.info(CONSTANT.AGENT_JOB_STARTED_LOG, job_number, module_name,
                    thread_id, conversation_id)
        try:
            asyncio.run(job())
        finally:
            logger.info(CONSTANT.AGENT_JOB_FINISHED_LOG, job_number, module_name,
                        thread_id, conversation_id)
            _current_job_number.reset(token)
            release(conversation_id)

    try:
        _executor.submit(run)
    except Exception:
        release(conversation_id)
        raise
