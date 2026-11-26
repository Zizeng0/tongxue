"""按需读取当前消息中的文本实验文件。"""

import json
from pathlib import Path

from docx import Document
from langchain_core.tools import tool

from . import CONSTANT


TEXT_EXTENSIONS = {".txt", ".csv", ".tsv", ".json", ".py"}
MAX_PREVIEW_BYTES = 64 * 1024


def experiment_file_preview(path: Path, name: str) -> dict:
    if path.suffix.lower() == ".docx":
        document = Document(path)
        content = "\n".join(paragraph.text for paragraph in document.paragraphs)
        content += "\n" + "\n".join("\t".join(cell.text for cell in row.cells)
                                     for table in document.tables for row in table.rows)
        content = content[:MAX_PREVIEW_BYTES]
    else:
        with path.open("rb") as source:
            content = source.read(MAX_PREVIEW_BYTES).decode("utf-8-sig", errors="replace")
    return {"name": name, "text": content, "truncated": path.stat().st_size > MAX_PREVIEW_BYTES}


def create_file_tools(task_dir: Path, metadata: dict):
    current = metadata["messages"][-1]
    message_dir = task_dir / "messages" / current["id"]
    files = {item["stored_name"]: item for item in current.get("other_files", [])
             if Path(item.get("stored_name", "")).suffix.lower() in TEXT_EXTENSIONS | {".docx"}}
    if not files:
        return []

    @tool("read_experiment_file")
    def read_experiment_file(stored_name: str) -> str:
        """读取上传的 TXT、CSV、TSV、JSON、PY 或 DOCX 实验文件，最多返回 64 KB 文本。"""
        if stored_name not in files:
            return json.dumps({"error": CONSTANT.UNKNOWN_EXPERIMENT_FILE}, ensure_ascii=False)
        path = message_dir / stored_name
        try:
            result = experiment_file_preview(path, files[stored_name]["name"])
        except (OSError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)

    return [read_experiment_file]
