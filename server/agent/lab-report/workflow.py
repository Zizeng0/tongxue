"""各软件工作流共用的 LangChain Agent 构建方式。"""

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool

from . import CONSTANT


REPORT_FALLBACK_CONTEXT_TOKENS = 32000
REPORT_LARGE_TOOL_TOKENS = 12000
REPORT_SUMMARY_PROMPT = CONSTANT.REPORT_SUMMARY_PROMPT


def estimate_report_tokens(messages):
    return count_tokens_approximately(messages, chars_per_token=2.0)


def summary_tool_argument(key, value):
    if key == "sections" and isinstance(value, list):
        return [{name: section[name] for name in ("heading", "images", "code_files") if name in section}
                for section in value if isinstance(section, dict)]
    if isinstance(value, (int, float, bool)) or isinstance(value, str) and len(value) <= 200:
        return value
    return "[较长参数省略，需从已保存文件或工具结果复核]"


class ReportSummarizationMiddleware(SummarizationMiddleware):
    def _should_summarize(self, messages, total_tokens):
        return super()._should_summarize(messages, total_tokens) or any(
            isinstance(message, (AIMessage, ToolMessage))
            and estimate_report_tokens([message]) >= REPORT_LARGE_TOOL_TOKENS
            for message in messages
        )

    def _determine_cutoff_index(self, messages):
        cutoff = super()._determine_cutoff_index(messages)
        large_result_end = 0
        first_completed_step = 0
        for index, message in enumerate(messages):
            if not isinstance(message, AIMessage) or not message.tool_calls:
                continue
            call_ids = {call["id"] for call in message.tool_calls if call.get("id")}
            if not call_ids:
                continue
            end = index + 1
            responses = set()
            while end < len(messages) and isinstance(messages[end], ToolMessage):
                responses.add(messages[end].tool_call_id)
                end += 1
            if call_ids <= responses and not first_completed_step:
                first_completed_step = end
            if call_ids <= responses and any(
                estimate_report_tokens([item]) >= REPORT_LARGE_TOOL_TOKENS
                for item in messages[index:end]
            ):
                large_result_end = end
        if large_result_end:
            return max(cutoff, large_result_end)
        if cutoff > 1:
            return cutoff
        return first_completed_step if cutoff else 0

    @staticmethod
    def _partition_messages(messages, cutoff_index):
        older = messages[:cutoff_index]
        uploads = [message for message in older if isinstance(message, HumanMessage) and isinstance(message.content, list)]
        upload_ids = {id(message) for message in uploads}
        return ([message for message in older if id(message) not in upload_ids],
                [*uploads, *messages[cutoff_index:]])

    def _trim_messages_for_summary(self, messages):
        anchor = HumanMessage(content="以下是较早的实验执行记录；原始上传资料仍保留在工作流中。")
        prepared = []
        for message in messages:
            if isinstance(message, ToolMessage) and isinstance(message.content, str) and len(message.content) > 12000:
                excerpt = message.content[:6000] + "\n[中间内容省略；如需细节请重新读取原文件或运行结果]\n" + message.content[-6000:]
                prepared.append(message.model_copy(update={"content": excerpt}))
            elif isinstance(message, AIMessage) and message.tool_calls and estimate_report_tokens([message]) > 4000:
                calls = [{**call, "args": {key: summary_tool_argument(key, value)
                          for key, value in call.get("args", {}).items()}}
                         for call in message.tool_calls]
                prepared.append(message.model_copy(update={"tool_calls": calls}))
            else:
                prepared.append(message)
        if self.trim_tokens_to_summarize is None:
            return [anchor, *prepared]
        retained = []
        for message in reversed(prepared):
            if self.token_counter([anchor, message, *retained]) > self.trim_tokens_to_summarize:
                break
            retained.insert(0, message)
        return [anchor, *retained]


def build_workflow(model: BaseChatModel, system_prompt: str, tools: list[BaseTool]):
    middleware = []
    if isinstance(model, BaseChatModel) and any(item.name == "write_experiment_report" for item in tools):
        profile = model.profile
        context_tokens = profile.get("max_input_tokens") if isinstance(profile, dict) else None
        if not isinstance(context_tokens, int) or context_tokens <= 0:
            context_tokens = REPORT_FALLBACK_CONTEXT_TOKENS
        middleware.append(ReportSummarizationMiddleware(
            model=model,
            trigger=("tokens", int(context_tokens * 0.75)),
            keep=("tokens", min(12000, max(4000, int(context_tokens * 0.12)))),
            token_counter=estimate_report_tokens,
            summary_prompt=REPORT_SUMMARY_PROMPT,
            trim_tokens_to_summarize=16000,
        ))
    return create_agent(model=model, tools=tools, system_prompt=system_prompt, middleware=middleware)
