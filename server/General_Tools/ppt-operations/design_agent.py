"""Creative direction and asset selection for the Roommate PowerPoint workflow."""

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from openai import APIError, BadRequestError
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from .materials import Material, search_materials
from .planning import model_json

from . import CONSTANT
from .CONSTANT import DESIGN_PROMPT


CREATIVE_TEMPERATURE = 0.85
WEB_IMAGES = re.compile(
    r"(?:联网|网上|网络|在线|全网|网搜).{0,8}(?:配图|图片|插图|照片|找图)|"
    r"(?:搜索|搜寻|查找)\s*(?:相关|公开|可用|合适的)?\s*(?:图片|照片|插图)|"
    r"\b(?:web|online|stock) (?:images|photos)\b", re.I,
)
IMAGE_REQUEST = re.compile(r"图文(?:结合|并茂)|多.{0,5}(?:配图|插图|图片|用图)|"
                           r"(?:插入|加上|加入|加些|配上|补充).{0,5}(?:图片|配图|插图|照片)|"
                           r"\billustrat(?:e|ions)\b", re.I)
NO_IMAGES = re.compile(r"(?:不要|无需|禁止|不需要|不加|不用)\s*(?:任何)?\s*(?:配图|插图|图片)|"
                       r"\bno (?:illustrations|images|pictures)\b", re.I)
NO_WEB_IMAGES = re.compile(
    r"(?:仅用|只用|仅使用|只使用|只允许使用)\s*(?:我|用户|已|本次)?(?:上传|提供|给出)|"
    r"(?:不要|无需|禁止|不需要|不使用|不用|不添加|不得)\s*(?:联网|网络配图|网上|网图|插图|配图|搜索图片)|"
    r"\b(?:no (?:web|online|external|stock) (?:images|photos)|use only (?:the )?(?:uploaded|provided))\b", re.I,
)


def wants_illustrations(request: str) -> bool:
    return not NO_IMAGES.search(request) and bool(WEB_IMAGES.search(request) or IMAGE_REQUEST.search(request))


class DesignDirection(BaseModel):
    direction: str = Field(default="", max_length=1400)
    outline: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=220)]] = Field(
        default_factory=list, max_length=19,
    )
    query_terms: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=65)]] = Field(
        default_factory=list, max_length=5,
    )
    query_subjects: dict[str, Annotated[str, StringConstraints(strip_whitespace=True, max_length=65)]] = Field(
        default_factory=dict, max_length=5,
    )


async def design_presentation(model, brief: dict, target: Path, progress: Callable[[str], None], *,
                              use_web: bool) -> tuple[str, list[Material]]:
    bind = getattr(model, "bind", None)
    require_web = use_web and (brief.get("详细档自动配图") or brief.get("明确要求联网配图"))
    if not callable(bind) and not require_web:
        return "", []
    progress(CONSTANT.DESIGNING_PRESENTATION)
    messages = [
        SystemMessage(content=DESIGN_PROMPT),
        HumanMessage(content=json.dumps({**brief, "可检索公开图片": use_web}, ensure_ascii=False)),
    ]
    design = DesignDirection()
    creative_model = model
    try:
        creative_model = bind(temperature=CREATIVE_TEMPERATURE) if callable(bind) else model
        response = await creative_model.ainvoke(messages)
        design = DesignDirection.model_validate_json(model_json(response))
    except BadRequestError as exc:
        if creative_model is not model and "temperature" in str(exc).lower():
            try:
                response = await model.ainvoke(messages)
                design = DesignDirection.model_validate_json(model_json(response))
            except (ValidationError, APIError):
                pass
    except (ValidationError, APIError):
        pass

    def public_queries(plan: DesignDirection) -> list[str]:
        return [query for query in dict.fromkeys(plan.query_terms) if not re.search(r"[@/\\:：\r\n]", query)]

    direction = design.direction
    if design.outline:
        direction += "\n逐页内容与配图方向：\n" + "\n".join(f"{index}. {page}" for index, page in enumerate(design.outline, 1))
    if not use_web:
        return direction, []
    queries = public_queries(design)
    assets = []
    if queries:
        progress(CONSTANT.SEARCHING_IMAGES)
        assets = await search_materials(queries, target)
    if require_web and not assets:
        retry = HumanMessage(content=f"首轮检索词 {json.dumps(queries, ensure_ascii=False)} 未取得配图。"
                                     "请返回 JSON 对象 query_terms 和 query_subjects。换用 2–5 个不同的通用画面短语，"
                                     "优先使用英文实物、设备、场景或实际应用名称。不要复制附件原文、文件名、个人姓名或私有信息。")
        try:
            response = await model.ainvoke([*messages, retry])
            design = DesignDirection.model_validate_json(model_json(response))
        except (ValidationError, APIError):
            return direction, []
        searched = {query.casefold() for query in queries}
        revised = [query for query in public_queries(design) if query.casefold() not in searched]
        if revised:
            progress(CONSTANT.SEARCHING_IMAGES)
            assets = await search_materials(revised, target)
    return direction, [replace(asset, subject=design.query_subjects.get(asset.query, "")) for asset in assets]
