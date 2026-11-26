"""维护仅追加的会话记录，以及模型上下文共用的规则。"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Mapped, mapped_column

from server import database

from . import CONSTANT


logger = logging.getLogger("uvicorn.error")
RECENT_TURNS = 4
SUMMARY_LIMIT = 2400
PROFILE_LIMIT = 1600
PREFERENCE = re.compile(
    r"^(?:请)?(?:记住|以后|今后|默认|长期偏好|我的偏好|我偏好|我习惯|我喜欢|我不喜欢)"
    r"[：:，, ]*.+$"
)
_schema_lock = Lock()


class ConversationEvent(database.Base):
    __tablename__ = "conversation_ledger"
    owner_id: Mapped[int] = mapped_column(index=True)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    message_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    event_key: Mapped[str] = mapped_column(String(64), unique=True)
    event: Mapped[dict] = mapped_column(JSON)


class ConversationView(database.Base):
    __tablename__ = "conversation_memory_views"
    conversation_id: Mapped[str] = mapped_column(String(32), unique=True)
    owner_id: Mapped[int] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[str] = mapped_column(String(48))
    archived: Mapped[bool] = mapped_column(default=False)


class UserProfile(database.Base):
    __tablename__ = "conversation_user_profiles"
    owner_id: Mapped[int] = mapped_column(unique=True)
    user_name: Mapped[str] = mapped_column(String(50))
    preferences: Mapped[list] = mapped_column(JSON)


def ensure_tables() -> None:
    with _schema_lock:
        engine = database.get_engine()
        database.Base.metadata.create_all(
            engine, tables=[ConversationEvent.__table__, ConversationView.__table__, UserProfile.__table__],
        )


def archive_view(session, metadata: dict, owner_id: int) -> ConversationView:
    view = session.scalar(select(ConversationView).where(ConversationView.conversation_id == metadata["id"]))
    if view is None:
        view = ConversationView(conversation_id=metadata["id"], owner_id=owner_id,
                                title=metadata["title"], started_at=metadata["created_at"], archived=False)
        session.add(view)
    return view


def archived_ids(session, owner_id: int):
    return select(ConversationView.conversation_id).where(
        ConversationView.owner_id == owner_id, ConversationView.archived.is_(True),
    )


def append_events(session, metadata: dict, owner_id: int) -> list[ConversationEvent]:
    """为新增消息或更新后的答案追加不可变记录。"""
    existing = list(session.scalars(select(ConversationEvent).where(
        ConversationEvent.owner_id == owner_id, ConversationEvent.conversation_id == metadata["id"],
    ).order_by(ConversationEvent.id)))
    latest = {(row.message_id, row.role): row.event for row in existing}
    additions = []
    for message in metadata["messages"]:
        for role, text in (("user", message["requirements"]), ("assistant", message.get("reply"))):
            if text is None:
                continue
            payload = {"text": text}
            if role == "user":
                payload["files"] = [item for item in [
                    message.get("guide"), message.get("report_template"), *message.get("attachments", []),
                    *message.get("other_files", []),
                ] if item]
            else:
                payload["status"] = message.get("status", "answered")
                payload["files"] = [*message.get("report_files", []), *message.get("output_images", []),
                                    *message.get("artifact_files", [])]
            key = (message["id"], role)
            previous = latest.get(key)
            if previous and (role == "user" or previous["payload"] == payload):
                continue
            event = {"conversation_id": metadata["id"], "message_id": message["id"], "role": role,
                     "kind": metadata.get("kind", "task"), "payload": payload,
                     "created_at": message["created_at"] if role == "user" else
                     message.get("replied_at", datetime.now(timezone.utc).isoformat())}
            row = ConversationEvent(owner_id=owner_id, conversation_id=metadata["id"], message_id=message["id"],
                                    role=role, event_key=uuid4().hex, event=event)
            session.add(row)
            additions.append(row)
    return [*existing, *additions]


def mirror_events(task_dir: Path, records: list[tuple[str, dict]]) -> None:
    ledger = task_dir / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    for key, event in records:
        path = ledger / f"{key}.json"
        if path.is_file():
            continue
        temporary = ledger / f".{key}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.rename(path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)


def declared_preferences(text: str) -> list[str]:
    return [line for part in re.split(r"[。\n；！？]", text)
            if PREFERENCE.fullmatch(line := part.strip())]


def store_preferences(session, owner_id: int, text: str) -> bool:
    additions = declared_preferences(text)
    if not additions:
        return False
    from server.data_model import User

    user = session.get(User, owner_id, with_for_update=True)
    if user is None:
        return False
    profile = session.scalar(select(UserProfile).where(UserProfile.owner_id == owner_id).with_for_update())
    if profile is None:
        profile = UserProfile(owner_id=owner_id, user_name=user.user_name, preferences=[])
        session.add(profile)
    profile.user_name = user.user_name
    profile.preferences = list(dict.fromkeys([*profile.preferences, *additions]))
    return True


def cache_profile(user) -> None:
    url = os.environ.get("REDIS_URL") or dotenv_values(database.ENV_FILE).get("REDIS_URL")
    if not url:
        return
    try:
        from redis import Redis
        from redis.exceptions import RedisError
    except ImportError:
        logger.warning(CONSTANT.REDIS_PACKAGE_MISSING_LOG)
        return
    ensure_tables()
    with database.get_session() as session:
        profile = session.scalar(select(UserProfile).where(UserProfile.owner_id == user.id))
        preferences = profile.preferences if profile else []
    try:
        with Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1) as client:
            client.hset(f"tongxue:profile:{user.id}", mapping={
                "user_name": user.user_name, "preferences": json.dumps(preferences, ensure_ascii=False),
            })
    except (RedisError, ValueError) as exc:
        logger.warning(CONSTANT.REDIS_UNAVAILABLE_LOG, type(exc).__name__)


def model_context(metadata: dict, owner_id: int) -> dict:
    """从消息记录读取所选会话的模型上下文。"""
    ensure_tables()
    with database.get_session() as session:
        profile = session.scalar(select(UserProfile).where(UserProfile.owner_id == owner_id))
        preferences = "\n".join(profile.preferences)[-PROFILE_LIMIT:] if profile else ""
        history = {}
        for message_id, role, event in session.execute(
            select(ConversationEvent.message_id, ConversationEvent.role, ConversationEvent.event)
            .where(ConversationEvent.owner_id == owner_id, ConversationEvent.conversation_id == metadata["id"])
            .order_by(ConversationEvent.id)
        ):
            field = "requirements" if role == "user" else "reply"
            history.setdefault(message_id, {})[field] = event["payload"]["text"]
    return {**metadata, "user_preferences": preferences, "ledger_history": history}


def excerpts(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    half = (limit - 3) // 2
    return f"{text[:half]} … {text[-half:]}"


def context_view(turns: list[dict]) -> tuple[str, list[dict]]:
    """将较早的对话压缩为限长摘要，并保留最近的对话。"""
    older, recent = turns[:-RECENT_TURNS], turns[-RECENT_TURNS:]
    if not older:
        return "", recent
    selected = older if len(older) <= 8 else [older[0], *older[-7:]]
    digest = []
    if len(selected) != len(older):
        digest.append(f"原始账本另有 {len(older) - len(selected)} 轮较早记录。")
    for message in selected:
        summary = f"用户：{excerpts(message['requirements'], 130)}"
        if message.get("reply"):
            summary += f" / 助手：{excerpts(message['reply'], 145)}"
        digest.append(summary)
    return "\n".join(digest)[:SUMMARY_LIMIT], recent


def context_lines(metadata: dict, turns: list[dict]) -> tuple[list[str], list[dict]]:
    history = metadata.get("ledger_history", {})
    summary, window = context_view([
        {**turn, **history.get(turn.get("id"), {})} for turn in turns
    ])
    notes = []
    if profile := metadata.get("user_preferences"):
        notes.append(f"用户档案中的长期偏好（当前请求优先）：\n{profile}")
    if summary:
        notes.append(f"较早会话的摘录摘要（仅供回顾）：\n{summary}")
    return notes, window
