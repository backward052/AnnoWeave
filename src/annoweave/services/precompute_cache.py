"""整批预推理的轻量本地缓存。

缓存只保存框、关联、裁剪范围和下游结果，不保存原帧/裁剪像素或模型运行对象；
复核到该帧时再从媒体读取图像并恢复裁剪，避免长视频预推理耗尽内存。
"""
from __future__ import annotations

import copy
import hashlib
import os
import pickle
from pathlib import Path

from ..inference.config import inference_config_path
from ..inference.datatypes import Frame


def _path(media_id: str, frame_index: int, workflow_id: str) -> Path:
    media = hashlib.sha256(str(media_id).encode('utf-8')).hexdigest()
    workflow = hashlib.sha256(str(workflow_id).encode('utf-8')).hexdigest()[:16]
    return inference_config_path().parent / 'precomputed' / workflow / media / f'{int(frame_index)}.pkl'


def save_packet(media_id: str, frame_index: int, workflow_id: str, packet) -> Path:
    compact = copy.deepcopy(packet)
    if compact.frame is not None:
        compact.frame = Frame(compact.frame.index, compact.frame.timestamp, None)
    for result in compact.full_results.values():
        result.raw = None
    for slot in compact.crops:
        slot.crop_image = None
        for result in list(slot.results.values()) + list(slot.predictions.values()):
            result.raw = None
    # trace、模型实例、渲染图会放大缓存或无法序列化；复核显示时重新绘制。
    compact.meta = {key: value for key, value in compact.meta.items()
                    if key in ('verdict', 'count_rule', 'roi', 'workflow_name')}
    path = _path(media_id, frame_index, workflow_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(pickle.dumps(compact, protocol=pickle.HIGHEST_PROTOCOL))
    os.replace(temporary, path)
    return path


def load_packet(media_id: str, frame_index: int, workflow_id: str):
    path = _path(media_id, frame_index, workflow_id)
    if not path.exists():
        return None
    try:
        return pickle.loads(path.read_bytes())
    except (OSError, pickle.PickleError, EOFError, AttributeError, ValueError):
        return None


def hydrate_packet(packet, image):
    """把缓存的结构结果与当前读取的原帧结合，恢复可编辑裁剪和渲染图。"""
    if packet is None or image is None:
        return packet
    index = packet.frame.index if packet.frame is not None else 0
    timestamp = packet.frame.timestamp if packet.frame is not None else 0.0
    packet.frame = Frame(index, timestamp, image)
    height, width = image.shape[:2]
    for slot in packet.crops:
        if slot.crop_rect is None:
            continue
        x1, y1, x2, y2 = [int(round(value)) for value in slot.crop_rect]
        x1, x2 = min(max(0, x1), width), min(max(0, x2), width)
        y1, y2 = min(max(0, y1), height), min(max(0, y2), height)
        slot.offset_x, slot.offset_y = x1, y1
        slot.crop_rect = (x1, y1, x2, y2)
        slot.crop_image = image[y1:y2, x1:x2].copy()
    from ..workflow.nodes import DrawNode
    DrawNode({}, '预推理缓存重绘').run(packet)
    return packet
