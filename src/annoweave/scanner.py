from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Tuple

from .models import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, MediaItem, ScanResult


def _natural_key(value: str) -> Tuple[object, ...]:
    parts = re.split(r"(\d+)", value.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _pair_key(parent_relative_path: str, stem: str) -> str:
    parent = parent_relative_path.replace("\\", "/").casefold()
    return f"{parent}|{stem.casefold()}"


def _media_key(relative_path: str) -> str:
    return relative_path.replace("\\", "/").casefold()


def scan_media(
    source_root: str | os.PathLike[str],
    image_extensions: Iterable[str] = IMAGE_EXTENSIONS,
    video_extensions: Iterable[str] = VIDEO_EXTENSIONS,
) -> ScanResult:
    root = Path(source_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    image_exts = {value.casefold() for value in image_extensions}
    video_exts = {value.casefold() for value in video_extensions}
    items = []
    skipped_symlinks = []

    for path in root.rglob("*"):
        if path.is_symlink():
            skipped_symlinks.append(str(path))
            continue
        if not path.is_file():
            continue
        extension = path.suffix.casefold()
        if extension in image_exts:
            media_type = "image"
        elif extension in video_exts:
            media_type = "video"
        else:
            continue

        relative = path.relative_to(root)
        relative_text = str(relative)
        parent_text = "" if str(relative.parent) == "." else str(relative.parent)
        stat = path.stat()
        items.append(
            MediaItem(
                media_key=_media_key(relative_text),
                relative_path=relative_text,
                absolute_path=str(path),
                parent_relative_path=parent_text,
                filename=path.name,
                stem=path.stem,
                extension=extension,
                media_type=media_type,
                pair_key=_pair_key(parent_text, path.stem),
                file_size=int(stat.st_size),
                modified_ns=int(stat.st_mtime_ns),
            )
        )

    items.sort(key=lambda item: _natural_key(item.relative_path))

    pair_groups = defaultdict(list)
    flat_names = defaultdict(list)
    for item in items:
        pair_groups[item.pair_key].append(item)
        flat_names[item.filename.casefold()].append(item.relative_path)

    collisions = {
        filename: sorted(paths, key=_natural_key)
        for filename, paths in flat_names.items()
        if len(paths) > 1
    }
    return ScanResult(
        source_root=str(root),
        items=items,
        pair_groups=dict(pair_groups),
        flat_collisions=collisions,
        skipped_symlinks=skipped_symlinks,
    )


def flat_collisions(items: Iterable[MediaItem]) -> dict[str, list[str]]:
    names = defaultdict(list)
    for item in items:
        names[item.filename.casefold()].append(item.relative_path)
    return {name: paths for name, paths in names.items() if len(paths) > 1}
