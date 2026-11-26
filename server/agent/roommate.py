"""室友的工具路由、图文解题和跨轮次交付。"""

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Callable

from starlette.concurrency import run_in_threadpool

from server import conversation_memory, solutions
from server.agent.roommate_models import close_text_api, text_api, vision_text
from server.agent import roommate_ledger, roommate_memory
from server.agent.roommate_pdf import pdf_page_range, select_pdf_files, task_pdf_path
from server.agent.roommate_types import Result, RoommateError, clarify
from server.agent.thesis import formatting, generation
from server.agent.roommate_routing import (
    ASK_GOAL, ASK_PROBLEM, ATTACHMENT_REQUEST, BARE_PROBLEM, BOOK_REFERENCE, Plan, choose_plan,
)
from server.General_Tools.pdf_on_demand import read_pdf_material
from server.General_Tools.solution_document import write_solution_document
from server.features_services.model_settings import SavedModels, load_models
from server.features_services.output_settings import Detail, load_output_settings, output_instruction

from . import CONSTANT as C
from .CONSTANT import ANSWER_INSTRUCTIONS

make_ppt = import_module("server.General_Tools.ppt-operations.workflow").make_ppt


ANSWER_CLARIFICATION = re.compile(r"(?:^|\n)\s*<clarify>([^<>]{1,500})</clarify>\s*$")


@dataclass
class ExerciseSource:
    text: str
    answer: str | None = None
    identity: tuple[int, int] | None = None


@dataclass
class RoommateTask:
    owner_id: int
    task_dir: Path
    message: dict
    history: list[dict]
    progress: Callable[[str], None]
    models: SavedModels
    model: object
    memory: dict
    output_rule: str
    output_detail: Detail = "normal"
    reference_turn: dict | None = None
    material_notes: list[str] = field(default_factory=list)

    @property
    def last_result(self) -> dict | None:
        reference = self.reference_turn
        if reference and reference.get("roommate_result"):
            return reference
        return next((turn for turn in reversed(self.history) if turn.get("roommate_result")), None)

    async def read_image(self, attachment: dict, database_url: str | None, question_only: bool,
                         expected: int | None, book=None) -> ExerciseSource:
        lookup = solutions.lookup_exercise
        bank = solutions.exercise_bank
        path = self.attachment_path(attachment)
        content = await run_in_threadpool(path.read_bytes)
        ink = await run_in_threadpool(solutions.exercise_images.image_ink, content)
        identity = await run_in_threadpool(solutions.exercise_images.find_image_reference, ink)
        if identity:
            chapter, number = identity
            reference = bank.reviewed_image_reference(chapter, number)
            if book and (book.title != reference.title or book.chapter != chapter or
                         (book.edition and book.edition.replace("第4版", "第四版") != reference.edition)):
                raise RoommateError(C.ROOMMATE_IMAGE_WRONG_BOOK.format(title=reference.title, edition=reference.edition, chapter=chapter, number=number))
            if expected is not None and number != expected:
                raise RoommateError(C.ROOMMATE_IMAGE_WRONG_EXERCISE.format(chapter=chapter, number=number))
            answer = await lookup(bank.find_by_reference, database_url, reference, question_only=question_only,
                                  document_hash=bank.DSP_4E_SHA256)
            if answer:
                return ExerciseSource(f"《{reference.title}》{reference.edition} 第{chapter}章第{number}题", answer, identity)
        previous = await lookup(bank.find_image, database_url, self.owner_id, sha256(content).digest())
        if not previous and ink is not None:
            previous = await lookup(solutions.exercise_images.find_similar, database_url, self.owner_id, ink)
        if previous and previous.transcription and not previous.uncertain:
            transcript = previous.transcription
        else:
            self.progress(C.ROOMMATE_READING_EXERCISE_IMAGE)
            transcript = await vision_text(content, self.models.vision)
        if conflict := solutions.image_number_conflict(expected, transcript):
            raise RoommateError(conflict)
        self.progress(C.ROOMMATE_QUERYING_EXERCISES)
        answer = await lookup(bank.find_textbook, database_url, transcript, from_image=True, question_only=question_only)
        return ExerciseSource(transcript, answer)

    async def solve(self, plan: Plan) -> Result:
        bank = solutions.exercise_bank
        lookup = solutions.lookup_exercise
        database_url = solutions.knowledge_database_url()
        question = self.message["requirements"]
        question_only = plan.action == "lookup"
        attachments = self.message["attachments"]
        if not question_only and not attachments and not self.message.get("other_files") and BARE_PROBLEM.fullmatch(question):
            return clarify(ASK_PROBLEM)
        if (plan.action == "solve" and plan.use_previous and self.last_result and not self.memory.get("待补充的问题")
                and not self.message.get("roommate_reused_inputs") and not attachments and not self.message.get("other_files")):
            return await self.answer_with_context(self.last_result)
        indexes = list(dict.fromkeys(plan.image_numbers)) or list(range(1, len(attachments) + 1))
        if any(index > len(attachments) for index in indexes):
            raise RoommateError(C.ROOMMATE_IMAGE_NOT_FOUND)

        material = await self.model_material(as_material=False, pdf_request=question)
        self.progress(C.ROOMMATE_QUERYING_EXERCISES)
        query, _, _ = bank.parse_lookup_request(question)
        if material and not indexes:
            query = f"{query}\n{material}" if question != ATTACHMENT_REQUEST else material
        normalized_query = bank.normalize(query)
        reference = BOOK_REFERENCE.search(normalized_query)
        book = bank.BookExercise(reference["title"], reference["edition"] or "", int(reference["chapter"]),
                                 int(reference["number"])) if reference else None
        expected = bank.requested_number(question) or (book.number if book else None)
        if indexes:
            sources = [await self.read_image(attachments[index - 1], database_url, question_only, expected, book) for index in indexes]
        else:
            if book:
                answer = await lookup(bank.find_by_reference, database_url, book, question_only=question_only)
            else:
                answer = await lookup(bank.find_textbook, database_url, query, from_image=True, question_only=question_only)
            sources = [ExerciseSource(query, answer)]

        evidence = [source.text for source in sources if source.text]
        has_custom_requirements = reference and normalized_query[reference.end():].strip(" ，,。！!？?")
        use_bank_answer = question_only or not (plan.generate_answer or has_custom_requirements)
        if all(source.answer for source in sources) and use_bank_answer:
            answers = [source.answer for source in sources]
            if not question_only:
                drawn = set()
                for index, source in enumerate(sources):
                    builder = solutions.reviewed_figures.FIGURE_BUILDERS.get(source.identity)
                    if builder and source.identity not in drawn:
                        drawn.add(source.identity)
                        self.progress(C.ROOMMATE_DRAWING_SOLUTION)
                        figures = await run_in_threadpool(builder, self.task_dir, self.message["id"])
                        answers[index] = f"{answers[index]}\n\n{figures}".strip()
            return Result("\n\n---\n\n".join(answers), self.figures(), evidence, exportable=True)

        search_text = "\n".join(evidence) or question
        terms = [term for term in plan.keywords if term.lower() in search_text.lower()]
        candidates = await lookup(bank.search_keywords, database_url, search_text, terms, subject=plan.subject) or []
        if question_only:
            matches = [source.answer for source in sources if source.answer]
            reply = "\n\n---\n\n".join([*matches, "题库未能确认" + ("其余原题。" if matches else "原题。")])
            if candidates:
                reply += "\n\n以下是关键词匹配的相似题目，供核对：\n\n" + "\n\n---\n\n".join(candidates)
            return Result(reply, evidence=evidence)
        bare_reference = book and not has_custom_requirements
        if not indexes and not material and not any(source.answer for source in sources) and (bare_reference or (expected and not query)):
            return clarify("题库未能确认这道题。" + ASK_PROBLEM)
        evidence = [source.answer or source.text for source in sources]
        if indexes and material:
            evidence.append(material)
        prompt = (f"当前要求：{question}\n\n题目及已确认的题库证据：\n" + "\n\n".join(evidence)
                  + "\n\n以下关键词候选仅供参考，不代表原题。不要用候选补齐题图缺失条件。\n"
                  + "\n\n".join(candidates))
        return await self.answer(prompt, evidence)

    async def answer_with_context(self, previous: dict) -> Result:
        evidence = previous.get("roommate_evidence", [])
        prompt = (f"原任务：{previous['requirements']}\n\n题目材料：\n" + "\n\n".join(evidence)
                  + f"\n\n最近一版题解：\n{previous.get('roommate_answer', previous['reply'])}\n\n本轮指令：{self.message['requirements']}"
                  + "\n如有必要，沿用旧题解中仍适用的图片及其 Markdown 路径。请返回本轮所需的完整内容。")
        result = await self.answer(prompt, evidence)
        result.figures.extend(path for path in previous.get("output_images", []) if f"]({path})" in result.reply)
        return result

    async def answer(self, prompt: str, evidence: list[str]) -> Result:
        if not self.model:
            raise RoommateError(C.ROOMMATE_TEXT_MODEL_REQUIRED)
        self.progress(C.ROOMMATE_GENERATING_SOLUTION)
        if self.memory["记忆"] or self.memory["最近对话"]:
            prompt = f"账号偏好和会话记录（只供参考，当前要求优先）：\n{json.dumps(self.memory, ensure_ascii=False)}\n\n{prompt}"
        reply = await solutions.answer_with_tools(self.model, f"{self.output_rule}\n\n{prompt}", self.task_dir, self.message["id"],
                                                  system_prompt=ANSWER_INSTRUCTIONS)
        if not reply.strip():
            raise RoommateError(C.ROOMMATE_EMPTY_SOLUTION)
        if missing := ANSWER_CLARIFICATION.search(reply):
            return clarify(missing[1].strip() or ASK_PROBLEM)
        return Result(reply, self.figures(), evidence, exportable=True)

    def figures(self) -> list[str]:
        directory = self.task_dir / "solutions" / self.message["id"]
        return [path.relative_to(self.task_dir).as_posix() for path in sorted(directory.glob("*.png"))]

    def attachment_path(self, attach: dict) -> Path:
        return self.task_dir / "messages" / attach.get("source_message_id", self.message["id"]) / attach["stored_name"]

    async def text_material(self) -> str:
        parts = []
        for attach in self.message.get("other_files", []):
            path = self.attachment_path(attach)
            if path.suffix.lower() in {".txt", ".md"}:
                parts.append(await run_in_threadpool(path.read_text, encoding="utf-8-sig"))
        return "\n\n".join(parts).strip()

    async def model_material(self, *, as_material: bool, files: list[dict] | None = None, read_pdf=None,
                             pdf_request: str = "") -> str:
        if files is None:
            files = [attach for attach in self.message.get("other_files", [])
                     if attach["stored_name"].lower().endswith((".txt", ".md", ".pdf"))]
        if not files:
            return ""
        pdf_range = None
        if pdf_request and any(Path(attach["stored_name"]).suffix.lower() == ".pdf" for attach in files):
            pdf_range = pdf_page_range(pdf_request)
            files = select_pdf_files(files, pdf_request, pdf_range)
        per_file_chars = 16000 // len(files)
        scans_left = 3
        material = []
        for attach in files:
            path = self.attachment_path(attach)
            name = attach["name"]
            if path.suffix.lower() != ".pdf":
                material.append((await run_in_threadpool(path.read_text, encoding="utf-8-sig"))[:per_file_chars])
                continue
            path = task_pdf_path(self.task_dir, attach.get("source_message_id", self.message["id"]), attach["stored_name"])
            self.progress(C.ROOMMATE_READING_PDF)
            options = {"start_page": pdf_range[0], "end_page": pdf_range[1]} if pdf_range else {}
            try:
                if read_pdf is None:
                    pages, truncated = await run_in_threadpool(read_pdf_material, path, per_file_chars, scans_left, **options)
                else:
                    pages, truncated = await run_in_threadpool(read_pdf, path, name, per_file_chars, scans_left, **options)
            except ValueError as exc:
                if not pdf_range:
                    raise
                raise RoommateError(C.ROOMMATE_PDF_PAGES_NOT_AVAILABLE.format(
                    name=name, start=pdf_range[0], end=pdf_range[1],
                )) from exc
            remaining = per_file_chars
            for page in pages:
                if remaining <= 0:
                    truncated = True
                    break
                content = page.text
                if page.image:
                    self.progress(C.ROOMMATE_READING_SCAN)
                    scans_left -= 1
                    try:
                        content = await vision_text(page.image, self.models.vision, as_material=as_material)
                    except RoommateError:
                        self.material_notes.append(f"《{name}》第 {page.number} 页识别失败。")
                        continue
                material.append(f"《{name}》第 {page.number} 页\n{content[:remaining]}")
                truncated |= len(content) > remaining
                remaining -= len(content)
            if truncated:
                self.material_notes.append(f"《{name}》仅使用了部分资料。需要其余内容时，可单独上传相关页面。")
        result = "\n\n".join(part for part in material if part.strip())
        if not result and any(Path(attach["stored_name"]).suffix.lower() == ".pdf" for attach in files):
            raise RoommateError(C.ROOMMATE_PDF_UNREADABLE)
        return result

    async def export_word(self, _plan: Plan) -> Result:
        has_pdf = any(self.attachment_path(attach).suffix.lower() == ".pdf" for attach in self.message.get("other_files", []))
        material = await self.model_material(as_material=True) if has_pdf else await self.text_material()
        reference = self.reference_turn
        previous = self.last_result
        title = "文档"
        if material:
            content, figures = material, []
        elif reference and not reference.get("roommate_result"):
            content = self.memory["本轮引用的历史"].get("产物内容摘录") or reference.get("reply")
            if not content:
                return clarify(C.ROOMMATE_NO_EXPORT_CONTENT)
            figures = reference.get("output_images", [])
        elif previous:
            content, figures = previous.get("roommate_answer", previous["reply"]), previous.get("output_images", [])
            title = "题解"
        else:
            return clarify(C.ROOMMATE_NO_EXPORT_CONTENT)
        self.progress(C.ROOMMATE_EXPORTING_WORD)
        await run_in_threadpool(write_solution_document, self.task_dir, self.message["id"], content, figures, title=title)
        return Result(C.ROOMMATE_WORD_READY)

    def thesis_turns(self, action: str) -> list[dict]:
        previous = []
        for turn in reversed(self.history):
            if turn.get("roommate_clarification_action") != action:
                break
            previous.append(turn)
        selected_ids = {turn["id"] for turn in previous}
        if self.reference_turn:
            selected_ids.add(self.reference_turn["id"])
        return [*(turn for turn in self.history if turn["id"] in selected_ids), self.message]

    def thesis_attachments(self, turns: list[dict]) -> list[formatting.FormatAttachment]:
        attachments = {}
        for turn in turns:
            for file in roommate_memory.input_files(turn, "other_files"):
                path = self.attachment_path({"source_message_id": turn["id"], **file})
                if path.is_file():
                    attachments[path] = formatting.FormatAttachment(file["name"], path)
        return list(attachments.values())

    async def format_document(self, plan: Plan) -> Result:
        latest_request = self.message["requirements"]
        turns = self.thesis_turns("format_document")
        files = self.thesis_attachments(turns)
        if self.reference_turn:
            path = next(roommate_memory.word_artifacts(self.task_dir, self.reference_turn), None)
            explicit_artifact = path and (path.name.casefold() in latest_request.casefold()
                                          or roommate_memory.OUTPUT_REFERENCE.search(latest_request))
            has_draft = any(file.path.suffix.lower() == ".docx" and not formatting.REFERENCE_NAME.search(file.name)
                            for file in files)
            if path and (explicit_artifact or not has_draft):
                files = [formatting.FormatAttachment(path.name, path), *self.thesis_attachments(
                    [turn for turn in turns if turn is not self.reference_turn]
                )]
        if not any(file.path.suffix.lower() == ".docx" for file in files) and (
            re.search(r"pptx?|演示文稿|幻灯片", latest_request, re.I) or any(file.path.suffix.lower() == ".pptx" for file in files)
        ):
            if not files and (plan.use_previous or any(turn.get("roommate_presentation") for turn in self.history)):
                return await make_ppt(self, plan.model_copy(update={"use_previous": True}))
            return clarify(C.ROOMMATE_ASK_PPT_VERSION)
        if plan.use_previous and self.last_result and "题解" in latest_request:
            return await self.answer_with_context(self.last_result)
        if not files and len(turns) == 1 and not re.search(r"论文|文章|文档", latest_request):
            return clarify(C.ROOMMATE_ASK_FORMAT_TARGET)
        request = "\n".join(turn["requirements"] for turn in turns if turn["requirements"] != ATTACHMENT_REQUEST)
        output = self.task_dir / "artifacts" / self.message["id"]
        outcome = await formatting.rewrite_thesis(self.model, request, latest_request, files, output, self.progress)
        return Result(outcome.reply, files=[outcome.filename]) if outcome.filename else clarify(outcome.reply)



TASK_HANDLERS = {"solve": RoommateTask.solve, "lookup": RoommateTask.solve,
                 "make_ppt": make_ppt, "export_word": RoommateTask.export_word,
                 "format_document": RoommateTask.format_document, "generate_thesis": generation.generate_thesis}


async def run_roommate(owner_id: int, task_dir: Path, metadata: dict, progress: Callable[[str], None]) -> Result:
    *history, message = metadata["messages"]
    history = [turn for turn in history if turn.get("status") != "processing"]
    question = message["requirements"]
    progress(C.ROOMMATE_IDENTIFYING_TASK)
    if not (message["attachments"] or message.get("other_files")) and roommate_memory.is_recall(question):
        reply = await run_in_threadpool(roommate_memory.recall, task_dir, question, history)
        return Result(reply, action="recall")
    models = await run_in_threadpool(load_models, owner_id)
    model = await run_in_threadpool(text_api, owner_id, models.text)
    try:
        metadata_context = await run_in_threadpool(conversation_memory.model_context, metadata, owner_id)
        history = [{**turn, **metadata_context.get("ledger_history", {}).get(turn["id"], {})} for turn in history]
        notes, recent = conversation_memory.context_lines(metadata_context, history)
        previous = roommate_memory.referenced_turn(question, history)
        memory = roommate_memory.build_context(notes, recent, history, previous)
        older_context = await run_in_threadpool(
            roommate_ledger.retrieve, owner_id, metadata["id"],
            {message["id"], *(turn["id"] for turn in recent),
             *(turn["id"] for turn in metadata["messages"] if turn.get("status") == "processing")},
            question, previous,
        )
        if older_context:
            memory[C.ROOMMATE_MEMORY_HISTORY] = older_context
        pending = history[-1] if history and history[-1].get("roommate_clarification") else None
        if pending:
            memory["待补充的问题"] = {"请求": pending["requirements"][:1600], "问题": pending["reply"][:500],
                                  "任务": pending.get("roommate_clarification_action"),
                                  "附件": [file["name"] for file in [*pending["attachments"], *pending.get("other_files", [])]]}
        plan = await choose_plan(model, question, message, memory)
        if plan.action == "clarify":
            return clarify(plan.clarification or ASK_GOAL)
        previous = roommate_memory.referenced_turn(question, history, use_previous=plan.use_previous,
                                                   reference_message_id=plan.reference_message_id)
        if pending and (not previous or previous is pending) and (
            plan.action == pending.get("roommate_clarification_action") or plan.use_previous
        ):
            pending_reference = pending.get("roommate_reference_id")
            previous = next((turn for turn in history if turn["id"] == pending_reference), previous)
        if previous:
            memory["本轮引用的历史"] = await run_in_threadpool(roommate_memory.describe_reference, task_dir, previous)
        has_current_files = bool(message["attachments"] or message.get("other_files"))
        refers_to_inputs = (bool(roommate_memory.INPUT_REFERENCE.search(question) and
                                 not roommate_memory.OUTPUT_REFERENCE.search(question))
                            or bool(previous and any(name.casefold() in question.casefold()
                                                     for name in roommate_memory.input_names(previous))))
        reuse = {}
        if previous and plan.action in {"solve", "lookup", "make_ppt", "export_word"}:
            if not has_current_files and (refers_to_inputs or previous is pending
                                          or not any(previous.get(key) for key in ("roommate_result", "roommate_presentation"))):
                reuse = await run_in_threadpool(roommate_memory.task_attachments, task_dir, message, previous)
                if refers_to_inputs and roommate_memory.input_names(previous) and not reuse:
                    return clarify(C.ROOMMATE_HISTORY_NOT_AVAILABLE)
                message = {**message, **reuse, "roommate_reused_inputs": reuse}
            if not has_current_files and not plan.use_previous:
                plan = plan.model_copy(update={"use_previous": True})
        detail = (await run_in_threadpool(load_output_settings, owner_id)).detail
        task = RoommateTask(owner_id, task_dir, message, history, progress, models, model, memory,
                            output_instruction(owner_id, detail=detail), detail, reference_turn=previous)
        result = await TASK_HANDLERS[plan.action](task, plan)
        result.action = plan.action
        result.reused_inputs = reuse
        if result.needs_clarification and plan.action in {"format_document", "generate_thesis"}:
            result.clarification_action = plan.action
            if previous:
                result.reference_message_id = previous["id"]
        if task.material_notes:
            result.reply = "\n\n".join([result.reply, *task.material_notes])
        return result
    finally:
        await close_text_api(model)
