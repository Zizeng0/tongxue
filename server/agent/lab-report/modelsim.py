"""Modelsim SE-64 10.1c 的仿真分析工具与工作流。"""

from langchain_core.tools import tool

from server.agent.workflow import build_workflow

from . import CONSTANT


@tool
def plan_modelsim_simulation(goal: str) -> str:
    """根据仿真目标列出 Modelsim 中需要检查的步骤。"""
    return CONSTANT.MODELSIM_PLAN.format(goal=goal)


def create_workflow(model, pdf_tools=None):
    return build_workflow(
        model,
        CONSTANT.MODELSIM_SYSTEM_PROMPT,
        [plan_modelsim_simulation, *(pdf_tools or [])],
    )
