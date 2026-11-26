"""按会话列出项目资料和成果，并提供鉴权下载。"""

from bisect import bisect_right
from datetime import datetime, timezone
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from server import database
from server.conversations import conversation_rows, ensure_table, find_conversation, read_conversation
from server.data_model import User
from server.features_services.jwt_validation import require_user

from . import CONSTANT


router = APIRouter(prefix="/api/projects", tags=["projects"])
SOURCE_EXTENSIONS = {".py", ".hfss", ".v", ".sv", ".vhd", ".vhdl", ".qpf", ".qsf", ".pkt", ".pka"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def data_dir() -> Path:
    from server.app import DATA_DIR
    return DATA_DIR


def _file_info(root: Path, path: Path, name: str | None = None) -> dict | None:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not path.is_file():
        return None
    return {"name": name or path.name, "path": path.relative_to(root).as_posix(), "size": path.stat().st_size}


def _deliverable_kind(relative: Path) -> str | None:
    if relative.parts[0] == "reports" and relative.suffix.lower() == ".docx":
        return "report"
    if relative.parts[0] == "papers" and relative.suffix.lower() == ".docx":
        return "paper"
    if relative.suffix.lower() in SOURCE_EXTENSIONS and not relative.name.startswith("."):
        return "code"
    if relative.suffix.lower() in IMAGE_EXTENSIONS and not relative.name.startswith("."):
        return "image"
    return None


OUTPUT_LABELS = {"image": "图片", "report": "实验报告", "paper": "论文", "code": "代码"}


def _name_outputs(row: dict, feature: str) -> None:
    timestamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).strftime("%Y%m%d_%H%M%S")
    counts: dict[str, int] = {}
    for item in row["outputs"]:
        kind = item["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        item["name"] = f"{timestamp}_{feature}_{OUTPUT_LABELS[kind]}_{counts[kind]:02d}{Path(item['path']).suffix.lower()}"


def project_detail(conversation_id: str, owner_id: int) -> dict:
    task_dir, raw_metadata = find_conversation(data_dir(), conversation_id, owner_id)
    metadata = read_conversation(raw_metadata)
    kind = metadata.get("kind", "task")
    messages = metadata["messages"]
    rows = []
    guide = None
    for message in messages:
        message_dir = task_dir / "messages" / message["id"]
        inputs = []
        for file_info in [message.get("report_template"), *message.get("attachments", []), *message.get("other_files", [])]:
            if file_info and file_info.get("stored_name"):
                item = _file_info(task_dir, message_dir / Path(file_info["stored_name"]).name, file_info.get("name"))
                if item:
                    inputs.append(item)
        guide_info = message.get("guide")
        if guide is None and guide_info and guide_info.get("stored_name"):
            guide = _file_info(task_dir, message_dir / Path(guide_info["stored_name"]).name, guide_info.get("name"))
        rows.append({
            "message_id": message["id"], "created_at": message["created_at"],
            "input_preview": message["requirements"],
            "inputs": inputs, "outputs": [],
        })

        if kind in {"solution", "roommate"}:
            for relative_name in message.get("output_images", []):
                relative = Path(relative_name)
                if len(relative.parts) != 3 or relative.parts[0] != "solutions" or relative.suffix.lower() != ".png":
                    continue
                if kind == "solution" and relative.parts[1] != message["id"]:
                    continue
                item = _file_info(task_dir, task_dir / relative)
                if item:
                    item["kind"] = "image"
                    rows[-1]["outputs"].append(item)

        if kind == "roommate":
            for filename in message.get("report_files", []):
                if filename == Path(filename).name and Path(filename).suffix.lower() == ".docx":
                    item = _file_info(task_dir, task_dir / "reports" / message["id"] / filename)
                    if item:
                        rows[-1]["outputs"].append({**item, "kind": "report"})
            for filename in message.get("artifact_files", []):
                output_kind = {".pptx": "presentation", ".docx": "paper"}.get(Path(filename).suffix.lower())
                if filename == Path(filename).name and output_kind:
                    item = _file_info(task_dir, task_dir / "artifacts" / message["id"] / filename)
                    if item:
                        rows[-1]["outputs"].append({**item, "kind": output_kind})

    timestamps = [datetime.fromisoformat(message["created_at"]).replace(tzinfo=timezone.utc).timestamp()
                  if datetime.fromisoformat(message["created_at"]).tzinfo is None
                  else datetime.fromisoformat(message["created_at"]).timestamp() for message in messages]
    for directory, children, files in os.walk(task_dir) if kind == "task" else []:
        children[:] = [child for child in children if child != "messages" and not child.startswith(".") and not child.lower().endswith(".hfssresults")]
        for filename in sorted(files):
            path = Path(directory) / filename
            relative = path.relative_to(task_dir)
            output_kind = _deliverable_kind(relative)
            if output_kind is None or not rows:
                continue
            item = _file_info(task_dir, path)
            if item:
                item["kind"] = output_kind
                message_id = relative.parts[1] if relative.parts[0] in {"reports", "papers", "pycharm"} and len(relative.parts) > 2 else None
                index = next((position for position, row in enumerate(rows) if row["message_id"] == message_id), None)
                if index is None:
                    index = max(0, bisect_right(timestamps, path.stat().st_mtime) - 1)
                rows[index]["outputs"].append(item)
    if kind != "roommate":
        for row in rows:
            _name_outputs(row, "解题" if kind == "solution" else "实验工作台")
    return {
        "id": metadata["id"], "title": metadata["title"], "created_at": metadata["created_at"],
        "kind": kind,
        "software_id": messages[-1].get("software_id", "") if messages else "",
        "guide": guide, "messages": rows,
    }


@router.get("")
def list_projects(user: User = Depends(require_user)):
    ensure_table()
    with database.get_session() as session:
        items = [{
            "id": metadata["id"], "title": metadata["title"], "created_at": metadata["created_at"],
            "kind": metadata.get("kind", "task"),
            "software_id": metadata["messages"][-1].get("software_id", "") if metadata["messages"] else "",
        } for metadata in (read_conversation(row.metadata_json) for row in conversation_rows(session, user.id))]
        return {"items": sorted(items, key=lambda item: item["created_at"], reverse=True)}


@router.get("/{conversation_id}")
def get_project(conversation_id: str, user: User = Depends(require_user)):
    return project_detail(conversation_id, user.id)


@router.get("/{conversation_id}/files/{file_path:path}")
def download_project_file(conversation_id: str, file_path: str, user: User = Depends(require_user)):
    detail = project_detail(conversation_id, user.id)
    allowed = {item["path"] for row in detail["messages"] for item in [*row["inputs"], *row["outputs"]]}
    if detail["guide"]:
        allowed.add(detail["guide"]["path"])
    if file_path not in allowed:
        raise HTTPException(status_code=404, detail=CONSTANT.FILE_NOT_FOUND)
    task_dir, _ = find_conversation(data_dir(), conversation_id, user.id)
    path = (task_dir / file_path).resolve()
    if not path.is_relative_to(task_dir.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail=CONSTANT.FILE_NOT_FOUND)
    original_name = next((item["name"] for row in detail["messages"] for item in [*row["inputs"], *row["outputs"]] if item["path"] == file_path), None)
    if detail["guide"] and detail["guide"]["path"] == file_path:
        original_name = detail["guide"]["name"]
    return FileResponse(path, filename=original_name or path.name)
