"""Shared error and result contracts for Roommate workflows."""

from dataclasses import dataclass, field


class RoommateError(Exception):
    """可以直接显示给用户的任务错误。"""



@dataclass
class Result:
    reply: str
    figures: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    exportable: bool = False
    files: list[str] = field(default_factory=list)
    presentation: dict | None = None
    needs_clarification: bool = False
    clarification_action: str | None = None
    action: str | None = None
    reused_inputs: dict[str, list[dict]] = field(default_factory=dict)
    reference_message_id: str | None = None


def clarify(question: str) -> Result:
    return Result(question, needs_clarification=True)
