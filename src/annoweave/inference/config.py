from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .. import paths

#: 保存选项模板
SAVE_TEMPLATES = {
    "full": "保存全图",
    "single_crop": "保存某张裁剪图",
    "full_and_crops": "保存全图及其所有裁剪图",
    "crops_only": "仅保存裁剪图",
}

#: 模型角色
ROLE_FULL_FRAME = "full_frame"
ROLE_DOWNSTREAM = "downstream"
ROLES = (ROLE_FULL_FRAME, ROLE_DOWNSTREAM)

#: MVP 可选择的模型注册表键（开放设计见 models.py 的 REGISTRY）
KNOWN_MODEL_TYPES = ("yolov8", "yolov8_cls", "yolov8_pose", "dwpose")

#: 任务类型中需要“框”的任务（裁剪阶段只关心这些）
BOX_TASKS = {"detection", "obb"}

#: 关键点排列可选值（空字符串 = 按点数自动判断）
KEYPOINT_FORMATS = ("", "coco17", "openpose18")


class ConfigError(ValueError):
    """配置非法。"""


def inference_config_path() -> Path:
    return paths.data_path("inference_config.json")


@dataclass
class ModelConfig:
    name: str
    role: str
    model_type: str
    model_path: str
    device: str = "cpu"
    input_size: tuple[int, int] = (640, 640)
    letterbox: bool = True
    score_threshold: float = 0.35
    iou_threshold: float = 0.45
    max_det: int = 300
    classes: list[str] = field(default_factory=list)
    output_format: str = "yolo"
    #: 关键点模型的点数；0 = 从输出列数自动推断
    num_keypoints: int = 0
    #: 关键点排列（coco17 / openpose18）；空 = 按点数自动判断
    keypoint_format: str = ""
    #: 自顶向下姿态模型：输入是"人框 + 图"，需要上游先给出人框（裁剪姿态节点用）
    top_down: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModelConfig":
        name = str(payload.get("name", "")).strip()
        role = str(payload.get("role", "")).strip()
        model_type = str(payload.get("type", "")).strip()
        model_path = str(payload.get("model_path", "")).strip()
        if not name:
            raise ConfigError("模型名称不能为空")
        if role not in ROLES:
            raise ConfigError(f"模型角色非法: {role!r}")
        if not model_type:
            raise ConfigError(f"模型 {name} 缺少 type")
        if not model_path:
            raise ConfigError(f"模型 {name} 缺少模型路径")
        size = payload.get("input_size", [640, 640])
        try:
            width, height = (int(size[0]), int(size[1]))
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"模型 {name} 输入尺寸非法") from exc
        if width <= 0 or height <= 0:
            raise ConfigError(f"模型 {name} 输入尺寸需为正整数")
        classes = [str(value).strip() for value in (payload.get("classes") or []) if str(value).strip()]
        return cls(
            name=name,
            role=role,
            model_type=model_type,
            model_path=model_path,
            device=str(payload.get("device", "cpu")),
            input_size=(width, height),
            letterbox=bool(payload.get("letterbox", True)),
            score_threshold=float(payload.get("score_threshold", 0.35)),
            iou_threshold=float(payload.get("iou_threshold", 0.45)),
            max_det=int(payload.get("max_det", 300)),
            classes=classes,
            output_format=str(payload.get("output_format", "yolo")),
            num_keypoints=int(payload.get("num_keypoints", 0) or 0),
            keypoint_format=str(payload.get("keypoint_format", "") or ""),
            top_down=bool(payload.get("top_down", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "type": self.model_type,
            "model_path": self.model_path,
            "device": self.device,
            "input_size": [self.input_size[0], self.input_size[1]],
            "letterbox": self.letterbox,
            "score_threshold": self.score_threshold,
            "iou_threshold": self.iou_threshold,
            "max_det": self.max_det,
            "classes": list(self.classes),
            "output_format": self.output_format,
            "num_keypoints": self.num_keypoints,
            "keypoint_format": self.keypoint_format,
            "top_down": self.top_down,
        }


@dataclass
class CropConfig:
    classes: list[str] = field(default_factory=list)
    expand_px: int = 0
    expand_ratio: float = 0.0
    min_size: int = 0
    clip_to_frame: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CropConfig":
        return cls(
            classes=[str(value).strip() for value in (payload.get("classes") or []) if str(value).strip()],
            expand_px=int(payload.get("expand_px", 0)),
            expand_ratio=float(payload.get("expand_ratio", 0.0)),
            min_size=int(payload.get("min_size", 0)),
            clip_to_frame=bool(payload.get("clip_to_frame", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "classes": list(self.classes),
            "expand_px": self.expand_px,
            "expand_ratio": self.expand_ratio,
            "min_size": self.min_size,
            "clip_to_frame": self.clip_to_frame,
        }


@dataclass
class SaveConfig:
    template: str = "full_and_crops"
    output_dir: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SaveConfig":
        template = str(payload.get("template", "full_and_crops"))
        if template not in SAVE_TEMPLATES:
            raise ConfigError(f"未知保存模板: {template!r}")
        return cls(template=template, output_dir=str(payload.get("output_dir", "")))

    def to_dict(self) -> dict[str, Any]:
        return {"template": self.template, "output_dir": self.output_dir}


@dataclass
class PipelineConfig:
    sampling_fps: float = 1.0
    models: list[ModelConfig] = field(default_factory=list)
    crop: CropConfig = field(default_factory=CropConfig)
    save: SaveConfig = field(default_factory=SaveConfig)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PipelineConfig":
        config = cls(
            sampling_fps=float(payload.get("sampling_fps", 1.0)),
            crop=CropConfig.from_dict(payload.get("crop") or {}),
            save=SaveConfig.from_dict(payload.get("save") or {}),
        )
        if config.sampling_fps <= 0:
            raise ConfigError("采样 FPS 必须大于 0")
        models = []
        for item in payload.get("models") or []:
            if isinstance(item, dict):
                models.append(ModelConfig.from_dict(item))
        config.models = models
        config.validate()
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "sampling_fps": self.sampling_fps,
            "models": [model.to_dict() for model in self.models],
            "crop": self.crop.to_dict(),
            "save": self.save.to_dict(),
        }

    @property
    def full_frame_models(self) -> list[ModelConfig]:
        return [model for model in self.models if model.role == ROLE_FULL_FRAME]

    @property
    def downstream_models(self) -> list[ModelConfig]:
        return [model for model in self.models if model.role == ROLE_DOWNSTREAM]

    def validate(self) -> None:
        names: set[str] = set()
        for model in self.models:
            folded = model.name.casefold()
            if folded in names:
                raise ConfigError(f"模型名称重复: {model.name}")
            names.add(folded)
        if not self.full_frame_models:
            raise ConfigError("请至少配置一个全图模型")
        if self.crop.classes:
            available = set()
            for model in self.full_frame_models:
                available.update(model.classes)
            missing = [name for name in self.crop.classes if name not in available]
            if missing:
                raise ConfigError(f"裁剪类别不在全图模型类别表中: {', '.join(missing)}")


def load_pipeline_config(path: str | Path | None = None) -> PipelineConfig:
    path = Path(path) if path else inference_config_path()
    if not path.exists():
        return PipelineConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取推理配置 {path}: {exc}") from exc
    return PipelineConfig.from_dict(payload)


def save_pipeline_config(config: PipelineConfig, path: str | Path | None = None) -> Path:
    path = Path(path) if path else inference_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def load_model_library(path: str | Path | None = None) -> list[ModelConfig]:
    """只解析推理配置里的模型库列表，不做旧管线的角色/裁剪校验。

    供工作流按用户定义的稳定名称引用模型。
    """
    path = Path(path) if path else inference_config_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = []
    for item in payload.get("models") or []:
        if isinstance(item, dict):
            try:
                models.append(ModelConfig.from_dict(item))
            except ConfigError:
                continue
    return models


def save_model_library(models: list[ModelConfig], path: str | Path | None = None) -> Path:
    """独立保存模型库，保留采样、裁剪及导出设置，允许仅添加分类模型。"""
    path = Path(path) if path else inference_config_path()
    names = [model.name.casefold() for model in models]
    if len(names) != len(set(names)):
        raise ConfigError("模型名称重复")
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else PipelineConfig().to_dict()
    payload["models"] = [model.to_dict() for model in models]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
    return path


def default_config() -> PipelineConfig:
    """一个可直接运行的示例配置：单全图检测模型 + 2 个下游分类模型。"""
    return PipelineConfig(
        sampling_fps=1.0,
        models=[
            ModelConfig(
                name="全图检测",
                role=ROLE_FULL_FRAME,
                model_type="yolov8",
                model_path="",
                classes=["person", "car"],
            ),
            ModelConfig(
                name="告警分级",
                role=ROLE_DOWNSTREAM,
                model_type="yolov8_cls",
                model_path="",
                classes=["correct", "false_positive", "uncertain", "invalid_media"],
            ),
        ],
        crop=CropConfig(classes=["person"], expand_ratio=0.05),
        save=SaveConfig(template="full_and_crops"),
    )


def pick_classes(model: ModelConfig, selected: Iterable[str] | None = None) -> list[str]:
    """返回要裁剪的类别：优先用户在裁剪配置中勾选的，否则取该模型的全部类别。"""
    selected = [str(value).strip() for value in (selected or []) if str(value).strip()]
    return selected or list(model.classes)
