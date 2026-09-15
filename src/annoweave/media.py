from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})


@dataclass
class MediaEntry:
    path: str
    kind: str  # 'video' | 'image'
    display: str = ""

    def __post_init__(self):
        if not self.display:
            self.display = self.path


@dataclass
class MediaSet:
    """多源输入：视频 / 图片 / 文件夹混合，统一成一个媒体列表。

    每个条目是一个视频或一张图片；工作区据此逐媒体、逐帧导航。
    """

    entries: list[MediaEntry] = field(default_factory=list)
    source: str = ""

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    @classmethod
    def from_paths(cls, paths) -> "MediaSet":
        entries: list[MediaEntry] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                entries.extend(_scan_folder(path))
            elif path.is_file():
                entry = _make_entry(path)
                if entry:
                    entries.append(entry)
        return cls(entries=entries)

    @classmethod
    def from_folder(cls, folder: str | Path) -> "MediaSet":
        return cls(entries=_scan_folder(Path(folder)), source=str(folder))

    @property
    def video_entries(self) -> list[MediaEntry]:
        return [e for e in self.entries if e.kind == "video"]

    @property
    def image_entries(self) -> list[MediaEntry]:
        return [e for e in self.entries if e.kind == "image"]


def _make_entry(path: Path) -> MediaEntry | None:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return MediaEntry(str(path), "video")
    if ext in IMAGE_EXTS:
        return MediaEntry(str(path), "image")
    return None


def _scan_folder(folder: Path) -> list[MediaEntry]:
    entries = []
    if not folder.is_dir():
        return entries
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        entry = _make_entry(path)
        if entry:
            # 用相对显示名，便于区分
            entry.display = str(path.relative_to(folder))
            entries.append(entry)
    return entries


def read_image(path: str | Path):
    """读取图片为 BGR ndarray。"""
    import cv2  # noqa: PLC0415

    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"无法读取图片: {path}")
    return image
