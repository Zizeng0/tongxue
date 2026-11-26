"""登录和注册接口。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from server.conversation_memory import cache_profile

from server.features_services.jwt_validation import create_token
from server.features_services.user_service import add_user, get_user, public_user, verify_password

from . import CONSTANT


router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    user_name: str = Field(min_length=1, max_length=50)
    user_password: str = Field(min_length=8)


class Registration(Credentials):
    confirm_password: str


@router.post("/login")
def login(credentials: Credentials) -> dict:
    user = get_user(credentials.user_name)
    if user is None or not verify_password(credentials.user_password, user.user_password):
        raise HTTPException(status_code=401, detail=CONSTANT.INVALID_LOGIN)
    if user.status != 0:
        raise HTTPException(status_code=403, detail=CONSTANT.ACCOUNT_DISABLED)
    cache_profile(user)
    return {"access_token": create_token(user), "token_type": "bearer", "user": public_user(user)}


@router.post("/register", status_code=201)
def register(registration: Registration) -> dict:
    if registration.user_password != registration.confirm_password:
        raise HTTPException(status_code=422, detail=CONSTANT.PASSWORD_MISMATCH)
    user_name = registration.user_name.strip()
    if not user_name:
        raise HTTPException(status_code=422, detail=CONSTANT.USERNAME_REQUIRED)
    user = add_user(user_name, registration.user_password)
    return {"access_token": create_token(user), "token_type": "bearer", "user": public_user(user)}
