"""Quartus II 13.1 (64-bit) 的综合分析工具与工作流。"""

from langchain_core.tools import tool

from server.agent.workflow import build_workflow

from . import CONSTANT


@tool
def plan_quartus_project(goal: str) -> str:
    """根据设计目标列出 Quartus 工程准备与验证步骤。"""
    return CONSTANT.QUARTUS_PLAN.format(goal=goal)


def create_workflow(model, pdf_tools=None):
    return build_workflow(
        model,
        CONSTANT.QUARTUS_SYSTEM_PROMPT,
        [plan_quartus_project, *(pdf_tools or [])],
    )
