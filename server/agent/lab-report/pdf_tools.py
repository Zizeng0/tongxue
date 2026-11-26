"""只允许模型读取当前会话上传的 PDF 指定页面。"""

import json
from pathlib import Path

from langchain_core.tools import tool

from server.General_Tools.pdf_on_demand import describe_pdf, read_pdf_pages

from . import CONSTANT


def current_documents(task_dir: Path, metadata: dict) -> dict[str, tuple[Path, str]]:
    messages = metadata["messages"]
    guide_message = next((message for message in reversed(messages) if message.get("guide")), None)
    selected = []
    if guide_message:
        selected.append((guide_message, guide_message["guide"], "guide"))
    selected.extend((messages[-1], item, item.get("stored_name", "")) for item in messages[-1]["attachments"])
    selected.extend((messages[-1], item, item.get("stored_name", "")) for item in messages[-1].get("other_files", []))
    documents = {}
    for message, item, document_id in selected:
        stored_name = item.get("stored_name", "")
        if Path(stored_name).suffix.lower() != ".pdf" or not document_id:
            continue
        documents[document_id] = (task_dir / "messages" / message["id"] / Path(stored_name).name, item["name"])
    return documents


def create_pdf_tools(task_dir: Path, metadata: dict):
    documents = current_documents(task_dir, metadata)
    if not documents:
        return []

    @tool("read_pdf_pages")
    def read_selected_pdf_pages(document_id: str, start_page: int, end_page: int) -> str:
        """从上传的 PDF 读取最多五个指定页，对缺少文本层的页面执行 OCR。"""
        if document_id not in documents:
            return json.dumps({"error": CONSTANT.PDF_TOOLS_ERROR_UNKNOWN_PDF_DOCUMENT_ID})
        try:
            return json.dumps(read_pdf_pages(documents[document_id][0], start_page, end_page), ensure_ascii=False)
        except (OSError, ValueError, RuntimeError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool("list_pdf_bookmarks")
    def list_selected_pdf_bookmarks(document_id: str, start: int = 0) -> str:
        """读取 PDF 的书签和页码，可用 start 分段浏览较长的书签目录。"""
        if document_id not in documents:
            return json.dumps({"error": CONSTANT.PDF_TOOLS_ERROR_UNKNOWN_PDF_DOCUMENT_ID})
        try:
            return json.dumps(describe_pdf(documents[document_id][0], start), ensure_ascii=False)
        except (OSError, ValueError, RuntimeError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return [read_selected_pdf_pages, list_selected_pdf_bookmarks]


def document_outline(task_dir: Path, metadata: dict) -> str:
    lines = []
    guide_message = next((message for message in reversed(metadata["messages"]) if message.get("guide")), None)
    if guide_message and not guide_message["guide"].get("stored_name"):
        lines.append(f"旧资料 {guide_message['guide']['name']} 没有可读取的本地文件。")
    for document_id, (path, name) in current_documents(task_dir, metadata).items():
        try:
            description = describe_pdf(path)
            chapters = ", ".join(f"第{item['page']}页 {item['title']}" for item in description["chapters"])
            lines.append(f"PDF {name}，document_id={document_id}，共{description['page_count']}页，书签{description['bookmark_count']}项；书签：{chapters or '无'}。更多书签可调用 list_pdf_bookmarks；需要内容时调用 read_pdf_pages，按需选择页码，每次最多5页。")
        except (OSError, ValueError, RuntimeError) as exc:
            lines.append(f"PDF {name} 无法读取：{exc}")
    return "\n".join(lines)
