"""Ansys HFSS 的电磁仿真规划工具与工作流。"""

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from server.General_Tools.hfss_tools import (
    _ALLOWED_STEP_METHODS,
    HfssExperimentSpec,
    execute_hfss_experiment,
    parse_hfss_parameters,
    prepare_hfss_experiment,
)
from server.General_Tools.pdf_segments import format_task_segments, select_task_segments
from server.agent.workflow import build_workflow

from . import CONSTANT


def _check_hfss_spec_fields(spec: dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise ValueError(CONSTANT.HFSS_ERROR_HFSS_SPEC_MUST_BE_OBJECT)
    allowed = {field.name for field in fields(HfssExperimentSpec)} | {"create_new_project"}
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise ValueError(CONSTANT.HFSS_ERROR_UNKNOWN_HFSS_SPEC_FIELDS.format(join=', '.join(unknown)))
    if "solve" in spec and not isinstance(spec["solve"], bool):
        raise ValueError(CONSTANT.HFSS_ERROR_SOLVE_MUST_BE_BOOLEAN)


def _task_path(value: Any, root: Path, field: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(CONSTANT.HFSS_ERROR_MUST_BE_NON_EMPTY_PATH.format(field=field))
    raw = Path(value).expanduser()
    path = (raw if raw.is_absolute() else root / raw).resolve()
    if not path.is_relative_to(root):
        raise ValueError(CONSTANT.HFSS_ERROR_MUST_STAY_WITHIN_CURRENT_TASK_HFSS.format(field=field, root=root))
    return path


def _task_scoped_spec(spec: dict[str, Any], experiment_root: Path) -> dict[str, Any]:
    _check_hfss_spec_fields(spec)
    root = experiment_root.resolve()
    missing = [field for field in ("project_path", "output_dir") if not spec.get(field)]
    if missing:
        raise ValueError(CONSTANT.HFSS_ERROR_HFSS_EXPERIMENT_SPEC_MISSING.format(join=', '.join(missing)))
    return {
        **spec,
        "project_path": str(_task_path(spec["project_path"], root, "project_path")),
        "output_dir": str(_task_path(spec["output_dir"], root, "output_dir")),
    }


def _task_guide_index(index_path: str, task_dir: Path, guide_index: Path | None) -> str:
    if guide_index is None:
        raise ValueError(CONSTANT.HFSS_ERROR_NO_GUIDE_SEGMENT_INDEX_AVAILABLE_FOR)
    root = task_dir.resolve()
    expected = guide_index.resolve()
    if not expected.is_relative_to(root) or not expected.is_file():
        raise ValueError(CONSTANT.HFSS_ERROR_CURRENT_TASK_GUIDE_SEGMENT_INDEX_UNAVAILABLE)
    raw = Path(index_path).expanduser()
    selected = (raw if raw.is_absolute() else root / raw).resolve()
    if selected != expected:
        raise ValueError(CONSTANT.HFSS_ERROR_INDEX_PATH_MUST_BE_CURRENT_TASK)
    return str(expected)


@tool
def plan_hfss_simulation(goal: str) -> str:
    """根据高频电磁仿真目标列出 HFSS 建模与结果验证步骤。"""
    return (
        CONSTANT.HFSS_PLAN.format(goal=goal)
    )


@tool
def extract_hfss_guide_parameters(guide_excerpt: str) -> str:
    """从当前任务相关的指导书文字中提取明确给出的数值参数；不推测缺失的几何或端口。"""
    return json.dumps(parse_hfss_parameters(guide_excerpt), ensure_ascii=False)


@tool
def describe_hfss_experiment_spec(topic: str = "all") -> str:
    """查询通用 HFSS 实验规格的参数格式，支持 spec、geometry、materials、simulation、raw_methods 或 all。"""
    sections: dict[str, Any] = {
        "spec": {
            "required": ["project_path", "output_dir"],
            "optional": ["project_mode", "design_name", "units", "processors", "solve", "variables",
                         "geometry", "materials", "ports", "boundaries", "setup", "sweeps",
                         "output_variables", "steps", "screenshots", "exports"],
            "project_mode": ["new", "existing"],
            "execution": "Use ordered steps for procedures whose sequence matters; use solve=false for modeling-only tasks.",
            "step_templates": "The geometry, materials, and simulation action examples belong in steps, in guide order.",
            "files": "project_path must end in .hfss; screenshot and export paths must stay within output_dir.",
        },
        "geometry": {
            "create_box": {"action": "create_box", "name": "Block", "x": "0mm", "y": "0mm", "z": "0mm",
                           "dx": "6mm", "dy": "8mm", "dz": "3mm", "material": "vacuum", "transparency": 0.4},
            "create_cylinder": {"action": "create_cylinder", "name": "Post", "x": "0mm", "y": "0mm", "z": "0mm",
                                "radius": "2mm", "height": "5mm", "axis": "Z", "num_sides": 0},
            "create_sphere": {"action": "create_sphere", "name": "Ball", "x": "0mm", "y": "0mm", "z": "0mm",
                              "radius": "2mm"},
            "create_circle": {"action": "create_circle", "name": "Disk", "x": "0mm", "y": "0mm", "z": "0mm",
                              "radius": "2mm", "axis": "Z"},
            "create_rectangle": {"action": "create_rectangle", "name": "Sheet", "x": "0mm", "y": "0mm", "z": "0mm",
                                 "width": "4mm", "height": "3mm", "axis": "Z"},
            "create_helix": {"action": "create_helix", "object": "HelixCircle", "center": ["0mm", "0mm", "0mm"],
                             "start_direction": ["0mm", "1mm", "0mm"], "pitch": "4mm", "turns": 3,
                             "right_hand": True, "radius_increment": "-1mm"},
            "create_spiral": {"action": "create_spiral", "object": "SpiralCircle", "center": ["0mm", "0mm", "0mm"],
                              "start_direction": ["0mm", "1mm", "0mm"], "turns": 3,
                              "right_hand": True, "radius_increment": "3mm"},
            "profile_sweeps": "Create the cross-section circle first. The sweep modifies that selected circle; do not pass NAME:Attributes, ParentPartID, Parts, Center, Vector or RadiusChange.",
            "move": {"action": "move", "object": "Block", "vector": ["2mm", "0mm", "0mm"]},
            "rotate": {"action": "rotate", "object": "Block", "axis": "Z", "angle": "90deg"},
            "scale": {"action": "scale", "object": "Block", "factors": [1, 2, 1.5]},
            "duplicate_along_line": {"action": "duplicate_along_line", "object": "Block",
                                     "vector": ["5mm", "0mm", "0mm"], "num_clones": 3},
            "unite": {"action": "unite", "objects": ["Block", "Block2"]},
            "subtract": {"action": "subtract", "blank": "Block", "tool": "Cutout"},
            "create_face_cs": {"action": "create_face_cs", "object": "Block", "face_point": ["0mm", "0mm", "0mm"],
                               "origin": ["0mm", "0mm", "0mm"], "x_axis_point": ["1mm", "0mm", "0mm"]},
            "capture": "Each step may set capture_after to an image filename; an explicit screenshot step uses action=screenshot and path.",
        },
        "materials": {
            "define_material": {"action": "define_material", "name": "MY_FR4", "permittivity": "4.4",
                                "dielectric_loss_tangent": "0.02"},
            "set_material": {"action": "set_material", "object": "Block", "material": "MY_FR4"},
        },
        "simulation": {
            "variable": {"variables": {"Offset": "0in"}},
            "wave_port": {"action": "assign_wave_port", "name": "Port1", "object": "Block",
                          "face_point": ["0mm", "4mm", "1.5mm"],
                          "integration_line": [["0mm", "4mm", "0mm"], ["0mm", "4mm", "3mm"]]},
            "face_ids": "Prefer object plus face_point to resolve the face ID at runtime; numeric faces are also accepted.",
            "setup": {"action": "insert_setup", "name": "Setup1", "frequency": "10GHz", "max_delta_s": 0.02},
            "frequency_sweep": {"action": "insert_frequency_sweep", "setup": "Setup1", "name": "Sweep1",
                                "start": "8GHz", "stop": "10GHz", "step_size": "0.01GHz"},
            "export": {"type": "network_data", "path": "sparams.s3p", "solutions": ["Setup1:Sweep1"]},
            "evidence": "Use screenshot valid_evidence for modeling steps. A model capture is not proof of solved S parameters or field data; inspect measured_results_available.",
        },
        "raw_methods": {
            "allowed": {module: sorted(methods) for module, methods in _ALLOWED_STEP_METHODS.items()},
            "step_format": {"module": "editor", "method": "Rotate", "args": [
                ["NAME:Selections", "Selections:=", "Block", "NewPartsModelFlag:=", "Model"],
                ["NAME:RotateParameters", "RotateAxis:=", "Z", "RotateAngle:=", "90deg"],
            ]},
            "guidance": "Use named create_helix/create_spiral actions for profile sweeps. For other methods without named actions, use the exact HFSS scripting signature and nested args; never infer undocumented argument names from a GUI instruction.",
        },
    }
    if topic not in {*sections, "all"}:
        return json.dumps({"valid": False, "error": CONSTANT.HFSS_ERROR_UNKNOWN_TOPIC.format(topic=topic), "topics": [*sections, "all"]}, ensure_ascii=False)
    content = sections if topic == "all" else {topic: sections[topic]}
    return json.dumps(content, ensure_ascii=False)


@tool
def retrieve_hfss_guide_sections(
    index_path: str,
    chapter_hint: str = "",
    task_request: str = "",
    max_chars: int = 9000,
    max_segments: int = 8,
) -> str:
    """从已建立的 PDF 片段索引中读取当前 HFSS 章节，不把整本指导书塞进上下文。

    ``index_path`` 是上传指导书旁的 ``*_analysis/segments.json`` 路径，
    ``chapter_hint`` 可传 ``2.2``、``2.3`` 或 ``4.1-4.8`` 等章节提示，
    ``task_request`` 传本次要完成的具体操作。工具只返回带页码、章节和复核标记的
    片段；检索不到明确匹配时返回 ``weak_match=true``，模型必须回到原文核对。
    """
    try:
        if not index_path or max_chars < 1 or max_chars > 24000 or max_segments < 1 or max_segments > 32:
            raise ValueError(CONSTANT.HFSS_ERROR_INVALID_RETRIEVAL_LIMITS_OR_INDEX_PATH)
        path = Path(index_path).expanduser().resolve()
        if path.suffix.lower() != ".json" or not path.is_file():
            raise FileNotFoundError(CONSTANT.HFSS_ERROR_HFSS_GUIDE_SEGMENT_INDEX_NOT_FOUND.format(path=path))
        index = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or not isinstance(index.get("segments"), list):
            raise ValueError(CONSTANT.HFSS_ERROR_HFSS_GUIDE_SEGMENT_INDEX_INVALID)
        query = " ".join(part.strip() for part in (chapter_hint, task_request) if part and part.strip())
        if not query:
            raise ValueError(CONSTANT.HFSS_ERROR_CHAPTER_HINT_OR_TASK_REQUEST_REQUIRED)
        selection = select_task_segments(index, query, max_chars=max_chars, max_segments=max_segments)
        return json.dumps(
            {
                "index_path": str(path),
                "chapter_hint": chapter_hint,
                "task_request": task_request,
                "weak_match": selection["weak_match"],
                "selected_pages": sorted({item["page"] for item in selection["segments"]}),
                "selected_chapters": sorted({item["chapter"] for item in selection["segments"]}),
                "requested_sections": selection.get("requested_sections", []),
                "omitted_sections": selection.get("omitted_sections", []),
                "truncated": selection.get("truncated", False),
                "formatted_segments": format_task_segments(selection),
            },
            ensure_ascii=False,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)


@tool
def read_hfss_guide_pages(index_path: str, start_page: int, end_page: int, max_chars: int = 12000) -> str:
    """按 PDF 页码连续读取最多 4 页指导书 OCR/原生文本，补全检索命中页附近的步骤。

    先调用 retrieve_hfss_guide_sections 找到章节页码，再把其 index_path 和
    start_page/end_page 传入本工具。每页保留原 PDF 页码、所属章节及待复核标记。
    """
    try:
        if not index_path or start_page < 1 or end_page < start_page or end_page - start_page > 3:
            raise ValueError(CONSTANT.HFSS_ERROR_REQUEST_CONSECUTIVE_PDF_PAGES)
        if max_chars < 1 or max_chars > 24000:
            raise ValueError(CONSTANT.HFSS_ERROR_MAX_CHARS_MUST_BE_BETWEEN)
        path = Path(index_path).expanduser().resolve()
        if path.suffix.lower() != ".json" or not path.is_file():
            raise FileNotFoundError(CONSTANT.HFSS_ERROR_HFSS_GUIDE_SEGMENT_INDEX_NOT_FOUND.format(path=path))
        index = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or not isinstance(index.get("segments"), list):
            raise ValueError(CONSTANT.HFSS_ERROR_HFSS_GUIDE_SEGMENT_INDEX_INVALID)
        if end_page > index.get("page_count", 0):
            raise ValueError(CONSTANT.HFSS_ERROR_REQUESTED_PAGE_EXCEEDS_GUIDE_PAGE_COUNT)
        segments = sorted(
            (item for item in index["segments"] if start_page <= item["page"] <= end_page),
            key=lambda item: (item["page"], item["id"]),
        )
        selected = []
        remaining = max_chars
        for item in segments:
            if remaining <= 0:
                break
            excerpt = {**item, "text": item["text"][:remaining]}
            selected.append(excerpt)
            remaining -= len(excerpt["text"])
        selection = {"source_pdf": index.get("source_pdf", path.name), "weak_match": False, "segments": selected}
        return json.dumps(
            {
                "index_path": str(path),
                "requested_pages": [start_page, end_page],
                "selected_pages": sorted({item["page"] for item in selected}),
                "truncated": len(selected) < len(segments) or bool(selected and len(selected[-1]["text"]) < len(segments[len(selected) - 1]["text"])),
                "formatted_segments": format_task_segments(selection),
            },
            ensure_ascii=False,
        )
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)


@tool
def prepare_parameterized_hfss_experiment(spec: dict[str, Any]) -> str:
    """根据指导书步骤和明确参数生成通用 HFSS 脚本，不启动 HFSS。

    spec 至少包含 project_path 和 output_dir。新工程用 project_mode="new"；
    按指导书顺序传入 steps，例如
    {"action":"create_box","name":"Block","x":"0mm","y":"0mm","z":"0mm",
     "dx":"6mm","dy":"8mm","dz":"3mm","capture_after":"01.jpg"}。
    支持基本几何体、移动、旋转、缩放、沿线复制、布尔运算、材料、端口及求解设置等具名动作；
    螺旋体用 create_helix/create_spiral 具名动作；其他允许的 HFSS 方法用 module/method/args。
    截图用 capture_after。仅在需要求解时设置 solve=true，并提供端口、设置、
    扫频及 exports。返回脚本和实验清单路径；配置错误返回 error 供修正。
    """
    try:
        _check_hfss_spec_fields(spec)
        result = prepare_hfss_experiment(spec)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@tool
def run_parameterized_hfss_experiment(spec: dict[str, Any]) -> str:
    """按完整参数规格调用通用 HFSS 脚本，建立模型并按需求解和导出结果。

    spec 至少包含 project_path 和 output_dir。用 steps 逐项传入指导书操作；
    建模演示设置 solve=false。只有指导书要求电磁求解，且已明确端口、激励、
    求解设置时才设置 solve=true。返回每步失败、求解状态和实测结果可用性；
    求解失败或未导出时，不得把示例数值称为实测结果。
    """
    try:
        _check_hfss_spec_fields(spec)
        manifest = execute_hfss_experiment(spec)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
    failed_steps = []
    for line in manifest["configuration_status"].splitlines():
        stage, separator, status = line.partition("=")
        if separator and status.split() and status.split()[0] != "0":
            failed_steps.append(line)
    return json.dumps(
        {
            "project": manifest["project"],
            "workflow_manifest": str(Path(manifest["output_dir"]) / "workflow.json"),
            "script": manifest["script"],
            "script_status": manifest.get("script_result", {}).get("status"),
            "script_error": manifest.get("script_result", {}).get("error"),
            "configuration_succeeded": manifest["configuration_succeeded"],
            "configuration_error": manifest.get("configuration_error"),
            "failed_steps": failed_steps,
            "screenshots": manifest.get("screenshots", []),
            "solve_status": manifest["solve_result"]["status"],
            "solve_error": manifest["solve_result"].get("error"),
            "exports": manifest["exports"],
            "measured_results_available": manifest["measured_results_available"],
        },
        ensure_ascii=False,
    )


def create_workflow(model, task_dir: Path, guide_index: Path | None = None, pdf_tools=None):
    task_dir = Path(task_dir).resolve()
    experiment_root = task_dir / "hfss"

    @tool("retrieve_hfss_guide_sections")
    def scoped_retrieve_hfss_guide_sections(
        index_path: str, chapter_hint: str = "", task_request: str = "",
        max_chars: int = 9000, max_segments: int = 8,
    ) -> str:
        """从当前任务上传的 HFSS 指导书分段索引中读取相关章节。"""
        try:
            selected = _task_guide_index(index_path, task_dir, guide_index)
        except (OSError, ValueError) as exc:
            return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
        return retrieve_hfss_guide_sections.invoke({
            "index_path": selected, "chapter_hint": chapter_hint, "task_request": task_request,
            "max_chars": max_chars, "max_segments": max_segments,
        })

    @tool("read_hfss_guide_pages")
    def scoped_read_hfss_guide_pages(
        index_path: str, start_page: int, end_page: int, max_chars: int = 12000,
    ) -> str:
        """从当前任务上传的 HFSS 指导书分段索引中读取连续页。"""
        try:
            selected = _task_guide_index(index_path, task_dir, guide_index)
        except (OSError, ValueError) as exc:
            return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
        return read_hfss_guide_pages.invoke({
            "index_path": selected, "start_page": start_page, "end_page": end_page,
            "max_chars": max_chars,
        })

    @tool("prepare_parameterized_hfss_experiment")
    def scoped_prepare_parameterized_hfss_experiment(spec: dict[str, Any]) -> str:
        """验证 HFSS 实验配置，并在当前任务的 HFSS 目录准备实验文件。"""
        try:
            scoped = _task_scoped_spec(spec, experiment_root)
        except (OSError, ValueError) as exc:
            return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
        return prepare_parameterized_hfss_experiment.invoke({"spec": scoped})

    @tool("run_parameterized_hfss_experiment")
    def scoped_run_parameterized_hfss_experiment(spec: dict[str, Any]) -> str:
        """在当前任务的 HFSS 目录运行实验。"""
        try:
            scoped = _task_scoped_spec(spec, experiment_root)
        except (OSError, ValueError) as exc:
            return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
        return run_parameterized_hfss_experiment.invoke({"spec": scoped})

    return build_workflow(
        model,
        CONSTANT.HFSS_SYSTEM_PROMPT,
        [plan_hfss_simulation, extract_hfss_guide_parameters, describe_hfss_experiment_spec,
         *([scoped_retrieve_hfss_guide_sections, scoped_read_hfss_guide_pages]
           if guide_index is not None and guide_index.is_file() else []),
         scoped_prepare_parameterized_hfss_experiment,
         scoped_run_parameterized_hfss_experiment, *(pdf_tools or [])],
    )
