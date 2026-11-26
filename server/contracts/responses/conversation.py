"""前端读取会话及历史列表使用的返回格式。"""

from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class ConversationMessageResponse(BaseModel):
    id: str
    created_at: str
    requirements: str
    software_id: str | None = None
    guide_name: str | None
    report_template_name: str | None = None
    report_files: list[str] = Field(default_factory=list)
    artifact_files: list[str] = Field(default_factory=list, exclude_if=lambda value: not value)
    processing_seconds: float | None = None
    stage_seconds: dict[str, float] = Field(default_factory=dict)
    attachment_names: list[str]
    output_images: list[str] = Field(default_factory=list)
    other_file_names: list[str] = Field(default_factory=list)
    reply: str | None = None
    exercise_candidates: str | None = Field(default=None, exclude_if=lambda value: value is None)
    vision_status: str | None = Field(default=None, exclude_if=lambda value: value is None)
    status: Literal["draft", "processing", "answered", "error"] = "draft"
    request_id: str | None = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: str
    messages: list[ConversationMessageResponse]


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
