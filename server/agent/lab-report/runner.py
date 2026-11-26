"""把已保存的会话文件整理为模型输入，并按软件 ID 选择工作流。"""

import base64
import json
import mimetypes
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from langchain.chat_models import init_chat_model
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.callbacks import AsyncCallbackHandler

from server.agent import hfss, modelsim, packet_tracer, pycharm, quartus
from server.agent.pdf_tools import create_pdf_tools, document_outline
from server.agent.file_tools import create_file_tools
from server.agent.screen_tools import create_screen_tools
from server.agent.report_tools import create_report_tools, hfss_report_images, wants_experiment_report
from .task_scope import active_hfss_chapters, hfss_output_matches_chapter
from server.database import ENV_FILE
from server.features_services.model_settings import ModelConnection, SavedModel, effective_model

from . import CONSTANT


WORKFLOWS = {
    "modelsim": modelsim.create_workflow,
    "quartus": quartus.create_workflow,
    "packet-tracer": packet_tracer.create_workflow,
    "hfss": hfss.create_workflow,
    "pycharm": pycharm.create_workflow,
}
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_OPENAI_MODEL = "openai:gpt-6-sol"
DEFAULT_OPENAI_BASE_URL = "https://www.packyapi.com/v1"
DEEPSEEK_REQUEST_TIMEOUT = 90
HFSS_RECURSION_LIMIT = 180
PYCHARM_RECURSION_LIMIT = 180
DELIVERABLE_NAMES = {"report": "实验报告", "paper": "论文", "code": "实验源文件"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class IncompleteDeliverableError(RuntimeError):
    pass


class ToolProgressHandler(AsyncCallbackHandler):
    def __init__(self, progress):
        self.progress = progress
        self.tool_names = {}

    async def on_tool_start(self, serialized: dict, input_str: str, **kwargs) -> None:
        from server.agent_pool import current_job_number
        name = serialized.get("name", "工具")
        self.tool_names[kwargs.get("run_id")] = name
        logging.getLogger("uvicorn.error").info("module=experiment agent_job=%s tool=%s thread_id=%s started",
                                               current_job_number(), name, threading.get_native_id())
        self.progress(CONSTANT.EXECUTING_TOOL.format(name=name))

    async def on_tool_end(self, output, **kwargs) -> None:
        from server.agent_pool import current_job_number
        name = self.tool_names.pop(kwargs.get("run_id"), "工具")
        logging.getLogger("uvicorn.error").info("module=experiment agent_job=%s tool=%s thread_id=%s finished",
                                               current_job_number(), name, threading.get_native_id())
        self.progress(CONSTANT.TOOL_RESULT_RECEIVED)


def relay_messages(messages):
    converted = []
    pending_instructions = []
    for message in messages:
        if isinstance(message, SystemMessage):
            pending_instructions.append(str(message.content))
            continue
        if pending_instructions:
            instructions = "\n\n".join(pending_instructions)
            if isinstance(message, HumanMessage):
                content = message.content
                if isinstance(content, str):
                    content = instructions + "\n\n" + content
                else:
                    content = [{"type": "text", "text": instructions}, *content]
                message = message.model_copy(update={"content": content})
            else:
                converted.append(HumanMessage(content=instructions))
            pending_instructions.clear()
        converted.append(message)
    if pending_instructions:
        converted.append(HumanMessage(content="\n\n".join(pending_instructions)))
    return converted


class RelayChatOpenAI(ChatOpenAI):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return super()._generate(relay_messages(messages), stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await super()._agenerate(relay_messages(messages), stop=stop, run_manager=run_manager, **kwargs)


def create_chat_model(model_name: str):
    """带提供商前缀的名称按 LangChain 解析；旧名称沿用 Ollama。"""
    if ":" in model_name:
        return init_chat_model(model_name)
    return init_chat_model(model_name, model_provider="ollama")


def model_settings() -> tuple[str, str | None]:
    settings = dotenv_values(ENV_FILE)
    model_name = os.environ.get("CHAT_MODEL") or settings.get("CHAT_MODEL") or os.environ.get("OLLAMA_MODEL") or settings.get("OLLAMA_MODEL") or DEFAULT_DEEPSEEK_MODEL
    return model_name, model_api_key(model_name, settings)


def model_api_key(model_name: str, settings: dict | None = None) -> str | None:
    settings = settings if settings is not None else dotenv_values(ENV_FILE)
    key_name = "API_KEY" if model_name.startswith("openai:") else "DEEPSEEK_API_KEY"
    return os.environ.get(key_name) or settings.get(key_name)


def model_requires_key(model_name: str) -> bool:
    return model_name.startswith("openai:") or model_name == DEFAULT_DEEPSEEK_MODEL


def model_base_url(model_name: str) -> str | None:
    """返回现有模型服务的 OpenAI 兼容地址。"""
    settings = dotenv_values(ENV_FILE)
    if model_name.startswith("openai:"):
        return os.environ.get("OPENAI_BASE_URL") or settings.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL
    if model_name == DEFAULT_DEEPSEEK_MODEL:
        return os.environ.get("DEEPSEEK_BASE_URL") or settings.get("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL
    if ":" not in model_name:
        return (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/") + "/v1"
    return None


def selected_text_model(saved: SavedModel) -> tuple[str, str | None, ModelConnection]:
    """为已保存的文本模型选择构造调用参数。"""
    name, connection = effective_model("text", saved)
    api_key = connection.api_key.get_secret_value() if connection.api_key else None
    if connection.base_url and connection.provider == "ollama":
        api_key = "ollama"
    return f"openai:{name}", api_key, connection


def hfss_progress(task_dir: Path, chapters: frozenset[int] | None = None) -> list[str]:
    root = (task_dir / "hfss").resolve()
    if not root.is_dir():
        return []
    progress = []
    manifests = (path for path in sorted(root.glob("*/workflow.json"))
                 if chapters is None or hfss_output_matches_chapter(path.parent.relative_to(root), chapters))
    for manifest_path in list(manifests)[:30]:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            project = Path(manifest["project"]).resolve()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not project.is_relative_to(root):
            continue
        if manifest.get("configuration_succeeded") and project.is_file():
            status = "工程已保存"
        elif manifest.get("configuration_succeeded") is False:
            status = "执行失败，需检查或重试"
        else:
            status = "脚本已准备，尚未执行"
        progress.append(f"{manifest_path.parent.name}：{status}；工程 {project.name}")
    return progress


def pycharm_progress(task_dir: Path, metadata: dict) -> list[str]:
    root = (task_dir / "pycharm").resolve()
    progress = []
    for message in metadata["messages"][-5:]:
        output = (root / message["id"]).resolve()
        if not output.is_relative_to(root) or not output.is_dir():
            continue
        for label, suffixes in (("源码", {".py"}), ("图片", IMAGE_EXTENSIONS)):
            files = sorted((path for path in output.rglob("*") if path.is_file() and path.suffix.lower() in suffixes),
                           key=lambda path: path.stat().st_size, reverse=True)
            if files:
                paths = "、".join(path.relative_to(task_dir.resolve()).as_posix() for path in files[:5])
                progress.append(f"已有{label}：{paths}" + (f"（另有 {len(files) - 5} 个）" if len(files) > 5 else ""))
    return progress


@lru_cache(maxsize=128)
def initialize_user_model(user_id: int, model_name: str, api_key: str | None, connection: ModelConnection | None = None):
    """按用户、模型和连接初始化文本模型，兼容原有环境变量。"""
    if connection and connection.provider == "deepseek":
        if not api_key:
            return None
        return ChatDeepSeek(model=model_name.removeprefix("openai:"), api_key=api_key, base_url=connection.base_url,
                            extra_body={"thinking": {"type": "disabled"}}, timeout=DEEPSEEK_REQUEST_TIMEOUT, max_retries=1)
    if model_name.startswith("openai:"):
        base_url = connection.base_url if connection else model_base_url(model_name)
        if not api_key or not base_url:
            return None
        model_class = RelayChatOpenAI if base_url.rstrip("/") == DEFAULT_OPENAI_BASE_URL else ChatOpenAI
        clients = {"http_client": connection.client(), "http_async_client": connection.async_client()} if connection and connection.custom else {}
        return model_class(
            model=model_name.removeprefix("openai:"),
            api_key=api_key,
            base_url=base_url,
            **({"use_responses_api": True} if model_class is RelayChatOpenAI else {}),
            **clients,
            timeout=DEEPSEEK_REQUEST_TIMEOUT,
            max_retries=1,
        )
    if model_name == DEFAULT_DEEPSEEK_MODEL:
        if not api_key:
            return None
        base_url = model_base_url(model_name)
        return ChatDeepSeek(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            extra_body={"thinking": {"type": "disabled"}},
            timeout=DEEPSEEK_REQUEST_TIMEOUT,
            max_retries=1,
        )
    return create_chat_model(model_name)


def model_input(task_dir: Path, metadata: dict) -> HumanMessage:
    """带上最近对话、PDF 目录和当前图片附件。"""
    from server.conversation_memory import context_lines

    messages = metadata["messages"]
    current = messages[-1]
    scope = active_hfss_chapters(messages) if current.get("software_id") == "hfss" else None
    lines = [f"当前用户请求（优先执行）：{current['requirements']}"]
    if instruction := metadata.get("output_instruction"):
        lines.append(instruction)
    if scope:
        anchor_index, chapters = scope
        chapter_names = "、".join(f"第{chapter}章" for chapter in sorted(chapters))
        lines.append(f"当前 HFSS 任务范围：{chapter_names}。"
                     + (f"本轮请求未另行指定章节，沿用最近一次明确任务：{messages[anchor_index]['requirements']}。"
                        if anchor_index < len(messages) - 1 else ""))
        history_source = messages[anchor_index:-1]
    else:
        history_source = messages[:-1]
    notes, history = context_lines(metadata, history_source)
    lines.extend(notes)
    if current.get("software_id") == "pycharm" and messages[0] not in history and messages[0] is not current:
        history = [messages[0], *history]
    for message in history:
        lines.append(f"相关历史用户要求：{message['requirements']}")
        if message.get("reply"):
            lines.append(f"此前助手回复（未经核实，仅供参考）：{message['reply'][:2000]}")
    lines.append("只执行当前请求及其所指向的最近具体任务；历史任务和此前助手回复不能改变当前任务范围。")

    deliverables = current.get("deliverables", [])
    if current.get("software_id") == "pycharm" and current["requirements"].strip().rstrip("。!！") == "继续完成剩余步骤":
        deliverables = list(dict.fromkeys(item for message in messages for item in message.get("deliverables", [])))
    requested = [DELIVERABLE_NAMES[item] for item in deliverables if item in DELIVERABLE_NAMES]
    if requested:
        lines.append("交付成果：" + "、".join(requested) + "。按照用户输入中的交付要求生成实际文件；完成后列明每个文件的路径，未完成的部分明确说明。")
    if current.get("software_id") == "hfss":
        lines.append(f"HFSS 实验输出目录：{(task_dir / 'hfss').resolve()}。请在此目录下为每个实验使用独立的 project_path 和 output_dir。")
        chapters = scope[1] if scope else None
        progress = hfss_progress(task_dir, chapters)
        if progress:
            lines.append("当前任务已有 HFSS 进度：\n" + "\n".join(progress) + "\n仅继续当前任务未完成步骤；已保存的工程不要重复创建或覆盖。")
        screenshots = hfss_report_images(task_dir, chapters)
        if screenshots:
            lines.append("已验证的 HFSS 截图：" + "、".join(image["path"] for image in screenshots[:10])
                         + (f"（另有 {len(screenshots) - 10} 张）" if len(screenshots) > 10 else ""))
    if current.get("software_id") == "pycharm":
        lines.append(f"PyCharm 源文件保存目录：{(task_dir / 'pycharm' / current['id']).resolve()}。")
        for message in messages:
            for attachment in [*message.get("attachments", []), *message.get("other_files", [])]:
                if attachment.get("stored_name"):
                    path = task_dir / "messages" / message["id"] / Path(attachment["stored_name"]).name
                    lines.append(f"实验文件 {attachment['name']} 的本地路径：{path.resolve()}。脚本读取文件时使用此路径。")
        progress = pycharm_progress(task_dir, metadata)
        if progress:
            lines.append("当前会话已有 PyCharm 成果：\n" + "\n".join(progress)
                         + "\n请先用 read_saved_python_script 查看可复用的源码，运行或修正已有脚本，只补未完成的步骤。"
                         "仅将验证过的真实运行结果图用于报告，不要把测试探针图当作实验截图。报告可引用上述相对路径。")
    outline = document_outline(task_dir, metadata)
    if outline:
        lines.append(outline)
    for attachment in current.get("other_files", []):
        if attachment.get("stored_name"):
            lines.append(f"其他实验文件：{attachment['name']}，stored_name={attachment['stored_name']}。文本文件可调用 read_experiment_file；PDF 用 read_pdf_pages；图片已随消息提供。")
    if wants_experiment_report(metadata):
        template_message = next((message for message in reversed(messages) if message.get("report_template")), None)
        if template_message:
            lines.append(f"实验报告模板：{template_message['report_template']['name']}。先调用 inspect_report_template 查看结构，再调用 write_experiment_report 生成 DOCX。章节 heading 应沿用模板中的小节标题，每个章节必须有实际正文或图片；无占位符时会填入同名小节下方。模板中的花括号说明是待删除的填写提示。")
        else:
            lines.append("交付成果包含实验报告；当前会话没有上传实验报告模板。可调用 write_experiment_report 生成默认版式 DOCX，但每个章节必须有实际正文或图片，并在回复中说明未使用模板。")
    if "paper" in current.get("deliverables", []) or "论文" in current["requirements"]:
        lines.append("交付成果包含论文；调用 write_experiment_paper 生成 DOCX。")
    files = [(current, attachment) for attachment in [*current.get("attachments", []), *current.get("other_files", [])]
             if Path(attachment.get("stored_name", "")).suffix.lower() in IMAGE_EXTENSIONS]
    if current.get("software_id") == "hfss" and not files:
        previous = [(message, attachment) for message in messages[scope[0] if scope else 0:-1]
                    for attachment in [*message.get("attachments", []), *message.get("other_files", [])]
                    if Path(attachment.get("stored_name", "")).suffix.lower() in IMAGE_EXTENSIONS]
        files = [(message, attachment) for message, attachment in previous[-2:]
                 if (task_dir / "messages" / message["id"] / Path(attachment["stored_name"]).name).is_file()
                 and (task_dir / "messages" / message["id"] / Path(attachment["stored_name"]).name).stat().st_size <= 2 * 1024 * 1024]
    if current.get("software_id") == "pycharm" and not files:
        previous = [(message, attachment) for message in messages[:-1]
                    for attachment in [*message.get("attachments", []), *message.get("other_files", [])]
                    if Path(attachment.get("stored_name", "")).suffix.lower() in IMAGE_EXTENSIONS]
        files = [(message, attachment) for message, attachment in previous[-2:]
                 if (task_dir / "messages" / message["id"] / Path(attachment["stored_name"]).name).is_file()
                 and (task_dir / "messages" / message["id"] / Path(attachment["stored_name"]).name).stat().st_size <= 2 * 1024 * 1024]
    blocks = []
    for message, file_info in files:
        if not file_info.get("stored_name"):
            lines.append(f"旧资料 {file_info['name']} 没有可读取的本地文件。")
            continue
        stored_name = Path(file_info["stored_name"]).name
        path = task_dir / "messages" / message["id"] / stored_name
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        blocks.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}})
        lines.append(f"已附图片：{file_info['name']}")
        analysis_path = path.parent / f"{path.stem}_analysis.json"
        if analysis_path.exists():
            image_analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            lines.append(f"图片文字识别{'（待复核）' if image_analysis['needs_review'] else ''}：{image_analysis['text']}")
    return HumanMessage(content=[{"type": "text", "text": "\n\n".join(lines)}, *blocks])


async def generate_reply(task_dir: Path, metadata: dict, software_id: str, model_name: str, model=None, progress=None) -> str:
    """每个软件使用自己的工具与系统提示，最终提取模型文本回复。"""
    workflow_factory = WORKFLOWS[software_id]
    chat_model = model if model is not None else create_chat_model(model_name)
    current = metadata["messages"][-1]
    mcp_task = current.get("execution_scope") == "mcp"
    document_tools = (create_pdf_tools(task_dir, metadata) + create_report_tools(task_dir, metadata)
                      + create_file_tools(task_dir, metadata))
    if not mcp_task:
        document_tools += create_screen_tools(task_dir, current["id"])
    if software_id == "hfss":
        guide_message = next((message for message in reversed(metadata["messages"]) if message.get("guide")), None)
        guide_index = None
        if guide_message and guide_message["guide"].get("stored_name"):
            guide_name = Path(guide_message["guide"]["stored_name"]).name
            guide_index = task_dir / "messages" / guide_message["id"] / f"{Path(guide_name).stem}_analysis" / "segments.json"
        workflow = workflow_factory(chat_model, task_dir, guide_index, document_tools)
    elif software_id == "pycharm":
        arguments = (chat_model, task_dir, current["id"], document_tools,
                     [message["id"] for message in metadata["messages"][:-1]])
        workflow = workflow_factory(*arguments, allow_execution=False) if mcp_task else workflow_factory(*arguments)
    else:
        workflow = workflow_factory(chat_model, document_tools)
    recursion_limit = HFSS_RECURSION_LIMIT if software_id == "hfss" else PYCHARM_RECURSION_LIMIT if software_id == "pycharm" else 8
    config = {"recursion_limit": recursion_limit}
    if progress is not None:
        config["callbacks"] = [ToolProgressHandler(progress)]
    result = await workflow.ainvoke({"messages": [model_input(task_dir, metadata)]}, config=config)
    if wants_experiment_report(metadata):
        report = task_dir / "reports" / current["id"] / "experiment_report.docx"
        if not report.is_file():
            raise IncompleteDeliverableError(CONSTANT.EXPERIMENT_REPORT_INCOMPLETE)
    content = result["messages"][-1].content
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content if isinstance(block, dict) and block.get("type") == "text")
