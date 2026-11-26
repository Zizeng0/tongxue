"""PyCharm 实验的 Python 源文件工具与工作流。"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool

from server.agent.workflow import build_workflow

from . import CONSTANT


MAX_SOURCE_BYTES = 1024 * 1024
SOURCE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.py\Z")


def create_python_tools(task_dir: Path, message_id: str, previous_message_ids: list[str] | None = None):
    output_root = (task_dir / "pycharm").resolve()
    output_dir = (output_root / message_id).resolve()
    previous_dirs = [(output_root / item).resolve() for item in previous_message_ids or []]
    previous_dirs = [path for path in previous_dirs if path.parent == output_root]
    allowed_dirs = {output_dir, *previous_dirs}

    def saved_script(reference: str) -> Path | None:
        parts = Path(reference).parts
        if not parts or not SOURCE_NAME.fullmatch(parts[-1]):
            return None
        if len(parts) == 1:
            candidates = [output_dir / parts[0], *(directory / parts[0] for directory in reversed(previous_dirs))]
        elif len(parts) == 3 and parts[0] == "pycharm":
            candidates = [task_dir / reference]
        else:
            return None
        for candidate in candidates:
            path = candidate.resolve()
            if path.parent in allowed_dirs and path.is_file():
                return path
        return None

    @tool("write_python_script")
    def write_python_script(filename: str, source: str) -> str:
        """检查 Python 脚本语法，并保存到当前会话的 PyCharm 输出目录。"""
        if not SOURCE_NAME.fullmatch(filename):
            return json.dumps({"error": CONSTANT.PYTHON_FILENAME_INVALID}, ensure_ascii=False)
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            return json.dumps({"error": CONSTANT.PYTHON_SOURCE_TOO_LARGE}, ensure_ascii=False)
        try:
            ast.parse(source, filename=filename)
        except SyntaxError as exc:
            return json.dumps({"error": CONSTANT.PYTHON_SYNTAX_ERROR.format(lineno=exc.lineno, msg=exc.msg)}, ensure_ascii=False)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / filename
        temporary = output_dir / f".{filename}.tmp"
        try:
            temporary.write_text(source, encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"path": path.relative_to(task_dir.resolve()).as_posix(), "bytes": path.stat().st_size}, ensure_ascii=False)

    @tool("run_python_script")
    def run_python_script(filename: str) -> str:
        """运行已保存的实验脚本，返回实际运行输出及生成的图片路径。"""
        script = saved_script(filename)
        if script is None:
            return json.dumps({"error": CONSTANT.PYTHON_SCRIPT_REQUIRED}, ensure_ascii=False)
        if script.parent != output_dir:
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                target = output_dir / script.name
                if target.exists():
                    if target.read_bytes() != script.read_bytes():
                        return json.dumps({"error": CONSTANT.PYTHON_SCRIPT_EXISTS}, ensure_ascii=False)
                else:
                    shutil.copyfile(script, target)
                script = target
            except OSError as exc:
                return json.dumps({"error": CONSTANT.PYTHON_SCRIPT_REUSE_FAILED.format(exc=exc)}, ensure_ascii=False)
        before = {path: path.stat().st_mtime_ns for path in output_dir.rglob("*") if path.is_file()}
        environment = os.environ.copy()
        environment["MPLBACKEND"] = "Agg"
        environment["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run([sys.executable, str(script)], cwd=output_dir, env=environment,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=90, check=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": CONSTANT.PYTHON_SCRIPT_TIMEOUT}, ensure_ascii=False)
        except OSError as exc:
            return json.dumps({"error": CONSTANT.PYTHON_SCRIPT_START_FAILED.format(exc=exc)}, ensure_ascii=False)
        images = [path.relative_to(task_dir.resolve()).as_posix() for path in sorted(output_dir.rglob("*"))
                  if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
                  and path.stat().st_mtime_ns != before.get(path)]
        return json.dumps({"exit_code": result.returncode, "stdout": result.stdout[-4000:],
                           "stderr": result.stderr[-4000:], "images": images}, ensure_ascii=False)

    @tool("read_saved_python_script")
    def read_saved_python_script(path: str) -> str:
        """读取本会话保存的 Python 源文件，含早期消息保存的文件。"""
        source = saved_script(path)
        if source is None:
            return json.dumps({"error": CONSTANT.PYTHON_SCRIPT_OUTSIDE_CONVERSATION}, ensure_ascii=False)
        try:
            content = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return json.dumps({"error": CONSTANT.PYTHON_SCRIPT_READ_FAILED.format(exc=exc)}, ensure_ascii=False)
        return json.dumps({"path": source.relative_to(task_dir.resolve()).as_posix(),
                           "source": content[:20000], "truncated": len(content) > 20000}, ensure_ascii=False)

    return [write_python_script, run_python_script, read_saved_python_script]


def create_workflow(model, task_dir: Path, message_id: str, document_tools=None, previous_message_ids=None,
                    *, allow_execution: bool = True):
    python_tools = create_python_tools(task_dir, message_id, previous_message_ids)
    prompt = CONSTANT.PYCHARM_SYSTEM_PROMPT
    if not allow_execution:
        python_tools = [item for item in python_tools if item.name != "run_python_script"]
        prompt = CONSTANT.PYCHARM_MCP_SYSTEM_PROMPT
    return build_workflow(
        model,
        prompt,
        [*python_tools, *(document_tools or [])],
    )
