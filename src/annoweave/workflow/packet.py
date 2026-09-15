from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..inference.datatypes import Detection, Frame, Keypoint, ModelResult


@dataclass
class Association:
    """一个主体与若干候选对象的关联结果。

    `candidates` 记录**全部**候选行为框的实际 IoA 与阈值（含未达阈值的），
    让复核界面能解释“为什么没裁这一个人”，而不是在 UI 侧按默认阈值重算。
    形如 [(behavior_label, ioa, threshold, passed, behavior_detection), ...]
    """

    subject: Detection
    matched_candidates: list[Detection] = field(default_factory=list)
    crop_hit: bool = False
    route: str = ""
    candidates: list[tuple] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)

    @property
    def should_crop(self) -> bool:
        return self.crop_hit

    @property
    def person(self) -> Detection:
        """Compatibility alias used by the canvas layer."""
        return self.subject

    @property
    def matched_behaviors(self) -> list[Detection]:
        """Compatibility alias; public workflow data uses matched_candidates."""
        return self.matched_candidates

    @property
    def target(self) -> str:
        return self.route

    def failed_candidates(self) -> list[tuple]:
        return [item for item in self.candidates if not item[3]]

    def explanation(self) -> str:
        """人类可读的关联解释，供检查器与导出清单复用。"""
        if self.should_crop:
            return "达到关联阈值并进入裁剪"
        if self.rejections:
            return "；“".join(self.rejections) + "。"
        return "本帧没有可关联的行为类别框。"


@dataclass
class CropSlot:
    """一个待/已裁剪的人员槽，承载其下游结果与人工编辑状态。

    offset_x/offset_y 为**真实**裁剪图左上角在原帧中的整数坐标；
    crop_rect 为真实整数裁剪范围 (x1,y1,x2,y2)，下游框回填必须使用它。
    """

    crop_id: str
    person_bbox: Detection
    crop_image: Any  # np.ndarray (H, W, 3)，懒加载或由 crop 节点写入
    route: str = "all"
    edited: bool = False  # 人工新增/修改过
    edited_source: str = ""  # 'added' / 'modified'
    user_result: dict[str, Any] = field(default_factory=dict)  # 人工覆盖结论
    offset_x: int = 0
    offset_y: int = 0
    crop_rect: Optional[tuple[float, float, float, float]] = None
    source_bbox: Optional[Detection] = None  # 扩边前的关联人体，用于检查器匹配
    results: dict[str, ModelResult] = field(default_factory=dict)  # 裁剪局部坐标，完整类别
    predictions: dict[str, ModelResult] = field(default_factory=dict)
    model_classes: dict[str, list[str]] = field(default_factory=dict)
    model_names: dict[str, str] = field(default_factory=dict)
    routes: dict[str, str] = field(default_factory=dict)
    needs_review: bool = False
    #: 该裁剪主体的人体关键点（**原图坐标**）。全图姿态模型的结果随主体带入裁剪，
    #: 或者在裁剪上跑自顶向下姿态模型后回填；用于在裁剪图上叠加骨架。
    keypoints: list[Keypoint] = field(default_factory=list)
    keypoint_format: str = ""


@dataclass
class Packet:
    """节点间流动的统一数据包。节点读/写命名键，互不直接耦合。"""

    frame: Optional[Frame] = None
    full_results: dict[str, ModelResult] = field(default_factory=dict)
    subjects: list[Detection] = field(default_factory=list)
    candidates: list[Detection] = field(default_factory=list)
    associations: list[Association] = field(default_factory=list)
    crops: list[CropSlot] = field(default_factory=list)
    output_dir: str = ""
    save_template: str = "full_and_crops"
    #: 产物命名身份：media_identity 为媒体稳定 id/路径，run_id 为一次运行标识。
    #: 缺省时导出服务回退到 "unknown"，绝不退化到只用帧序号命名（AR-03）。
    media_identity: str = ""
    run_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value) -> None:
        setattr(self, key, value)

    def get(self, key: str, default=None):
        return getattr(self, key, default)
