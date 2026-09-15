from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskType(str, Enum):
    """开放的任务类型集合。MVP 实现 detection / classification，其余留作扩展位。

    参考 X-AnyLabeling：模型类通过 Meta.task_type 声明自己的任务类型，
    ModelResult 用统一的归一化结构承载不同任务的输出，供上游节点通用消费。
    """

    DETECTION = "detection"
    CLASSIFICATION = "classification"
    SEGMENTATION = "segment"
    OBB = "obb"
    KEYPOINT = "keypoint"
    POSE = "pose"


@dataclass
class Keypoint:
    """人体/物体关键点（坐标已归一化到原图坐标系）。

    `score <= 0` 或缺省表示模型没有给出这个点（例如被遮挡），绘制与统计都要跳过。
    """

    x: float
    y: float
    score: float = 0.0
    name: str = ""

    @property
    def visible(self) -> bool:
        return self.score > 0 and (self.x > 0 or self.y > 0)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.score)


@dataclass
class Detection:
    """检测框（归一化到原图坐标）。OBB 可额外携带 angle；locked/hidden 用于画布交互。

    `keypoints` 承载姿态任务的结果：关键点与这个框一一对应（人框 → 17 个点）。
    为了让既有的框相等性判断与缓存指纹保持稳定，keypoints 不参与 == 比较。
    """

    x1: float
    y1: float
    x2: float
    y2: float
    label: str
    score: float
    angle: Optional[float] = None
    locked: bool = False
    hidden: bool = False
    object_id: str = field(default_factory=lambda: uuid.uuid4().hex, compare=False)
    model_key: str = field(default="", compare=False)
    keypoints: list["Keypoint"] = field(default_factory=list, compare=False)
    #: 关键点排列名称，如 "coco17" / "openpose18" / "unknown"
    keypoint_format: str = field(default="", compare=False)

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    def visible_keypoints(self, threshold: float = 0.0) -> list["Keypoint"]:
        return [point for point in self.keypoints if point.score > threshold and point.visible]

    def with_keypoints(self, keypoints: list["Keypoint"], keypoint_format: str = "") -> "Detection":
        """返回带了关键点的副本（不修改原对象）。"""
        clone = Detection(
            self.x1,
            self.y1,
            self.x2,
            self.y2,
            self.label,
            self.score,
            angle=self.angle,
            locked=self.locked,
            hidden=self.hidden,
            keypoints=list(keypoints),
            keypoint_format=keypoint_format or self.keypoint_format,
        )
        clone.object_id = self.object_id
        clone.model_key = self.model_key
        return clone


@dataclass
class Classification:
    """分类结果。"""

    label: str
    score: float


@dataclass
class ModelResult:
    """模型推理的统一结果结构，对上层节点保持任务无关。"""

    task_type: TaskType
    detections: list[Detection] = field(default_factory=list)
    classification: Optional[Classification] = None
    masks: list[Any] = field(default_factory=list)
    keypoints: list[Any] = field(default_factory=list)
    raw: Any = None

    @property
    def has_boxes(self) -> bool:
        return bool(self.detections)


@dataclass
class Crop:
    """从帧上裁剪出的子图，记录其来源框与裁剪偏移，便于把结果画回原帧。"""

    image: Any  # np.ndarray (H, W, 3)
    bbox: Detection
    offset_x: int
    offset_y: int
    source_frame_index: int

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass
class Frame:
    """按 FPS 采样得到的一帧。"""

    index: int
    timestamp: float
    image: Any  # np.ndarray (H, W, 3)，BGR，来自 cv2

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])
