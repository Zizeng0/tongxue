"""用户资料与管理员操作。"""

import hashlib
import hmac
import secrets
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from server import database
from server.data_model import User, UserRemark
from server.features_services.jwt_validation import require_admin, require_user

from . import CONSTANT


router = APIRouter(prefix="/api/users", tags=["users"])
AVATAR_DIR = Path(__file__).resolve().parents[2] / "data" / "avatars"
MAX_AVATAR_SIZE = 5 * 1024 * 1024
AVATAR_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


class UserInfo(BaseModel):
    id: int
    user_name: str
    identity: int
    status: int
    image: str
    create_time: datetime | None = None
    update_time: datetime | None = None
    remark: str | None = None


class UserUpdate(BaseModel):
    user_name: str = Field(min_length=1, max_length=50)
    user_password: str | None = Field(default=None, min_length=8)
    status: int | None = Field(default=None, ge=0, le=1)
    identity: int | None = Field(default=None, ge=0, le=1)
    image: str | None = Field(default=None, max_length=255)
    remark: str | None = Field(default=None, max_length=2000)


class UserPage(BaseModel):
    items: list[UserInfo]
    total: int
    page: int
    page_size: int


def public_user(user: User, remark: str | None = None) -> UserInfo:
    return UserInfo(id=user.id, user_name=user.user_name, identity=user.identity, status=user.status,
                    image=user.image, create_time=user.create_time, update_time=user.update_time, remark=remark)


def ensure_remark_table() -> None:
    UserRemark.__table__.create(database.get_engine(), checkfirst=True)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260_000)
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except ValueError:
        return False


def get_user(user_name: str) -> User | None:
    User.__table__.create(database.get_engine(), checkfirst=True)
    with database.get_session() as session:
        user = session.scalar(select(User).where(User.user_name == user_name))
        if user is not None:
            session.expunge(user)
        return user


def add_user(user_name: str, user_password: str) -> User:
    User.__table__.create(database.get_engine(), checkfirst=True)
    with database.get_session() as session:
        user = User(user_name=user_name, user_password=hash_password(user_password))
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail=CONSTANT.USERNAME_EXISTS) from None
        session.refresh(user)
        session.expunge(user)
        return user


@router.get("", response_model=list[UserInfo])
def user_list(admin: User = Depends(require_admin)) -> list[UserInfo]:
    ensure_remark_table()
    with database.get_session() as session:
        rows = session.execute(select(User, UserRemark.remark).outerjoin(UserRemark, UserRemark.user_id == User.id).order_by(User.id))
        return [public_user(user, remark if user.identity == 0 else None) for user, remark in rows]


@router.get("/me", response_model=UserInfo)
def current_user(user: User = Depends(require_user)) -> UserInfo:
    return public_user(user)


@router.get("/search", response_model=UserPage)
def search_users(
    name: str = Query(default="", max_length=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    admin: User = Depends(require_admin),
) -> UserPage:
    ensure_remark_table()
    search = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    condition = User.user_name.ilike(f"%{search}%", escape="\\")
    with database.get_session() as session:
        total = session.scalar(select(func.count()).select_from(User).where(condition)) or 0
        rows = session.execute(
            select(User, UserRemark.remark)
            .outerjoin(UserRemark, UserRemark.user_id == User.id)
            .where(condition).order_by(User.id)
            .offset((page - 1) * page_size).limit(page_size)
        )
        items = [public_user(user, remark if user.identity == 0 else None) for user, remark in rows]
    return UserPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/me/avatar", response_model=UserInfo)
async def upload_avatar(avatar: UploadFile = File(...), user: User = Depends(require_user)) -> UserInfo:
    content = await avatar.read(MAX_AVATAR_SIZE + 1)
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=413, detail=CONSTANT.AVATAR_TOO_LARGE)
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            extension = AVATAR_FORMATS.get(image.format)
    except (UnidentifiedImageError, OSError, ValueError):
        extension = None
    if extension is None:
        raise HTTPException(status_code=422, detail=CONSTANT.AVATAR_TYPE_UNSUPPORTED)
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"{uuid4().hex}{extension}"
    target = AVATAR_DIR / file_name
    target.write_bytes(content)
    try:
        with database.get_session() as session:
            account = session.get(User, user.id)
            account.image = f"/api/users/avatars/{file_name}"
            session.commit()
            session.refresh(account)
            return public_user(account)
    except Exception:
        target.unlink(missing_ok=True)
        raise


@router.get("/avatars/{file_name}")
def get_avatar(file_name: str):
    if not re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|webp)", file_name):
        raise HTTPException(status_code=404, detail=CONSTANT.AVATAR_NOT_FOUND)
    target = AVATAR_DIR / file_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail=CONSTANT.AVATAR_NOT_FOUND)
    return FileResponse(target)


@router.patch("/me", response_model=UserInfo)
def update_self(update: UserUpdate, user: User = Depends(require_user)) -> UserInfo:
    return update_user(user.id, update, actor=user)


@router.patch("/{user_id}", response_model=UserInfo)
def update_user(user_id: int, update: UserUpdate, actor: User = Depends(require_admin)) -> UserInfo:
    if actor.identity == 1:
        ensure_remark_table()
    with database.get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=CONSTANT.USER_NOT_FOUND)
        user.user_name = update.user_name.strip()
        if not user.user_name:
            raise HTTPException(status_code=422, detail=CONSTANT.USERNAME_REQUIRED)
        if update.user_password:
            user.user_password = hash_password(update.user_password)
        if update.image is not None:
            user.image = update.image
        if actor.identity == 1 and user_id != actor.id and update.status is not None:
            user.status = update.status
        if actor.identity == 1 and user_id != actor.id and update.identity is not None:
            user.identity = update.identity
        if actor.identity == 1 and user.identity == 0 and update.remark is not None:
            note = session.scalar(select(UserRemark).where(UserRemark.user_id == user_id))
            if note is None:
                note = UserRemark(user_id=user_id, remark=update.remark)
                session.add(note)
            else:
                note.remark = update.remark
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail=CONSTANT.USERNAME_EXISTS) from None
        session.refresh(user)
        note = session.scalar(select(UserRemark.remark).where(UserRemark.user_id == user_id)) if actor.identity == 1 and user.identity == 0 else None
        return public_user(user, note)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, admin: User = Depends(require_admin)) -> None:
    if user_id == admin.id:
        raise HTTPException(status_code=409, detail=CONSTANT.OWN_ACCOUNT_DELETE_FORBIDDEN)
    ensure_remark_table()
    with database.get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=CONSTANT.USER_NOT_FOUND)
        note = session.scalar(select(UserRemark).where(UserRemark.user_id == user_id))
        if note is not None:
            session.delete(note)
        session.delete(user)
        session.commit()
