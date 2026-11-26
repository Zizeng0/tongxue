"""Cisco Packet Tracer 的网络实验规划工具与工作流。"""

from langchain_core.tools import tool

from server.agent.workflow import build_workflow

from . import CONSTANT


@tool
def plan_packet_tracer_network(goal: str) -> str:
    """根据网络目标列出拓扑配置与连通性验证步骤。"""
    return CONSTANT.PACKET_TRACER_PLAN.format(goal=goal)


def create_workflow(model, pdf_tools=None):
    return build_workflow(
        model,
        CONSTANT.PACKET_TRACER_SYSTEM_PROMPT,
        [plan_packet_tracer_network, *(pdf_tools or [])],
    )
