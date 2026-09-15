"""人体关键点的排列定义与骨架绘制。

约定
----
* 关键点排列用字符串标识（`format`）：`coco17`、`openpose18`、`unknown`。
* 索引都是 **0 基**；`edges` 里的下标直接对应关键点列表。
* 颜色沿用 mmpose / DWPose 的默认配色，画出来的骨架与主流标注工具一致。
* 绘制坐标是"图像像素坐标"，与 `Detection` 里的框坐标系一致。

骨架定义兼容 COCO-17 与 OpenPose-18 常用关键点排列。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

# --------------------------------------------------------------------------- #
# COCO-17（最常用：YOLOv8-pose / DWPose / RTMPose 都是这一套）
# --------------------------------------------------------------------------- #
COCO17_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)

COCO17_EDGES = (
    (15, 13), (13, 11), (16, 14), (14, 12), (11, 12),
    (5, 11), (6, 12), (5, 6), (5, 7), (6, 8), (7, 9), (8, 10),
    (1, 2), (0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6),
)

#: mmpose 的默认关键点配色（RGB）
COCO17_COLORS = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
    (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
    (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
    (255, 0, 255), (255, 0, 170),
)

#: 骨架连线配色（RGB），按边顺序循环
COCO17_EDGE_COLORS = (
    (0, 255, 0), (0, 255, 0), (0, 255, 0), (0, 255, 0), (255, 128, 0),
    (255, 128, 0), (255, 128, 0), (51, 153, 255), (51, 153, 255), (51, 153, 255),
    (51, 153, 255), (51, 153, 255), (0, 255, 255), (0, 255, 255), (0, 255, 255),
    (0, 255, 255), (0, 255, 255), (255, 0, 255), (255, 0, 255),
)

# --------------------------------------------------------------------------- #
# OpenPose-18（部分导出模型用这一套：17 个身体点 + 颈/骨盆替换）
# --------------------------------------------------------------------------- #
OPENPOSE18_NAMES = (
    "nose", "neck", "right_shoulder", "right_elbow", "right_wrist",
    "left_shoulder", "left_elbow", "left_wrist",
    "right_hip", "right_knee", "right_ankle",
    "left_hip", "left_knee", "left_ankle",
    "right_eye", "left_eye", "right_ear", "left_ear",
)

OPENPOSE18_EDGES = (
    (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7),
    (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13),
    (1, 0), (0, 14), (14, 16), (0, 15), (15, 17),
)

OPENPOSE18_COLORS = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
    (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
    (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
    (255, 0, 255), (255, 0, 170), (255, 0, 85),
)


@dataclass(frozen=True)
class Skeleton:
    """一套骨架定义。"""

    name: str
    display_name: str
    names: tuple[str, ...]
    edges: tuple[tuple[int, int], ...]
    colors: tuple[tuple[int, int, int], ...]
    edge_colors: tuple[tuple[int, int, int], ...] = ()

    @property
    def count(self) -> int:
        return len(self.names)

    def edge_color(self, index: int) -> tuple[int, int, int]:
        palette = self.edge_colors or self.colors
        return palette[index % len(palette)]

    def point_color(self, index: int) -> tuple[int, int, int]:
        return self.colors[index % len(self.colors)]

    def point_name(self, index: int) -> str:
        if 0 <= index < len(self.names):
            return self.names[index]
        return f"kp{index}"


COCO17 = Skeleton("coco17", "COCO-17（人体）", COCO17_NAMES, COCO17_EDGES,
                  COCO17_COLORS, COCO17_EDGE_COLORS)
OPENPOSE18 = Skeleton("openpose18", "OpenPose-18", OPENPOSE18_NAMES, OPENPOSE18_EDGES,
                      OPENPOSE18_COLORS)

SKELETONS: dict[str, Skeleton] = {COCO17.name: COCO17, OPENPOSE18.name: OPENPOSE18}

#: 模型没声明关键点排列时，按点数自动猜一个
AUTO_BY_COUNT = {17: COCO17, 18: OPENPOSE18}


def resolve_skeleton(name: str = "", count: int = 0) -> Optional[Skeleton]:
    """按名称或点数找到骨架定义；找不到返回 None（调用方退化为只画点）。"""
    key = (name or "").strip().lower()
    if key in SKELETONS:
        return SKELETONS[key]
    if key in ("coco", "coco_17", "body17"):
        return COCO17
    if key in ("openpose", "body18"):
        return OPENPOSE18
    return AUTO_BY_COUNT.get(int(count or 0))


def guess_format(count: int) -> str:
    skeleton = AUTO_BY_COUNT.get(int(count or 0))
    return skeleton.name if skeleton else "unknown"


# --------------------------------------------------------------------------- #
# 绘制（OpenCV，BGR）
# --------------------------------------------------------------------------- #
def draw_skeleton(
    image: Any,
    keypoints: Sequence[Any],
    *,
    skeleton: Optional[Skeleton] = None,
    keypoint_format: str = "",
    score_threshold: float = 0.3,
    radius: int = 3,
    line_width: int = 2,
    draw_points: bool = True,
    color: Optional[tuple[int, int, int]] = None,
) -> Any:
    """在 BGR 图上绘制骨架，返回同一张图（原地修改）。

    `keypoints` 元素可以是 `Keypoint`，也可以是 `(x, y, score)` 序列。
    低于 `score_threshold` 的点既不画点也不参与连线。
    """
    import cv2  # noqa: PLC0415

    if image is None or not len(keypoints):
        return image

    points: list[tuple[float, float, float]] = []
    for item in keypoints:
        if hasattr(item, "x") and hasattr(item, "y"):
            points.append((float(item.x), float(item.y), float(getattr(item, "score", 0.0))))
        else:
            seq = list(item)
            x = float(seq[0]) if len(seq) > 0 else 0.0
            y = float(seq[1]) if len(seq) > 1 else 0.0
            score = float(seq[2]) if len(seq) > 2 else 1.0
            points.append((x, y, score))

    layout = skeleton or resolve_skeleton(keypoint_format, len(points))
    height, width = image.shape[:2]
    visible = [index for index, (_, _, score) in enumerate(points) if score > score_threshold]

    if layout is not None:
        for edge_index, (start, end) in enumerate(layout.edges):
            if start not in visible or end not in visible:
                continue
            x1, y1, _ = points[start]
            x2, y2, _ = points[end]
            if not _inside(x1, y1, width, height) or not _inside(x2, y2, width, height):
                continue
            edge_color = color or layout.edge_color(edge_index)
            cv2.line(image, (int(x1), int(y1)), (int(x2), int(y2)),
                     _bgr(edge_color), int(line_width), cv2.LINE_AA)

    if draw_points:
        for index in visible:
            x, y, _ = points[index]
            if not _inside(x, y, width, height):
                continue
            point_color = color or (layout.point_color(index) if layout else (0, 255, 255))
            cv2.circle(image, (int(x), int(y)), int(radius), _bgr(point_color), -1, cv2.LINE_AA)
    return image


def _inside(x: float, y: float, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height


def _bgr(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return (int(rgb[2]), int(rgb[1]), int(rgb[0]))


# --------------------------------------------------------------------------- #
# 导出用的序列化
# --------------------------------------------------------------------------- #
def keypoints_payload(keypoints: Sequence[Any], keypoint_format: str = "") -> dict:
    """把关键点转成导出清单里的 JSON 结构（保留点名，便于人工核对）。"""
    points = list(keypoints or [])
    layout = resolve_skeleton(keypoint_format, len(points))
    return {
        "format": keypoint_format or (layout.name if layout else guess_format(len(points))),
        "count": len(points),
        "visible": sum(1 for item in points if float(getattr(item, "score", 1.0)) > 0.3),
        "points": [
            {
                "name": layout.point_name(index) if layout else f"kp{index}",
                "x": round(float(getattr(item, "x", 0.0)), 2),
                "y": round(float(getattr(item, "y", 0.0)), 2),
                "score": round(float(getattr(item, "score", 0.0)), 4),
            }
            for index, item in enumerate(points)
        ],
    }
