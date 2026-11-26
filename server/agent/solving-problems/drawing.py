"""受限函数绘图与电路示意图绘制，输出保存在解题会话目录。"""

import ast
import math
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import Arc, Circle

from . import CONSTANT


FUNCTIONS = {name: getattr(np, name) for name in ("sin", "cos", "tan", "exp", "sqrt", "log", "log10", "abs")}
ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call, ast.Load,
           ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)
MAX_SEQUENCE_SAMPLES = 256
MAX_SEQUENCE_START = 10000


def _output(task_dir: Path, message_id: str, name: str) -> Path:
    directory = task_dir / "solutions" / message_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def plot_function(task_dir: Path, message_id: str, expression: str, x_min: float = -10,
                  x_max: float = 10, title: str = "函数图", x_label: str = "x",
                  y_label: str = "y") -> str:
    if len(expression) > 200 or not -1000 <= x_min < x_max <= 1000:
        raise ValueError(CONSTANT.FUNCTION_OR_DOMAIN_INVALID)
    tree = ast.parse(expression.replace("^", "**"), mode="eval")
    allowed_names = {"x", "pi", "e", *FUNCTIONS}
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED) or isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError(CONSTANT.FUNCTION_OPERATION_UNSUPPORTED)
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS):
            raise ValueError(CONSTANT.MATH_FUNCTION_UNSUPPORTED)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) and (
                not isinstance(node.right, ast.Constant) or not isinstance(node.right.value, (int, float))
                or abs(node.right.value) > 12):
            raise ValueError(CONSTANT.FUNCTION_EXPONENT_INVALID)
    x_values = np.linspace(x_min, x_max, 600)
    with np.errstate(all="ignore"):
        y_values = eval(compile(tree, "<plot>", "eval"), {"__builtins__": {}},
                        {"x": x_values, "pi": math.pi, "e": math.e, **FUNCTIONS})
    values = np.broadcast_to(np.asarray(y_values, dtype=float), x_values.shape)
    values = np.where(np.isfinite(values), values, np.nan)
    if np.isnan(values).all():
        raise ValueError(CONSTANT.NO_PLOTTABLE_REAL_VALUES)
    figure, axis = plt.subplots(figsize=(7, 4))
    axis.plot(x_values, values, color="#236d91", linewidth=2)
    axis.axhline(0, color="#9cabb8", linewidth=.7)
    axis.axvline(0, color="#9cabb8", linewidth=.7)
    axis.set(xlabel=x_label[:40], ylabel=y_label[:40], title=title[:80])
    axis.grid(alpha=.25)
    figure.tight_layout()
    output = _output(task_dir, message_id, f"function-{uuid4().hex[:8]}.png")
    figure.savefig(output, dpi=160)
    plt.close(figure)
    return output.relative_to(task_dir).as_posix()


def plot_discrete_sequence(task_dir: Path, message_id: str, samples: list[float],
                           start_index: int = 0, title: str = "离散序列", *, max_samples: int = MAX_SEQUENCE_SAMPLES) -> str:
    content = discrete_sequence_png(samples, start_index, title, max_samples=max_samples)
    output = _output(task_dir, message_id, f"discrete-{uuid4().hex[:8]}.png")
    output.write_bytes(content)
    return output.relative_to(task_dir).as_posix()


def discrete_sequence_png(samples: list[float], start_index: int, title: str, *, max_samples: int = MAX_SEQUENCE_SAMPLES) -> bytes:
    if (not 1 <= len(samples) <= max_samples or not isinstance(start_index, int)
            or not -MAX_SEQUENCE_START <= start_index <= MAX_SEQUENCE_START):
        raise ValueError(CONSTANT.SEQUENCE_LENGTH_OR_START_INVALID)
    try:
        values = np.asarray(samples, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(CONSTANT.SEQUENCE_REQUIRES_REAL_VALUES) from exc
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError(CONSTANT.SEQUENCE_REQUIRES_FINITE_VALUES)
    indices = np.arange(start_index, start_index + len(values))
    figure = Figure(figsize=(7, 4))
    axis = figure.subplots()
    axis.stem(indices, values, linefmt="#236d91", markerfmt="o", basefmt="#9cabb8")
    axis.set(xlabel="n", ylabel="幅值", title=title[:80])
    axis.grid(alpha=.25)
    figure.tight_layout()
    with BytesIO() as output:
        figure.savefig(output, dpi=160, format="png")
        return output.getvalue()


def plot_pole_zero(task_dir: Path, message_id: str, zeros: list[list[float]],
                   poles: list[list[float]], title: str = "极零点分布") -> str:
    if len(zeros) > 40 or len(poles) > 40 or not zeros and not poles:
        raise ValueError(CONSTANT.POLE_ZERO_COUNT_INVALID)
    points = [complex(*point) for point in [*zeros, *poles] if len(point) == 2]
    if len(points) != len(zeros) + len(poles) or any(not math.isfinite(value) for point in points for value in (point.real, point.imag)):
        raise ValueError(CONSTANT.POLE_ZERO_COORDINATES_INVALID)
    figure, axis = plt.subplots(figsize=(6, 6))
    if zeros:
        axis.scatter([point[0] for point in zeros], [point[1] for point in zeros],
                     facecolors="none", edgecolors="#236d91", s=120, label="零点")
    if poles:
        axis.scatter([point[0] for point in poles], [point[1] for point in poles],
                     marker="x", color="#bb554c", s=120, label="极点")
    axis.add_patch(Circle((0, 0), 1, fill=False, linestyle="--", color="#aeb9c3"))
    axis.axhline(0, color="#9cabb8", linewidth=.7)
    axis.axvline(0, color="#9cabb8", linewidth=.7)
    axis.set(xlabel="Re(z)", ylabel="Im(z)", title=title[:80])
    axis.set_aspect("equal")
    axis.grid(alpha=.25)
    axis.legend()
    figure.tight_layout()
    output = _output(task_dir, message_id, f"pole-zero-{uuid4().hex[:8]}.png")
    figure.savefig(output, dpi=160)
    plt.close(figure)
    return output.relative_to(task_dir).as_posix()


def render_circuit(task_dir: Path, message_id: str, components: list[dict], wires: list[dict],
                   tikz_source: str = "") -> str:
    if tikz_source:
        if len(tikz_source) > 12000 or any(command in tikz_source.lower() for command in (
                "\\input", "\\include", "\\openin", "\\read", "\\write", "\\catcode", "\\csname",
                "\\usepackage", "\\documentclass", "\\special", "\\immediate", "\\directlua")):
            raise ValueError(CONSTANT.TIKZ_OPERATION_UNSUPPORTED)
        try:
            from cictikz.render import pdf_to_png, render_tex, wrap_body
            rendered = render_tex(wrap_body(tikz_source))
            if rendered.ok:
                output = _output(task_dir, message_id, "circuit.png")
                output.write_bytes(pdf_to_png(rendered.pdf_path))
                return output.relative_to(task_dir).as_posix()
        except (ImportError, OSError, RuntimeError):
            if not components:
                raise ValueError(CONSTANT.CIRCUITIKZ_TOOLS_REQUIRED)
    if not components or len(components) > 40 or len(wires) > 80:
        raise ValueError(CONSTANT.CIRCUIT_SIZE_INVALID)
    figure, axis = plt.subplots(figsize=(8, 5))
    for wire in wires:
        start, end = wire["start"], wire["end"]
        axis.plot([float(start[0]), float(end[0])], [float(start[1]), float(end[1])], color="#26394c", linewidth=1.8)
    for component in components:
        kind = str(component["type"]).lower()
        center_x, center_y = float(component["x"]), float(component["y"])
        label = str(component.get("label", ""))[:30]
        if kind == "resistor":
            zigzag_x = np.linspace(center_x - .5, center_x + .5, 9)
            zigzag_y = center_y + np.array([0, .18, -.18, .18, -.18, .18, -.18, .18, 0])
            axis.plot(zigzag_x, zigzag_y, color="#26394c", linewidth=1.8)
        elif kind == "capacitor":
            axis.plot([center_x - .12, center_x - .12], [center_y - .38, center_y + .38], color="#26394c", linewidth=2)
            axis.plot([center_x + .12, center_x + .12], [center_y - .38, center_y + .38], color="#26394c", linewidth=2)
        elif kind == "inductor":
            for offset in (-.3, -.1, .1, .3):
                axis.add_patch(Arc((center_x + offset, center_y), .22, .35, theta1=0, theta2=180, color="#26394c", linewidth=1.7))
        elif kind in {"source", "voltage", "current"}:
            axis.add_patch(Circle((center_x, center_y), .38, fill=False, color="#26394c", linewidth=1.8))
            axis.text(center_x, center_y, "+\n−" if kind != "current" else "↑", ha="center", va="center", fontsize=11)
        elif kind == "ground":
            for index, width in enumerate((.55, .35, .15)):
                axis.plot([center_x-width/2, center_x+width/2], [center_y-index*.1]*2, color="#26394c", linewidth=1.7)
        else:
            raise ValueError(CONSTANT.CIRCUIT_COMPONENT_UNSUPPORTED.format(kind=kind))
        axis.text(center_x, center_y + .55, label, ha="center", fontsize=9)
    axis.set_aspect("equal")
    axis.autoscale()
    axis.margins(.18)
    axis.axis("off")
    figure.tight_layout()
    output = _output(task_dir, message_id, "circuit.png")
    figure.savefig(output, dpi=160)
    plt.close(figure)
    return output.relative_to(task_dir).as_posix()


def render_filter_structure(task_dir: Path, message_id: str, numerator: list[str],
                            denominator: list[str], structure: str = "direct",
                            sections: list[dict] | None = None, *,
                            bias: str = "", input_filter: str = "") -> str:
    """绘制 IIR 直接 II 型信号流图或传递函数节的级联结构图。"""
    if structure not in {"direct", "cascade"}:
        raise ValueError(CONSTANT.FILTER_FORM_INVALID)
    if not 1 <= len(numerator) <= 5 or not 1 <= len(denominator) <= 5:
        raise ValueError(CONSTANT.FILTER_COEFFICIENT_COUNT_INVALID)
    if any(not isinstance(value, str) or not value.strip() or len(value) > 30
           for value in [*numerator, *denominator]):
        raise ValueError(CONSTANT.FILTER_COEFFICIENT_INVALID)
    if denominator[0].strip() != "1":
        raise ValueError(CONSTANT.FILTER_DENOMINATOR_LEADING_TERM_INVALID)
    figure, axis = plt.subplots(figsize=(10, 5))
    if structure == "cascade":
        if not sections or not 1 <= len(sections) <= 4 or any(
            not isinstance(section, dict) or not str(section.get("numerator", "")).strip()
            or not str(section.get("denominator", "")).strip()
            or len(str(section)) > 180 for section in sections
        ):
            plt.close(figure)
            raise ValueError(CONSTANT.CASCADE_SECTIONS_INVALID)
        axis.annotate("", xy=(1.3, 2), xytext=(.2, 2), arrowprops={"arrowstyle": "->"})
        axis.text(.1, 2.2, "x(n)" if not input_filter else "v(n)")
        for index, section in enumerate(sections):
            center = 2 + index * 2
            axis.text(center, 2, f"H{index + 1}(z)\n{section['numerator']}\n────────\n{section['denominator']}",
                      ha="center", va="center", bbox={"boxstyle": "round", "facecolor": "#eaf3f8"})
            axis.annotate("", xy=(center + 1.4, 2), xytext=(center + .6, 2),
                          arrowprops={"arrowstyle": "->"})
        axis.text(2 + len(sections) * 2, 2.2, "y(n)")
        if input_filter:
            axis.text(.2, 3.25, f"x(n) → FIR [{input_filter}] → (+) → v(n)\n"
                      f"{' ' * 29}↑ {bias}", fontsize=11)
        axis.set_xlim(0, 3 + len(sections) * 2)
    else:
        order = max(len(numerator), len(denominator)) - 1
        if order < 1:
            plt.close(figure)
            raise ValueError(CONSTANT.DIRECT_FORM_DELAY_REQUIRED)
        axis.text(.1, 3, "x(n)")
        axis.annotate("", xy=(1.3, 3), xytext=(.7, 3), arrowprops={"arrowstyle": "->"})
        axis.text(1.5, 3, "+", ha="center", va="center", bbox={"boxstyle": "circle", "facecolor": "white"})
        axis.annotate("", xy=(3, 3), xytext=(1.8, 3), arrowprops={"arrowstyle": "->"})
        axis.text(3, 3.25, "w(n)", ha="center")
        axis.annotate("", xy=(7, 3), xytext=(3, 3), arrowprops={"arrowstyle": "->"})
        axis.text(5, 3.15, numerator[0], ha="center")
        axis.text(7.2, 3, "+", ha="center", va="center", bbox={"boxstyle": "circle", "facecolor": "white"})
        axis.annotate("", xy=(8.8, 3), xytext=(7.4, 3), arrowprops={"arrowstyle": "->"})
        axis.text(8.8, 3.2, "y(n)")
        if bias:
            polynomial = " ".join(f"+ ({coefficient}) z^-{index}" for index, coefficient in enumerate(denominator[1:], 1))
            axis.text(.9, 3.7, f"{bias} → 1 / [1 {polynomial}]", fontsize=9)
            axis.annotate("", xy=(7.2, 3.15), xytext=(6.9, 3.7), arrowprops={"arrowstyle": "->"})
        for index in range(1, order + 1):
            level = 3 - index * .65
            axis.annotate("", xy=(3, level + .13), xytext=(3, level + .55), arrowprops={"arrowstyle": "->"})
            axis.text(3, level, "z^-1", ha="center", va="center", bbox={"boxstyle": "round", "facecolor": "#eaf3f8"})
            axis.text(3.35, level - .2, f"w(n-{index})", fontsize=8)
            if index < len(denominator) and denominator[index] != "0":
                axis.annotate("", xy=(1.5, 2.85), xytext=(2.8, level),
                              arrowprops={"arrowstyle": "->", "connectionstyle": "angle,angleA=180,angleB=90"})
                axis.text(1.7, level + .12, f"−({denominator[index]})", fontsize=8)
            if index < len(numerator) and numerator[index] != "0":
                axis.annotate("", xy=(7.2, 2.85), xytext=(3.2, level),
                              arrowprops={"arrowstyle": "->", "connectionstyle": "angle,angleA=0,angleB=90"})
                axis.text(5, level + .12, numerator[index], fontsize=8)
        axis.set_xlim(0, 9.5)
    axis.set_ylim(-.2, 4)
    axis.set_aspect("auto")
    axis.axis("off")
    figure.tight_layout()
    output = _output(task_dir, message_id, f"filter-{uuid4().hex[:8]}.png")
    figure.savefig(output, dpi=160)
    plt.close(figure)
    return output.relative_to(task_dir).as_posix()
