from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import DeleteResult, ExportResult, MediaItem
from .scanner import flat_collisions
from .store import ReviewStore


class FileOperationError(RuntimeError):
    pass


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path.resolve()), str(root.resolve())]) == str(root.resolve())
    except ValueError:
        return False


def _unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    while True:
        candidate = path.with_name(f"{path.stem}__dup_{uuid.uuid4().hex[:8]}{path.suffix}")
        if not candidate.exists():
            return candidate


class DeleteService:
    def __init__(self, source_root: str | Path, store: ReviewStore, batch_id: str | None = None):
        self.source_root = Path(source_root).resolve(strict=True)
        if not self.source_root.is_dir():
            raise NotADirectoryError(str(self.source_root))
        self.store = store
        self.delete_root = self.source_root.parent / "_delete"
        self.batch_id = batch_id or (
            datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
        )
        self.batch_root = self.delete_root / self.source_root.name / self.batch_id

    def delete(self, items: Iterable[MediaItem]) -> DeleteResult:
        unique_items = {item.media_key: item for item in items}
        if not unique_items:
            raise ValueError("没有可删除的媒体文件")

        planned: dict[str, tuple[Path, Path]] = {}
        for key, item in unique_items.items():
            source = Path(item.absolute_path).resolve(strict=True)
            if not _is_within(source, self.source_root):
                raise FileOperationError(f"拒绝移动源目录外的文件: {source}")
            relative = Path(item.relative_path)
            target = _unique_target(self.batch_root / relative)
            if not _is_within(target, self.delete_root):
                raise FileOperationError(f"删除目标越界: {target}")
            planned[key] = (source, target)

        moved: dict[str, tuple[Path, Path]] = {}
        try:
            for key, (source, target) in planned.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                moved[key] = (source, target)
        except Exception as exc:
            rollback_errors = []
            for source, target in reversed(list(moved.values())):
                try:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(source))
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            detail = f"移动失败: {exc}"
            if rollback_errors:
                detail += "; 回滚失败: " + " | ".join(rollback_errors)
            raise FileOperationError(detail) from exc

        operation_id = uuid.uuid4().hex
        store_moves = {
            key: (str(source), str(target)) for key, (source, target) in moved.items()
        }
        try:
            self.store.mark_deleted(operation_id, store_moves)
            self._write_delete_manifest(operation_id, store_moves)
        except Exception as exc:
            for source, target in reversed(list(moved.values())):
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(source))
            raise FileOperationError(f"删除记录保存失败，文件已回滚: {exc}") from exc

        return DeleteResult(
            operation_id=operation_id,
            moved={key: target for key, (_source, target) in store_moves.items()},
            batch_id=self.batch_id,
        )

    def _write_delete_manifest(
        self, operation_id: str, moves: dict[str, tuple[str, str]]
    ) -> None:
        self.batch_root.mkdir(parents=True, exist_ok=True)
        csv_path = self.batch_root / "delete_manifest.csv"
        exists = csv_path.exists()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with csv_path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "operation_id",
                    "media_key",
                    "source_root",
                    "source_path",
                    "target_path",
                    "delete_time",
                ],
            )
            if not exists:
                writer.writeheader()
            for media_key, (source, target) in moves.items():
                writer.writerow(
                    {
                        "operation_id": operation_id,
                        "media_key": media_key,
                        "source_root": str(self.source_root),
                        "source_path": source,
                        "target_path": target,
                        "delete_time": now,
                    }
                )

    def undo_latest(self) -> int:
        rows = self.store.latest_delete_operation()
        if not rows:
            return 0
        operation_id = rows[0]["operation_id"]
        planned = []
        for row in rows:
            source = Path(row["source_path"])
            target = Path(row["target_path"])
            if source.exists():
                raise FileOperationError(f"无法恢复，原位置已存在文件: {source}")
            if not target.exists():
                raise FileOperationError(f"无法恢复，删除目录文件不存在: {target}")
            planned.append((row["media_key"], source, target))

        restored = []
        try:
            for media_key, source, target in planned:
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(source))
                restored.append((media_key, source, target))
        except Exception as exc:
            for _key, source, target in reversed(restored):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
            raise FileOperationError(f"恢复失败，已回滚: {exc}") from exc

        try:
            self.store.mark_delete_undone(operation_id, [key for key, _s, _t in restored])
        except Exception as exc:
            for _key, source, target in reversed(restored):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
            raise FileOperationError(f"恢复记录保存失败，已重新移回删除目录: {exc}") from exc
        return len(restored)


def _flat_name(item: MediaItem, used: set[str], rename_conflicts: bool) -> str:
    filename = item.filename
    folded = filename.casefold()
    if folded not in used:
        used.add(folded)
        return filename
    if not rename_conflicts:
        raise FileOperationError(f"平铺目标存在同名文件: {filename}")
    digest = hashlib.sha256(item.relative_path.casefold().encode("utf-8")).hexdigest()[:8]
    candidate = f"{item.stem}__{digest}{item.extension}"
    counter = 1
    while candidate.casefold() in used:
        candidate = f"{item.stem}__{digest}_{counter}{item.extension}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def export_labeled(
    source_root: str | Path,
    store: ReviewStore,
    layout: str = "preserve",
    output_name: str | None = None,
) -> ExportResult:
    if layout not in {"preserve", "flat", "flat_rename"}:
        raise ValueError(f"未知导出布局: {layout}")
    source = Path(source_root).resolve(strict=True)
    items = store.list_media()
    collisions = flat_collisions(items)
    if layout == "flat" and collisions:
        examples = next(iter(collisions.values()))
        raise FileOperationError("存在同名文件，不能使用纯平铺: " + " | ".join(examples))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = output_name or f"{source.name}_labeled_{timestamp}"
    final_root = source.parent / base_name
    if final_root.exists() or final_root.with_name(final_root.name + ".incomplete").exists():
        final_root = source.parent / f"{base_name}_{uuid.uuid4().hex[:4]}"
    stage_root = final_root.with_name(final_root.name + ".incomplete")
    stage_root.mkdir(parents=True, exist_ok=False)

    labels_by_media = store.label_names_by_media(item.media_key for item in items)
    manifest = []
    errors = []
    copied = 0
    used_by_label: dict[str, set[str]] = {}
    for item in items:
        source_path = Path(item.absolute_path)
        label_names = labels_by_media.get(item.media_key) or ["unlabeled"]
        for label_name in label_names:
            try:
                label_root = stage_root / label_name
                if layout == "preserve":
                    target = label_root / item.relative_path
                else:
                    used = used_by_label.setdefault(label_name.casefold(), set())
                    filename = _flat_name(item, used, layout == "flat_rename")
                    target = label_root / filename
                if not _is_within(target, stage_root):
                    raise FileOperationError(f"导出目标越界: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise FileOperationError(f"导出目标已存在: {target}")
                shutil.copy2(str(source_path), str(target))
                copied += 1
                manifest.append(
                    {
                        "media_key": item.media_key,
                        "relative_path": item.relative_path,
                        "source_path": str(source_path),
                        "media_type": item.media_type,
                        "pair_key": item.pair_key,
                        "label": label_name,
                        "output_relative_path": str(target.relative_to(stage_root)),
                        "status": "copied",
                        "error": "",
                    }
                )
            except Exception as exc:
                message = f"{item.relative_path} [{label_name}]: {exc}"
                errors.append(message)
                manifest.append(
                    {
                        "media_key": item.media_key,
                        "relative_path": item.relative_path,
                        "source_path": str(source_path),
                        "media_type": item.media_type,
                        "pair_key": item.pair_key,
                        "label": label_name,
                        "output_relative_path": "",
                        "status": "error",
                        "error": str(exc),
                    }
                )

    fieldnames = [
        "media_key",
        "relative_path",
        "source_path",
        "media_type",
        "pair_key",
        "label",
        "output_relative_path",
        "status",
        "error",
    ]
    with (stage_root / "review_manifest.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest)
    with (stage_root / "review_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    summary = {
        "source_root": str(source),
        "layout": layout,
        "copied_files": copied,
        "manifest_rows": len(manifest),
        "errors": errors,
        "complete": not errors,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with (stage_root / "export_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    if errors:
        return ExportResult(str(stage_root), False, copied, len(manifest), errors)
    stage_root.rename(final_root)
    return ExportResult(str(final_root), True, copied, len(manifest), [])
