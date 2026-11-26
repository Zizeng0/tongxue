"""为当前任务捕获完整的桌面截图。"""

import json
from pathlib import Path

from langchain_core.tools import tool
from PIL import ImageGrab

from . import CONSTANT


def capture_full_screen(task_dir: Path, message_id: str) -> Path:
    output_dir = task_dir / "desktop" / message_id
    output_dir.mkdir(parents=True, exist_ok=True)
    number = len(list(output_dir.glob("screen-*.png"))) + 1
    output = output_dir / f"screen-{number:03d}.png"
    image = ImageGrab.grab(all_screens=True)
    image.save(output, format="PNG")
    return output


def create_screen_tools(task_dir: Path, message_id: str):
    @tool("capture_desktop_screen")
    def capture_desktop_screen() -> str:
        """截取所有显示器的完整桌面，保留原始尺寸和范围。"""
        try:
            output = capture_full_screen(task_dir, message_id)
        except OSError as exc:
            return json.dumps({"error": CONSTANT.DESKTOP_CAPTURE_FAILED.format(exc=exc)}, ensure_ascii=False)
        return json.dumps({"image": output.relative_to(task_dir).as_posix()}, ensure_ascii=False)

    return [capture_desktop_screen]
