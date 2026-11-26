"""Create and revise Roommate presentations."""

from __future__ import annotations

import re
from pathlib import Path

from typing import TYPE_CHECKING

from starlette.concurrency import run_in_threadpool

from server.agent.roommate_models import vision_text
from server.agent.roommate_pdf import pdf_page_range
from server.agent.roommate_routing import ATTACHMENT_REQUEST, Plan
from server.agent.roommate_types import Result, RoommateError, clarify
from server.General_Tools.pdf_on_demand import read_pdf_material
from .design_agent import NO_IMAGES, NO_WEB_IMAGES, WEB_IMAGES, design_presentation, wants_illustrations
from .pdf_figures import SECTION_LIMIT, PdfFigure, extract_pdf_figures, find_section
from .planning import (DEPTH_GUIDANCE, NO_COVER_IMAGE, draft_presentation, enrich_notes, place_available_figures,
                       requested_slide_count, resolve_image_references)
from .presentation_writer import MAX_IMAGES, PPTX_FILENAME, DeckContent, write_presentation

from . import CONSTANT

if TYPE_CHECKING:
    from server.agent.roommate import RoommateTask


BARE_PPT = re.compile(r"(?:(?:请|帮我|麻烦)\s*)*(?:制作|做|生成|创建)\s*"
                      r"(?:一(?:份|个|套)\s*)?(?:pptx?|演示文稿|幻灯片)\s*[。！!]?", re.I)
PPT_MATERIAL_SUFFIXES = (".txt", ".md", ".pdf")


async def make_ppt(task: RoommateTask, plan: Plan) -> Result:
    template = next((task.attachment_path(attach) for attach in task.message.get("other_files", [])
                     if Path(attach["stored_name"]).suffix.lower() == ".pptx"), None)
    attachments = task.message["attachments"]
    images = [task.attachment_path(attach) for attach in attachments]
    request = task.message["requirements"]
    files = [attach for attach in task.message.get("other_files", [])
             if attach["stored_name"].lower().endswith(PPT_MATERIAL_SUFFIXES)]
    previous_turn = None
    if plan.use_previous:
        if task.reference_turn and task.reference_turn.get("roommate_presentation"):
            previous_turn = task.reference_turn
        else:
            previous_turn = next((turn for turn in reversed(task.history) if turn.get("roommate_presentation")), None)
    previous = previous_turn["roommate_presentation"] if previous_turn else None
    original_request = previous.get("original_request", previous_turn["requirements"]) if previous else request
    material_request = original_request
    source_turn_id = files[0].get("source_message_id", task.message["id"]) if files else None
    if not files:
        recorded_source = previous.get("material_source_id") if previous else None
        candidates = [turn for turn in reversed(task.history) if turn.get("roommate_presentation")
                      and turn["requirements"].strip() == original_request.strip() and turn["id"] != recorded_source]
        source_turn = next((turn for turn in task.history if turn["id"] == recorded_source), None)
        if source_turn:
            candidates.insert(0, source_turn)
        for turn in candidates:
            inherited = [{**attach, "source_message_id": turn["id"]} for attach in turn.get("other_files", [])
                         if attach["stored_name"].lower().endswith(PPT_MATERIAL_SUFFIXES)]
            files = [attach for attach in inherited if task.attachment_path(attach).is_file()]
            if files:
                source_turn_id = turn["id"]
                material_request = turn["requirements"]
                break
    if not images and not files and not plan.use_previous and (
        request == ATTACHMENT_REQUEST or BARE_PPT.fullmatch(request)
    ):
        return clarify(CONSTANT.ASK_PRESENTATION_TOPIC)
    if not task.model:
        raise RoommateError(CONSTANT.TEXT_MODEL_REQUIRED)
    figure_target = task.task_dir / "materials" / task.message["id"]
    include_images = not NO_IMAGES.search(request)
    can_search_web = include_images and not NO_WEB_IMAGES.search(request)
    explicit_web = bool(can_search_web and WEB_IMAGES.search(request))
    can_search_web = can_search_web and (explicit_web or not NO_WEB_IMAGES.search(original_request))
    illustrations_requested = wants_illustrations(request)
    detailed_images = task.output_detail == "detailed" and can_search_web
    textbook_figures: list[PdfFigure] = []
    def read_presentation_pdf(path, name, char_limit, scan_limit, **options):
        if not options:
            section = find_section(path, request)
            if section is None and material_request != request:
                section = find_section(path, material_request)
            if section:
                options = {"start_page": section, "page_limit": SECTION_LIMIT}
        pages, truncated = read_pdf_material(path, char_limit, scan_limit, **options)
        if include_images and len(textbook_figures) < 6:
            source_pages = [page.number for page in pages if page.text][:8]
            textbook_figures.extend(extract_pdf_figures(path, name, source_pages, figure_target, 6 - len(textbook_figures)))
        return pages, truncated

    pdf_request = request
    if source_turn_id and source_turn_id != task.message["id"] and material_request != request and not pdf_page_range(request):
        pdf_request = material_request
    material = await task.model_material(as_material=True, files=files, read_pdf=read_presentation_pdf, pdf_request=pdf_request)
    credits: dict[int, str] = {}
    captions = {}
    online_numbers = set()
    if previous:
        if not template and previous.get("template"):
            template = task.task_dir / previous["template"]
        if not images and include_images:
            for index, relative in enumerate(previous["images"], 1):
                key = str(index)
                credit = previous.get("image_credits", {}).get(key, "")
                is_online = bool(re.search(r"(?m)^来源：https?://", credit))
                if (is_online and (explicit_web or not can_search_web)) or (
                    textbook_figures and credit.startswith("图片来源：")
                ):
                    continue
                path = task.task_dir / relative
                if not path.is_file():
                    continue
                images.append(path)
                number = len(images)
                if credit:
                    credits[number] = credit
                if is_online:
                    online_numbers.add(number)
                if info := previous.get("image_captions", {}).get(key):
                    captions[number] = info
    elif not material and plan.use_previous and task.last_result:
        material = task.last_result["reply"][:12000]
    if request == ATTACHMENT_REQUEST and not material and not images:
        return clarify(CONSTANT.ASK_PRESENTATION_TOPIC)
    if images:
        task.progress(CONSTANT.READING_IMAGES)
        for index, path in enumerate(images, 1):
            if index in captions or index in credits:
                continue
            name = attachments[index - 1]["name"] if attachments else f"上一版图片 {index}"
            captions[index] = {"文件名": name}
            try:
                content = await run_in_threadpool(path.read_bytes)
                captions[index]["图片资料"] = await vision_text(content, task.models.vision, as_material=True)
            except RoommateError:
                if not material and request == ATTACHMENT_REQUEST:
                    raise
    if not include_images:
        images, credits, captions = [], {}, {}
    source_figures = {}
    for figure in textbook_figures[:MAX_IMAGES - len(images)]:
        images.append(figure.path)
        number = len(images)
        credits[number] = figure.credit
        captions[number] = {"教材图": figure.caption, "图旁文字": figure.context}
        source_figures[number] = figure
    prompt = {
        "请求": request,
        "文字资料": material,
        "图片": captions,
        "模板说明": "使用上传模板。正文可采用与素材相关的图片。封面仅在有图片占位符时可配图。" if template else "使用内置版式。",
        "输出详略": task.output_rule,
        **task.memory,
    }
    if original_request != request:
        prompt["原始课件要求"] = original_request
    prompt["内容深度"] = DEPTH_GUIDANCE[task.output_detail]
    if count := requested_slide_count(request):
        prompt["总页数（含封面）"] = count
    output = task.task_dir / "artifacts" / task.message["id"] / PPTX_FILENAME
    try:
        prior_deck = None
        if previous:
            prior_deck = DeckContent.model_validate(previous["deck"])
            if previous["images"] != [path.relative_to(task.task_dir).as_posix() for path in images]:
                prior_deck = DeckContent.model_validate(
                    resolve_image_references(prior_deck.model_dump(), 0))
        design_brief = {**prompt, "上一版主题": prior_deck.title if prior_deck else "",
                        "明确要求配图": illustrations_requested, "明确要求联网配图": explicit_web,
                        "详细档自动配图": detailed_images}
        use_web = (can_search_web and (not template or illustrations_requested or detailed_images)
                   and len(images) < MAX_IMAGES)
        direction, assets = await design_presentation(
            task.model, design_brief, figure_target, task.progress,
            use_web=use_web,
        )
        if direction:
            prompt["视觉方向"] = direction
        for asset in assets[:MAX_IMAGES - len(images)]:
            images.append(asset.path)
            number = len(images)
            credits[number] = asset.credit
            online_numbers.add(number)
            captions[number] = {"公开图片": asset.title, "作者": asset.creator, "来源": asset.source,
                                "检索主题": " ".join(filter(None, (asset.query, asset.subject)))}
        task.progress(CONSTANT.PLANNING_SLIDES)
        deck = await draft_presentation(task.model, prompt, image_count=len(images), previous=prior_deck,
                                            preferred_online_images=online_numbers or None,
                                            require_online_image=explicit_web or detailed_images)
        if source_figures or illustrations_requested or detailed_images:
            deck = place_available_figures(deck, captions, source_figures,
                                               allow_cover=not template and not NO_COVER_IMAGE.search(request),
                                               replace_illustrations=illustrations_requested or detailed_images,
                                               preferred_online_images=online_numbers if explicit_web or detailed_images else None)
        if task.output_detail == "detailed":
            task.progress(CONSTANT.WRITING_SPEAKER_NOTES)
            deck = await enrich_notes(task.model, deck, prompt)
        task.progress(CONSTANT.WRITING_PRESENTATION)
        placed = await run_in_threadpool(write_presentation, output, deck, template, images, credits)
    except ValueError as exc:
        raise RoommateError(str(exc)) from exc
    presentation = {
        "deck": deck.model_dump(mode="json"),
        "template": template.relative_to(task.task_dir).as_posix() if template else None,
        "images": [path.relative_to(task.task_dir).as_posix() for path in images],
        "image_credits": {str(number): credit for number, credit in credits.items()},
        "image_captions": {str(number): info for number, info in captions.items()},
        "original_request": original_request,
        "material_source_id": source_turn_id,
    }
    image_feedback = f" 已嵌入 {len(placed)} 张不同图片。" if placed else ""
    online_placed = online_numbers & placed
    if online_placed:
        image_feedback += f" 其中 {len(online_placed)} 张来自联网检索。"
    elif explicit_web or detailed_images:
        if online_numbers:
            image_feedback += f" 已下载 {len(online_numbers)} 张联网候选图，当前课件未采用。"
        elif use_web:
            image_feedback += " 本次联网检索未获得可嵌入的图片。"
        else:
            image_feedback += " 已达到素材上限，本次未检索联网图片。"
    elif illustrations_requested and not placed:
        image_feedback = " 本次未找到能放入当前版式的相关配图。"
    return Result(CONSTANT.PRESENTATION_READY.format(title=deck.title, slide_count=len(deck.slides) + 1, image_feedback=image_feedback),
                  files=[PPTX_FILENAME], presentation=presentation)
