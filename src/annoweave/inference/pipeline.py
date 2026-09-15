from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from .config import PipelineConfig, SaveConfig
from .crop import crop_for_frame
from .datatypes import Crop, Detection, Frame, ModelResult


class VideoSampler:
    """按目标 FPS 对视频采样抽帧。

    采样步长 = round(源视频帧率 / 目标帧率)，保证“每秒约 N 帧”。
    """

    def __init__(self, video_path: str | Path) -> None:
        import cv2  # noqa: PLC0415

        self.video_path = str(video_path)
        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            raise ValueError(f"无法打开视频: {self.video_path}")
        self.source_fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def sample_indices(self, target_fps: float) -> list[int]:
        if self.frame_count <= 0:
            return []
        if self.source_fps <= 0:
            return list(range(self.frame_count))
        step = max(1, round(self.source_fps / target_fps))
        return list(range(0, self.frame_count, step))

    def sample_count(self, target_fps: float) -> int:
        return len(self.sample_indices(target_fps))

    def read_frame(self, index: int) -> Optional[Frame]:
        import cv2  # noqa: PLC0415

        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = self.cap.read()
        if not ok or image is None:
            return None
        timestamp = index / self.source_fps if self.source_fps > 0 else float(index)
        return Frame(index=index, timestamp=timestamp, image=image)

    def iter_frames(self, target_fps: float):
        for index in self.sample_indices(target_fps):
            frame = self.read_frame(index)
            if frame is not None:
                yield frame

    def close(self) -> None:
        self.cap.release()


class VideoSource:
    """单实例视频解码器：支持逐原始帧访问与元数据查询。

    评审第 6/7 节要求时间轴能访问未采样原始帧；每次 read 都新建
    VideoCapture 会很慢，因此由调用方持有一个 VideoSource 并显式关闭。
    """

    def __init__(self, video_path: str | Path) -> None:
        self.sampler = VideoSampler(video_path)

    @property
    def path(self) -> str:
        return self.sampler.video_path

    @property
    def fps(self) -> float:
        return self.sampler.source_fps

    @property
    def frame_count(self) -> int:
        return self.sampler.frame_count

    @property
    def duration_s(self) -> float:
        if self.fps <= 0:
            return 0.0
        return self.frame_count / self.fps

    def sample_indices(self, target_fps: float) -> list[int]:
        return self.sampler.sample_indices(target_fps)

    def read(self, source_frame: int) -> Optional[Frame]:
        return self.sampler.read_frame(int(source_frame))

    @staticmethod
    def probe(video_path: str | Path) -> tuple[float, int]:
        """只读取视频元数据，避免为缩略图/时长打开完整解码器。"""
        source = VideoSource(video_path)
        try:
            return source.fps, source.frame_count
        finally:
            source.close()

    def close(self) -> None:
        self.sampler.close()


@dataclass
class CropResult:
    crop: Crop
    downstream: dict[str, ModelResult] = field(default_factory=dict)
    """{下游模型名: 该模型在此裁剪图上的推理结果}"""


@dataclass
class FrameResult:
    frame_index: int
    full_result: Optional[ModelResult] = None
    crops: list[CropResult] = field(default_factory=list)


class Pipeline:
    """流水线编排器：单全图模型 -> 裁剪 -> 每个裁剪依次跑所有下游模型。

    接受已实例化的模型对象（鸭子类型：.predict(image) -> ModelResult），
    便于测试时注入桩模型。
    """

    def __init__(
        self,
        config: PipelineConfig,
        full_frame_models: list,
        downstream_models: list,
    ) -> None:
        self.config = config
        self.full_frame_models = full_frame_models
        self.downstream_models = downstream_models

    @classmethod
    def build(cls, config: PipelineConfig, model_factory=None):
        """从配置构造模型实例。model_factory 可注入以替换默认 create_model。"""
        from .models import create_model  # noqa: PLC0415

        factory = model_factory or create_model
        full_frame = [factory(m) for m in config.full_frame_models]
        downstream = [factory(m) for m in config.downstream_models]
        return cls(config, full_frame, downstream)

    def process_frame(self, frame: Frame) -> FrameResult:
        crop_config = self.config.crop
        full_result = None
        if self.full_frame_models:
            full_result = self.full_frame_models[0].predict(frame.image)

        detections = list(full_result.detections) if full_result else []
        crops: list[CropResult] = []
        for crop in crop_for_frame(
            frame.image,
            detections,
            set(crop_config.classes),
            expand_px=crop_config.expand_px,
            expand_ratio=crop_config.expand_ratio,
            min_size=crop_config.min_size,
            clip_to_frame=crop_config.clip_to_frame,
            source_frame_index=frame.index,
        ):
            downstream = {}
            for model in self.downstream_models:
                downstream[model.config.name] = model.predict(crop.image)
            crops.append(CropResult(crop=crop, downstream=downstream))
        return FrameResult(frame_index=frame.index, full_result=full_result, crops=crops)


def draw_detections(
    image: np.ndarray,
    detections: list[Detection],
    color=(0, 255, 0),
    thickness: int = 2,
) -> np.ndarray:
    """在 BGR 帧上绘制检测框与标签，返回新数组。"""
    import cv2  # noqa: PLC0415

    output = image.copy()
    for det in detections:
        x1, y1 = int(round(det.x1)), int(round(det.y1))
        x2, y2 = int(round(det.x2)), int(round(det.y2))
        cv2.rectangle(output, (x1, y1), (x2, y2), color, thickness)
        label = f"{det.label} {det.score:.2f}"
        cv2.putText(
            output,
            label,
            (x1, max(0, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return output


def save_frame_result(
    source_frame: np.ndarray,
    frame_index: int,
    frame_result: FrameResult,
    save_config: SaveConfig,
    output_dir: str | Path,
    selected_crop_index: Optional[int] = None,
    draw_boxes: bool = True,
) -> list[str]:
    """按保存模板写出帧图/裁剪图，返回已写入的文件路径列表。

    模板：
    - full        -> 仅帧图（可叠加检测框）
    - single_crop -> 仅指定序号的裁剪图
    - full_and_crops -> 帧图 + 该帧全部裁剪图
    - crops_only  -> 该帧全部裁剪图
    """
    import cv2  # noqa: PLC0415

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"frame_{frame_index:06d}"
    template = save_config.template

    full_path: Optional[str] = None
    if template in ("full", "full_and_crops"):
        frame_to_save = draw_detections(source_frame, frame_result.full_result.detections) if (
            draw_boxes and frame_result.full_result
        ) else source_frame
        full_path = str(output_dir / f"{prefix}.jpg")
        cv2.imwrite(full_path, frame_to_save)

    crop_paths: list[str] = []
    for index, crop_result in enumerate(frame_result.crops):
        path = str(output_dir / f"{prefix}_crop_{index}.jpg")
        cv2.imwrite(path, crop_result.crop.image)
        crop_paths.append(path)

    saved: list[str] = []
    if template == "full":
        if full_path:
            saved.append(full_path)
    elif template == "single_crop":
        if selected_crop_index is None:
            raise ValueError("single_crop 模板需要指定裁剪图序号")
        if 0 <= selected_crop_index < len(crop_paths):
            saved.append(crop_paths[selected_crop_index])
    elif template == "full_and_crops":
        if full_path:
            saved.append(full_path)
        saved.extend(crop_paths)
    elif template == "crops_only":
        saved.extend(crop_paths)
    return saved


def packet_crops(packet) -> list[dict]:
    """把 Packet.crops 转成导出服务需要的 {image, meta} 结构。"""
    return [
        {
            "image": slot.crop_image,
            "meta": {
                "crop_id": slot.crop_id,
                "person_bbox": [
                    slot.person_bbox.x1, slot.person_bbox.y1, slot.person_bbox.x2, slot.person_bbox.y2
                ],
                "offset_x": slot.offset_x,
                "offset_y": slot.offset_y,
                "crop_rect": list(slot.crop_rect) if slot.crop_rect else None,
                "route": slot.route,
                "result_layers": sorted(slot.results),
                "edited": slot.edited,
                "edited_source": slot.edited_source,
                "user_result": dict(slot.user_result),
            },
        }
        for slot in packet.crops
    ]


def save_single_crop(
    packet,
    frame_image: np.ndarray,
    crop_index: int,
    output_dir: str | Path,
    template: str = "full_and_crops",
    media_identity: object = "",
    revision: object = "",
):
    """单独保存某一帧裁剪图，委托统一导出服务（AR-03/AR-07/AR-08）。

    返回 ExportResult：成功/失败/跳过计数与真实失败路径，
    调用方不得把 `failed>0` 的结果显示成成功。
    """
    from ..export.service import ExportService
    from ..export.service import frame_tag as build_frame_tag

    exporter = ExportService(output_dir, template)
    return exporter.export_single_crop(
        frame_tag=build_frame_tag(
            media_identity or getattr(packet, "media_identity", "") or "unknown",
            packet.frame.index,
            run_id=getattr(packet, "run_id", ""),
            revision=revision,
        ),
        frame_image=frame_image,
        rendered=packet.meta.get("rendered", frame_image),
        crops=packet_crops(packet),
        crop_index=crop_index,
    )


def pipeline_config_sample() -> None:
    """占位：提示用户使用 config.default_config() 生成示例配置。"""
    from .config import default_config  # noqa: PLC0415

    default_config()
