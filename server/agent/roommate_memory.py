"""Resolve historical replies and project files in the current Roommate conversation."""

import re
from pathlib import Path

from server import conversation_memory
from server.agent.thesis.word_format import read_word_standard

from . import CONSTANT as C

REFERENCE = re.compile(
    r"刚才|刚刚|上一(?:轮|次|版|条|份|步)|前一(?:轮|版)|上面|之前|前面|继续|接着|"
    r"(?:这|那)(?:道|份|个|篇|张)(?:题|文档|论文|PPT|课件|文件|答案|报告)", re.I,
)
INPUT_REFERENCE = re.compile(r"附件|上传|发给你|发了|接收|收到|题图|素材|资料|原稿|源文件")
OUTPUT_REFERENCE = re.compile(r"输出|产物|生成的|生成了|结果|回复|答复|说了")
RECALL_QUESTION = re.compile(r"在哪|哪里|哪些|什么|叫什么|有啥|有哪|列一下|列出|查看|回顾|复述")
FOLLOWUP_ACTION = re.compile(
    r"(?:然后|并|再)(?:用|修改|重做|生成|制作|回答|解答|导出)|"
    r"^(?:(?:请|帮我|能否|可以)\s*)*(?:根据|用|把|将).*(?:继续|回答|解答|生成|制作|重做|改成|导出)"
)


def input_files(turn: dict, key: str) -> list[dict]:
    return turn.get(key, []) or turn.get("roommate_reused_inputs", {}).get(key, [])


def input_names(turn: dict) -> list[str]:
    return [item["name"] for key in ("attachments", "other_files") for item in input_files(turn, key)]


def output_names(turn: dict) -> list[str]:
    return list(dict.fromkeys([
        *turn.get("artifact_files", []), *turn.get("report_files", []),
        *(Path(name).name for name in turn.get("output_images", [])),
    ]))


def latest_delivery(history: list[dict]) -> dict | None:
    return next((turn for turn in reversed(history)
                 if turn.get("reply") and not turn.get("roommate_clarification")
                 and turn.get("status", "answered") == "answered" and turn.get("roommate_action") != "recall"), None)


def referenced_turn(question: str, history: list[dict], *, use_previous: bool = False,
                    reference_message_id: str = "") -> dict | None:
    question = question.casefold()
    for turn in reversed(history):
        if any(name and name.casefold() in question for name in input_names(turn) + output_names(turn)):
            return turn
    if reference_message_id and use_previous:
        selected = next((turn for turn in history if turn["id"] == reference_message_id), None)
        if selected:
            return selected
    reference = REFERENCE.search(question)
    if not (use_previous or reference):
        return None
    if INPUT_REFERENCE.search(question) and not OUTPUT_REFERENCE.search(question):
        return next((turn for turn in reversed(history) if input_names(turn)), None)
    if history and history[-1].get("roommate_clarification") and use_previous:
        return history[-1]
    return latest_delivery(history)


def task_attachments(task_dir: Path, message: dict, previous: dict) -> dict:
    """Fill missing attachment types using verified files from their original turns."""
    inherited = {}
    question = message["requirements"].casefold()
    selected = {name for name in input_names(previous) if name.casefold() in question}
    root = task_dir.resolve() / "messages"
    for key in ("attachments", "other_files"):
        if message.get(key):
            continue
        available = []
        for file in input_files(previous, key):
            if selected and file["name"] not in selected:
                continue
            source = file.get("source_message_id", previous["id"])
            path = (root / source / file["stored_name"]).resolve()
            if path.is_relative_to(root) and path.is_file():
                available.append({**file, "source_message_id": source})
        if available:
            inherited[key] = available
    return inherited


def word_artifacts(task_dir: Path, turn: dict):
    for folder, key in (("artifacts", "artifact_files"), ("reports", "report_files")):
        root = task_dir.resolve() / folder
        for filename in turn.get(key, []):
            if filename != Path(filename).name or not filename.lower().endswith(".docx"):
                continue
            path = (root / turn["id"] / filename).resolve()
            if path.is_relative_to(root) and path.is_file():
                yield path


def artifact_excerpt(task_dir: Path, turn: dict) -> str:
    if presentation := turn.get("roommate_presentation"):
        deck = presentation["deck"]
        text = "\n".join([deck["title"], *(
            " ".join(value for value in [slide.get("title"), slide.get("subtitle"), *slide.get("points", [])]
                     if isinstance(value, str))
            for slide in deck.get("slides", [])
        )])
        return conversation_memory.excerpts(text, 6000)
    for path in word_artifacts(task_dir, turn):
        try:
            text, _ = read_word_standard(path, 6000)
        except (OSError, ValueError):
            continue
        if text:
            return text
    return ""


def describe_turn(turn: dict, reply_limit: int = 1200) -> dict:
    entry = {"轮次": turn["id"], "请求": conversation_memory.excerpts(turn["requirements"], 1200),
             "答复": conversation_memory.excerpts(turn.get("reply") or "", reply_limit),
             "附件": list(dict.fromkeys(input_names(turn))), "产物": output_names(turn)}
    if action := turn.get("roommate_action"):
        entry["任务"] = action
    return entry


def describe_reference(task_dir: Path, turn: dict) -> dict:
    entry = describe_turn(turn, 4500)
    if text := artifact_excerpt(task_dir, turn):
        entry["产物内容摘录"] = text
    return entry


def build_context(notes: list[str], recent: list[dict], history: list[dict], selected: dict | None) -> dict:
    memory = {"记忆": notes, "最近对话": [describe_turn(turn, 900) for turn in recent],
              "有可导出的正文": any(turn.get("roommate_result") for turn in history),
              "有可修改的演示文稿": any(turn.get("roommate_presentation") for turn in history)}
    delivery = latest_delivery(history)
    if delivery:
        memory["上一轮"] = describe_turn(delivery, 4500)
        if presentation := delivery.get("roommate_presentation"):
            memory["最近交付的是演示文稿"] = presentation["deck"]["title"]
    recent_ids = {turn["id"] for turn in recent}
    older_files = [turn for turn in history if turn["id"] not in recent_ids
                   and (input_names(turn) or output_names(turn))]
    if older_files:
        memory["较早的文件记录"] = [describe_turn(turn, 200) for turn in older_files[-6:]]
    if selected:
        memory["本轮引用的历史"] = describe_turn(selected, 4500)
    return memory


def is_recall(question: str) -> bool:
    return bool(REFERENCE.search(question) and RECALL_QUESTION.search(question)
                and (INPUT_REFERENCE.search(question) or OUTPUT_REFERENCE.search(question) or "文件" in question)
                and not FOLLOWUP_ACTION.search(question))


def recall(task_dir: Path, question: str, history: list[dict]) -> str:
    turn = referenced_turn(question, history, use_previous=True)
    if not turn:
        return C.ROOMMATE_NO_PREVIOUS_TURN
    inputs, outputs = input_names(turn), output_names(turn)
    sections = []
    if OUTPUT_REFERENCE.search(question) and turn.get("reply"):
        sections.append(C.ROOMMATE_PREVIOUS_REPLY.format(reply=turn["reply"]))
        if not re.search(r"在哪|哪里|叫什么", question) and (content := artifact_excerpt(task_dir, turn)):
            sections.append(C.ROOMMATE_ARTIFACT_EXCERPT.format(content=content))
    if inputs:
        sections.append(C.ROOMMATE_PREVIOUS_INPUTS.format(names="、".join(dict.fromkeys(inputs))))
    if outputs:
        sections.append(C.ROOMMATE_PREVIOUS_OUTPUTS.format(names="、".join(outputs)))
    return "\n\n".join(sections) or C.ROOMMATE_PREVIOUS_NO_FILES
