"""把报告模板和生成路径限定在当前会话内。"""

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from server.General_Tools.report_writer import inspect_report_template, write_experiment_report
from .task_scope import active_hfss_chapters, hfss_output_matches_chapter

from . import CONSTANT


def hfss_report_images(task_dir: Path, chapters: frozenset[int] | None = None) -> list[dict[str, str]]:
    root = task_dir.resolve()
    hfss_root = (root / "hfss").resolve()
    if not hfss_root.is_dir():
        return []
    images = []
    seen = set()
    for manifest_path in sorted(hfss_root.rglob("workflow.json")):
        if chapters is not None and not hfss_output_matches_chapter(manifest_path.parent.relative_to(hfss_root), chapters):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for screenshot in manifest.get("screenshots", []):
            if not isinstance(screenshot, dict) or not screenshot.get("valid_evidence"):
                continue
            path = Path(str(screenshot.get("path", ""))).resolve()
            if (not path.is_relative_to(hfss_root) or path in seen or not path.is_file()
                    or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}):
                continue
            seen.add(path)
            images.append({"path": path.relative_to(root).as_posix(), "caption": path.stem})
            if len(images) == 40:
                return images
    return images


def wants_experiment_report(metadata: dict) -> bool:
    current = metadata["messages"][-1]
    requirements = current.get("requirements", "")
    if ("report" in current.get("deliverables", []) or "实验报告" in requirements
            or "word" in requirements.lower() and any(word in requirements for word in ("截图", "图片", "报告"))):
        return True
    if requirements.strip().rstrip("。!！") != "继续完成剩余步骤":
        return False
    return any("report" in message.get("deliverables", []) or "实验报告" in message.get("requirements", "")
               for message in metadata["messages"][:-1])


def create_report_tools(task_dir: Path, metadata: dict):
    current = metadata["messages"][-1]
    wants_report = wants_experiment_report(metadata)
    wants_paper = "paper" in current.get("deliverables", []) or "论文" in current.get("requirements", "")
    if not wants_report and not wants_paper:
        return []
    template_message = next((message for message in reversed(metadata["messages"]) if message.get("report_template")), None)
    template_path = None
    if template_message:
        name = Path(template_message["report_template"]["stored_name"]).name
        template_path = task_dir / "messages" / template_message["id"] / name
        if template_path.suffix.lower() == ".doc":
            template_path = template_path.with_suffix(".docx")

    @tool("inspect_report_template")
    def inspect_current_report_template() -> str:
        """检查本会话上传的 DOCX 报告模板及其中的占位符。"""
        if template_path is None:
            return json.dumps({"template": None, "message": CONSTANT.REPORT_TOOLS_MESSAGE_NO_TEMPLATE_UPLOADED_USE_DEFAULT_WORD})
        try:
            return json.dumps(inspect_report_template(template_path), ensure_ascii=False)
        except (OSError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool("write_experiment_report")
    def write_current_experiment_report(title: str, sections: list[dict[str, Any]], replacements: dict[str, str] | None = None) -> str:
        """生成 DOCX 实验报告。章节支持 paragraphs、images、code_files 和 code_blocks。"""
        output = task_dir / "reports" / current["id"] / "experiment_report.docx"
        try:
            prepared = sections
            if current.get("software_id") == "pycharm" and isinstance(sections, list):
                source_dir = task_dir / "pycharm" / current["id"]
                scripts = sorted(source_dir.glob("*.py")) if source_dir.is_dir() else []
                screenshots = sorted(path for path in source_dir.rglob("*") if path.suffix.lower() in {".png", ".jpg", ".jpeg"}) if source_dir.is_dir() else []
                prepared = []
                for section in sections:
                    if not isinstance(section, dict):
                        prepared.append(section)
                        continue
                    item = dict(section)
                    heading = str(item.get("heading", ""))
                    if ("源代码" in heading or "源码" in heading) and scripts and not item.get("code_files"):
                        item["code_files"] = [path.relative_to(task_dir).as_posix() for path in scripts]
                    if ("截图" in heading or "实验结果" in heading) and screenshots and not item.get("images"):
                        item["images"] = [{"path": path.relative_to(task_dir).as_posix(), "caption": path.stem} for path in screenshots]
                    if "截图" in heading and not item.get("images"):
                        raise ValueError(CONSTANT.PYTHON_SCREENSHOTS_REQUIRED)
                    prepared.append(item)
            elif current.get("software_id") == "hfss" and isinstance(sections, list):
                scope = active_hfss_chapters(metadata["messages"])
                screenshots = hfss_report_images(task_dir, scope[1] if scope else None)
                prepared = []
                for section in sections:
                    if not isinstance(section, dict):
                        prepared.append(section)
                        continue
                    item = dict(section)
                    if ("截图" in str(item.get("heading", "")) or "图片" in str(item.get("heading", ""))) and not item.get("images"):
                        if not screenshots:
                            raise ValueError(CONSTANT.HFSS_SCREENSHOTS_REQUIRED)
                        item["images"] = screenshots
                    prepared.append(item)
            path = write_experiment_report(task_dir, output, title, prepared, template_path, replacements)
        except (OSError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"report_file": path.name, "template_used": template_message["report_template"]["name"] if template_message else None,
                           "download_url": f"/api/tasks/{metadata['id']}/reports/{current['id']}/{path.name}"}, ensure_ascii=False)

    @tool("write_experiment_paper")
    def write_current_experiment_paper(title: str, sections: list[dict[str, Any]]) -> str:
        """根据标题和章节生成 DOCX 论文，并保存到当前会话的 papers 目录。"""
        output = task_dir / "papers" / current["id"] / "experiment_paper.docx"
        try:
            path = write_experiment_report(task_dir, output, title, sections)
        except (OSError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"paper_file": path.name, "path": path.relative_to(task_dir).as_posix()}, ensure_ascii=False)

    return ([inspect_current_report_template, write_current_experiment_report] if wants_report else []) + ([write_current_experiment_paper] if wants_paper else [])
