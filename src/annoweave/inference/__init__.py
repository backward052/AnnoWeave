from __future__ import annotations

from .config import (
    ROLES,
    SAVE_TEMPLATES,
    ConfigError,
    CropConfig,
    ModelConfig,
    PipelineConfig,
    SaveConfig,
    default_config,
    inference_config_path,
    load_pipeline_config,
    save_pipeline_config,
)

# 只在此处安全地导入不依赖 numpy 的子模块；依赖 numpy/cv2/onnx 的子模块
# （crop/models/pipeline/backends）由使用方在运行时按需导入，避免无依赖时包导入失败。
from .datatypes import (
    Classification,
    Crop,
    Detection,
    Frame,
    ModelResult,
    TaskType,
)

__all__ = [
    "ROLES",
    "SAVE_TEMPLATES",
    "ConfigError",
    "CropConfig",
    "ModelConfig",
    "PipelineConfig",
    "SaveConfig",
    "default_config",
    "inference_config_path",
    "load_pipeline_config",
    "save_pipeline_config",
    "Classification",
    "Crop",
    "Detection",
    "Frame",
    "ModelResult",
    "TaskType",
]


def _lazy_import():
    """在需要 numpy/cv2/onnx 时才加载推理执行相关的子模块。"""
    import importlib

    names = ("crop", "models", "pipeline", "backends")
    for name in names:
        importlib.import_module(f"{__name__}.{name}")
