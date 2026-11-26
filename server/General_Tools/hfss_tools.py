"""HFSS 本地批处理、实验配置和结果证据工具。

这些函数只通过 HFSS 的公开命令行和 VBScript 接口工作。求解器没有返回成功
状态时，结果清单会明确标记为不可用，不会把示例数据当作实测数据。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import CONSTANT


DEFAULT_HFSS_PATHS = (
    Path(r"D:\HFSS\HFSS15.0\Win64\hfss.exe"),
    Path(r"C:\Program Files\AnsysEM\AnsysEM\v*\Win64\hfss.exe"),
)
DEFAULT_TEE_EXAMPLE = Path(r"D:\HFSS\HFSS15.0\Win64\Examples\RF_Microwave\OptimTee.hfss")
SOLVER_ERROR_MARKERS = (
    "Error decoding model data or writing it to disk",
    "Engine Detected Error",
    "execution with error",
    "execution error on server",
)


def _vb_quote(value: str) -> str:
    value = str(value)
    if any(character in value for character in "\r\n\x00"):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_SCRIPT_VALUES_CANNOT_CONTAIN_NEWLINES)
    parts = []
    ascii_text = ""
    for character in value:
        if ord(character) < 128:
            ascii_text += character
            continue
        if ascii_text:
            parts.append('"' + ascii_text.replace('"', '""') + '"')
            ascii_text = ""
        encoded = character.encode("utf-16-le")
        for position in range(0, len(encoded), 2):
            parts.append(f"ChrW({int.from_bytes(encoded[position:position + 2], 'little')})")
    if ascii_text or not parts:
        parts.append('"' + ascii_text.replace('"', '""') + '"')
    return " & ".join(parts)


def _vb_value(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    return _vb_quote(str(value))


def _vb_array(values: Sequence[Any]) -> str:
    rendered = []
    for value in values:
        rendered.append(_vb_array(value) if isinstance(value, (list, tuple)) else _vb_value(value))
    return "Array(" + ", ".join(rendered) + ")"


@dataclass(frozen=True)
class HfssExperimentSpec:
    """来自指导书或大模型的可执行 HFSS 实验规格。

    ``geometry`` 使用 ``{"type": "box", "name": ..., "x": ..., ...}``；
    ``ports``、``boundaries``、``sweeps`` 和 ``exports`` 也采用字典列表，便于
    从 OCR/结构化模型输出直接传入。未知字段会被忽略并记录在清单之外。
    """

    project_path: Path
    output_dir: Path
    project_mode: str = "existing"
    solve: bool = False
    design_name: str = "HFSSDesign1"
    units: str = "mm"
    processors: int = 1
    variables: Mapping[str, str] | None = None
    geometry: Sequence[Mapping[str, Any]] = ()
    materials: Sequence[Mapping[str, Any]] = ()
    ports: Sequence[Mapping[str, Any]] = ()
    boundaries: Sequence[Mapping[str, Any]] = ()
    setup: Mapping[str, Any] | None = None
    sweeps: Sequence[Mapping[str, Any]] = ()
    output_variables: Sequence[Mapping[str, Any]] = ()
    steps: Sequence[Mapping[str, Any]] = ()
    screenshots: Sequence[Mapping[str, Any]] = ()
    exports: Sequence[Mapping[str, Any]] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HfssExperimentSpec":
        if not isinstance(data, Mapping):
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_EXPERIMENT_SPEC_MUST_BE_OBJECT)
        required = ("project_path", "output_dir")
        missing = [name for name in required if not data.get(name)]
        if missing:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_EXPERIMENT_SPEC_MISSING.format(join=', '.join(missing)))
        for name in ("create_new_project", "solve"):
            if name in data and not isinstance(data[name], bool):
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_MUST_BE_JSON_BOOLEAN.format(name=name))
        processors = int(data.get("processors", 1))
        if processors < 1 or processors > 256:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_PROCESSORS_MUST_BE_BETWEEN)
        project_mode = str(data.get("project_mode", "new" if data.get("create_new_project") else "existing"))
        if project_mode not in {"existing", "new"}:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_PROJECT_MODE_MUST_BE_EXISTING_OR)
        for name in ("variables", "setup"):
            if data.get(name) is not None and not isinstance(data[name], Mapping):
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_MUST_BE_OBJECT.format(name=name))
        for name in ("geometry", "materials", "ports", "boundaries", "sweeps",
                     "output_variables", "steps", "screenshots", "exports"):
            items = data.get(name)
            if items is None:
                continue
            if not isinstance(items, (list, tuple)):
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_MUST_BE_LIST_OBJECTS.format(name=name))
            for index, item in enumerate(items):
                if not isinstance(item, Mapping):
                    raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_MUST_BE_OBJECT_BE_AN_OBJECT.format(name=name, index=index))
        return cls(
            project_path=Path(str(data["project_path"])).resolve(),
            output_dir=Path(str(data["output_dir"])).resolve(),
            project_mode=project_mode,
            solve=bool(data.get("solve", False)),
            design_name=str(data.get("design_name", "HFSSDesign1")),
            units=str(data.get("units", "mm")),
            processors=processors,
            variables=dict(data.get("variables") or {}),
            geometry=tuple(data.get("geometry") or ()),
            materials=tuple(data.get("materials") or ()),
            ports=tuple(data.get("ports") or ()),
            boundaries=tuple(data.get("boundaries") or ()),
            setup=dict(data.get("setup") or {}) or None,
            sweeps=tuple(data.get("sweeps") or ()),
            output_variables=tuple(data.get("output_variables") or ()),
            steps=tuple(data.get("steps") or ()),
            screenshots=tuple(data.get("screenshots") or ()),
            exports=tuple(data.get("exports") or ()),
        )


def parse_hfss_parameters(text: str) -> dict[str, Any]:
    """从指导书/OCR 文本提取常见 HFSS 参数，供规格层继续补全。

    该方法只提取明确出现的数值，不猜测材料、端口位置或几何拓扑；无法
    确认的字段应由大模型或用户补充到 ``HfssExperimentSpec``。
    """
    if not isinstance(text, str):
        raise TypeError(CONSTANT.HFSS_TOOLS_ERROR_TEXT_MUST_BE_STRING)
    result: dict[str, Any] = {"variables": {}, "sweeps": []}
    for name, value, unit in re.findall(
        r"(?i)\b(Offset|Width|Length|Height|Frequency|Freq)\s*[:=：]\s*([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|in|GHz|MHz)?",
        text,
    ):
        result["variables"][name] = f"{value}{unit or ''}"
    sweep = re.search(
        r"(?i)\b(Offset|Width|Length|Height)\b[^\n]{0,30}?(?:from|从)\s*([0-9.]+\s*[a-z]+)\s*(?:to|至|到)\s*([0-9.]+\s*[a-z]+)[^\n]{0,30}?(?:step|步长)\s*[=:：]?\s*([0-9.]+\s*[a-z]+)",
        text,
    )
    if sweep:
        variable, start, stop, step = sweep.groups()
        result["sweeps"].append({"variable": variable, "data": f"LIN {start.replace(' ', '')} {stop.replace(' ', '')} {step.replace(' ', '')}"})
    frequency = result["variables"].get("Frequency") or result["variables"].get("Freq")
    if frequency:
        result["frequency"] = frequency
    return result


def _property_array(name: str, values: Mapping[str, Any]) -> str:
    parts: list[Any] = [f"NAME:{name}"]
    for key, value in values.items():
        parts.extend([f"{key}:=", value])
    return _vb_array(parts)


def _output_path(spec: HfssExperimentSpec, value: Any) -> Path:
    path = Path(str(value))
    resolved = (spec.output_dir / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(spec.output_dir):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_OUTPUT_PATH_MUST_STAY_INSIDE.format(resolved=resolved))
    return resolved


def _capture_targets(spec: HfssExperimentSpec) -> list[tuple[Path, str, str | None]]:
    default_image = spec.output_dir / "hfss_model.jpg"
    targets = []
    for index, item in enumerate(spec.steps, start=1):
        stage = f"Step{index}:{item.get('label') or item.get('action') or item.get('method')}"
        if item.get("action") == "screenshot":
            targets.append((_output_path(spec, item.get("path", default_image)), stage, None))
        if capture := item.get("capture_after"):
            details = capture if isinstance(capture, Mapping) else {
                "path": capture if isinstance(capture, str) else f"step_{index:02}.jpg"
            }
            targets.append((_output_path(spec, details.get("path", default_image)), stage + ":capture", stage))
    for item in spec.screenshots or (() if targets else ({"path": default_image},)):
        path = _output_path(spec, item.get("path", default_image))
        targets.append((path, f"Screenshot:{path.name}", None))
    return targets


def _capture_paths(spec: HfssExperimentSpec) -> list[Path]:
    return [path for path, _, _ in _capture_targets(spec)]


def _recorded_stages(script: str) -> list[str]:
    stages = []
    for expression in re.findall(r"(?m)^Record (.+)$", script):
        fragments = re.findall(r'"(?:[^"]|"")*"|ChrW\(\d+\)', expression)
        if not fragments or expression != " & ".join(fragments):
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_INVALID_HFSS_STEP_STATUS_EXPRESSION)
        encoded = bytearray()
        for fragment in fragments:
            if fragment.startswith('"'):
                encoded.extend(fragment[1:-1].replace('""', '"').encode("utf-16-le"))
            else:
                encoded.extend(int(fragment[5:-1]).to_bytes(2, "little"))
        stages.append(encoded.decode("utf-16-le"))
    return stages


def _configuration_status(script: str, status: str) -> bool:
    expected = _recorded_stages(script)
    lines = status.splitlines()
    if len(lines) != len(expected) or not expected or expected[-1] != "Save":
        return False
    for stage, line in zip(expected, lines):
        name, separator, code = line.partition("=")
        if not separator or name != stage or re.fullmatch(r"0(?:\s.*)?", code) is None:
            return False
    return True


def _read_status_file(path: Path, missing_message: str) -> str:
    if not path.is_file():
        return missing_message
    content = path.read_bytes()
    encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    return content.decode(encoding, errors="replace")


_ALLOWED_STEP_METHODS = {
    "editor": {
        "CreateBox", "CreateCylinder", "CreateSphere", "CreateRectangle", "CreateCircle",
        "CreatePolyline", "CreateHelix", "CreateSpiral", "CreateFaceCS", "AssignMaterial", "Unite", "Subtract", "Intersect", "Imprint", "ImprintProjection", "Split", "Move", "Rotate", "Scale",
        "DuplicateAlongLine", "DuplicateAroundAxis", "Mirror", "ProjectSheet", "CreateRelativeCS",
        "SetWCS", "ChangeProperty", "SetModelUnits", "FitAll", "Delete",
    },
    "design": {"ChangeProperty"},
    "boundaries": {"AssignWavePort", "AssignLumpedPort", "AssignRadiation", "AssignPerfectE", "AssignPerfectH"},
    "analysis": {"InsertSetup", "InsertFrequencySweep"},
    "optimetrics": {"InsertSetup"},
    "variables": {"CreateOutputVariable"},
    "definition_manager": {"AddMaterial"},
}


def _step_argument(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return _vb_array(value)
    return _vb_value(value)


def _primitive_attributes(item: Mapping[str, Any], default_name: str) -> str:
    attributes = {
        "PartCoordinateSystem": item.get("part_coordinate_system", "Global"),
        "Name": item.get("name", default_name),
        "MaterialName": item.get("material", "vacuum"),
        "SolveInside": bool(item.get("solve_inside", True)),
    }
    if "transparency" in item:
        attributes["Transparency"] = float(item["transparency"])
    return _property_array("Attributes", attributes)


def _step_objects(item: Mapping[str, Any], minimum: int = 1) -> str:
    objects = item.get("objects", [item["object"]] if "object" in item else [])
    if not isinstance(objects, (list, tuple)) or len(objects) < minimum or any(not isinstance(name, str) or not name for name in objects):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_STEP_REQUIRES_AT_LEAST_OBJECT.format(minimum=minimum))
    return ",".join(objects)


def _step_vector(item: Mapping[str, Any], field: str) -> Sequence[Any]:
    vector = item.get(field)
    if not isinstance(vector, (list, tuple)) or len(vector) != 3:
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_MUST_CONTAIN_THREE_COORDINATES.format(field=field))
    return vector


def _profile_sweep_command(item: Mapping[str, Any], action: str) -> str:
    allowed = {"action", "object", "center", "start_direction", "turns", "right_hand", "radius_increment", "label", "capture_after"}
    if action == "create_helix":
        allowed.add("pitch")
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_UNSUPPORTED_FIELDS_USE_NAMED_ACTION_FIELDS.format(action=action, join=', '.join(sorted(unknown))))
    object_name = item.get("object")
    if not isinstance(object_name, str) or not object_name.strip():
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_REQUIRES_EXISTING_CROSS_SECTION_CIRCLE_IN.format(action=action))
    center = _step_vector(item, "center")
    start_direction = _step_vector(item, "start_direction")
    turns = item.get("turns")
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_TURNS_MUST_BE_POSITIVE_INTEGER.format(action=action))
    right_hand = item.get("right_hand", True)
    if not isinstance(right_hand, bool):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_RIGHT_HAND_MUST_BE_BOOLEAN.format(action=action))
    if action == "create_helix" and not item.get("pitch"):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_CREATE_HELIX_REQUIRES_PITCH)
    if action == "create_spiral" and not item.get("radius_increment"):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_CREATE_SPIRAL_REQUIRES_RADIUS_INCREMENT)
    parameters = {
        **dict(zip(("XCenter", "YCenter", "ZCenter"), center)),
        **dict(zip(("XStartDir", "YStartDir", "ZStartDir"), start_direction)),
    }
    if action == "create_helix":
        parameters["Thread"] = item["pitch"]
    parameters.update({"NumThread": turns, "RightHand": right_hand})
    if item.get("radius_increment") is not None:
        parameters["RadiusIncrement"] = item["radius_increment"]
    method, parameter_name = ("CreateHelix", "HelixParameters") if action == "create_helix" else ("CreateSpiral", "SpiralParameters")
    selections = _vb_array(["NAME:Selections", "Selections:=", object_name])
    return f"editor.{method} {selections}, {_property_array(parameter_name, parameters)}"


def _validate_raw_profile_sweep(method: str, args: Sequence[Any]) -> None:
    parameter_name = "HelixParameters" if method == "CreateHelix" else "SpiralParameters"
    if len(args) != 2 or any(not isinstance(argument, (list, tuple)) for argument in args):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_NEEDS_SELECTIONS_ARRAYS_PREFER_CREATE_HELIX.format(method=method, parameter_name=parameter_name))
    selection, parameters = args
    if len(selection) != 3 or selection[0] != "NAME:Selections" or selection[1] != "Selections:=" or not isinstance(selection[2], str) or not selection[2]:
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_REQUIRES_NAME_SELECTIONS_EXISTING_CIRCLE_PREFER.format(method=method))
    if not parameters or parameters[0] != f"NAME:{parameter_name}" or len(parameters) % 2 != 1:
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_REQUIRES_NAME_PREFER_CREATE_HELIX_CREATE.format(method=method, parameter_name=parameter_name))
    keys = parameters[1::2]
    required = {"XCenter:=", "YCenter:=", "ZCenter:=", "XStartDir:=", "YStartDir:=", "ZStartDir:=", "NumThread:=", "RightHand:="}
    if method == "CreateHelix":
        required.add("Thread:=")
    else:
        required.add("RadiusIncrement:=")
    allowed = required | ({"RadiusIncrement:="} if method == "CreateHelix" else set())
    if any(not isinstance(key, str) for key in keys) or not required.issubset(keys) or len(keys) != len(set(keys)) or set(keys) - allowed:
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_PARAMETERS_MUST_USE_HFSS_XCENTER_XSTARTDIR.format(method=method))


def _step_command(item: Mapping[str, Any]) -> str:
    action = str(item.get("action", ""))
    if action in {"create_helix", "create_spiral"}:
        return _profile_sweep_command(item, action)
    if action == "create_box":
        geometry = {
            "XPosition": item.get("x", item.get("XPosition", "0mm")),
            "YPosition": item.get("y", item.get("YPosition", "0mm")),
            "ZPosition": item.get("z", item.get("ZPosition", "0mm")),
            "XSize": item.get("dx", item.get("XSize", "1mm")),
            "YSize": item.get("dy", item.get("YSize", "1mm")),
            "ZSize": item.get("dz", item.get("ZSize", "1mm")),
        }
        attributes = {
            "PartCoordinateSystem": item.get("part_coordinate_system", "Global"),
            "Name": item.get("name", "Box"),
            "MaterialName": item.get("material", "vacuum"),
            "SolveInside": bool(item.get("solve_inside", True)),
        }
        if "transparency" in item:
            attributes["Transparency"] = float(item["transparency"])
        return f"editor.CreateBox {_property_array('BoxParameters', geometry)}, {_property_array('Attributes', attributes)}"
    if action in {"create_cylinder", "create_sphere", "create_circle", "create_rectangle"}:
        if action == "create_cylinder":
            required = ("radius", "height")
            parameters = {"XCenter": item.get("x", "0mm"), "YCenter": item.get("y", "0mm"), "ZCenter": item.get("z", "0mm"),
                          "Radius": item.get("radius"), "Height": item.get("height"), "WhichAxis": item.get("axis", "Z"),
                          "NumSides": int(item.get("num_sides", 0))}
            method, parameter_name = "CreateCylinder", "CylinderParameters"
        elif action == "create_sphere":
            required = ("radius",)
            parameters = {"XCenter": item.get("x", "0mm"), "YCenter": item.get("y", "0mm"), "ZCenter": item.get("z", "0mm"),
                          "Radius": item.get("radius")}
            method, parameter_name = "CreateSphere", "SphereParameters"
        elif action == "create_circle":
            required = ("radius",)
            parameters = {"XCenter": item.get("x", "0mm"), "YCenter": item.get("y", "0mm"), "ZCenter": item.get("z", "0mm"),
                          "Radius": item.get("radius"), "WhichAxis": item.get("axis", "Z"),
                          "NumSegments": int(item.get("num_segments", 0))}
            method, parameter_name = "CreateCircle", "CircleParameters"
        else:
            required = ("width", "height")
            parameters = {"IsCovered": bool(item.get("is_covered", True)), "XStart": item.get("x", "0mm"),
                          "YStart": item.get("y", "0mm"), "ZStart": item.get("z", "0mm"),
                          "Width": item.get("width"), "Height": item.get("height"), "WhichAxis": item.get("axis", "Z")}
            method, parameter_name = "CreateRectangle", "RectangleParameters"
        missing = [field for field in required if item.get(field) is None]
        if missing:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_REQUIRES.format(action=action, join=', '.join(missing)))
        if "WhichAxis" in parameters and parameters["WhichAxis"] not in {"X", "Y", "Z"}:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_AXIS_MUST_BE_X_Y_OR.format(action=action))
        return f"editor.{method} {_property_array(parameter_name, parameters)}, {_primitive_attributes(item, method[6:])}"
    if action in {"move", "rotate", "scale", "duplicate_along_line"} and "args" not in item:
        selection = _vb_array(["NAME:Selections", "Selections:=", _step_objects(item),
                               "NewPartsModelFlag:=", item.get("new_parts_model_flag", "Model")])
        if action == "move":
            vector = _step_vector(item, "vector")
            parameters = _property_array("TranslateParameters", dict(zip(
                ("TranslateVectorX", "TranslateVectorY", "TranslateVectorZ"), vector)))
            return f"editor.Move {selection}, {parameters}"
        if action == "rotate":
            axis = item.get("axis", "Z")
            if axis not in {"X", "Y", "Z"} or not item.get("angle"):
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_ROTATE_REQUIRES_ANGLE_AXIS_X_Y)
            return f"editor.Rotate {selection}, {_property_array('RotateParameters', {'RotateAxis': axis, 'RotateAngle': item['angle']})}"
        if action == "scale":
            factors = _step_vector(item, "factors")
            return f"editor.Scale {selection}, {_property_array('ScaleParameters', dict(zip(('ScaleX', 'ScaleY', 'ScaleZ'), factors)))}"
        vector = _step_vector(item, "vector")
        count = int(item.get("num_clones", 0))
        if count < 1:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_DUPLICATE_ALONG_LINE_REQUIRES_NUM_CLONES)
        parameters = _property_array("DuplicateToAlongLineParameters", {
            "CreateNewObjects": bool(item.get("create_new_objects", True)),
            **dict(zip(("XComponent", "YComponent", "ZComponent"), vector)), "NumClones": count,
        })
        options = _property_array("Options", {"DuplicateBoundaries": bool(item.get("duplicate_boundaries", False))})
        return f"editor.DuplicateAlongLine {selection}, {parameters}, {options}"
    if action == "unite":
        selections = item.get("objects") or []
        if len(selections) < 2:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_UNITE_REQUIRES_AT_LEAST_TWO_OBJECTS)
        return f"editor.Unite {_vb_array(['NAME:Selections', 'Selections:=', ','.join(map(str, selections))])}, {_vb_array(['NAME:UniteParameters', 'KeepOriginals:=', bool(item.get('keep_originals', False))])}"
    if action == "subtract":
        blank = item.get("blank")
        tool = item.get("tool")
        if not blank or not tool:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_SUBTRACT_REQUIRES_BLANK_TOOL)
        return f"editor.Subtract {_vb_array(['NAME:Selections', 'Blank Parts:=', blank, 'Tool Parts:=', tool])}, {_vb_array(['NAME:SubtractParameters', 'KeepOriginals:=', bool(item.get('keep_originals', False))])}"
    if action == "assign_wave_port":
        faces = item.get("faces") or ([item["face"]] if "face" in item else [])
        face_point = item.get("face_point")
        if faces and face_point is not None:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_ASSIGN_WAVE_PORT_ACCEPTS_EITHER_FACES)
        if face_point is not None:
            if not item.get("object") or not isinstance(face_point, (list, tuple)) or len(face_point) != 3:
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_ASSIGN_WAVE_PORT_REQUIRES_OBJECT_THREE)
            face_query = _property_array("FaceParameters", {
                "BodyName": item["object"], "XPosition": face_point[0],
                "YPosition": face_point[1], "ZPosition": face_point[2],
            })
            face_expression = "Array(port_face_id)"
        elif faces:
            if not isinstance(faces, (list, tuple)):
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_ASSIGN_WAVE_PORT_FACES_MUST_BE)
            face_expression = _vb_array(faces)
        else:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_ASSIGN_WAVE_PORT_REQUIRES_FACES_OR)
        params: list[Any] = ["NumModes:=", int(item.get("modes", 1)), "DoDeembed:=", bool(item.get("deembed", False)),
                             "DeembedDist:=", item.get("deembed_dist", "0mm"), "DoRenorm:=", bool(item.get("renormalize", False)),
                             "RenormValue:=", item.get("renorm_value", "50ohm")]
        if item.get("integration_line"):
            line = item["integration_line"]
            if not isinstance(line, (list, tuple)) or len(line) != 2 or any(not isinstance(point, (list, tuple)) or len(point) != 3 for point in line):
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_ASSIGN_WAVE_PORT_INTEGRATION_LINE_REQUIRES)
            params.append(["NAME:Modes", ["NAME:Mode1", "ModeNum:=", 1, "UseIntLine:=", True,
                          ["NAME:IntLine", "Start:=", line[0], "End:=", line[1], "CharImp:=", item.get("char_impedance", "Zpi")]]])
        arguments = [_vb_value(f"NAME:{item.get('name', 'Port1')}"), _vb_value("Faces:="), face_expression]
        arguments.extend(_step_argument(value) for value in params)
        command = f"boundaries.AssignWavePort Array({', '.join(arguments)})"
        if face_point is None:
            return command
        return "\n".join([
            f"port_face_id = editor.GetFaceByPosition({face_query})",
            "If port_face_id <= 0 Then",
            "  Err.Raise vbObjectError + 1001, \"AssignWavePort\", \"Unable to resolve port face ID\"",
            "Else",
            f"  {command}",
            "End If",
        ])
    if action == "insert_setup":
        params = [f"NAME:{item.get('name', 'Setup1')}", "Frequency:=", item.get("frequency", "10GHz"), "MaxDeltaS:=", float(item.get("max_delta_s", 0.02)), "MaximumPasses:=", int(item.get("maximum_passes", 6)), "MinimumPasses:=", 1, "MinimumConvergedPasses:=", 1, "IsEnabled:=", True]
        return f"analysis.InsertSetup \"HfssDriven\", {_vb_array(params)}"
    if action == "insert_frequency_sweep":
        params = [f"NAME:{item.get('name', 'Sweep1')}", "IsEnabled:=", True, "StartValue:=", item.get("start", "8GHz"), "StopValue:=", item.get("stop", "10GHz"), "Type:=", item.get("type", "Interpolating"), "SaveFields:=", bool(item.get("save_fields", False)), "ExtrapToDC:=", False, "SetupType:=", item.get("setup_type", "LinearSetup"), "StepSize:=", item.get("step_size", "0.01GHz")]
        return f"analysis.InsertFrequencySweep {_vb_quote(str(item.get('setup', 'Setup1')))}, {_vb_array(params)}"
    if action == "define_material":
        name = item.get("name")
        if not name:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_DEFINE_MATERIAL_REQUIRES_NAME)
        params: list[Any] = [f"NAME:{name}"]
        for field in ("permittivity", "permeability", "conductivity", "dielectric_loss_tangent", "magnetic_loss_tangent"):
            if field in item:
                params.extend([f"{field}:=", str(item[field])])
        if len(params) == 1:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_DEFINE_MATERIAL_REQUIRES_AT_LEAST_ONE)
        return f"definition_manager.AddMaterial {_vb_array(params)}"
    if action == "set_material":
        object_name = item.get("object")
        material = item.get("material")
        if not object_name or not material:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_SET_MATERIAL_REQUIRES_OBJECT_MATERIAL)
        selections = _vb_array(["NAME:Selections", "Selections:=", object_name])
        attributes = _vb_array(["NAME:Attributes", "MaterialName:=", material, "SolveInside:=", bool(item.get("solve_inside", True))])
        return f"editor.AssignMaterial {selections}, {attributes}"
    if action == "create_face_cs":
        object_name = item.get("object")
        if not object_name:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_CREATE_FACE_CS_REQUIRES_OBJECT)
        positions = {}
        for field in ("face_point", "origin", "x_axis_point"):
            position = item.get(field)
            if not isinstance(position, (list, tuple)) or len(position) != 3:
                raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_CREATE_FACE_CS_REQUIRES_THREE_COORDINATES.format(field=field))
            positions[field] = position
        face_query = _property_array("FaceParameters", {
            "BodyName": object_name,
            "XPosition": positions["face_point"][0],
            "YPosition": positions["face_point"][1],
            "ZPosition": positions["face_point"][2],
        })
        def position_array(name: str, values: Sequence[Any], uv: Sequence[float]) -> str:
            if item.get("attach_to_face", False):
                return (f'Array("NAME:{name}", "IsAttachedToEntity:=", True, '
                        f'"EntityID:=", face_cs_face_id, "PositionType:=", "OnFace", '
                        f'"UParam:=", {float(uv[0])}, "VParam:=", {float(uv[1])})')
            return _vb_array([f"NAME:{name}", "IsAttachedToEntity:=", False, "EntityID:=", -1,
                              "PositionType:=", "AbsolutePosition", "UParam:=", 0, "VParam:=", 0,
                              "XPosition:=", values[0], "YPosition:=", values[1], "ZPosition:=", values[2]])
        origin_uv = item.get("origin_uv", [0.5, 0.5])
        axis_uv = item.get("axis_uv", [0.75, 0.5])
        if any(not isinstance(value, (list, tuple)) or len(value) != 2 for value in (origin_uv, axis_uv)):
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_CREATE_FACE_CS_UV_POSITIONS_MUST)
        params = (f'Array("NAME:FaceCSParameters", "FaceID:=", face_cs_face_id, "PartID:=", face_cs_part_id, '
                  f'{position_array("OriginPosn", positions["origin"], origin_uv)}, '
                  f'{position_array("AxisPosn", positions["x_axis_point"], axis_uv)}, '
                  f'"WhichAxis:=", {_vb_quote(str(item.get("axis", "X")))})')
        attributes = _vb_array(["NAME:Attributes", "Name:=", str(item.get("name", "FaceCS1"))])
        return "\n".join([
            f"face_cs_face_id = editor.GetFaceByPosition({face_query})",
            f"face_cs_part_id = editor.GetObjectIDByName({_vb_quote(str(object_name))})",
            "If face_cs_face_id <= 0 Or face_cs_part_id <= 0 Then",
            "  Err.Raise vbObjectError + 1000, \"CreateFaceCS\", \"Unable to resolve face or object ID\"",
            "Else",
            f"  editor.CreateFaceCS {params}, {attributes}",
            "End If",
        ])
    module = str(item.get("module", "editor"))
    method = str(item.get("method", ""))
    if action and not method:
        aliases = {
            "move": "Move", "rotate": "Rotate", "scale": "Scale",
            "project_sheet": "ProjectSheet",
            "duplicate_along_line": "DuplicateAlongLine", "duplicate_around_axis": "DuplicateAroundAxis",
            "create_relative_cs": "CreateRelativeCS", "set_wcs": "SetWCS",
            "change_property": "ChangeProperty", "set_model_units": "SetModelUnits",
        }
        method = aliases.get(action, "")
    if method not in _ALLOWED_STEP_METHODS.get(module, set()):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_UNSUPPORTED_HFSS_STEP.format(module=module, method=method))
    args = item.get("args")
    if not isinstance(args, (list, tuple)):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_STEP_ARGS_MUST_BE_LIST)
    if module == "editor" and method == "Delete" and not item.get("allow_delete", False):
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_DELETE_STEP_REQUIRES_ALLOW_DELETE)
    if module == "editor" and method in {"CreateHelix", "CreateSpiral"}:
        _validate_raw_profile_sweep(method, args)
    return f"{module}.{method} " + ", ".join(_step_argument(value) for value in args)


def build_hfss_script(spec: HfssExperimentSpec | Mapping[str, Any], *, save_path: Path | None = None) -> str:
    """根据声明式实验规格生成可重复执行的 HFSS VBScript。"""
    if not isinstance(spec, HfssExperimentSpec):
        spec = HfssExperimentSpec.from_mapping(spec)
    if spec.project_path.suffix.lower() != ".hfss":
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_PROJECT_PATH_MUST_END_IN_HFSS)
    if spec.project_mode == "existing" and not spec.project_path.is_file():
        raise FileNotFoundError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_PROJECT_NOT_FOUND.format(project_path=spec.project_path))
    if spec.project_mode == "new" and spec.project_path.exists():
        raise FileExistsError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_PROJECT_ALREADY_EXISTS.format(project_path=spec.project_path))
    output = spec.output_dir
    image_default = output / "hfss_model.jpg"
    lines = [
        "On Error Resume Next",
        "Dim app, desktop, project, design, editor, boundaries, analysis, optimetrics, variables, solutions, definition_manager, output, face_cs_face_id, face_cs_part_id, port_face_id, last_step_succeeded",
        "Sub Record(stage)",
        "  last_step_succeeded = (Err.Number = 0)",
        "  output.WriteLine stage & \"=\" & Hex(Err.Number) & \" \" & Err.Description",
        "  Err.Clear",
        "End Sub",
        f"Set output = CreateObject(\"Scripting.FileSystemObject\").CreateTextFile({_vb_quote((output / 'hfss_script_status.txt').as_posix())}, True, True)",
        "Set app = CreateObject(\"AnsoftHfss.HfssScriptInterface\")",
        "Record \"Connect\"",
        "Set desktop = app.GetAppDesktop()",
        "Record \"Desktop\"",
        f"app.SetNumberOfProcessors {spec.processors}",
        "Record \"Processors\"",
        f"Set project = desktop.{'OpenProject(' + _vb_quote(spec.project_path.as_posix()) + ')' if spec.project_mode == 'existing' else 'NewProject()'}",
        "Record \"Project\"",
        f"Set design = {'project.GetDesign(' + _vb_quote(spec.design_name) + ')' if spec.project_mode == 'existing' else 'project.InsertDesign(\"HFSS\", ' + _vb_quote(spec.design_name) + ', \"DrivenModal\", \"\")'}",
        "Record \"Design\"",
        "Set editor = design.SetActiveEditor(\"3D Modeler\")",
        "Set boundaries = design.GetModule(\"BoundarySetup\")",
        "Set analysis = design.GetModule(\"AnalysisSetup\")",
        "Set optimetrics = design.GetModule(\"Optimetrics\")",
        "Set definition_manager = project.GetDefinitionManager()",
        "Set variables = design.GetModule(\"OutputVariable\")",
        "Set solutions = design.GetModule(\"Solutions\")",
        f"editor.SetModelUnits {_vb_array(['NAME:Units Parameter', 'Units:=', spec.units, 'Rescale:=', False])}",
        "Record \"Units\"",
    ]
    def add_step(stage: str, command: str) -> None:
        if "=" in stage:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_STEP_LABEL_CANNOT_CONTAIN)
        lines.append(command)
        lines.append(f"Record {_vb_quote(stage)}")

    def add_capture(item: Mapping[str, Any], stage: str, after_step: bool = False) -> None:
        path = _output_path(spec, item.get("path", image_default))
        if path.suffix.lower() not in {".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_SCREENSHOT_PATH_MUST_USE_JPG)
        command = f"editor.ExportModelImageToFile {_vb_quote(path.as_posix())}, {int(item.get('width', 1200))}, {int(item.get('height', 900))}, {_vb_array(['NAME:SaveImageParams', 'ShowAxis:=', 'Default', 'ShowGrid:=', 'Default', 'ShowRuler:=', 'Default'])}"
        if after_step:
            lines.extend(["If last_step_succeeded Then", "  editor.FitAll", f"  {command}", "Else",
                          '  Err.Raise vbObjectError + 1002, "Capture", "Skipped because preceding step failed"', "End If"])
            lines.append(f"Record {_vb_quote(stage)}")
        else:
            lines.append("editor.FitAll")
            add_step(stage, command)

    for name, value in (spec.variables or {}).items():
        command = "design.ChangeProperty " + _vb_array([
            "NAME:AllTabs", [
                "NAME:LocalVariableTab", ["NAME:PropServers", "LocalVariables"],
                ["NAME:NewProps", ["NAME:" + str(name), "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", value]],
            ],
        ])
        add_step(f"Variable:{name}", command)
    for item in spec.geometry:
        kind = str(item.get("type", "box")).lower()
        if kind == "box":
            geometry = {key: item.get(key, "0") for key in ("XPosition", "YPosition", "ZPosition", "XSize", "YSize", "ZSize")}
            attributes = {"PartCoordinateSystem": item.get("part_coordinate_system", "Global"), "Name": item.get("name", "Box"), "MaterialName": item.get("material", "vacuum"), "SolveInside": bool(item.get("solve_inside", True))}
            if "transparency" in item:
                attributes["Transparency"] = float(item["transparency"])
            add_step(f"Geometry:{item.get('name', 'Box')}", f"editor.CreateBox {_property_array('BoxParameters', geometry)}, {_property_array('Attributes', attributes)}")
        elif kind == "cylinder":
            geometry = {key: item.get(key, "0") for key in ("XCenter", "YCenter", "ZCenter", "Radius", "Height", "WhichAxis")}
            attributes = {"PartCoordinateSystem": item.get("part_coordinate_system", "Global"), "Name": item.get("name", "Cylinder"), "MaterialName": item.get("material", "vacuum"), "SolveInside": bool(item.get("solve_inside", True))}
            add_step(f"Geometry:{item.get('name', 'Cylinder')}", f"editor.CreateCylinder {_property_array('CylinderParameters', geometry)}, {_property_array('Attributes', attributes)}")
        else:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_UNSUPPORTED_HFSS_GEOMETRY_TYPE.format(kind=kind))
    for item in spec.materials:
        object_name = item.get("object") or item.get("name")
        material = item.get("material")
        if not object_name or not material:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_MATERIAL_ENTRIES_REQUIRE_OBJECT_MATERIAL)
        selections = _vb_array(["NAME:Selections", "Selections:=", object_name])
        attributes = _vb_array(["NAME:Attributes", "MaterialName:=", material, "SolveInside:=", bool(item.get("solve_inside", True))])
        add_step(f"Material:{object_name}", f"editor.AssignMaterial {selections}, {attributes}")
    for item in spec.ports:
        name = str(item.get("name", "Port1"))
        add_step(f"Port:{name}", _step_command({**item, "action": "assign_wave_port"}))
    for item in spec.boundaries:
        kind = str(item.get("type", "radiation")).lower()
        name = str(item.get("name", "Boundary1"))
        faces = list(item.get("faces") or [])
        if kind in {"radiation", "radiationboundary"}:
            add_step(f"Boundary:{name}", f"boundaries.AssignRadiation {_vb_array([f'NAME:{name}', 'Faces:=', faces])}")
        elif kind in {"perfecte", "perfect_e", "pec"}:
            add_step(f"Boundary:{name}", f"boundaries.AssignPerfectE {_vb_array([f'NAME:{name}', 'Faces:=', faces])}")
        else:
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_UNSUPPORTED_HFSS_BOUNDARY_TYPE.format(kind=kind))
    if spec.setup:
        setup_name = str(spec.setup.get("name", "Setup1"))
        setup_values = {"NAME": setup_name, "Frequency": spec.setup.get("frequency", "10GHz"), "MaxDeltaS": spec.setup.get("max_delta_s", 0.02), "MaximumPasses": spec.setup.get("maximum_passes", 10), "MinimumPasses": spec.setup.get("minimum_passes", 1), "MinimumConvergedPasses": spec.setup.get("minimum_converged_passes", 1), "IsEnabled": spec.setup.get("enabled", True)}
        setup_params = [f"NAME:{setup_values.pop('NAME')}"]
        for key, value in setup_values.items():
            setup_params.extend([f"{key}:=", value])
        add_step(f"Setup:{setup_name}", f"analysis.InsertSetup \"HfssDriven\", {_vb_array(setup_params)}")
    for item in spec.sweeps:
        setup_name = str(item.get("setup", "Setup1"))
        sweep_params = [f"NAME:{item.get('name', 'Sweep1')}", "IsEnabled:=", bool(item.get("enabled", True)), "StartValue:=", item.get("start", "1GHz"), "StopValue:=", item.get("stop", "10GHz"), "Type:=", item.get("type", "Interpolating"), "SaveFields:=", bool(item.get("save_fields", False)), "ExtrapToDC:=", bool(item.get("extrapolate_to_dc", False))]
        if "step_size" in item:
            sweep_params.extend(["SetupType:=", item.get("setup_type", "LinearSetup"), "StepSize:=", item["step_size"]])
        add_step(f"Sweep:{item.get('name', 'Sweep1')}", f"analysis.InsertFrequencySweep {_vb_quote(setup_name)}, {_vb_array(sweep_params)}")
    for item in spec.output_variables:
        add_step(f"OutputVariable:{item['name']}", f"variables.CreateOutputVariable {_vb_quote(str(item['name']))}, {_vb_quote(str(item['expression']))}, {_vb_quote(str(item.get('solution', 'Setup1 : LastAdaptive')))}, {_vb_quote(str(item.get('report_type', 'Modal Solution Data')))}, {_vb_array(item.get('context', ['Domain:=', 'Sweep']))}")
    for index, item in enumerate(spec.steps, start=1):
        stage = f"Step{index}:{item.get('label') or item.get('action') or item.get('method')}"
        if item.get("action") == "screenshot":
            add_capture(item, stage)
        else:
            add_step(stage, _step_command(item))
        if item.get("capture_after"):
            capture = item["capture_after"]
            capture_data = capture if isinstance(capture, Mapping) else {"path": str(capture) if isinstance(capture, str) else f"step_{index:02}.jpg"}
            add_capture(capture_data, stage + ":capture", after_step=True)
    end_screenshots = spec.screenshots or (() if any(item.get("action") == "screenshot" or item.get("capture_after") for item in spec.steps) else ({"path": image_default.as_posix()},))
    for item in end_screenshots:
        add_capture(item, f"Screenshot:{Path(str(item.get('path', image_default))).name}")
    if spec.project_mode == "new":
        target = save_path or spec.project_path
        if target != spec.project_path:
            lines.extend([
                f"project.SaveAs {_vb_quote(target.as_posix())}, True",
                "If Err.Number = 0 Then",
                f"  CreateObject(\"Scripting.FileSystemObject\").CopyFile {_vb_quote(target.as_posix())}, {_vb_quote(spec.project_path.as_posix())}, False",
                "End If",
            ])
            lines.append('Record "Save"')
        else:
            add_step("Save", f"project.SaveAs {_vb_quote(target.as_posix())}, True")
    else:
        add_step("Save", "project.Save")
    lines.append("output.Close")
    return "\n".join(lines) + "\n"


def build_hfss_export_script(spec: HfssExperimentSpec | Mapping[str, Any]) -> str:
    """生成在求解成功后运行的结果导出脚本。"""
    if not isinstance(spec, HfssExperimentSpec):
        spec = HfssExperimentSpec.from_mapping(spec)
    lines = [
        "On Error Resume Next",
        "Dim app, desktop, project, design, solutions, output",
        f"Set output = CreateObject(\"Scripting.FileSystemObject\").CreateTextFile({_vb_quote((spec.output_dir / 'hfss_export_status.txt').as_posix())}, True, True)",
        "Set app = CreateObject(\"AnsoftHfss.HfssScriptInterface\")",
        "Set desktop = app.GetAppDesktop()",
        f"Set project = desktop.OpenProject({_vb_quote(spec.project_path.as_posix())})",
        f"Set design = project.GetDesign({_vb_quote(spec.design_name)})",
        "Set solutions = design.GetModule(\"Solutions\")",
    ]
    for item in spec.exports:
        kind = str(item.get("type", "network_data")).lower()
        if kind != "network_data":
            raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_UNSUPPORTED_HFSS_EXPORT_TYPE.format(kind=kind))
        path = _output_path(spec, item["path"])
        lines.append(
            f"solutions.ExportNetworkData {_vb_quote(str(item.get('variation', '')))}, "
            f"{_vb_array(item.get('solutions', ['Setup1:Sweep1']))}, {int(item.get('format', 3))}, "
            f"{_vb_quote(path.as_posix())}, {_vb_array(item.get('frequencies', ['all']))}, "
            f"{_vb_value(bool(item.get('renormalize', False)))}, {float(item.get('impedance', 50))}, "
            f"{_vb_quote(str(item.get('data_type', 'S')))}, {int(item.get('pass', -1))}, "
            f"{int(item.get('complex_format', 0))}, {int(item.get('precision', 15))}"
        )
        lines.append(f"output.WriteLine {_vb_quote(path.name + '=')} & Hex(Err.Number) & \" \" & Err.Description")
        lines.append("Err.Clear")
    lines.append("output.Close")
    return "\n".join(lines) + "\n"


def prepare_hfss_experiment(spec: HfssExperimentSpec | Mapping[str, Any]) -> dict[str, Any]:
    """写入通用 HFSS 脚本和机器可读清单，不启动求解器。"""
    if not isinstance(spec, HfssExperimentSpec):
        spec = HfssExperimentSpec.from_mapping(spec)
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    save_path = None
    if spec.project_mode == "new" and not str(spec.project_path).isascii():
        staging_root = Path(__file__).resolve().parents[2] / "data" / "hfss_staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        save_path = Path(tempfile.mkdtemp(prefix="project-", dir=staging_root)) / "model.hfss"
    script_path = write_hfss_script(spec.output_dir / "run_hfss_experiment.vbs", build_hfss_script(spec, save_path=save_path))
    export_path = write_hfss_script(spec.output_dir / "export_hfss_results.vbs", build_hfss_export_script(spec)) if spec.exports else None
    step_captures = sum(bool(item.get("action") == "screenshot") + bool(item.get("capture_after")) for item in spec.steps)
    manifest = {
        "project": str(spec.project_path),
        "staged_project": str(save_path) if save_path else None,
        "project_mode": spec.project_mode,
        "solve_requested": spec.solve,
        "design_name": spec.design_name,
        "script": str(script_path),
        "export_script": str(export_path) if export_path else None,
        "output_dir": str(spec.output_dir),
        "parameters": {"units": spec.units, "processors": spec.processors, "variables": dict(spec.variables or {}), "geometry_count": len(spec.geometry), "material_count": len(spec.materials), "port_count": len(spec.ports), "boundary_count": len(spec.boundaries), "sweep_count": len(spec.sweeps), "ordered_step_count": len(spec.steps), "screenshot_count": step_captures + len(spec.screenshots or (() if step_captures else ({"path": str(spec.output_dir / 'hfss_model.jpg')},))), "export_count": len(spec.exports)},
        "measured_results_available": False,
    }
    (spec.output_dir / "workflow.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def execute_hfss_experiment(spec: HfssExperimentSpec | Mapping[str, Any], *, executable: str | Path | None = None, timeout: int = 3600) -> dict[str, Any]:
    """按规格生成脚本、执行配置和批处理求解，并写入结果清单。"""
    if not isinstance(spec, HfssExperimentSpec):
        spec = HfssExperimentSpec.from_mapping(spec)
    manifest = prepare_hfss_experiment(spec)
    status_path = spec.output_dir / "hfss_script_status.txt"
    capture_targets = _capture_targets(spec)
    screenshots = [path for path, _, _ in capture_targets]
    previous_images = {
        path: (path.stat().st_size, path.stat().st_mtime_ns) if path.is_file() else None
        for path in screenshots
    }
    status_path.unlink(missing_ok=True)
    script_result = run_hfss_command(["-RunScriptAndExit", manifest["script"]], executable=executable, cwd=spec.output_dir, timeout=timeout, log_path=spec.output_dir / "script_run.json")
    configuration_status = _read_status_file(status_path, "missing configuration status")
    stage_status = {}
    for line in configuration_status.splitlines():
        stage, separator, status = line.partition("=")
        if separator and status.split():
            stage_status[stage] = status.split()[0]
    screenshot_checks = []
    for path, stage, preceding_stage in capture_targets:
        current = (path.stat().st_size, path.stat().st_mtime_ns) if path.is_file() else None
        fresh = bool(current and current[0] > 0 and current != previous_images[path])
        screenshot_checks.append({
            "path": str(path),
            "available": bool(current and current[0] > 0),
            "fresh": fresh,
            "valid_evidence": fresh and stage_status.get(stage) == "0" and
                              (preceding_stage is None or stage_status.get(preceding_stage) == "0"),
        })
    script = Path(manifest["script"]).read_text(encoding="ascii")
    incomplete = []
    if not _configuration_status(script, configuration_status):
        incomplete.append("HFSS step status is incomplete or contains an error")
    if not spec.project_path.is_file():
        incomplete.append("HFSS source project was not saved")
    if any(not item["fresh"] for item in screenshot_checks):
        incomplete.append("HFSS did not create fresh screenshots: " + ", ".join(item["path"] for item in screenshot_checks if not item["fresh"]))
    configuration_ok = script_result.status in {"success", "incomplete_log"} and not incomplete
    if configuration_ok and spec.solve:
        solve_result = run_hfss_command(["-BatchSolve", str(spec.project_path)], executable=executable, cwd=spec.output_dir, timeout=timeout, log_path=spec.output_dir / "solver_run.json")
    elif configuration_ok:
        solve_result = HfssRunResult([], None, "not_requested", "", "", error=None)
    else:
        solve_result = HfssRunResult([], None, "configuration_error", "", "", error="; ".join(incomplete) or script_result.error or configuration_status)
    exported = [_output_path(spec, item["path"]) for item in spec.exports if item.get("path")]
    export_result = None
    export_status = ""
    if solve_result.status == "success" and manifest["export_script"]:
        export_status_path = spec.output_dir / "hfss_export_status.txt"
        export_status_path.unlink(missing_ok=True)
        export_result = run_hfss_command(["-RunScriptAndExit", manifest["export_script"]], executable=executable, cwd=spec.output_dir, timeout=timeout, log_path=spec.output_dir / "export_run.json")
        export_status = _read_status_file(export_status_path, "missing export status")
    export_codes = [line.split("=", 1)[1].split()[0] for line in export_status.splitlines() if "=" in line]
    measured = solve_result.status == "success" and bool(exported) and export_result is not None and export_result.status == "success" and len(export_codes) == len(exported) and all(code == "0" for code in export_codes) and all(path.is_file() and path.stat().st_size > 0 for path in exported)
    manifest.update({"script_result": asdict(script_result), "configuration_status": configuration_status, "configuration_succeeded": configuration_ok, "configuration_error": None if configuration_ok else "; ".join(incomplete) or script_result.error, "screenshots": screenshot_checks, "solve_result": asdict(solve_result), "export_result": asdict(export_result) if export_result else None, "export_status": export_status, "exports": [str(path) for path in exported], "measured_results_available": measured})
    (spec.output_dir / "workflow.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def inspect_hfss_project(project_path: str | Path) -> dict:
    """只读检查 HFSS 工程中的设计、求解和 Optimetrics 设置。"""
    path = Path(project_path).resolve()
    if path.suffix.lower() != ".hfss" or not path.is_file():
        raise ValueError(CONSTANT.HFSS_PROJECT_REQUIRED)
    content = path.read_text(encoding="latin-1", errors="replace")
    design = re.search(r"\$begin 'HFSSModel'\s+.*?Name='([^']+)'", content, re.DOTALL)
    return {
        "project": str(path),
        "designs": [design.group(1)] if design else [],
        "parametric_setups": re.findall(r"(?m)^\s*\$begin '(ParametricSetup[^']*)'", content),
        "optimization_setups": re.findall(r"(?m)^\s*\$begin '(OptimizationSetup[^']*)'", content),
        "has_setup1": "$begin 'Setup1'" in content,
        "has_three_ports": all(f"$begin 'Port{number}'" in content for number in (1, 2, 3)),
    }


def create_tee_23_source(output_dir: str | Path, *, example_path: str | Path = DEFAULT_TEE_EXAMPLE) -> Path:
    """以 HFSS 自带 OptimTee 实例生成符合指导书容差的独立源工程。"""
    example = Path(example_path)
    if not example.is_file():
        raise FileNotFoundError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_OPTIMTEE_EXAMPLE_NOT_FOUND.format(example=example))
    source = example.read_bytes()
    original = b"Acceptable_Cost=0.001"
    if source.count(original) != 1 or b"Data='LIN 0in 1in 0.1in'" not in source:
        raise ValueError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_EXAMPLE_DOES_NOT_MATCH_EXPECTED)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / "OptimTee_2_3.hfss"
    if target.exists():
        raise FileExistsError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_SOURCE_ALREADY_EXISTS.format(target=target))
    target.write_bytes(source.replace(original, b"Acceptable_Cost=0.0001"))
    return target


@dataclass(frozen=True)
class HfssRunResult:
    command: list[str]
    returncode: int | None
    status: str
    stdout: str
    stderr: str
    log_path: str | None = None
    error: str | None = None


def find_hfss_executable(explicit: str | Path | None = None) -> Path | None:
    """按显式路径、环境变量和常见安装目录查找 hfss.exe。"""
    candidates: list[Path] = []
    if explicit:
        explicit_path = Path(explicit)
        return explicit_path if explicit_path.is_file() else None
    for variable in ("HFSS_EXE", "HFSS_HOME"):
        value = os.environ.get(variable)
        if value:
            path = Path(value)
            candidates.append(path / "hfss.exe" if path.suffix.lower() != ".exe" else path)
    candidates.extend(DEFAULT_HFSS_PATHS)
    for candidate in candidates:
        if "*" in str(candidate):
            continue
        if candidate.is_file():
            return candidate
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "AnsysEM" / "AnsysEM"
    for candidate in sorted(program_files.glob("v*/Win64/hfss.exe"), reverse=True):
        if candidate.is_file():
            return candidate
    found = shutil.which("hfss.exe")
    return Path(found) if found else None


def _script_array(values: Sequence[str]) -> str:
    return "Array(" + ", ".join(values) + ")"


def build_tee_optimization_script(
    project_path: str | Path,
    output_dir: str | Path,
    *,
    design_name: str = "TeeModal",
) -> str:
    """生成指导书 2.3 的参数扫描、输出变量和优化设置脚本。"""
    project = Path(project_path).resolve()
    output = Path(output_dir).resolve()
    image = output / "hfss_model.jpg"
    project_text = project.as_posix()
    image_text = image.as_posix()
    return f'''On Error Resume Next
Dim app, desktop, project, design, optimetrics, editor, output, variables
Set app = CreateObject("AnsoftHfss.HfssScriptInterface")
Set desktop = app.GetAppDesktop()
app.SetNumberOfProcessors 1
Set project = desktop.OpenProject("{project_text}")
Set design = project.GetDesign("{design_name}")
Set optimetrics = design.GetModule("Optimetrics")
Set variables = design.GetModule("OutputVariable")
Set editor = design.SetActiveEditor("3D Modeler")
editor.FitAll
editor.ExportModelImageToFile "{image_text}", 800, 600, Array("NAME:SaveImageParams", "ShowAxis:=", "Default", "ShowGrid:=", "Default", "ShowRuler:=", "Default")
variables.CreateOutputVariable "Power11", "(mag(S(Port1,Port1)))^2", "Setup1 : LastAdaptive", "Modal Solution Data", Array("Domain:=", "Sweep")
variables.CreateOutputVariable "Power21", "(mag(S(Port2,Port1)))^2", "Setup1 : LastAdaptive", "Modal Solution Data", Array("Domain:=", "Sweep")
variables.CreateOutputVariable "Power31", "(mag(S(Port3,Port1)))^2", "Setup1 : LastAdaptive", "Modal Solution Data", Array("Domain:=", "Sweep")
Err.Clear
optimetrics.InsertSetup "OptiParametric", Array("NAME:ParametricSetup1", "SaveFields:=", True, Array("NAME:StartingPoint"), "Sim. Setups:=", Array("Setup1"), Array("NAME:Sweeps", Array("NAME:SweepDefinition", "Variable:=", "Offset", "Data:=", "LIN 0in 1in 0.1in", "OffsetF1:=", False, "Synchronize:=", 0)), Array("NAME:Sweep Operations"), Array("NAME:Goals"))
Set output = CreateObject("Scripting.FileSystemObject").CreateTextFile("{(output / 'hfss_script_status.txt').as_posix()}", True)
output.WriteLine "Parametric=" & Hex(Err.Number) & " " & Err.Description
Err.Clear
optimetrics.InsertSetup "OptiOptimization", Array("NAME:OptimizationSetup1", "SaveFields:=", False, Array("NAME:StartingPoint", "Offset:=", "0.1in"), "Optimizer:=", "Quasi Newton", "MaxIterations:=", 100, "PriorPSetup:=", "ParametricSetup1", "PreSolvePSetup:=", True, Array("NAME:Variables", "Offset:=", Array("i:=", True, "Min:=", "0in", "Max:=", "0.3in", "MinStep:=", "0.002in", "MaxStep:=", "0.02in")), Array("NAME:LCS"), Array("NAME:Goals", Array("NAME:Goal", "Solution:=", "Setup1 : LastAdaptive", "Calculation:=", "Power31-2*Power21", "Condition:=", "==", Array("NAME:GoalValue", "GoalValueType:=", "Independent", "Format:=", "Real/Imag", "bG:=", Array("v:=", "[0;]")), "Weight:=", "[1;]")), "Acceptable_Cost:=", 0.0001, "Noise:=", 0.0001, "UpdateDesignWhenDone:=", False)
output.WriteLine "Optimization=" & Hex(Err.Number) & " " & Err.Description
Err.Clear
project.Save
output.WriteLine "Save=" & Hex(Err.Number) & " " & Err.Description
output.Close
'''


def write_hfss_script(path: str | Path, content: str) -> Path:
    """以 HFSS 可读取的 ASCII 编码写入脚本。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="ascii", errors="strict", newline="\r\n")
    return target


def _classify_process(returncode: int, stdout: str, stderr: str) -> tuple[str, str | None]:
    combined = f"{stdout}\n{stderr}"
    marker = next((item for item in SOLVER_ERROR_MARKERS if item.lower() in combined.lower()), None)
    if marker:
        return "solver_error", marker
    if "[error]" in combined.lower():
        return "solver_error", "HFSS log contains [error] messages"
    if returncode != 0:
        return "process_error", f"HFSS exited with code {returncode}"
    return "success", None


def run_hfss_command(
    args: Sequence[str],
    *,
    executable: str | Path | None = None,
    cwd: str | Path | None = None,
    timeout: int = 3600,
    log_path: str | Path | None = None,
) -> HfssRunResult:
    """运行 HFSS 命令并把求解器错误转为结构化状态。"""
    binary = find_hfss_executable(executable)
    if binary is None:
        result = HfssRunResult([], None, "missing_executable", "", "", str(log_path) if log_path else None, "找不到 hfss.exe")
        if log_path:
            log = Path(log_path)
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    command = [str(binary), *map(str, args)]
    native_log = None
    previous_log = None
    if args and args[0] in {"-BatchSolve", "-RunScriptAndExit"} and len(args) > 1:
        native_log = Path(args[1]).with_suffix(".log")
        if native_log.is_file():
            previous_log = (native_log.stat().st_size, native_log.stat().st_mtime_ns)
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        fresh_log = False
        if native_log is not None:
            deadline = time.monotonic() + min(timeout, 60)
            while time.monotonic() < deadline:
                if native_log.is_file():
                    current_log = native_log.stat()
                    changed = previous_log != (current_log.st_size, current_log.st_mtime_ns)
                    if changed:
                        log_content = native_log.read_text(encoding="utf-8", errors="replace")
                        latest_run = "Starting Batch Run" + log_content.rsplit("Starting Batch Run", 1)[-1]
                        if "Starting Batch Run" in log_content and "Stopping Batch Run" in latest_run:
                            stdout += "\n" + latest_run
                            fresh_log = True
                            break
                time.sleep(0.5)
        status, error = _classify_process(completed.returncode, stdout, stderr)
        if native_log and not fresh_log:
            status, error = "incomplete_log", "HFSS did not finish writing a new batch log"
        result = HfssRunResult(command, completed.returncode, status, stdout, stderr, str(log_path) if log_path else None, error)
    except subprocess.TimeoutExpired as exc:
        result = HfssRunResult(command, None, "timeout", exc.stdout or "", exc.stderr or "", str(log_path) if log_path else None, "HFSS command timed out")
    except OSError as exc:
        result = HfssRunResult(command, None, "process_error", "", str(exc), str(log_path) if log_path else None, str(exc))
    if log_path:
        log = Path(log_path)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def prepare_tee_experiment(project_path: str | Path, output_dir: str | Path) -> dict:
    """为指导书 2.3 准备脚本、截图和结果目录。"""
    project = Path(project_path).resolve()
    output = Path(output_dir).resolve()
    if not project.is_file():
        raise FileNotFoundError(CONSTANT.HFSS_TOOLS_ERROR_HFSS_PROJECT_NOT_FOUND_2.format(project=project))
    output.mkdir(parents=True, exist_ok=True)
    script_path = write_hfss_script(output / "run_2_3.vbs", build_tee_optimization_script(project, output))
    manifest = {
        "experiment": "2.3 T形波导参数扫描与优化",
        "project": str(project),
        "script": str(script_path),
        "settings": {"offset_sweep": "LIN 0in 1in 0.1in", "optimization_range": "0in..0.3in", "goal": "Power31-2*Power21 == 0", "optimizer": "Quasi Newton"},
        "measured_results_available": False,
    }
    (output / "workflow.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def execute_tee_experiment(project_path: str | Path, output_dir: str | Path, *, executable: str | Path | None = None, timeout: int = 3600) -> dict:
    """执行 2.3 脚本和批处理求解，并记录是否产生实测数据。"""
    manifest = prepare_tee_experiment(project_path, output_dir)
    output = Path(output_dir).resolve()
    script_result = run_hfss_command(["-RunScriptAndExit", manifest["script"]], executable=executable, cwd=output, timeout=timeout, log_path=output / "script_run.json")
    status_path = output / "hfss_script_status.txt"
    configuration_status = status_path.read_text(encoding="utf-8", errors="replace") if status_path.is_file() else "missing configuration status"
    if script_result.status == "success" and "Optimization=0 " in configuration_status and "Parametric=0 " in configuration_status:
        solve_result = run_hfss_command(["-BatchSolve", str(Path(project_path).resolve())], executable=executable, cwd=output, timeout=timeout, log_path=output / "solver_run.json")
    else:
        solve_result = HfssRunResult([], None, "configuration_error", "", "", error=configuration_status)
    touchstone = output / "tee_sparameters.s3p"
    manifest.update({"script_result": asdict(script_result), "configuration_status": configuration_status, "solve_result": asdict(solve_result), "touchstone": str(touchstone), "measured_results_available": solve_result.status == "success" and touchstone.is_file()})
    (output / "workflow.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
