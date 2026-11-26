"""室友的显式规则、示例向量和文本 API 决策。"""

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from statistics import mean

import httpx
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, SecretStr
from starlette.concurrency import run_in_threadpool

from server import solutions
from server.database import ENV_FILE
from server.features_services.model_settings import ModelConnection

from . import CONSTANT as C
from .CONSTANT import ASK_GOAL, ASK_PROBLEM, ATTACHMENT_REQUEST, ROUTER_PROMPT

logger = logging.getLogger("uvicorn.error")
TOP_K = 5
DEFAULT_THRESHOLD = 0.55
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_BATCH_SIZE = 10
BOOK_REFERENCE = re.compile(r"《(?P<title>[^《》]{1,100})》\s*(?:[（(]?\s*(?P<edition>第[^版\s]{1,10}版)\s*[）)]?)?\s*"
                            r"第\s*(?P<chapter>\d{1,3})\s*章\s*(?:第|习题\s*)?\s*(?P<number>\d{1,4})\s*题")
WORD_EXPORT = re.compile(r"(?:把(?:刚才的|上一版|当前)?(?:题解|解答|答案)\s*)?"
                         r"(?:导出|保存|生成)(?:为|成)?\s*(?:一个\s*)?(?:word|\.?docx)(?:\s*文档)?$", re.I)
PPT = r"(?:(?<![A-Za-z0-9_])pptx?(?![A-Za-z0-9_])|演示文稿|幻灯片)"
MAKE_PPT = re.compile(rf"^(?:(?:根据|用|基于)[^，。！？]{{1,60}})?(?:制作|生成|创建|做)[^。！？]*{PPT}", re.I)
MAKE_THESIS = re.compile(r"^(?:(?:根据|用|基于)[^，。！？]{1,60})?(?:写(?:作)?|撰写|生成|起草|完成|拟定|整理|列)"
                         r"[^，。！？]{0,85}(?:论文(?:初稿|草稿|提纲|大纲|选题|框架)?|文献综述|开题报告)"
                         r"(?=$|[，,：:\s。！？]|并导出|并生成|并写成|[《“])|^(?:帮忙)?(?:给|推荐|设计|拟)(?:我)?[^，。！？]{0,30}论文选题")
FORMAT = re.compile(rf"(?:(?:修改|更改|调整|改一下|检查)(?:这篇|这份)?(?:文档|(?:毕业)?论文|文章|{PPT})?(?:的)?(?:格式|排版)|"
                    rf"(?:给|把|将)?(?:这篇|这份)?(?:文档|(?:毕业)?论文|文章|{PPT})(?:的)?(?:格式|排版|重新排版)|"
                    r"(?:按|按照).{1,50}(?:排版|(?:修改|调整)(?:论文)?格式))(?=$|[，,：:\s。！？])", re.I)
SOLVE = re.compile(r"^(?:解题|解答|求解)(?=$|[这下题第图《\s:：，,。]|\d)")
BARE_PROBLEM = re.compile(r"(?:(?:帮我|请|麻烦)\s*)*(?:(?:解题|解答(?:这道题)?|求解)|"
                          r"这(?:道|个)?题(?:有点难|太难了?|不会做|怎么做|怎么算))\s*[。！？.!?]?")
CONTEXTUAL = re.compile(r"刚才|上一(?:版|轮|次)|继续|^(?:(?:请|帮我|麻烦)\s*)*改成|再(?:加|删|做|作|导出)|这次|接着|"
                        r"第[一二三四1234]张|(?:最后|前|后)[一二两三四1234]张|(?:左|右)边的图")
REMAKE_WITH_WEB_IMAGES = re.compile(
    rf"(?:(?:请|帮我|麻烦|试着|尝试|这次)\s*)*"
    rf"(?:(?:使用|采用|用|加上)\s*)?(?:联网|在线|网络|网上)\s*(?:配图|图片|插图)[，,\s]*"
    rf"(?:再[做作]|重[做作])\s*(?:一(?:份|个|版|套))?\s*(?:{PPT})?\s*[。！!]?", re.I,
)
COMPOUND = re.compile(rf"(?:并|再|然后)(?:帮我)?(?:解答|解题|做题|导出|生成|制作|保存)|"
                      rf"(?:不用|不要|别|暂不|无需)(?:解答|解题|做题|导出|生成|制作|改格式|写论文|撰写论文|做\s*{PPT})", re.I)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=40)
    image_numbers: list[PositiveInt] = Field(default_factory=list, max_length=4)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    generate_answer: bool = False
    use_previous: bool = False
    reference_message_id: str = Field(default="", max_length=64)
    subject: str = Field(default="", max_length=100)
    clarification: str = Field(default="", max_length=500)


@dataclass(frozen=True)
class Route:
    description: str
    examples: tuple[str, ...]
    rule: re.Pattern | None = None


ROUTES = {
    "solve": Route("解答题目、推导、绘图或解释学习概念。解题并要求 Word 时先解题。", (
        "这道题我不太会做，能帮我看看吗", "这题好难，怎么做", "能讲讲这道练习的思路吗",
        "这道题有点难，能告诉我怎么解吗", "这道题太难了，我不会算", "我卡在这题了，想知道怎么算",
        "看不懂这道题的条件，怎么求答案", "能一步一步推导给我看吗", "能解释一下采样定理吗",
        "已知小球的初速度和重力加速度，求最大高度", "计算这个矩阵的特征值", "画出系统的幅频曲线",
        "小球从地面以 12 m/s 竖直向上抛出，计算它能升到多高",
        "已知 3x-5=7，求未知数 x", "分析这个函数在零点附近的变化趋势",
    ), SOLVE),
    "lookup": Route("按书名、章节、题号或关键词查询题库中的原题。", (
        "想找一下这个习题的原文", "老师布置的这道题在教材哪一章", "这题有原题出处吗",
        "看看题库有没有相同的题目", "我想定位课本上的第十道习题",
    )),
    "make_ppt": Route("制作演示文稿，或修改本会话生成的演示文稿。", (
        "我要做课堂展示，能帮忙准备幻灯片吗", "下周要答辩，需要一套演示用的页面",
        "帮我把这些材料整理成演示文稿", "汇报时想分几屏展示这些要点", "会议要放一套投影片",
    ), MAKE_PPT),
    "generate_thesis": Route("生成论文初稿、论文选题、论文提纲、开题方案或文献综述。", (
        "我打算研究校园雨水回收，想把这个问题发展成论文", "毕业论文方向还没定，有哪些题目比较可行",
        "以供应链韧性为主题，帮我梳理研究框架和文献综述", "我需要一份可编辑的学术论文草稿",
        "把我的问卷材料整理成一篇课程论文", "我想从研究问题入手写一份开题报告",
    ), MAKE_THESIS),
    "export_word": Route("将已生成的题解或提供的正文导出为 Word。", (
        "希望把现成的这段正文存成可编辑的 Word 文档", "能发我一个 docx 文件吗",
        "把之前的答案另存为 Word 吧", "我想用 Word 打开上面的内容", "这份材料需要交 .docx 版",
    ), WORD_EXPORT),
    "format_document": Route("按文字要求、学校规范或 Word 样文修改上传的论文格式，也可处理现有演示文稿格式请求。", (
        "论文格式有误，排版需要改一下", "论文的版式有问题，怎么调整", "毕业论文格式不符合学校要求",
        "文章的字号、行距和页边距不统一", "文档排得很乱，标题和参考文献格式不对",
        "论文的目录和正文样式对不上", "我想把全文统一成模板要求的版式",
    ), FORMAT),
}
CLARIFICATION_EXAMPLES = ("你好", "在吗", "能帮我弄一下吗", "这件事帮我处理下", "我需要一点帮助",
                          "没想好要做什么", "今天上海天气怎么样", "帮我预订餐厅", "帮我设置闹钟")



def response_json(response) -> str:
    content = response.text.strip()
    if content.startswith("```") and content.endswith("```"):
        return content.split("\n", 1)[-1].rsplit("```", 1)[0]
    return content


def rule_plans(question: str, message: dict) -> list[Plan]:
    normalized = solutions.exercise_bank.normalize(question).strip(" 。.!！?？")
    instruction = re.sub(r"^(?:请\s*|帮我\s*|麻烦\s*)+", "", normalized)
    if instruction == ATTACHMENT_REQUEST:
        if any(file["name"].lower().endswith(".docx") for file in message.get("other_files", [])):
            return [Plan(action="format_document")]
        return [Plan(action="solve")] if message["attachments"] and not message.get("other_files") else []

    actions = {action for action, route in ROUTES.items() if route.rule and route.rule.match(instruction)}
    if message["attachments"]:
        actions.discard("export_word")
    query, lookup, solve = solutions.exercise_bank.parse_lookup_request(normalized)
    reference = BOOK_REFERENCE.search(query)
    book_request = reference and (reference.start() == 0 or re.match(r"^(?:查|检索|搜索|解答|解题|求解)", instruction))
    if lookup or book_request:
        if "solve" not in actions:
            actions.add("solve" if lookup and solve else "lookup")
    return [Plan(action=action) for action in actions]


def embedding_settings() -> tuple[str, ModelConnection]:
    settings = dotenv_values(ENV_FILE)

    def setting(name: str) -> str:
        return (os.environ.get(name, settings.get(name)) or "").strip()

    base_url = setting("ROOMMATE_EMBEDDING_BASE_URL").rstrip("/")
    if not base_url:
        vision_url = setting("SOLUTION_VISION_BASE_URL").rstrip("/")
        base_url = vision_url if solutions.is_dashscope_url(vision_url) else solutions.DASHSCOPE_VISION_URL
    url = httpx.URL(base_url)
    if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment:
        raise ValueError(C.ROOMMATE_EMBEDDING_URL_INVALID)
    api_key = setting("ROOMMATE_EMBEDDING_API_KEY")
    if not api_key and solutions.is_dashscope_url(base_url):
        api_key = setting("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError(C.ROOMMATE_EMBEDDING_KEY_MISSING)
    model = setting("ROOMMATE_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
    return model, ModelConnection(base_url, SecretStr(api_key))


def embedding_batch(client: httpx.Client, url: str, model: str, texts: tuple[str, ...]) -> list[object]:
    response = client.post(f"{url}/embeddings", json={"model": model, "input": list(texts)})
    response.raise_for_status()
    payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) != len(texts):
        raise ValueError(C.ROOMMATE_EMBEDDING_COUNT_INVALID)
    vectors = {}
    for item in items:
        index = item.get("index") if isinstance(item, dict) else None
        if type(index) is not int or not 0 <= index < len(texts) or index in vectors:
            raise ValueError(C.ROOMMATE_EMBEDDING_INDEX_INVALID)
        vectors[index] = item.get("embedding")
    return [vectors[index] for index in range(len(texts))]


def unit_embeddings(connection: ModelConnection, model: str, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    if not connection.base_url or not connection.api_key:
        raise ValueError(C.ROOMMATE_EMBEDDING_UNCONFIGURED)
    headers = {"Authorization": f"Bearer {connection.api_key.get_secret_value()}"}
    normalized = []
    with connection.client(headers=headers, timeout=httpx.Timeout(15, connect=5)) as client:
        for start in range(0, len(inputs), EMBEDDING_BATCH_SIZE):
            batch = inputs[start:start + EMBEDDING_BATCH_SIZE]
            for vector in embedding_batch(client, connection.base_url, model, batch):
                if not isinstance(vector, list) or not all(type(value) in (float, int) for value in vector):
                    raise ValueError(C.ROOMMATE_EMBEDDING_RESPONSE_INVALID)
                norm = math.hypot(*vector)
                if not math.isfinite(norm) or not norm or (normalized and len(vector) != len(normalized[0])):
                    raise ValueError(C.ROOMMATE_EMBEDDING_VECTOR_INVALID)
                normalized.append(tuple(value / norm for value in vector))
    return tuple(normalized)


@lru_cache(maxsize=4)
def example_embeddings(connection: ModelConnection, model: str, examples: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    return unit_embeddings(connection, model, examples)


def vector_scores(question: str) -> dict[str, float]:
    model, connection = embedding_settings()
    examples = [(action, example) for action, route in ROUTES.items() for example in route.examples]
    examples.extend(("clarify", example) for example in CLARIFICATION_EXAMPLES)
    vectors = example_embeddings(connection, model, tuple(example for _, example in examples))
    query, = unit_embeddings(connection, model, (question,))
    if len(query) != len(vectors[0]):
        raise ValueError(C.ROOMMATE_EMBEDDING_DIMENSIONS_MISMATCH)
    similarities = [(action, math.fsum(a * b for a, b in zip(query, vector)))
                    for (action, _), vector in zip(examples, vectors)]
    similarities.sort(key=lambda item: item[1], reverse=True)
    groups: dict[str, list[float]] = {}
    for action, score in similarities[:TOP_K]:
        groups.setdefault(action, []).append(score)
    return {action: mean(scores) for action, scores in groups.items()}


def similarity_threshold() -> float:
    try:
        threshold = float(os.environ.get("ROOMMATE_INTENT_THRESHOLD", DEFAULT_THRESHOLD))
        if 0 <= threshold <= 1:
            return threshold
    except ValueError:
        pass
    logger.warning(C.ROOMMATE_EMBEDDING_THRESHOLD_INVALID_LOG, DEFAULT_THRESHOLD)
    return DEFAULT_THRESHOLD


async def decision_plan(model, question: str, message: dict, memory: dict, scores: dict[str, float] | None) -> Plan:
    if model is None:
        return Plan(action="clarify", clarification=ASK_GOAL)
    context = {**memory, "当前请求": question,
               "本轮附件": [file["name"] for file in [*message["attachments"], *message.get("other_files", [])]]}
    prompt = "\n".join((ROUTER_PROMPT.format(routes="\n".join(
        f"{action}：{route.description}" for action, route in ROUTES.items())), C.ROOMMATE_ROUTE_HISTORY))
    if scores is not None:
        context["最相似示例的路由平均分"] = scores
        prompt += C.ROOMMATE_ROUTE_LOW_SCORE
    try:
        response = await model.ainvoke([SystemMessage(content=prompt),
                                       HumanMessage(content=json.dumps(context, ensure_ascii=False))])
        plan = Plan.model_validate_json(response_json(response))
    except Exception as exc:
        logger.warning(C.ROOMMATE_DECISION_FAILED_LOG, type(exc).__name__)
        return Plan(action="clarify", clarification=ASK_GOAL)
    if plan.action in {"clarify", "unsupported"}:
        return Plan(action="clarify", clarification=plan.clarification.strip() or ASK_GOAL)
    if scores is not None or plan.action not in ROUTES:
        return Plan(action="clarify", clarification=ASK_GOAL)
    return plan


async def choose_plan(model, question: str, message: dict, memory: dict) -> Plan:
    if (memory.get("最近交付的是演示文稿") and not message["attachments"] and not message.get("other_files")
            and REMAKE_WITH_WEB_IMAGES.fullmatch(question.strip())):
        return Plan(action="make_ppt", use_previous=True)
    rules = rule_plans(question, message)
    contextual = CONTEXTUAL.search(question) or COMPOUND.search(question) or memory.get("本轮引用的历史")
    if len(rules) == 1 and (rules[0].action == "export_word" or not contextual):
        return rules[0]
    pending_action = memory.get("待补充的问题", {}).get("任务")
    if not rules and not COMPOUND.search(question) and pending_action in {"format_document", "generate_thesis"}:
        return Plan(action=pending_action)
    # 附件、修改指令和多个明确动作需要结合上下文判定。
    if rules or question == ATTACHMENT_REQUEST or contextual or memory.get("待补充的问题"):
        return await decision_plan(model, question, message, memory, None)
    try:
        scores = await run_in_threadpool(vector_scores, question)
    except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, OverflowError) as exc:
        logger.warning(C.ROOMMATE_EMBEDDING_FAILED_LOG, type(exc).__name__)
        return await decision_plan(model, question, message, memory, None)
    if scores:
        best = max(scores, key=scores.get)
        score = scores[best]
        if best in ROUTES and score > similarity_threshold() and list(scores.values()).count(score) == 1:
            return Plan(action=best)
    return await decision_plan(model, question, message, memory, scores)
