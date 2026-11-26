"""Resolve a thesis draft and its formatting requirements for Roommate."""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from server.agent.roommate_routing import response_json
from server.General_Tools.pdf_on_demand import read_pdf_material
from .word_format import DEFAULT_FORMAT, ThesisFormat, format_thesis, read_word_standard, sample_thesis_format

from . import CONSTANT
from .CONSTANT import ASK_DRAFT, ASK_STANDARD, FORMAT_PROMPT

logger = logging.getLogger("uvicorn.error")
REFERENCE_NAME = re.compile(r"模板|规范|格式要求|排版要求|样[文例稿]|写作指南")
WRITTEN_STANDARD = re.compile(r"规范|格式要求|排版要求|写作指南")
DEFAULT_REQUEST = re.compile(r"默认|通用(?:中文)?论文格式|按通用(?:格式|排版)")
EXPLICIT_STYLE = re.compile(r"宋体|黑体|楷体|仿宋|微软雅黑|\b(?:Times New Roman|Arial)\b|\d\s*(?:pt|磅|cm|mm|倍|字符)|"
                            r"[初一二三四五六七八]号|居中|左对齐|右对齐|两端对齐|加粗|首行|悬挂|段前|段后|页眉|页脚|页码|封面|"
                            r"A4|Letter|字体|字号|行距|页边距|目录|参考文献|磅|厘米|毫米|边框|颜色", re.I)


class ParsedRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: ThesisFormat = Field(default_factory=ThesisFormat)
    clarification: str = Field(default="", max_length=400)
    unsupported: str = Field(default="", max_length=400)


@dataclass(frozen=True)
class FormatAttachment:
    name: str
    path: Path


@dataclass(frozen=True)
class FormatOutcome:
    reply: str
    filename: str | None = None


def choose_draft(request: str, files: list[FormatAttachment]) -> FormatAttachment | None:
    word_files = [file for file in files if file.path.suffix.lower() == ".docx"]
    drafts = [file for file in word_files if not REFERENCE_NAME.search(file.name)]
    if len(drafts) == 1:
        return drafts[0]
    named = [file for file in word_files if file.name.casefold() in request.casefold()]
    return named[0] if len(named) == 1 else None


def read_standard(reference: FormatAttachment) -> tuple[str, bool]:
    suffix = reference.path.suffix.lower()
    if suffix == ".pdf":
        pages, limited = read_pdf_material(reference.path, 16000, 0)
        return "\n\n".join(f"第 {page.number} 页\n{page.text}" for page in pages), limited
    if suffix == ".docx":
        return read_word_standard(reference.path, 16000)
    text = reference.path.read_text(encoding="utf-8-sig")
    return text[:16000], len(text) > 16000


async def rewrite_thesis(model, request: str, latest_request: str, files: list[FormatAttachment], directory: Path,
                         progress: Callable[[str], None]) -> FormatOutcome:
    draft = choose_draft(latest_request, files) or choose_draft(request, files)
    if not draft:
        if sum(file.path.suffix.lower() == ".docx" for file in files) > 1:
            return FormatOutcome(CONSTANT.ASK_DRAFT_NAME)
        return FormatOutcome(ASK_DRAFT)
    references = [file for file in files if file != draft and file.path.suffix.lower() in {".docx", ".pdf", ".txt", ".md"}]
    if len(references) > 1:
        named = [file for file in references if file.name.casefold() in latest_request.casefold()]
        if len(named) != 1:
            return FormatOutcome(CONSTANT.ASK_REFERENCE_NAME)
        references = named

    reference = next(iter(references), None)
    uses_default = bool(DEFAULT_REQUEST.search(request)) and reference is None
    spec = DEFAULT_FORMAT if uses_default else ThesisFormat()
    standard_text = ""
    limited = False
    progress(CONSTANT.IDENTIFY_FORMAT)
    if reference and reference.path.suffix.lower() == ".docx" and not WRITTEN_STANDARD.search(reference.name):
        try:
            spec = await run_in_threadpool(sample_thesis_format, reference.path)
        except ValueError:
            return FormatOutcome(CONSTANT.UNREADABLE_SAMPLE)
    elif reference:
        standard_text, limited = await run_in_threadpool(read_standard, reference)
        if not standard_text.strip():
            return FormatOutcome(CONSTANT.EMPTY_STANDARD)

    parsed = ParsedRequirements()
    if not reference and not uses_default and not EXPLICIT_STYLE.search(request):
        return FormatOutcome(ASK_STANDARD)
    if standard_text or EXPLICIT_STYLE.search(request):
        if model is None:
            return FormatOutcome(CONSTANT.FORMAT_MODEL_REQUIRED)
        payload = {"排版要求": request, "规范资料": standard_text}
        schema = json.dumps(ParsedRequirements.model_json_schema(), ensure_ascii=False)
        try:
            response = await model.ainvoke([SystemMessage(content=f"{FORMAT_PROMPT}\nJSON Schema\n{schema}"),
                                            HumanMessage(content=json.dumps(payload, ensure_ascii=False))])
            parsed = ParsedRequirements.model_validate_json(response_json(response))
        except Exception as exc:
            logger.warning(CONSTANT.FORMAT_MODEL_FAILED_LOG, type(exc).__name__)
            return FormatOutcome(CONSTANT.FORMAT_UNCLEAR)
        if parsed.clarification.strip():
            return FormatOutcome(parsed.clarification.strip())
        spec = spec.with_overrides(parsed.format)

    progress(CONSTANT.APPLY_FORMAT)
    stem = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", Path(draft.name).stem).strip(" .")[:36] or "论文"
    filename = f"{stem}-排版版.docx"
    try:
        changed = await run_in_threadpool(format_thesis, draft.path, directory / filename, spec)
    except ValueError as exc:
        return FormatOutcome(str(exc))
    if not changed:
        if parsed.unsupported.strip():
            return FormatOutcome(CONSTANT.FORMAT_UNSUPPORTED.format(unsupported=parsed.unsupported.strip()))
        return FormatOutcome(ASK_STANDARD)

    basis = "通用论文格式" if uses_default else (reference.name if reference else "本轮要求")
    reply = f"已按「{basis}」调整{'、'.join(changed)}，排版版 Word 可在「我的项目」下载。"
    if limited:
        reply += "本次仅采用规范中已读取的文字。"
    if parsed.unsupported.strip():
        reply += f"仍需在 Word 中处理{parsed.unsupported.strip()}。"
    return FormatOutcome(reply, filename)
