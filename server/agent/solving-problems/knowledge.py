"""按学科检索 GBrain 知识页，并提供本地 Wiki 备选。"""

import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WIKI = ROOT / "knowledge-base" / "wiki"
GBRAIN = ROOT / "knowledge-base" / "gbrain.ps1"
SUBJECTS = {
    "数字信号处理": ("数字信号处理", "采样", "混叠", "离散时间", "z变换", "傅里叶", "滤波器", "频谱", "冲激响应", "单位脉冲响应", "差分方程", "系统函数", "极零点", "零极点", "收敛域", "幅频特性", "频率响应", "线性卷积", "循环卷积", "卷积", "矩形序列", "dft", "fft"),
    "通信原理": ("通信原理", "调制", "解调", "信道", "码元", "误码率", "载波", "通信系统"),
    "电磁场与电磁波": ("电磁场", "电磁波", "麦克斯韦", "波导", "天线", "电场", "磁场", "边界条件"),
    "电路原理": ("电路", "电阻", "电容", "电感", "基尔霍夫", "rc电路", "rl电路", "运算放大器"),
}
WIKI_FILES = {"数字信号处理": "dsp.md", "通信原理": "communications.md", "电磁场与电磁波": "electromagnetics.md"}


def classify_question(text: str) -> dict[str, str | bool]:
    lowered = text.lower()
    scores = {subject: sum(len(word) for word in words if word.lower() in lowered)
              for subject, words in SUBJECTS.items()}
    subject = max(scores, key=scores.get) if max(scores.values()) else "未分类"
    diagram = "circuit" if any(word in lowered for word in ("电路图", "原理图", "电路示意", "circuit")) else "plot" if any(
        word in lowered for word in ("画图", "绘图", "波形", "频谱图", "函数图", "曲线", "plot")) else "none"
    problem_type = "计算与推导" if any(word in lowered for word in ("计算", "求解", "求出", "推导", "证明")) else "概念分析"
    return {"subject": subject, "problem_type": problem_type, "diagram": diagram}


def _gbrain_json(*arguments: str):
    if not GBRAIN.is_file():
        return None
    env = os.environ.copy()
    env["GBRAIN_HOME"] = str(ROOT / "knowledge-base" / "data" / "gbrain-home")
    if os.name == "nt":
        command = ["powershell", "-NoProfile", "-File", str(GBRAIN), *arguments, "--json"]
    else:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=20, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^[\[{]", result.stdout or ""):
        try:
            return decoder.raw_decode(result.stdout[match.start():])[0]
        except ValueError:
            continue
    return None


def wiki_passages(query: str, subject: str) -> list[dict[str, str]]:
    pages = _gbrain_json("query", f"{subject} {query[:160]}")
    passages = [{"title": item.get("title", item.get("slug", "Wiki")), "text": item["chunk_text"][:1400]}
                for item in pages[:4] if isinstance(item, dict) and item.get("chunk_text")] if isinstance(pages, list) else []
    if subject in WIKI_FILES and not any(item["title"] == subject for item in passages):
        page = _gbrain_json("get", subject)
        if isinstance(page, dict) and page.get("compiled_truth"):
            passages.insert(0, {"title": subject, "text": page["compiled_truth"][:1400]})
        else:
            path = WIKI / WIKI_FILES[subject]
            if path.is_file():
                passages.insert(0, {"title": subject, "text": path.read_text(encoding="utf-8")[:1400]})
    return passages[:5]
