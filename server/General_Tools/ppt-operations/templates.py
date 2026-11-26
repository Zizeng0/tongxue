"""Built-in presentation templates and their compatible color themes."""

from dataclasses import dataclass
from typing import Literal


Category = Literal["teaching", "technical", "speech", "competition", "research_report", "workshop"]
Theme = Literal["academic", "business", "dark", "classroom", "research", "keynote", "pitch", "journal", "studio"]


@dataclass(frozen=True)
class Template:
    title: str
    theme: Theme
    guidance: str


TEMPLATES: dict[Category, Template] = {
    "teaching": Template("教学", "classroom", "学习目标、概念示例、课堂练习、回顾。可用流程和双栏对照。"),
    "technical": Template("专业知识讲解", "research", "定义、原理、证据或对照、应用。标题写成可验证的结论，优先使用对照表。"),
    "speech": Template("演讲", "keynote", "开场问题、故事或论点、结尾行动。用重点页和图片，细节写入讲稿。"),
    "competition": Template("竞赛", "pitch", "问题、方案、验证结果、价值或下一步。以真实指标和验证证据支撑结论。"),
    "research_report": Template("研究汇报", "journal", "研究问题、样本与方法、发现、局限及后续验证。用对照和实际图表呈现证据。"),
    "workshop": Template("实训与工作坊", "studio", "任务目标、准备条件、操作、示例和验收。配步骤图或实物照片，指出常见错误。"),
}

PALETTES = {
    "academic": ("F6FAF9", "122F3C", "274858", "0F766E"),
    "business": ("F8FAFD", "182943", "314966", "2457AD"),
    "dark": ("142A37", "F8FAFC", "DCE7EF", "72DFC7"),
    "classroom": ("FBF7ED", "162D27", "28453B", "287451"),
    "research": ("F4F7FC", "132C4A", "243E5C", "235CA6"),
    "keynote": ("201A33", "FFF7EF", "F1E5F2", "FFC784"),
    "pitch": ("10172A", "F4F4FF", "DAE1F4", "B8A9FF"),
    "journal": ("F7F4F8", "302539", "514359", "79436E"),
    "studio": ("FCF7F0", "382921", "554439", "9D4426"),
}
