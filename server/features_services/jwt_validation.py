"""JWT 签发与受保护路由的身份校验。"""

import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import dotenv_values
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError

from server import database
from server.data_model import User

from . import CONSTANT


bearer = HTTPBearer(auto_error=False)
_process_secret = secrets.token_urlsafe(48)


def jwt_secret() -> str:
    return os.environ.get("JWT_SECRET") or dotenv_values(database.ENV_FILE).get("JWT_SECRET") or _process_secret


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user.id), "iat": now, "exp": now + timedelta(hours=12)},
        jwt_secret(), algorithm="HS256",
    )


def require_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail=CONSTANT.LOGIN_REQUIRED, headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(credentials.credentials, jwt_secret(), algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail=CONSTANT.SESSION_EXPIRED, headers={"WWW-Authenticate": "Bearer"}) from None
    with database.get_session() as session:
        user = session.get(User, user_id)
        if user is None or user.status != 0:
            raise HTTPException(status_code=401, detail=CONSTANT.SESSION_EXPIRED, headers={"WWW-Authenticate": "Bearer"})
        session.expunge(user)
        return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.identity != 1:
        raise HTTPException(status_code=403, detail=CONSTANT.ADMIN_REQUIRED)
    return user
