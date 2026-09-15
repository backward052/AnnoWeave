from __future__ import annotations

from typing import Optional

import numpy as np

from .datatypes import Crop, Detection


def crop_region(
    frame_image: np.ndarray,
    detection: Detection,
    expand_px: int = 0,
    expand_ratio: float = 0.0,
    min_size: int = 0,
    clip_to_frame: bool = True,
    source_frame_index: int = 0,
) -> Optional[Crop]:
    """按检测框裁剪一帧中的子图。

    每个方向先加 `expand_px` 像素，再按该方向框长度的 `expand_ratio` 比例扩边；
    可选裁剪到帧内。返回的 `Crop.offset_x/offset_y` 为子图左上角在原帧中的坐标，
    便于把下游结果画回原帧。
    """
    height, width = frame_image.shape[:2]
    box_w = detection.x2 - detection.x1
    box_h = detection.y2 - detection.y1
    expand_x = expand_px + expand_ratio * box_w
    expand_y = expand_px + expand_ratio * box_h

    x1 = detection.x1 - expand_x
    y1 = detection.y1 - expand_y
    x2 = detection.x2 + expand_x
    y2 = detection.y2 + expand_y

    if clip_to_frame:
        x1 = max(0.0, x1)
        y1 = max(0.0, y1)
        x2 = min(float(width), x2)
        y2 = min(float(height), y2)

    x1i, y1i = int(round(x1)), int(round(y1))
    x2i, y2i = int(round(x2)), int(round(y2))
    crop_w = x2i - x1i
    crop_h = y2i - y1i
    if crop_w <= 0 or crop_h <= 0:
        return None
    if min_size and (crop_w < min_size or crop_h < min_size):
        return None

    sub = frame_image[y1i:y2i, x1i:x2i]
    if sub.size == 0:
        return None
    return Crop(
        image=sub,
        bbox=detection,
        offset_x=x1i,
        offset_y=y1i,
        source_frame_index=source_frame_index,
    )


def crop_for_frame(
    frame_image: np.ndarray,
    detections: list[Detection],
    classes: set[str],
    expand_px: int = 0,
    expand_ratio: float = 0.0,
    min_size: int = 0,
    clip_to_frame: bool = True,
    source_frame_index: int = 0,
) -> list[Crop]:
    """按类别过滤检测框并逐一裁剪，返回该帧的裁剪图列表。"""
    crops: list[Crop] = []
    for detection in detections:
        if classes and detection.label not in classes:
            continue
        crop = crop_region(
            frame_image,
            detection,
            expand_px=expand_px,
            expand_ratio=expand_ratio,
            min_size=min_size,
            clip_to_frame=clip_to_frame,
            source_frame_index=source_frame_index,
        )
        if crop is not None:
            crops.append(crop)
    return crops
