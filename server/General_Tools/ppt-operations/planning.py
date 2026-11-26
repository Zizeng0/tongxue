"""Translate a presentation brief into a validated, renderable deck."""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError
from openai import APIError, BadRequestError

from .presentation_writer import MAX_IMAGES, DeckContent, Figure
from .pdf_figures import PdfFigure
from .templates import PALETTES, TEMPLATES

from . import CONSTANT
from .CONSTANT import DEPTH_GUIDANCE, PPT_PROMPT




EXACT_COUNT = re.compile(
    r"(?:^|[\s，,。；;：:、（(]|PPT|幻灯片|演示文稿|制作|做|一份|改成|压缩成|增加到|减少到|共计|共|总计|总共|要|含|为)"
    r"\s*(\d{1,3}|[一二两三四五六七八九十廿]{1,3})\s*(?:页|张(?:幻灯片)?)"
    r"(?!\s*(?:左右|上下|以内|以上|以下|起步|左右的|到\s*\d|[-–—~～]))",
    re.I,
)
APPROXIMATE_PREFIX = re.compile(r"(?:至少|最多|大约|约|不少于|不超过|不少过|不多于|第|到|至|[-–—~～])\s*$")
MATERIAL_COUNT_PREFIX = re.compile(r"(?:pdf|素材|原文|文档|讲义|课件)[的（(]?\s*$", re.I)
CHINESE_DIGITS = {digit: number for number, digit in enumerate("零一二三四五六七八九")}
HORIZONTAL_FLOW = re.compile(r"横向流程图|横向时间轴|horizontal (?:flowchart|timeline)", re.I)
IMAGE_COVER = re.compile(r"封面[用加放插]图|封面[右左]图|封面配图|image on the cover", re.I)
NO_COVER_IMAGE = re.compile(r"(?:不要|无需|不用|禁止|不加|不放|不使用|不需要)\s*封面(?:配图|图)|封面\s*(?:不要|不加|不放|不使用)\s*(?:配图|图片|图)", re.I)


def requested_slide_count(request: str) -> int | None:
    for match in EXACT_COUNT.finditer(request):
        prefix = request[:match.start(1)].rstrip()
        if APPROXIMATE_PREFIX.search(prefix) or MATERIAL_COUNT_PREFIX.search(prefix):
            continue
        value = match[1]
        if value.isdecimal():
            return int(value)
        value = value.replace("两", "二").replace("廿", "二十")
        if "十" in value:
            tens, _, ones = value.partition("十")
            if (not tens or tens in CHINESE_DIGITS) and (not ones or ones in CHINESE_DIGITS):
                return 10 * CHINESE_DIGITS.get(tens, 1) + CHINESE_DIGITS.get(ones, 0)
        elif value in CHINESE_DIGITS:
            return CHINESE_DIGITS[value]
    return None


def model_json(response) -> str:
    raw = response.text.strip()
    if raw.startswith("```") and raw.endswith("```"):
        return raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    return raw


def validation_issue(error: ValidationError) -> str:
    errors = error.errors(include_url=False, include_context=False, include_input=False)
    if any(item["type"] == "json_invalid" for item in errors):
        return "模型响应缺少完整的合法 JSON，可能在输出时被截断。请缩短备注并输出完整对象。"
    labels = {
        "literal_error": "值不在允许范围内", "string_too_long": "文字超长", "too_long": "条目过多",
        "too_short": "条目不足", "missing": "缺少必填字段", "extra_forbidden": "有未定义字段",
        "value_error": "组合不符合所选版式",
    }
    problems = []
    for item in errors[:3]:
        location = ".".join(str(part) for part in item["loc"]) or "整份文稿"
        problems.append(f"{location}（{labels.get(item['type'], '字段格式有误')}）")
    return "未通过幻灯片字段校验：" + "、".join(problems) + "。请按原要求修复这些字段。"


def available_image_number(value, image_count: int) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,2}", value.strip()):
        value = int(value)
    if type(value) is int and 1 <= value <= min(image_count, MAX_IMAGES):
        return value
    return None


def resolve_image_references(payload, image_count: int):
    """Drop unavailable references before validating the rest of the deck."""
    if not isinstance(payload, dict):
        return payload
    if "cover_image_number" in payload:
        payload["cover_image_number"] = available_image_number(payload["cover_image_number"], image_count)
    slides = payload.get("slides")
    if not isinstance(slides, list):
        return payload
    for page in slides:
        if not isinstance(page, dict):
            continue
        if "image_number" in page:
            page["image_number"] = available_image_number(page["image_number"], image_count)
        if page.get("layout") != "gallery" and "figures" not in page:
            continue
        figures = []
        seen = set()
        entries = page.get("figures")
        for entry in entries if isinstance(entries, list) else ():
            if not isinstance(entry, dict):
                continue
            number = available_image_number(entry.get("image_number"), image_count)
            if number is None or number in seen:
                continue
            try:
                figure = Figure.model_validate({**entry, "image_number": number})
            except ValidationError:
                continue
            figures.append(figure.model_dump())
            seen.add(number)
            if len(figures) == 2:
                break
        if page.get("layout") != "gallery":
            page["figures"] = figures
            continue
        if len(figures) == 2:
            page["figures"] = figures
            continue
        page["image_number"] = figures[0]["image_number"] if figures else None
        if page.get("blocks"):
            page["layout"] = "editorial"
        elif page.get("points") or figures:
            page["layout"] = "image_left" if figures else "list"
            if figures:
                points = page.get("points", [])
                if isinstance(points, list):
                    page["points"] = [*points[:4], figures[0]["caption"]]
        else:
            page["layout"] = "section"
        page.pop("figures")
    return payload


async def draft_presentation(model, brief: dict, *, image_count: int, previous: DeckContent | None = None,
                             preferred_online_images: set[int] | None = None,
                             require_online_image: bool = False) -> DeckContent:
    request = brief["请求"]
    count = requested_slide_count(request)
    if count is not None:
        if not 2 <= count <= 20:
            raise ValueError(CONSTANT.SLIDE_COUNT_UNSUPPORTED)
        brief = {**brief, "总页数（含封面）": count, "正文页数": count - 1}
    if previous:
        brief = {**brief, "上一版演示文稿": previous.model_dump(mode="json")}
    brief = {**brief, "模板编排": {name: f"{template.title}（{template.theme}）。{template.guidance}"
                                  for name, template in TEMPLATES.items()},
             "可用主题": list(PALETTES), "可用图片编号": list(range(1, min(image_count, MAX_IMAGES) + 1))}
    if preferred_online_images:
        brief["联网图片编号"] = sorted(preferred_online_images)
    require_flow = HORIZONTAL_FLOW.search(request) and "使用内置版式" in brief.get("模板说明", "")
    omit_cover = bool(NO_COVER_IMAGE.search(request))
    require_cover = (image_count and IMAGE_COVER.search(request) and not omit_cover
                     and "使用内置版式" in brief.get("模板说明", ""))
    messages = [SystemMessage(content=PPT_PROMPT), HumanMessage(content=json.dumps(brief, ensure_ascii=False))]
    bind = getattr(model, "bind", None)
    structured = bind(response_format={"type": "json_object"}) if callable(bind) and not getattr(model, "use_responses_api", False) else model
    issue = ""
    fallback = None
    best_coverage = -1
    for attempt in range(3):
        try:
            try:
                response = await structured.ainvoke(messages)
            except BadRequestError as exc:
                if structured is model or not re.search(r"response.format|json.object", str(exc), re.I):
                    raise
                structured = model
                response = await model.ainvoke(messages)
        except APIError:
            if fallback is not None:
                return fallback
            raise
        try:
            payload = resolve_image_references(json.loads(model_json(response)), image_count)
            deck = DeckContent.model_validate(payload)
        except json.JSONDecodeError:
            issue = "模型响应缺少完整的合法 JSON，可能在输出时被截断。请缩短备注并输出完整对象。"
        except ValidationError as exc:
            issue = validation_issue(exc)
        else:
            if omit_cover and deck.cover_image_number is not None:
                deck = deck.model_copy(update={"cover_image_number": None})
            if count is not None and len(deck.slides) + 1 != count:
                issue = f"实际总页数是 {len(deck.slides) + 1} 页。请输出恰好 {count - 1} 张正文页，加上封面共 {count} 页。"
            elif require_flow and not any(page.layout == "diagram" and page.image_number is None and len(page.points) >= 2
                                          for page in deck.slides):
                issue = "本轮明确要求横向流程图。至少一页须用 diagram、提供 2–4 条短步骤并省略该页图片。"
            elif require_cover and deck.cover_image_number is None:
                issue = "本轮明确要求封面配图，且有可用素材。请填写 cover_image_number。"
            else:
                available_online = preferred_online_images or set()
                coverage = len(available_online.intersection(deck.image_numbers()))
                target = min(len(available_online), (len(deck.slides) + 1) // 2, 4)
                if not require_online_image and len(deck.slides) < 3:
                    target = 0
                if coverage >= target:
                    return deck
                if coverage > best_coverage:
                    fallback, best_coverage = deck, coverage
                if attempt >= 1:
                    return fallback
                issue = "请复核相关页面的公开配图。优先采用不同的匹配图片，保留指定的版式与页数。"
        if attempt < 2:
            messages = [*messages[:2], response, HumanMessage(content=issue)]
    if fallback is not None:
        return fallback
    raise ValueError(CONSTANT.SLIDE_PLAN_INVALID.format(issue=issue))


def searchable_terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return words - {"可以", "进行", "根据", "通过", "下面", "说明", "图片", "关系", "实验", "the", "and"}


def place_available_figures(deck: DeckContent, captions: dict, figures: dict[int, PdfFigure], *, allow_cover: bool,
                            replace_illustrations: bool, preferred_online_images: set[int] | None = None) -> DeckContent:
    used = set(deck.image_numbers())
    desired = max(1, min((len(deck.slides) + 1) // 2, 4))
    online = preferred_online_images or set()
    total_target = min(len(captions), desired)
    cover = deck.cover_image_number

    def filled():
        online_target = min(len(online), desired + int(cover in online))
        return len(used) >= total_target and len(used & online) >= online_target

    if filled() and (not online or not allow_cover or deck.cover_image_number is not None):
        return deck
    pages = list(deck.slides)
    eligible = {}
    for index, page in enumerate(pages):
        if page.image_numbers() or page.layout not in {"editorial", "list", "columns", "image_left", "focus"} or len(page.blocks) > 3:
            continue
        text = " ".join([page.title, page.lead, *page.template_points()])
        eligible[index] = searchable_terms(text)

    scores = []
    cover_scores = []
    cover_terms = searchable_terms(f"{deck.title} {deck.subtitle}")
    for number, info in captions.items():
        if number in used or (number not in figures and not replace_illustrations):
            continue
        title = " ".join(str(info.get(field, "")) for field in ("教材图", "文件名", "公开图片", "检索主题"))
        context = figures[number].context if number in figures else str(info.get("图片资料", ""))
        names = searchable_terms(title)
        concepts = searchable_terms(context)
        if allow_cover and deck.cover_image_number is None and number in online:
            similarity = 3 * len(cover_terms & names) + len(cover_terms & concepts)
            if similarity >= 3:
                cover_scores.append((similarity, number))
        for index, terms in eligible.items():
            score = 3 * len(terms & names) + len(terms & concepts)
            if score >= 2:
                scores.append((score, number, index))
    if cover_scores:
        _, cover = max(cover_scores, key=lambda match: (match[0], -match[1]))
        used.add(cover)
    for _, number, index in sorted(scores, key=lambda match: (match[1] not in online, -match[0], match[1], match[2])):
        if filled():
            break
        if number in used or index not in eligible:
            continue
        if len(used) >= total_target and number not in online:
            break
        page = pages[index]
        layout = page.layout if page.layout == "editorial" else "image_left"
        pages[index] = page.model_copy(update={"image_number": number, "layout": layout})
        used.add(number)
        del eligible[index]
    if not used and allow_cover and replace_illustrations and not figures:
        title = searchable_terms(deck.title)
        cover = next((number for number, info in captions.items() if title & searchable_terms(
            " ".join(str(info.get(field, "")) for field in ("检索主题", "公开图片", "图片资料", "文件名"))
        )), None)
    return deck.model_copy(update={"slides": pages, "cover_image_number": cover})


async def enrich_notes(model, deck: DeckContent, brief: dict) -> DeckContent:
    system = SystemMessage(content="""补写 PPT 讲稿，以给定的正文和资料为依据。
每页补充因果或推导、证据的含义、操作示例或适用条件。保留已有讲稿里的有用细节。
可用明确标注的假设示例，禁止虚构实测结果、引文、来源和数据。避免逐条重复正文。
只输出 JSON 对象。notes 是字符串数组，与所给页面的顺序和数量严格一致。每页约 150–350 字。""")
    slides = []
    for start in range(0, len(deck.slides), 4):
        batch = deck.slides[start:start + 4]
        payload = {"请求": brief["请求"], "主题": deck.title, "文字资料": brief.get("文字资料", ""),
                   "页码起点": start + 2, "页面": [page.model_dump(mode="json") for page in batch]}
        try:
            response = await model.ainvoke([system, HumanMessage(content=json.dumps(payload, ensure_ascii=False))])
            result = json.loads(model_json(response))
        except (ValueError, APIError):
            slides.extend(batch)
            continue
        notes = result.get("notes") if isinstance(result, dict) else None
        if not isinstance(notes, list) or len(notes) != len(batch):
            slides.extend(batch)
            continue
        for page, note in zip(batch, notes):
            if isinstance(note, str) and note.strip() and note.strip() != page.notes:
                combined = "\n\n".join(filter(None, (page.notes, note.strip())))
                page = page.model_copy(update={"notes": combined[:3600]})
            slides.append(page)
    return deck.model_copy(update={"slides": slides})
