"""按账号和模型类型保存配置，并用对应服务的模型列表验证修改。"""

import ipaddress
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from server.data_model import User
from . import CONSTANT as C
from .jwt_validation import require_user


CONFIG_DIR = Path(__file__).resolve().parents[2] / "data" / "model-settings"
_write_lock = Lock()
ModelRole = Literal["text", "vision"]


class ConnectionInput(BaseModel):
    """接收新的 OpenAI 兼容服务地址及密钥。"""

    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr


class SettingsInput(BaseModel):
    """接收单个模型类型的版本号、名称和可选的新连接。"""

    model_config = ConfigDict(extra="forbid")
    revision: str | None
    model: str = Field(min_length=1, max_length=512)
    connection: ConnectionInput | None = None


class SavedModel(BaseModel):
    """记录单个模型类型的选项和连接。"""

    revision: str | None = None
    model: str | None = None
    connection: ConnectionInput | None = None


class SavedModels(BaseModel):
    """记录同一账号的两份独立配置，并兼容旧版共享连接。"""

    text: SavedModel = Field(default_factory=SavedModel)
    vision: SavedModel = Field(default_factory=SavedModel)

    @model_validator(mode="before")
    @classmethod
    def read_shared_settings(cls, data):
        if isinstance(data, dict) and ("text_model" in data or "vision_model" in data):
            return {
                role: {"revision": data.get("revision"), "model": data.get(f"{role}_model"),
                       "connection": data.get("connection")}
                for role in ("text", "vision")
            }
        return data


@dataclass(frozen=True)
class ModelConnection:
    """记录模型目录和实际推理共同使用的连接。"""

    base_url: str | None
    api_key: SecretStr | None
    custom: bool = False
    trusted_hosts: tuple[tuple[str, str, int | None], ...] = ()
    provider: str = "openai"

    def authorize_request(self, request: httpx.Request) -> None:
        """验证自定义连接的目的地址，并将实际连接固定到已验证的 IP。"""
        if not self.custom or origin(request.url) in self.trusted_hosts:
            return
        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise httpx.ConnectError(C.MODEL_CATALOG_UNAVAILABLE, request=request) from exc
        for _, _, _, _, peer in addresses:
            address = ipaddress.ip_address(peer[0])
            address_for_check = getattr(address, "ipv4_mapped", None) or address
            if address_for_check.is_global and not address_for_check.is_multicast:
                request.extensions["sni_hostname"] = host
                request.url = request.url.copy_with(host=str(address))
                return
        raise httpx.ConnectError(C.MODEL_ENDPOINT_PRIVATE, request=request)

    def client(self, **options) -> httpx.Client:
        """创建使用此连接的同步客户端。"""
        return httpx.Client(trust_env=False, event_hooks={"request": [self.authorize_request]}, **options)

    def async_client(self) -> httpx.AsyncClient:
        """创建使用相同地址检查的异步客户端。"""
        async def authorize(request: httpx.Request) -> None:
            await run_in_threadpool(self.authorize_request, request)

        return httpx.AsyncClient(trust_env=False, event_hooks={"request": [authorize]})


def origin(url: httpx.URL) -> tuple[str, str, int | None]:
    """返回地址的协议、主机名及端口。"""
    return url.scheme, url.host, url.port


def load_models(owner_id: int) -> SavedModels:
    """读取单个账号的模型配置，兼容尚未保存设置的账号。"""
    try:
        return SavedModels.model_validate_json((CONFIG_DIR / f"{owner_id}.json").read_bytes())
    except FileNotFoundError:
        return SavedModels()
    except (OSError, ValidationError):
        raise HTTPException(500, C.MODEL_SETTINGS_STORAGE_ERROR) from None


def save_model(owner_id: int, role: ModelRole, updated: SavedModel, previous_revision: str | None) -> None:
    """仅检查目标类型的版本，合并最新配置后原子替换文件。"""
    with _write_lock:
        saved = load_models(owner_id)
        if getattr(saved, role).revision != previous_revision:
            raise HTTPException(409, C.MODEL_SETTINGS_CHANGED)
        setattr(saved, role, updated)
        temporary_path = None
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            payload = saved.model_dump(exclude_none=True)
            for name, model in saved:
                if model.connection:
                    payload[name]["connection"]["api_key"] = model.connection.api_key.get_secret_value()
            with NamedTemporaryFile(dir=CONFIG_DIR, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            temporary_path.replace(CONFIG_DIR / f"{owner_id}.json")
        except OSError:
            raise HTTPException(500, C.MODEL_SETTINGS_STORAGE_ERROR) from None
        finally:
            if temporary_path and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass


def normalize_connection(value: ConnectionInput) -> ConnectionInput:
    """规范化地址及密钥，并校验可用于 HTTP 请求头的值。"""
    try:
        url = httpx.URL(value.base_url.strip())
        if (url.scheme not in ("http", "https") or not url.host or url.userinfo
                or url.query or url.fragment or "\\" in value.base_url):
            raise ValueError
    except (httpx.InvalidURL, ValueError):
        raise HTTPException(422, C.MODEL_BASE_URL_INVALID) from None
    key = value.api_key.get_secret_value().strip()
    if not key or len(key) > 4096 or any(not 33 <= ord(char) <= 126 for char in key):
        raise HTTPException(422, C.MODEL_SETTINGS_INVALID)
    return ConnectionInput(base_url=str(url).rstrip("/"), api_key=key)


def default_models() -> dict[ModelRole, tuple[str, ModelConnection]]:
    """从模型原有入口读取当前名称及各自的连接。"""
    from server.agent import runner
    from server import solutions

    text_name, text_key = runner.model_settings()
    text_url = runner.model_base_url(text_name)
    text_provider = "deepseek" if text_name == runner.DEFAULT_DEEPSEEK_MODEL else ("openai" if text_name.startswith("openai:") else "ollama")
    if text_provider == "ollama":
        text_key = None
    vision_name, vision_url, vision_key = solutions.vision_model_settings()
    return {
        "text": (text_name.removeprefix("openai:") if text_url else text_name,
                 ModelConnection(text_url.rstrip("/") if text_url else None, SecretStr(text_key) if text_key else None,
                                 provider=text_provider)),
        "vision": (vision_name, ModelConnection(vision_url or f"{solutions.local_vision_url()}/v1",
                                                SecretStr(vision_key) if vision_key else None)),
    }


def custom_connection(value: ConnectionInput) -> ModelConnection:
    """沿用管理员在现有模型配置中指定的内网服务。"""
    allowed_hosts = tuple(origin(httpx.URL(connection.base_url)) for _, connection in default_models().values()
                          if connection.base_url)
    return ModelConnection(value.base_url, value.api_key, custom=True, trusted_hosts=allowed_hosts)


def effective_model(role: ModelRole, saved: SavedModel) -> tuple[str, ModelConnection]:
    """合并指定模型类型的已保存选择和默认配置。"""
    name, connection = default_models()[role]
    if saved.connection:
        connection = custom_connection(saved.connection)
    return saved.model or name, connection


def model_catalog(connection: ModelConnection) -> list[dict]:
    """请求当前密钥可见的模型列表，并识别提供方声明的输入和输出类型。"""
    if not connection.base_url:
        return []
    headers = {"Authorization": f"Bearer {connection.api_key.get_secret_value()}"} if connection.api_key else {}
    try:
        with connection.client(timeout=8) as client:
            response = client.get(f"{connection.base_url}/models", headers=headers)
            response.raise_for_status()
            items = response.json()["data"]
        if not isinstance(items, list):
            raise ValueError
    except httpx.HTTPStatusError as exc:
        message = C.MODEL_CATALOG_AUTH if exc.response.status_code in (401, 403) else C.MODEL_CATALOG_UNAVAILABLE
        raise HTTPException(502, message) from None
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        message = C.MODEL_ENDPOINT_PRIVATE if str(exc) == C.MODEL_ENDPOINT_PRIVATE else C.MODEL_CATALOG_UNAVAILABLE
        raise HTTPException(502, message) from None

    models = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            continue
        # 忽略返回内容中可能回显的凭据。
        if connection.api_key and connection.api_key.get_secret_value() in item["id"]:
            continue
        architecture = item.get("architecture")
        if not isinstance(architecture, dict):
            architecture = {}
        inputs = item.get("input_modalities", architecture.get("input_modalities"))
        outputs = item.get("output_modalities", architecture.get("output_modalities"))
        if isinstance(outputs, list) and "text" not in outputs:
            continue
        roles = [role for role, modality in (("text", "text"), ("vision", "image"))
                 if not isinstance(inputs, list) or modality in inputs]
        models[item["id"]] = {"id": item["id"], "roles": roles}
    return sorted(models.values(), key=lambda model: model["id"].casefold())


def available_for(catalog: list[dict], role: ModelRole) -> list[str]:
    """提取适用于指定用途的模型名称。"""
    return [entry["id"] for entry in catalog if role in entry["roles"]]


class PrivateInputRoute(APIRoute):
    """以固定提示替代可能回显请求内容的校验错误。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, C.MODEL_SETTINGS_INVALID) from None

        return handle


router = APIRouter(prefix="/api/settings/models", route_class=PrivateInputRoute)


@router.get("/{role}")
def get_settings(role: ModelRole, user: User = Depends(require_user)):
    """返回指定类型的当前模型和可选项，加载失败时仍返回当前名称。"""
    saved = getattr(load_models(user.id), role)
    name, connection = effective_model(role, saved)
    options, error = [], None
    try:
        options = available_for(model_catalog(connection), role)
    except HTTPException as exc:
        error = exc.detail
    return {"revision": saved.revision, "current": name, "options": options, "error": error}


@router.post("/{role}/connection")
def preview_connection(role: ModelRole, value: ConnectionInput, user: User = Depends(require_user)):
    """验证新地址和密钥，返回指定类型的可选模型。"""
    catalog = model_catalog(custom_connection(normalize_connection(value)))
    return {"options": available_for(catalog, role)}


@router.patch("/{role}")
def update_settings(role: ModelRole, value: SettingsInput, user: User = Depends(require_user)):
    """验证并保存指定类型的模型与连接。"""
    saved = getattr(load_models(user.id), role)
    if saved.revision != value.revision:
        raise HTTPException(409, C.MODEL_SETTINGS_CHANGED)
    replacement = normalize_connection(value.connection) if value.connection else None
    name, connection = effective_model(role, saved)
    if replacement or value.model != name:
        if replacement:
            connection = custom_connection(replacement)
        if value.model not in available_for(model_catalog(connection), role):
            raise HTTPException(422, C.MODEL_SELECTION_UNAVAILABLE)
    updated = SavedModel(revision=uuid4().hex, model=value.model, connection=replacement or saved.connection)
    save_model(user.id, role, updated, value.revision)
    return {"revision": updated.revision}
