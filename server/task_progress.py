"""短期保存任务执行阶段，供当前用户轮询。"""

import time
from threading import Lock
from uuid import UUID

from fastapi import HTTPException

from . import CONSTANT


_lock = Lock()
_tasks: dict[tuple[int, str], dict] = {}
_retention_seconds = 3600


def start_progress(user_id: int, request_id: str | None) -> None:
    if request_id is None:
        return
    try:
        UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=CONSTANT.REQUEST_ID_INVALID) from None
    with _lock:
        now = time.monotonic()
        for key, state in list(_tasks.items()):
            if now - state["updated"] > _retention_seconds:
                del _tasks[key]
        _tasks[user_id, request_id] = {"stage": "正在接收任务", "done": False, "updated": now}


def update_progress(user_id: int, request_id: str | None, stage: str, done: bool = False, *,
                    conversation_id: str | None = None) -> None:
    if request_id is None:
        return
    with _lock:
        state = _tasks.get((user_id, request_id))
        if state is not None:
            state.update(stage=stage, done=done, updated=time.monotonic())
            if conversation_id is not None:
                state["conversation_id"] = conversation_id


def get_progress(user_id: int, request_id: str) -> dict:
    with _lock:
        state = _tasks.get((user_id, request_id))
        if state is None:
            raise HTTPException(status_code=404, detail=CONSTANT.TASK_PROGRESS_NOT_FOUND)
        return {key: state[key] for key in ("stage", "done", "conversation_id") if key in state}
