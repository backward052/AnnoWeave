from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm"})


@dataclass(frozen=True)
class MediaItem:
    media_key: str
    relative_path: str
    absolute_path: str
    parent_relative_path: str
    filename: str
    stem: str
    extension: str
    media_type: str
    pair_key: str
    file_size: int
    modified_ns: int


@dataclass
class ScanResult:
    source_root: str
    items: List[MediaItem]
    pair_groups: Dict[str, List[MediaItem]] = field(default_factory=dict)
    flat_collisions: Dict[str, List[str]] = field(default_factory=dict)
    skipped_symlinks: List[str] = field(default_factory=list)

    @property
    def image_count(self) -> int:
        return sum(item.media_type == "image" for item in self.items)

    @property
    def video_count(self) -> int:
        return sum(item.media_type == "video" for item in self.items)

    @property
    def has_flat_collisions(self) -> bool:
        return bool(self.flat_collisions)


@dataclass(frozen=True)
class LabelDefinition:
    label_id: int
    name: str
    color: str
    shortcut: str
    sort_order: int
    enabled: bool = True


@dataclass
class DeleteResult:
    operation_id: str
    moved: Dict[str, str]
    batch_id: str


@dataclass
class ExportResult:
    output_directory: str
    complete: bool
    copied_files: int
    manifest_rows: int
    errors: List[str] = field(default_factory=list)
