"""处理数字信号处理习题中已核验的原图和可复现图表。"""

import re
from pathlib import Path
from shutil import copyfile

import numpy as np

from .drawing import plot_discrete_sequence, plot_pole_zero, render_filter_structure

from . import CONSTANT


FILTER_IMAGE = bytes.fromhex("253720393c8bce0397d6ffba1490b6ec584f2cddb2debb1756f531d6fe5ba9ec")
FILTER_REQUEST = re.compile(r"(?:试)?完成(?:题目中的)?(?:第)?2\s*[,，、]\s*(?:第)?3\s*题(?:并画出图像)?[。.!]?$")
QUESTION_HEADING = re.compile(r"(?m)^##[ \t]+第[ \t]*(\d+)[ \t]*题[^\n]*")


def image_link(title: str, path: str) -> str:
    return f"![{title}]({path})"


def place_figures(reply: str, figures: dict[str, list[str]]) -> str:
    headings = list(QUESTION_HEADING.finditer(reply))
    if not figures or not headings:
        return reply
    locations = {heading[1]: heading.start() for heading in headings}
    captions = {}
    for number, paths in figures.items():
        if number not in locations:
            continue
        for path in dict.fromkeys(paths):
            pattern = rf"(?m)^[ \t]*!\[([^\]\n]*)\]\({re.escape(path)}\)[ \t]*$"
            link = re.search(pattern, reply)
            captions.setdefault(number, []).append(image_link(link[1] if link else f"第 {number} 题生成图", path))
            reply = re.sub(pattern, "", reply)
    headings = list(QUESTION_HEADING.finditer(reply))
    parts = [reply[:headings[0].start()].strip()]
    stops = [*(heading.start() for heading in headings[1:]), len(reply)]
    for heading, stop in zip(headings, stops, strict=True):
        parts.append(reply[heading.start():stop].strip())
        parts.extend(captions.pop(heading[1], []))
    return "\n\n".join(part for part in parts if part)


def convolution_figures(task_dir, message_id) -> str:
    h = [1] * 6
    x = list(range(8))
    linear = [sum(x[index - tap] for tap in range(len(h)) if 0 <= index - tap < len(x))
              for index in range(len(h) + len(x) - 1)]
    series = [("h(n)", h), ("x(n)", x)]
    for length in (8, 16):
        series.append((f"Circular convolution (N={length})",
                       [sum(linear[offset::length]) for offset in range(length)]))
    series.append(("Linear convolution", linear))
    return "\n\n".join(image_link(title, plot_discrete_sequence(task_dir, message_id, values, 0, title))
                       for title, values in series)


def difference_equation_figures(task_dir, message_id) -> str:
    poles = [[(1 + sign * float(np.sqrt(5))) / 2, 0] for sign in (1, -1)]
    path = plot_pole_zero(task_dir, message_id, [[0, 0]], poles, "Pole-zero map")
    return image_link("极零点图", path)


def pole_zero_figures(task_dir, message_id) -> str:
    zeros = np.roots([1, 5, -50])
    denominator = [2, -2.98, .17, 2.3418, -1.5147]
    poles = np.roots(denominator)
    zero_coordinates = [[float(value.real), float(value.imag)] for value in zeros]
    pole_coordinates = [[float(value.real), float(value.imag)] for value in poles]
    pole_zero = plot_pole_zero(task_dir, message_id, zero_coordinates, pole_coordinates, "Pole-zero map")
    step = []
    for index in range(1500):
        forcing = sum(value for delay, value in enumerate([0, 0, 1, 5, -50]) if delay <= index)
        feedback = sum(value * step[index - delay] for delay, value in enumerate(denominator[1:], 1) if delay <= index)
        step.append((forcing - feedback) / denominator[0])
    response = plot_discrete_sequence(task_dir, message_id, step, 0, "Causal unit-step response", max_samples=1500)
    return f"{image_link('极零点图', pole_zero)}\n\n{image_link('因果单位阶跃响应', response)}"


FIGURE_BUILDERS = {(2, 23): difference_equation_figures, (2, 31): pole_zero_figures, (3, 25): convolution_figures}


def cached_figures(data_dir: Path, source: dict) -> list[tuple[Path, str]]:
    directory, old_message = source["directory"], source["message"]
    if Path(directory).name != directory or not re.fullmatch(r"[a-f0-9]{32}", old_message):
        return []
    parent = (data_dir / "tasks" / directory / "solutions" / old_message).resolve()
    if not parent.is_relative_to((data_dir / "tasks").resolve()):
        return []
    paths = []
    for name in source["names"]:
        if not re.fullmatch(r"[\w.-]+\.png", name) or not (parent / name).is_file() or (parent / name).is_symlink():
            return []
        paths.append((parent / name, f"solutions/{old_message}/{name}"))
    return paths


def restore_figures(task_dir: Path, message_id: str, files: list[tuple[Path, str]], reply: str) -> str:
    output = task_dir / "solutions" / message_id
    output.mkdir(parents=True, exist_ok=True)
    for source, old_path in files:
        destination = output / source.name
        copyfile(source, destination)
        reply = reply.replace(old_path, destination.relative_to(task_dir).as_posix())
    return reply


def filter_answer(task_dir, message_id) -> str:
    second = render_filter_structure(task_dir, message_id, ["1", "1"], ["1", "-1/3", "-1/4"])
    third = render_filter_structure(task_dir, message_id, ["0", "a+b", "1"], ["1", "-(a+b)", "ab"],
                                    bias="ab")
    cascade = render_filter_structure(task_dir, message_id, ["0", "a+b", "1"], ["1", "-(a+b)", "ab"],
                                      "cascade", [{"numerator": "1", "denominator": "1-a z^-1"},
                                                  {"numerator": "1", "denominator": "1-b z^-1"}],
                                      bias="ab", input_filter="(a+b) z^-1 + z^-2")
    return (
        CONSTANT.REVIEWED_FILTER_ANSWER.format(image_link=image_link('第 2 题直接型', second), value2=image_link('第 3 题直接型', third), value3=image_link('第 3 题级联型', cascade))
    )
