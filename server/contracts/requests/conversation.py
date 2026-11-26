"""与前端 multipart/form-data 字段同名的会话请求格式。"""

from typing import Literal

from fastapi import UploadFile
from pydantic import BaseModel, Field


class ConversationRequest(BaseModel):
    requirements: str
    software_id: str
    deliverables: list[str]
    conversation_id: str | None = None
    request_id: str | None = None
    execution_scope: Literal["mcp"] | None = None
    guide: UploadFile | None = None
    report_template: UploadFile | None = None
    attachments: list[UploadFile] = Field(default_factory=list)
    other_files: list[UploadFile] = Field(default_factory=list)
