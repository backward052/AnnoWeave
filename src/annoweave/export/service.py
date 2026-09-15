"""统一导出服务（M2）：四种模板共用同一实现，检查写盘返回值，媒体身份命名，默认不覆盖。

解决评审 AR-03 / AR-07 / AR-08 / AR-09：
- 全图/单裁剪/全裁剪/全裁剪+元数据 语义严格（全图模板只出全图）。
- 所有 imwrite 检查返回值，统计成功/失败/跳过，记录失败路径与原因。
- 命名含媒体/运行/帧/对象/修订身份；目标已存在则加后缀，默认不覆盖。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SAVE_TEMPLATES = ("full", "single_crop", "full_and_crops", "crops_only")

#: 命名规则版本：project/media/run/frame/object/revision 六段身份（AR-03）
FRAME_TAG_VERSION = "m1"

_SAFE_SEGMENT = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def safe_segment(value: object, fallback: str = "x", limit: int = 40) -> str:
    """把任意标识压成可安全用于文件名的短片段（保留中文，去掉分隔符与空白）。"""
    text = _SAFE_SEGMENT.sub("-", str(value or "").strip()).strip("-._")
    text = text[:limit]
    return text or fallback


def frame_tag(
    media_identity: object,
    frame_index: object,
    run_id: object = "",
    revision: object = "",
) -> str:
    """构造含媒体身份的帧标签。

    评审 AR-03：仅用帧序号命名会让不同媒体的同序号帧互相覆盖。
    `media_identity` 可以是媒体稳定 id、媒体 key 或源文件路径/stem；
    调用方优先传稳定身份，避免路径变化导致命名漂移。
    """
    parts = [FRAME_TAG_VERSION, f"m{safe_segment(media_identity, 'unknown')}"]
    run = safe_segment(run_id, "", limit=12)
    if run:
        parts.append(f"r{run}")
    parts.append(f"f{int(frame_index or 0):06d}")
    rev = safe_segment(revision, "", limit=12)
    if rev:
        parts.append(f"v{rev}")
    return "_".join(parts)


@dataclass
class ExportResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    paths: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (path, error)
    conflicts: list[str] = field(default_factory=list)


class ExportError(RuntimeError):
    pass


class ExportService:
    """面向一帧的导出：给出帧图、渲染图、裁剪槽列表与命名前缀。"""

    def __init__(
        self,
        output_dir: str | Path,
        template: str = "full_and_crops",
        conflict: str = "rename",
    ):
        if template not in SAVE_TEMPLATES:
            raise ExportError(f"未知保存模板: {template}")
        if conflict not in ("rename", "skip"):
            raise ExportError(f"未知冲突策略: {conflict}")
        self.output_dir = Path(output_dir)
        self.template = template
        self.conflict = conflict
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_frame(
        self,
        frame_tag: str,
        frame_image,
        rendered,
        crops,
        selected_crop_index: Optional[int] = None,
        include_meta: bool = True,
    ) -> ExportResult:
        """导出单帧。

        crops: 可迭代的 {image, meta(dict), kind}；meta 用于附带结果说明。
        """
        result = ExportResult()
        prefix = self._sanitize(frame_tag)

        # 1) 全图
        full_rel = f"{prefix}.jpg"
        if self.template in ("full", "full_and_crops"):
            self._write_image(result, full_rel, rendered)

        # 2) 裁剪（仅相关模板写裁剪；full 模板只出全图）
        crop_paths: list[Path] = []
        if self.template != "full":
            crop_list = list(crops)
            for index, crop in enumerate(crop_list):
                if self.template == "single_crop" and index != (selected_crop_index or 0):
                    continue
                crop_rel = f"{prefix}_crop_{index}.jpg"
                self._write_image(result, crop_rel, crop["image"])
                crop_paths.append(self.output_dir / crop_rel)
                if include_meta and crop.get("meta"):
                    self._write_meta(result, f"{prefix}_crop_{index}_result.json", crop["meta"])

        result.total = result.succeeded + result.failed + result.skipped
        return result

    def export_single_crop(
        self,
        frame_tag: str,
        frame_image,
        rendered,
        crops,
        crop_index: int,
        include_meta: bool = True,
    ) -> ExportResult:
        """只输出指定序号的裁剪图（`full`/`full_and_crops` 模板会附带全图）。"""
        crop_list = list(crops)
        if crop_index < 0 or crop_index >= len(crop_list):
            raise ExportError(f"裁剪序号 {crop_index} 越界（共 {len(crop_list)} 个）")
        return self.export_frame(
            frame_tag=frame_tag,
            frame_image=frame_image,
            rendered=rendered,
            crops=crop_list,
            selected_crop_index=crop_index,
            include_meta=include_meta,
        )

    # ------------------------------------------------ 写盘 ------------------------------------------------- #
    def _resolve_target(self, result: ExportResult, rel_path: str) -> Optional[Path]:
        """确定目标路径：默认不覆盖；已存在时按冲突策略改名或跳过。"""
        target = self.output_dir / rel_path
        if not target.exists():
            return target
        result.conflicts.append(str(target))
        if self.conflict == "skip":
            result.skipped += 1
            return None
        return self._unique_path(target)

    def _write_image(self, result: ExportResult, rel_path: str, image) -> None:
        import cv2  # noqa: PLC0415

        resolved = self._resolve_target(result, rel_path)
        if resolved is None:
            return
        target = resolved
        # 临时文件保留图片扩展名，避免 cv2 无法判定格式；写成功后再原子替换
        temporary = target.with_name(f".{target.stem}.tmp{target.suffix}")
        try:
            ok = cv2.imwrite(str(temporary), image)
            if not ok:
                raise ExportError(f"opencv 写盘返回 False: {temporary}")
            os.replace(temporary, target)
            result.succeeded += 1
            result.paths.append(str(target))
        except Exception as exc:  # noqa: BLE001
            self._cleanup(temporary)
            result.failed += 1
            result.failures.append((str(target), str(exc)))

    def _write_meta(self, result: ExportResult, rel_path: str, meta: dict) -> None:
        resolved = self._resolve_target(result, rel_path)
        if resolved is None:
            return
        target = resolved
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, target)
            result.succeeded += 1
            result.paths.append(str(target))
        except OSError as exc:  # noqa: BLE001
            self._cleanup(temporary)
            result.failed += 1
            result.failures.append((str(target), str(exc)))

    # ------------------------------------------------ 辅助 ------------------------------------------------- #
    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        counter = 1
        while True:
            candidate = path.with_name(f"{path.stem}__{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _cleanup(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _sanitize(tag: str) -> str:
        value = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(tag))
        return value or "frame"
