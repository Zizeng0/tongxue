"""Account-level rules for model-generated content."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from server.data_model import User
from server.features_services.jwt_validation import require_user

from . import CONSTANT

CONFIG_DIR = Path(__file__).resolve().parents[2] / "data" / "output-settings"
Detail = Literal["detailed", "normal", "brief"]
RULES: dict[Detail, str] = {
    "detailed": "详细、丰富。解题展开必要的推导、理由和适用条件，适合时加入对照或边界情况。实验报告说明实验条件、操作、结果和依据。论文围绕研究问题组织证据并说明局限。演示文稿把展开说明放入讲稿备注，并用相关图像、图解或对照帮助理解。",
    "normal": "正常。保留理解结论所需的关键步骤、依据与结果。按任务组织段落或页面，控制重复和次要背景。",
    "brief": "简略。先给核心结论，只展开理解或复现所需的关键步骤。保留必需的数据、单位、条件、证据与交付项。简化次要背景、举例和讲稿。",
}

router = APIRouter(prefix="/api/settings/output", tags=["settings"])


class OutputSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: Detail


def load_output_settings(owner_id: int) -> OutputSettings:
    try:
        return OutputSettings.model_validate_json((CONFIG_DIR / f"{owner_id}.json").read_bytes())
    except FileNotFoundError:
        return OutputSettings(detail="normal")
    except (OSError, ValidationError):
        raise HTTPException(500, CONSTANT.OUTPUT_SETTINGS_READ_FAILED) from None


def output_instruction(owner_id: int, *, detail: Detail | None = None) -> str:
    detail = detail or load_output_settings(owner_id).detail
    return CONSTANT.OUTPUT_INSTRUCTION.format(rule=RULES[detail])


@router.get("", response_model=OutputSettings)
def get_output_settings(user: User = Depends(require_user)):
    return load_output_settings(user.id)


@router.patch("", response_model=OutputSettings)
def save_output_settings(settings: OutputSettings, user: User = Depends(require_user)):
    pending = None
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=CONFIG_DIR, delete=False) as file:
            pending = Path(file.name)
            file.write(settings.model_dump_json().encode("utf-8"))
        pending.replace(CONFIG_DIR / f"{user.id}.json")
    except OSError:
        raise HTTPException(500, CONSTANT.OUTPUT_SETTINGS_WRITE_FAILED) from None
    finally:
        if pending:
            pending.unlink(missing_ok=True)
    return settings
