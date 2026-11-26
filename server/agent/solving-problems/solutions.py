"""处理图文题目，检索教材并维护独立的解题会话。"""

import base64
import logging
import os
import re
import threading
from hashlib import sha256
from io import BytesIO
from time import perf_counter, sleep
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import psycopg
from dotenv import dotenv_values
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from PIL import Image
from starlette.concurrency import run_in_threadpool

from server import conversation_memory
from server.General_Tools.image_analysis import read_image_text
from server.agent.runner import initialize_user_model, model_settings, selected_text_model
from server.features_services.output_settings import output_instruction
from server.contracts.requests.conversation import ConversationRequest
from server.contracts.responses.conversation import ConversationListResponse, ConversationResponse
from server.conversations import MAX_IMAGE_SIZE, append_message, delete_conversation, list_conversations, public_conversation, save_reply, write_metadata
from server.data_model import User
from server.features_services.jwt_validation import require_user
from server.features_services.model_settings import ModelConnection, effective_model, load_models

from .drawing import plot_discrete_sequence, plot_function, plot_pole_zero, render_circuit, render_filter_structure
from . import exercise_bank, exercise_images, exercise_retrieval, reviewed_figures
from .knowledge import classify_question, wiki_passages
from .theory_retrieval import theory_passages

from . import CONSTANT


router = APIRouter(prefix="/api/solutions", dependencies=[Depends(require_user)], tags=["solutions"])
ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
POSTGRES_ENV = ROOT / "PostgreSQL" / ".env"
VISION_ENV = ROOT / ".env"
OPENROUTER_VISION_URL = "https://openrouter.ai/api/v1"
DASHSCOPE_VISION_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_VISION_MODEL = "qwen3.5:35b-a3b"
DASHSCOPE_QWEN_VISION_MODEL = "qwen3.5-35b-a3b"
VISION_PROMPT = (CONSTANT.VISION_PROMPT)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
SEARCH_TERMS = ("离散时间傅里叶变换", "傅里叶变换", "傅里叶", "循环卷积", "线性卷积", "卷积", "采样", "混叠", "频谱", "滤波器", "时移", "系统函数", "差分方程", "极零点", "零极点", "单位脉冲响应", "收敛域", "幅频特性", "频率响应", "调制", "解调", "电磁波", "电路")
logger = logging.getLogger("uvicorn.error")


def is_dashscope_url(url: str) -> bool:
    try:
        endpoint = urlsplit(url)
    except ValueError:
        return False
    return endpoint.scheme == "https" and endpoint.netloc.lower() in {
        "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
    }


def vision_model_settings() -> tuple[str, str, str]:
    """解析默认视觉模型及服务，未配置远程服务时使用本地 Ollama。"""
    settings = dotenv_values(VISION_ENV)

    def setting(name):
        return (os.environ.get(name, settings.get(name)) or "").strip()

    remote_url = setting("SOLUTION_VISION_BASE_URL").rstrip("/")
    remote_key = setting("SOLUTION_VISION_API_KEY")
    dashscope_key = setting("DASHSCOPE_API_KEY")
    if not remote_url and dashscope_key:
        remote_url, remote_key = DASHSCOPE_VISION_URL, dashscope_key
    elif remote_url and is_dashscope_url(remote_url) and not remote_key:
        remote_key = dashscope_key
    if not remote_url and remote_key:
        remote_url = OPENROUTER_VISION_URL
    if not remote_key and remote_url in ("", OPENROUTER_VISION_URL):
        if openrouter_key := setting("OPENROUTER_API_KEY"):
            remote_url, remote_key = OPENROUTER_VISION_URL, openrouter_key
    if remote_url:
        model = setting("SOLUTION_VISION_API_MODEL") or QWEN_VISION_MODEL
        if is_dashscope_url(remote_url) and model == QWEN_VISION_MODEL:
            model = DASHSCOPE_QWEN_VISION_MODEL
        elif remote_url == OPENROUTER_VISION_URL and model == QWEN_VISION_MODEL:
            model = "qwen/qwen3.5-35b-a3b"
        return model, remote_url, remote_key
    return os.environ.get("SOLUTION_VISION_MODEL", "qwen2.5vl:3b"), "", ""


def local_vision_url() -> str:
    """返回用于视觉识别的本地 Ollama 地址。"""
    return os.environ.get("KB_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def transcribe_problem_image(content: bytes, diagnostics: dict | None = None, *,
                             model_override: str | None = None, connection: ModelConnection | None = None) -> str:
    """使用当前视觉模型从原图转录题目和数学符号。"""
    if connection:
        transcription = transcribe_with_vision_api(content, connection.base_url,
                                                   connection.api_key.get_secret_value(), model_override, connection)
        if transcription and diagnostics is not None:
            diagnostics["provider"] = f"视觉 API · {model_override}"
        return transcription

    configured_model, remote_url, remote_key = vision_model_settings()
    if remote_url:
        dashscope = is_dashscope_url(remote_url)
        provider = "阿里云百炼" if dashscope else "远程视觉 API"
        service = "阿里云百炼" if dashscope else "视觉 API"
        model = model_override or configured_model
        if dashscope and not remote_key:
            remote_failure = CONSTANT.DASHSCOPE_KEY_MISSING
        elif remote_url == OPENROUTER_VISION_URL and not remote_key.startswith("sk-or-"):
            remote_failure = CONSTANT.OPENROUTER_KEY_INVALID
        else:
            started = perf_counter()
            try:
                transcription = transcribe_with_vision_api(content, remote_url, remote_key, model)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                remote_failure = (f"{service} 拒绝认证（HTTP {status}）" if status in (401, 403)
                                  else f"{service} 返回 HTTP {status}")
            except (httpx.HTTPError, ValueError, OSError) as exc:
                remote_failure = CONSTANT.VISION_REQUEST_FAILED.format(service=service, __name__=type(exc).__name__)
            else:
                if transcription:
                    if diagnostics is not None:
                        diagnostics["provider"] = f"{provider} · {model}"
                    logger.info(CONSTANT.LOG_SOLUTION_VISION_API_PROVIDER_MODEL_COMPLETED,
                                provider, model, perf_counter() - started)
                    return transcription
                remote_failure = CONSTANT.VISION_TRANSCRIPT_EMPTY.format(service=service)
        if diagnostics is not None:
            diagnostics["remote_failure"] = remote_failure
        logger.warning(CONSTANT.LOG_SOLUTION_VISION_API_MODEL_FAILED_USING, model, remote_failure)
    model = (model_override if not remote_url else None) or os.environ.get("SOLUTION_VISION_MODEL", "qwen2.5vl:3b")
    request = {"model": model, "stream": False, "messages": [{"role": "user",
        "content": VISION_PROMPT,
        "images": [base64.b64encode(content).decode("ascii")]}]}
    url = f"{local_vision_url()}/api/chat"
    for attempt in range(2):
        try:
            response = httpx.post(url, json=request, timeout=240, trust_env=False)
            response.raise_for_status()
            result = response.json()
            transcription = str(result.get("message", {}).get("content", "")).strip()
            if not transcription and result.get("done_reason") == "length":
                raise ValueError(CONSTANT.VISION_OUTPUT_TRUNCATED.format(model=model))
            if transcription and diagnostics is not None:
                diagnostics["provider"] = f"本地 Ollama · {model}"
            return transcription
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.HTTPStatusError) as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {500, 502, 503, 504}
            if attempt or not retryable:
                raise
            logger.warning(CONSTANT.LOG_SOLUTION_VISION_TRANSIENT_FAILURE_RETRYING_ONCE, type(exc).__name__)
            sleep(1)
    return ""


def transcribe_with_vision_api(content: bytes, base_url: str, api_key: str, model: str,
                               connection: ModelConnection | None = None, *, prompt: str = VISION_PROMPT) -> str:
    """将图片编码为兼容接口的消息并读取转录结果。"""
    with Image.open(BytesIO(content)) as image:
        mime = Image.MIME.get(image.format)
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            converted = BytesIO()
            image.convert("RGB").save(converted, "PNG")
            content, mime = converted.getvalue(), "image/png"
    encoded = base64.b64encode(content).decode("ascii")
    request = {
        "model": model, "stream": False,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]}],
    }
    if base_url == OPENROUTER_VISION_URL:
        request["reasoning"] = {"enabled": False}
    elif is_dashscope_url(base_url) and model.startswith(("qwen3.5-", "qwen3.6-", "qwen3-vl-")):
        request["enable_thinking"] = False
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{base_url}/chat/completions"
    if connection:
        with connection.client(timeout=35) as client:
            response = client.post(url, json=request, headers=headers)
    else:
        response = httpx.post(url, json=request, headers=headers, timeout=35, trust_env=False)
    response.raise_for_status()
    result = response.json()
    choices = result.get("choices") if isinstance(result, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    choice = choices[0]
    message = choice.get("message")
    if choice.get("finish_reason") == "length" or not isinstance(message, dict):
        return ""
    text = message.get("content")
    return text.strip() if isinstance(text, str) else ""


def knowledge_database_url() -> str | None:
    database_url = os.environ.get("KB_DATABASE_URL")
    if database_url:
        return database_url
    if password := dotenv_values(POSTGRES_ENV).get("KB_PG_PASSWORD"):
        return f"postgresql://kb_admin:{quote(password, safe='')}@127.0.0.1:5435/tongxue_kb"
    return None


def image_number_conflict(expected: int | None, text: str) -> str | None:
    found = {int(marker[1]) for marker in exercise_retrieval.NUMBERED.finditer(text)}
    if expected is not None and found and expected not in found:
        numbers = "、".join(str(number) for number in sorted(found))
        return CONSTANT.QUESTION_IMAGE_NUMBER_MISMATCH.format(expected=expected, numbers=numbers)
    return None


async def lookup_exercise(lookup, database_url, *args, **kwargs):
    if database_url:
        try:
            return await run_in_threadpool(lookup, database_url, *args, **kwargs)
        except (psycopg.Error, httpx.HTTPError, ValueError) as exc:
            logger.info(CONSTANT.LOG_EXERCISE_LOOKUP_UNAVAILABLE, type(exc).__name__)
    return None


def textbook_passages(query: str, subject: str = "数字信号处理") -> list[dict]:
    database_url = knowledge_database_url()
    if not database_url:
        raise RuntimeError(CONSTANT.TEXTBOOK_DATABASE_UNCONFIGURED)
    model = os.environ.get("KB_EMBEDDING_MODEL", "bge-m3")
    if subject == "数字信号处理":
        try:
            theory = theory_passages(query, subject, database_url, model)
        except psycopg.errors.UndefinedTable:
            theory = None
        if theory is not None:
            return theory
    exercise_numbers = list(dict.fromkeys(re.findall(r"(?<![\d.])(\d{1,2})\s*\*?\s*[.．、，。](?!\d)", query)))[:4]
    number_patterns = [rf"(^|\n)[[:space:]]*{number}[[:space:]]*\*?[.．、，。]" for number in exercise_numbers]
    terms = [term for term in SEARCH_TERMS if term.lower() in query.lower()]
    if not terms:
        terms = [term for term in re.split(r"[\s，。；、,;:：]+", query) if len(term) >= 3][:3]
    embedding_query = query[:4000]
    if len(query) > 100 and terms:
        concepts = [term for term in terms if not any(term != other and term in other for other in terms)]
        embedding_query = f"{subject} " + " ".join(concepts[:8])
    vector = None
    embedding_error = None
    try:
        response = httpx.post(f"{os.environ.get('KB_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/api/embed",
                              json={"model": model, "input": embedding_query}, timeout=120, trust_env=False)
        response.raise_for_status()
        vectors = response.json().get("embeddings", [])
        if len(vectors) != 1 or len(vectors[0]) != 1024:
            raise ValueError(CONSTANT.TEXTBOOK_EMBEDDING_DIMENSIONS_INVALID)
        vector = "[" + ",".join(str(float(value)) for value in vectors[0]) + "]"
    except (httpx.HTTPError, ValueError) as exc:
        embedding_error = exc
    with psycopg.connect(database_url) as connection:
        columns = "SELECT c.id, c.content, c.page_number, d.title, d.edition, c.needs_review FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id "
        lexical = connection.execute(
            columns + "WHERE d.subject = %s AND (" + " OR ".join(
                [*("c.content ~ %s" for _ in number_patterns), *("c.content ILIKE %s" for _ in terms)]) + ") "
            "ORDER BY " + " + ".join([
                *("(CASE WHEN c.content ~ %s THEN 4 ELSE 0 END)" for _ in number_patterns),
                *("(CASE WHEN c.content ILIKE %s THEN 1 ELSE 0 END)" for _ in terms),
            ]) + " DESC, c.page_number LIMIT 3",
            (subject, *number_patterns, *(f"%{term}%" for term in terms),
             *number_patterns, *(f"%{term}%" for term in terms)),
        ).fetchall() if number_patterns or terms else []
        semantic = connection.execute(
            columns + "WHERE d.subject = %s AND c.embedding IS NOT NULL AND c.embedding_model = %s "
            "ORDER BY c.embedding <=> %s::vector LIMIT 5", (subject, model, vector),
        ).fetchall() if vector else []
    if embedding_error and not lexical:
        raise RuntimeError(CONSTANT.TEXTBOOK_SEARCH_UNAVAILABLE) from embedding_error
    rows = list(dict.fromkeys(row[0] for row in ([*lexical, *semantic] if exercise_numbers else [*semantic, *lexical])))
    matches = {row[0]: row for row in [*lexical, *semantic]}
    return [{"text": matches[chunk_id][1][:1200], "page": matches[chunk_id][2],
             "title": matches[chunk_id][3], "edition": matches[chunk_id][4],
             "needs_review": matches[chunk_id][5]} for chunk_id in rows[:5]]


def answer_prompt(metadata: dict, question: str, image_text: str, passages: list[dict],
                  wiki: list[dict] | None = None, classification: dict | None = None,
                  retrieval_status: str = "", vision_text: str = "") -> str:
    history, recent = conversation_memory.context_lines(metadata, metadata["messages"][:-1])
    for message in recent:
        history.append(f"用户：{message['requirements']}")
        if message.get("reply"):
            history.append(f"助手：{message['reply'][:1500]}")
    sources = []
    for item in passages:
        source = f"《{item['title']}》{item['edition']} PDF 第{item['page']}页{'（提取内容待复核）' if item['needs_review'] else ''}"
        if item.get("kind"):
            source += f" {item['kind']}：{item['concept']}"
            if item.get("formula"):
                source += f"\n教材公式{'（待对照原页核对）' if item['needs_review'] else ''}：{item['formula']}"
            source += f"\n佐证原文：{item['text']}"
            if item.get("figure_path"):
                source += "\n该图保留在所引 PDF 页图中；文字描述不能代替图像核对。"
        else:
            source += f"：\n{item['text']}"
        sources.append(source)
    wiki_sources = [f"GBrain Wiki《{item['title']}》：\n{item['text']}" for item in (wiki or [])]
    recognition = metadata["messages"][-1].get("image_recognition") or {}
    recognition_note = ("视觉转录不可用；图片文字仅来自本地 OCR，公式、上下标和数字需对照原图复核，"
                        "看不清的条件应明确说明并请用户补充。") if recognition and not vision_text else ""
    if any(word in (question + vision_text + image_text) for word in ("直接型", "级联型", "结构图")):
        recognition_note += ("\n结构题核对：题图若含独立常数项，即使与所要求的线性系统结构矛盾，"
                             "也应按图保留原式并指出矛盾；只有用户确认修正后才能给出该修正版的结构。")
    return "\n\n".join([*history, f"当前问题：{question}",
                         f"识别结果：{classification or {}}",
                         f"知识库检索状态：{retrieval_status}",
                         f"题目图片 OCR（可能有误）：{image_text or '未识别到文字'}",
                         f"原图视觉转录（不确定处仍须核对）：{vision_text or '未使用'}",
                         recognition_note,
                         "教材理论检索条目：\n" + ("\n\n".join(sources) if sources else "本次没有可用教材条目"),
                         "GBrain Wiki：\n" + ("\n\n".join(wiki_sources) if wiki_sources else "本次没有可用知识页")])


async def answer_with_tools(model, prompt: str, task_dir: Path, message_id: str, *,
                            system_prompt: str = CONSTANT.SOLUTION_SYSTEM_PROMPT) -> str:
    figures: dict[str, list[str]] = {}

    def assign(path: str, question_number: str) -> str:
        if question_number.strip():
            figures.setdefault(question_number.strip(), []).append(path)
        return path

    @tool("plot_function")
    def plot_math_function(expression: str, x_min: float = -10, x_max: float = 10,
                           title: str = "函数图", x_label: str = "x", y_label: str = "y", question_number: str = "") -> str:
        """绘制函数曲线，expression 使用 x、sin、cos、exp、sqrt，question_number 为所属大题编号。"""
        return assign(plot_function(task_dir, message_id, expression, x_min, x_max, title, x_label, y_label), question_number)

    @tool("plot_discrete_sequence")
    def draw_discrete_sequence(samples: list[float], start_index: int = 0, title: str = "离散序列", question_number: str = "") -> str:
        """绘制离散时间序列的茎状图，samples 按 n 从 start_index 起排列，question_number 为大题编号。"""
        return assign(plot_discrete_sequence(task_dir, message_id, samples, start_index, title), question_number)

    @tool("plot_pole_zero")
    def draw_pole_zero(zeros: list[list[float]], poles: list[list[float]], title: str = "极零点分布", question_number: str = "") -> str:
        """绘制零极点和单位圆，坐标使用 [实部, 虚部]，question_number 为大题编号。"""
        return assign(plot_pole_zero(task_dir, message_id, zeros, poles, title), question_number)

    @tool("render_circuit")
    def draw_circuit(components: list[dict], wires: list[dict], tikz_source: str = "", question_number: str = "") -> str:
        """绘制电路图。元件字段为 type,x,y,label，导线字段为 start,end。question_number 为大题编号。"""
        return assign(render_circuit(task_dir, message_id, components, wires, tikz_source), question_number)

    @tool("render_filter_structure")
    def draw_filter_structure(numerator: list[str], denominator: list[str], structure: str = "direct",
                              sections: list[dict] | None = None, question_number: str = "") -> str:
        """绘制直接 II 型或级联型结构。系数按 z^-1 幂次升序，级联节含 numerator、denominator，question_number 为大题编号。"""
        return assign(render_filter_structure(task_dir, message_id, numerator, denominator, structure, sections), question_number)

    system = system_prompt
    if not hasattr(model, "bind_tools"):
        response = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=prompt)])
    else:
        workflow = create_agent(model, tools=[plot_math_function, draw_discrete_sequence, draw_pole_zero, draw_circuit,
                                              draw_filter_structure], system_prompt=system)
        messages = (await workflow.ainvoke({"messages": [HumanMessage(content=prompt)]},
                                           config={"recursion_limit": 24}))["messages"]
        text = "\n\n".join(text for message in messages if isinstance(message, AIMessage)
                            if (text := message.content if isinstance(message.content, str) else "\n".join(
                                block.get("text", "") for block in message.content if isinstance(block, dict))).strip())
        return reviewed_figures.place_figures(text, figures)
    return response.content if isinstance(response.content, str) else "\n".join(
        block.get("text", "") for block in response.content if isinstance(block, dict))


@router.get("", response_model=ConversationListResponse)
def list_solutions(user: User = Depends(require_user)):
    return {"items": list_conversations(user.id, kind="solution")}


@router.delete("/{conversation_id}", status_code=204)
def delete_solution(conversation_id: str, user: User = Depends(require_user)):
    delete_conversation(DATA_DIR, conversation_id, user.id, kind="solution")


@router.get("/{conversation_id}/images/{message_id}/{filename}")
def solution_image(conversation_id: str, message_id: str, filename: str, user: User = Depends(require_user)):
    from server.conversations import find_conversation
    task_dir, metadata = find_conversation(DATA_DIR, conversation_id, user.id)
    if metadata.get("kind") != "solution":
        raise HTTPException(status_code=404, detail=CONSTANT.IMAGE_NOT_FOUND)
    message = next((item for item in metadata["messages"] if item["id"] == message_id), None)
    relative = f"solutions/{message_id}/{filename}"
    if not message or filename != Path(filename).name or relative not in message.get("output_images", []):
        raise HTTPException(status_code=404, detail=CONSTANT.IMAGE_NOT_FOUND)
    path = (task_dir / relative).resolve()
    if not path.is_relative_to(task_dir.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail=CONSTANT.IMAGE_NOT_FOUND)
    return FileResponse(path, media_type="image/png")


@router.get("/{conversation_id}/input-images/{message_id}")
def solution_input_image(conversation_id: str, message_id: str, user: User = Depends(require_user)):
    from server.conversations import find_conversation
    task_dir, metadata = find_conversation(DATA_DIR, conversation_id, user.id)
    if metadata.get("kind") != "solution":
        raise HTTPException(status_code=404, detail=CONSTANT.IMAGE_NOT_FOUND)
    message = next((item for item in metadata["messages"] if item["id"] == message_id), None)
    attachment = message["attachments"][0] if message and message.get("attachments") else None
    if not attachment or Path(attachment.get("stored_name", "")).name != attachment.get("stored_name"):
        raise HTTPException(status_code=404, detail=CONSTANT.IMAGE_NOT_FOUND)
    path = (task_dir / "messages" / message_id / attachment["stored_name"]).resolve()
    if not path.is_relative_to(task_dir.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail=CONSTANT.IMAGE_NOT_FOUND)
    return FileResponse(path)


@router.post("", status_code=201, response_model=ConversationResponse)
async def create_solution(
    background_tasks: BackgroundTasks,
    question: str = Form(""),
    image: UploadFile | None = File(None),
    conversation_id: str | None = Form(None),
    request_id: str | None = Form(None, max_length=64),
    user: User = Depends(require_user),
):
    started_at = perf_counter()
    stage_started = started_at
    stage_seconds = {}
    logger.info(CONSTANT.LOG_MODULE_SOLVING_PROBLEMS_ROUTE_POST_API,
                threading.get_native_id(), conversation_id or "new")
    question = question.strip()
    if not question and image is None:
        raise HTTPException(status_code=422, detail=CONSTANT.QUESTION_REQUIRED)
    saved_models = await run_in_threadpool(load_models, user.id)
    vision_config = saved_models.vision
    image_text = ""
    vision_text = ""
    vision_used = False
    vision_failure = None
    vision_diagnostics = {}
    exercise_match_status = None
    ocr_result = None
    image_hash = None
    ink = None
    previous = None
    exact_image = False
    database_url = knowledge_database_url()
    bank_reply = None
    candidates = []
    task_dir = metadata = None
    request = ConversationRequest(requirements=question or "请解答图片中的题目", software_id="solve",
                                  deliverables=[], conversation_id=conversation_id, request_id=request_id,
                                  attachments=[image] if image else [])
    figure_builder = None
    reused_files = []
    lookup_query, lookup_requested, solve_requested = exercise_bank.parse_lookup_request(question)
    expected_number = exercise_bank.requested_number(question)
    lookup_only = lookup_requested and not solve_requested
    use_image_question = not question or (lookup_requested and not lookup_query) or exercise_bank.is_presentation(question)
    use_text_question = bool(lookup_query) if lookup_requested else bool(
        exercise_bank.REFERENCE.fullmatch(exercise_bank.normalize(lookup_query))
    )
    if image is not None:
        if not image.filename or Path(image.filename).suffix.lower() not in IMAGE_EXTENSIONS:
            raise HTTPException(status_code=422, detail=CONSTANT.QUESTION_IMAGE_UNSUPPORTED)
        content = await image.read(MAX_IMAGE_SIZE + 1)
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=413, detail=CONSTANT.QUESTION_IMAGE_TOO_LARGE)
        image_hash = sha256(content).digest()
        if use_image_question:
            ink = await run_in_threadpool(exercise_images.image_ink, content)
            identity = await run_in_threadpool(exercise_images.find_image_reference, ink)
            if identity:
                chapter, number = identity
                if expected_number is not None and expected_number != number:
                    bank_reply = f"你请求的是第 {expected_number} 题，上传图匹配到第 {chapter} 章第 {number} 题。请核对附件。"
                else:
                    reference = f"《数字信号处理》第四版 第{chapter}章第{number}题"
                    bank_reply = await lookup_exercise(exercise_bank.find_textbook, database_url, reference, question_only=lookup_only)
                    if bank_reply and not lookup_only:
                        figure_builder = reviewed_figures.FIGURE_BUILDERS.get(identity)
        if image_hash == reviewed_figures.FILTER_IMAGE and (
            not question or reviewed_figures.FILTER_REQUEST.fullmatch(question)
        ):
            bank_reply = ""
            figure_builder = reviewed_figures.filter_answer
        if bank_reply is None:
            previous = await lookup_exercise(exercise_bank.find_image, database_url, user.id, image_hash)
            exact_image = previous is not None
            if previous is None and ink is not None:
                previous = await lookup_exercise(exercise_images.find_similar, database_url, user.id, ink)
                if previous:
                    stage_seconds["similar_image_lookup"] = round(perf_counter() - stage_started, 1)
        if exact_image and previous and not previous.uncertain and not previous.has_image_template and ink is not None:
            descriptor = await run_in_threadpool(exercise_images.encode_template, ink)
            if descriptor:
                background_tasks.add_task(exercise_bank.save_image_template, database_url, user.id, image_hash, descriptor)
        stage_seconds["image_exercise_lookup" if bank_reply is None else "exercise_lookup"] = round(perf_counter() - stage_started, 1)
        stage_started = perf_counter()
        await image.seek(0)
        if bank_reply is None and previous and not previous.uncertain and not use_text_question:
            vision_text = previous.transcription
            bank_reply = image_number_conflict(expected_number, vision_text)
            if (bank_reply is None and not lookup_only and previous.reusable_answer and not conversation_id
                    and question == previous.request_text):
                if previous.figure_source:
                    reused_files = await run_in_threadpool(reviewed_figures.cached_figures, DATA_DIR, previous.figure_source)
                if not previous.figure_source or reused_files:
                    bank_reply = "根据本地题库中的历史解答（模型生成）\n\n" + previous.answer
        elif bank_reply is None:
            try:
                ocr_result = await run_in_threadpool(read_image_text, content)
                image_text = ocr_result["text"]
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=CONSTANT.QUESTION_IMAGE_UNREADABLE) from exc
            stage_seconds["image_ocr"] = round(perf_counter() - stage_started, 1)
            stage_started = perf_counter()
            ocr_agrees = (exercise_bank.normalize(ocr_result.get("ocr_original", image_text))
                          == exercise_bank.normalize(ocr_result.get("ocr_enhanced", image_text)))
            if use_text_question or (use_image_question and not ocr_result.get("needs_review") and ocr_agrees):
                bank_reply = await lookup_exercise(
                    exercise_bank.find_textbook, database_url, lookup_query if use_text_question else image_text,
                    from_image=not use_text_question, question_only=lookup_only,
                )
                stage_seconds["exercise_lookup"] = round(perf_counter() - stage_started, 1)
                stage_started = perf_counter()
            if bank_reply is None and use_image_question:
                candidates = await lookup_exercise(exercise_retrieval.search, database_url, image_text, expected_number) or []
                if not image_number_conflict(expected_number, image_text) and (
                        match := exercise_retrieval.high_confidence(candidates)):
                    bank_reply = match.candidate.reply(lookup_only)
                    exercise_match_status = match.summary()
                    if not lookup_only:
                        figure_builder = reviewed_figures.FIGURE_BUILDERS.get(match.candidate.identity)
                stage_seconds["exercise_candidate_lookup"] = round(perf_counter() - stage_started, 1)
                stage_started = perf_counter()
            if bank_reply is None:
                preview = exercise_retrieval.describe(candidates)
                if preview:
                    task_dir, metadata = await append_message(DATA_DIR, request, user.id, kind="solution")
                    metadata["messages"][-1].update(status="processing", exercise_candidates=preview)
                    await run_in_threadpool(write_metadata, task_dir, metadata, user.id)
                    stage_seconds["message_save"] = round(perf_counter() - stage_started, 1)
                    stage_started = perf_counter()
                vision_failure = CONSTANT.VISION_NO_TEXT
                try:
                    if vision_config.model or vision_config.connection:
                        vision_model, vision_connection = effective_model("vision", vision_config)
                        vision_text = await run_in_threadpool(transcribe_problem_image, content, vision_diagnostics,
                                                              model_override=vision_model,
                                                              connection=vision_connection if vision_connection.custom else None)
                    else:
                        vision_text = await run_in_threadpool(transcribe_problem_image, content, vision_diagnostics)
                    vision_used = bool(vision_text)
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    if vision_config.connection:
                        logger.warning(CONSTANT.CONFIGURED_VISION_FAILED, type(exc).__name__)
                        vision_failure = CONSTANT.CHECK_CONFIGURED_VISION_MODEL
                    elif isinstance(exc, httpx.HTTPStatusError):
                        logger.warning(CONSTANT.LOG_SOLUTION_VISION_HTTP, exc.response.status_code, exc.response.text[:300])
                        vision_failure = "模型未安装" if exc.response.status_code == 404 else f"Ollama HTTP {exc.response.status_code}"
                    else:
                        logger.warning(CONSTANT.LOG_SOLUTION_VISION_FAILED, type(exc).__name__, str(exc)[:300])
                        vision_failure = "无法连接 Ollama" if isinstance(exc, httpx.ConnectError) else (
                            "视觉模型处理超时" if isinstance(exc, httpx.TimeoutException) else (
                                str(exc)[:150] if isinstance(exc, ValueError) else type(exc).__name__))
                if not vision_text and not (image_text.strip() or question):
                    if vision_config.connection:
                        raise HTTPException(status_code=503, detail=CONSTANT.VISION_RECOGNITION_FAILED.format(vision_failure=vision_failure))
                    raise HTTPException(status_code=503, detail=CONSTANT.OLLAMA_VISION_RECOGNITION_FAILED.format(vision_failure=vision_failure))
                if not vision_text:
                    logger.warning(CONSTANT.LOG_SOLUTION_VISION_UNAVAILABLE_CONTINUING, "OCR" if image_text.strip() else "question text")
                stage_seconds["image_vision"] = round(perf_counter() - stage_started, 1)
                stage_started = perf_counter()
        if bank_reply is None and expected_number is not None:
            bank_reply = image_number_conflict(expected_number, vision_text)
        if (bank_reply is None and use_image_question and vision_text
                and "[不确定]" not in exercise_bank.normalize(vision_text)):
            bank_reply = await lookup_exercise(exercise_bank.find_textbook, database_url, vision_text,
                                               from_image=True, question_only=lookup_only)
            stage_seconds["vision_exercise_lookup"] = round(perf_counter() - stage_started, 1)
        if bank_reply is None and use_image_question and vision_text:
            stage_started = perf_counter()
            vision_candidates = await lookup_exercise(
                exercise_retrieval.search, database_url, vision_text, expected_number)
            if vision_candidates:
                candidates = vision_candidates
                if match := exercise_retrieval.high_confidence(candidates):
                    bank_reply = match.candidate.reply(lookup_only)
                    exercise_match_status = match.summary()
                    if not lookup_only:
                        figure_builder = reviewed_figures.FIGURE_BUILDERS.get(match.candidate.identity)
            stage_seconds["vision_exercise_candidate_lookup"] = round(perf_counter() - stage_started, 1)
        if bank_reply is None and lookup_only and previous and vision_text == previous.transcription:
            bank_reply = "**本地题库收录的图片转录**\n\n" + previous.transcription
        stage_started = perf_counter()
    elif lookup_query:
        bank_reply = await lookup_exercise(exercise_bank.find_textbook, database_url, lookup_query, question_only=lookup_only)
        stage_seconds["exercise_lookup"] = round(perf_counter() - stage_started, 1)
        stage_started = perf_counter()
        if bank_reply is None and not exercise_bank.REFERENCE.fullmatch(exercise_bank.normalize(lookup_query)):
            subject = classify_question(lookup_query)["subject"]
            if lookup_requested or subject == "数字信号处理":
                candidates = await lookup_exercise(exercise_retrieval.search, database_url, lookup_query, expected_number) or []
                stage_seconds["exercise_candidate_lookup"] = round(perf_counter() - stage_started, 1)
                stage_started = perf_counter()

    candidate_text = exercise_retrieval.describe(candidates)
    vision_status = "。".join(value for value in (
        vision_diagnostics.get("remote_failure"), vision_diagnostics.get("provider"),
        vision_failure if not vision_text else None,
        exercise_match_status,
    ) if value) or None
    if lookup_only and bank_reply is None:
        if image is None and not lookup_query:
            bank_reply = "请上传题图，或提供教材名称、章节和题号。例如《数字信号处理》第四版 第3章第25题。"
        elif not database_url:
            bank_reply = "本次无法查询原题，题库数据库尚未配置。"
        elif candidate_text:
            bank_reply = "本次未能在题库确认原题。以下是向量检索得到的相似题目，可按教材页码核对。"
            bank_reply += "\n\n" + candidate_text
        else:
            bank_reply = "本次未能在题库确认原题。可以补充教材名称、版次、章节和题号，或提供完整题干。"

    if bank_reply is not None:
        if metadata is None:
            task_dir, metadata = await append_message(DATA_DIR, request, user.id, kind="solution")
        if figure_builder or reused_files:
            message_id = metadata["messages"][-1]["id"]
            if figure_builder:
                figures = await run_in_threadpool(figure_builder, task_dir, message_id)
                bank_reply = f"{bank_reply}\n\n{figures}".strip()
            else:
                bank_reply = await run_in_threadpool(reviewed_figures.restore_figures, task_dir, message_id, reused_files, bank_reply)
            directory = task_dir / "solutions" / message_id
            metadata["messages"][-1]["output_images"] = sorted(
                path.relative_to(task_dir).as_posix() for path in directory.glob("*.png")
            )
        metadata["messages"][-1].update(stage_seconds=stage_seconds, exercise_candidates=None, vision_status=vision_status)
        save_reply(task_dir, metadata, user.id, bank_reply, processing_seconds=perf_counter() - started_at)
        return public_conversation(metadata)

    if saved_models.text.model or saved_models.text.connection:
        model_name, api_key, connection = selected_text_model(saved_models.text)
        model = initialize_user_model(user.id, model_name, api_key, connection)
    else:
        model_name, api_key = model_settings()
        model = initialize_user_model(user.id, model_name, api_key)
    if model is None:
        if metadata is not None:
            metadata["messages"][-1].update(stage_seconds=stage_seconds, exercise_candidates=candidate_text,
                                             vision_status=vision_status)
            save_reply(task_dir, metadata, user.id, CONSTANT.SOLUTION_MODEL_UNCONFIGURED, status="error",
                       processing_seconds=perf_counter() - started_at)
            return public_conversation(metadata)
        raise HTTPException(status_code=503, detail=CONSTANT.SOLUTION_MODEL_UNCONFIGURED)
    stage_seconds["model_initialization"] = round(perf_counter() - stage_started, 1)
    stage_started = perf_counter()
    search_text = (question + " " + (vision_text or image_text)).strip()
    classification = classify_question(search_text)
    subject = str(classification["subject"])
    try:
        wiki = await run_in_threadpool(wiki_passages, search_text, subject)
    except (OSError, ValueError, RuntimeError):
        wiki = []
    wiki_status = "已返回内容" if wiki else "本次无可用内容或检索降级"
    stage_seconds["wiki_retrieval"] = round(perf_counter() - stage_started, 1)
    stage_started = perf_counter()
    try:
        passages = await run_in_threadpool(textbook_passages, search_text) if subject == "数字信号处理" else (
            await run_in_threadpool(textbook_passages, search_text, subject))
    except (RuntimeError, ValueError, psycopg.Error, httpx.HTTPError):
        passages = []
        rag_status = "服务不可用"
    else:
        rag_status = "已返回内容" if passages else "该学科暂无匹配教材片段"
    stage_seconds["textbook_retrieval"] = round(perf_counter() - stage_started, 1)
    stage_started = perf_counter()
    if metadata is None:
        task_dir, metadata = await append_message(DATA_DIR, request, user.id, kind="solution")
        stage_seconds["message_save"] = round(perf_counter() - stage_started, 1)
    logger.info(CONSTANT.LOG_MODULE_SOLVING_PROBLEMS_THREAD_ID_CONVERSATION,
                threading.get_native_id(), metadata["id"])
    metadata["messages"][-1].update(status="processing", stage_seconds=stage_seconds,
                                    exercise_candidates=candidate_text or None, vision_status=vision_status)
    if image is not None:
        recognition = ocr_result or {}
        metadata["messages"][-1]["image_recognition"] = {
            "ocr_mean_confidence": recognition.get("ocr_mean_confidence"),
            "ocr_needs_review": recognition.get("needs_review"),
            "vision_used": vision_used,
            "vision_text": vision_text,
            "vision_failure": vision_failure if not vision_text else None,
        }
    try:
        write_metadata(task_dir, metadata, user.id)
        stage_started = perf_counter()
        context = await run_in_threadpool(conversation_memory.model_context, metadata, user.id)
        prompt = answer_prompt(context, question or "请解答图片中的题目", image_text, passages, wiki,
                               classification, f"GBrain Wiki：{wiki_status}；教材 RAG：{rag_status}", vision_text)
        if lookup_requested:
            prompt = "本次题库未确认原题。下方教材理论仅供推导，请说明原题匹配结果并按现有题目条件解答。\n\n" + prompt
        if candidate_text:
            prompt += ("\n\n题库向量召回的相似候选仅供对照。每道题按用户输入和题图条件独立解答，"
                       "勿将候选视为已确认原题，勿用候选补齐未识别的公式或图。\n\n" + candidate_text)
        instruction = await run_in_threadpool(output_instruction, user.id)
        reply = await answer_with_tools(model, f"{instruction}\n\n{prompt}", task_dir, metadata["messages"][-1]["id"])
        stage_seconds["answer_generation"] = round(perf_counter() - stage_started, 1)
        if not reply.strip():
            raise ValueError(CONSTANT.MODEL_EMPTY_REPLY)
        if lookup_requested and candidate_text:
            reply += "\n\n---\n\n" + candidate_text
        output_dir = task_dir / "solutions" / metadata["messages"][-1]["id"]
        metadata["messages"][-1]["output_images"] = [path.relative_to(task_dir).as_posix()
                                                        for path in sorted(output_dir.glob("*.png"))]
        save_reply(task_dir, metadata, user.id, reply, processing_seconds=perf_counter() - started_at)
        logger.info(CONSTANT.LOG_MODULE_SOLVING_PROBLEMS_THREAD_ID_CONVERSATION_CONVERSATION_STAGES_COMPLETED,
                    threading.get_native_id(), metadata["id"], stage_seconds)
    except Exception as exc:
        stage_seconds.setdefault("answer_generation", round(perf_counter() - stage_started, 1))
        if saved_models.text.connection:
            logger.error(CONSTANT.CUSTOM_SOLUTION_ANSWER_FAILED_LOG, metadata["messages"][-1]["id"], type(exc).__name__)
        else:
            logger.exception(CONSTANT.LOG_SOLUTION_ANSWER_FAILED_FOR_MESSAGE, metadata["messages"][-1]["id"])
        metadata["messages"][-1]["failure_type"] = type(exc).__name__
        save_reply(task_dir, metadata, user.id, CONSTANT.SOLUTION_MODEL_UNAVAILABLE, status="error",
                   processing_seconds=perf_counter() - started_at)
    else:
        if database_url and image_hash and (vision_used or (exact_image and previous and vision_text and not previous.reusable_answer)):
            output = metadata["messages"][-1]
            figure_source = None
            if output["output_images"]:
                figure_source = {"directory": task_dir.name, "message": output["id"],
                                 "names": [Path(path).name for path in output["output_images"]]}
            reusable = not conversation_id and "[不确定]" not in exercise_bank.normalize(vision_text)
            if reusable:
                if ink is None:
                    ink = await run_in_threadpool(exercise_images.image_ink, content)
                descriptor = await run_in_threadpool(exercise_images.encode_template, ink) if ink is not None else None
            else:
                descriptor = None
            background_tasks.add_task(
                exercise_bank.save_image, database_url, user.id, image_hash,
                exercise_bank.ImageExercise(vision_text, question, reply, reusable, figure_source),
                descriptor,
            )
    return public_conversation(metadata)
