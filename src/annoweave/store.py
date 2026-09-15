from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .models import LabelDefinition, MediaItem

RESERVED_LABELS = {"unlabeled"}
INVALID_LABEL_CHARS = re.compile(r'[\\/:*?"<>|]')
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def validate_label_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("标签名不能为空")
    if value.casefold() in RESERVED_LABELS:
        raise ValueError(f"{value} 是系统保留标签")
    if (
        INVALID_LABEL_CHARS.search(value)
        or value in {".", ".."}
        or value.endswith((".", " "))
        or value.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("标签名包含 Windows 文件名不允许的字符")
    return value


class ReviewStore:
    def __init__(self, database_path: str | Path, source_root: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.source_root = str(Path(source_root).resolve())
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.database_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()
        existing_root = self.get_meta("source_root")
        if existing_root and Path(existing_root) != Path(self.source_root):
            raise ValueError(f"数据库属于其他源目录: {existing_root}")
        self.set_meta("source_root", self.source_root)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS media (
                    media_key TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    absolute_path TEXT NOT NULL,
                    parent_relative_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    stem TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    media_type TEXT NOT NULL CHECK(media_type IN ('image', 'video')),
                    pair_key TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    present INTEGER NOT NULL DEFAULT 1,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    trash_path TEXT NOT NULL DEFAULT '',
                    last_scan_token TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_media_pair ON media(pair_key);
                CREATE INDEX IF NOT EXISTS idx_media_active ON media(present, deleted, media_type);
                CREATE TABLE IF NOT EXISTS labels (
                    label_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    color TEXT NOT NULL DEFAULT '#4caf50',
                    shortcut TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS media_labels (
                    media_key TEXT NOT NULL REFERENCES media(media_key) ON DELETE CASCADE,
                    label_id INTEGER NOT NULL REFERENCES labels(label_id) ON DELETE CASCADE,
                    assignment_source TEXT NOT NULL DEFAULT 'manual',
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY(media_key, label_id)
                );
                CREATE TABLE IF NOT EXISTS file_operations (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    media_key TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    undone INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_file_ops_id ON file_operations(operation_id, undone);
                """
            )

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def get_json_meta(self, key: str, default):
        raw = self.get_meta(key)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def set_json_meta(self, key: str, value) -> None:
        self.set_meta(key, json.dumps(value, ensure_ascii=False, sort_keys=True))

    def sync_scan(self, items: Sequence[MediaItem]) -> None:
        scan_token = datetime.now().strftime("%Y%m%d%H%M%S%f")
        with self.transaction() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO media(
                        media_key, relative_path, absolute_path, parent_relative_path,
                        filename, stem, extension, media_type, pair_key, file_size,
                        modified_ns, present, deleted, trash_path, last_scan_token
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, '', ?)
                    ON CONFLICT(media_key) DO UPDATE SET
                        relative_path=excluded.relative_path,
                        absolute_path=excluded.absolute_path,
                        parent_relative_path=excluded.parent_relative_path,
                        filename=excluded.filename,
                        stem=excluded.stem,
                        extension=excluded.extension,
                        media_type=excluded.media_type,
                        pair_key=excluded.pair_key,
                        file_size=excluded.file_size,
                        modified_ns=excluded.modified_ns,
                        present=1,
                        deleted=CASE WHEN media.deleted=1 THEN 1 ELSE 0 END,
                        last_scan_token=excluded.last_scan_token
                    """,
                    (
                        item.media_key,
                        item.relative_path,
                        item.absolute_path,
                        item.parent_relative_path,
                        item.filename,
                        item.stem,
                        item.extension,
                        item.media_type,
                        item.pair_key,
                        item.file_size,
                        item.modified_ns,
                        scan_token,
                    ),
                )
            conn.execute(
                "UPDATE media SET present=0 WHERE last_scan_token<>? AND deleted=0",
                (scan_token,),
            )
        self.set_meta("last_scan_token", scan_token)

    @staticmethod
    def _row_to_media(row: sqlite3.Row) -> MediaItem:
        return MediaItem(
            media_key=row["media_key"],
            relative_path=row["relative_path"],
            absolute_path=row["absolute_path"],
            parent_relative_path=row["parent_relative_path"],
            filename=row["filename"],
            stem=row["stem"],
            extension=row["extension"],
            media_type=row["media_type"],
            pair_key=row["pair_key"],
            file_size=int(row["file_size"]),
            modified_ns=int(row["modified_ns"]),
        )

    def list_media(self, media_type: str | None = None) -> list[MediaItem]:
        sql = "SELECT * FROM media WHERE present=1 AND deleted=0"
        params: list[object] = []
        if media_type:
            sql += " AND media_type=?"
            params.append(media_type)
        sql += " ORDER BY relative_path COLLATE NOCASE"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_media(row) for row in rows]

    def media_by_keys(self, media_keys: Iterable[str]) -> list[MediaItem]:
        keys = list(dict.fromkeys(media_keys))
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM media WHERE media_key IN ({placeholders})", keys
            ).fetchall()
        by_key = {row["media_key"]: self._row_to_media(row) for row in rows}
        return [by_key[key] for key in keys if key in by_key]

    def group_members(self, pair_key: str, active_only: bool = True) -> list[MediaItem]:
        sql = "SELECT * FROM media WHERE pair_key=?"
        if active_only:
            sql += " AND present=1 AND deleted=0"
        sql += " ORDER BY media_type, relative_path COLLATE NOCASE"
        with self._lock:
            rows = self._conn.execute(sql, (pair_key,)).fetchall()
        return [self._row_to_media(row) for row in rows]

    def add_label(self, name: str, color: str, shortcut: str = "") -> LabelDefinition:
        value = validate_label_name(name)
        shortcut_value = str(shortcut or "").strip()
        with self.transaction() as conn:
            if shortcut_value:
                conflict = conn.execute(
                    "SELECT name FROM labels WHERE shortcut=? COLLATE NOCASE AND enabled=1",
                    (shortcut_value,),
                ).fetchone()
                if conflict:
                    raise ValueError(f"快捷键已被标签 {conflict['name']} 使用")
            order_row = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM labels").fetchone()
            cursor = conn.execute(
                "INSERT INTO labels(name, color, shortcut, sort_order, enabled) VALUES(?, ?, ?, ?, 1)",
                (value, color or "#4caf50", shortcut_value, int(order_row["n"])),
            )
            label_id = int(cursor.lastrowid)
        return LabelDefinition(label_id, value, color or "#4caf50", shortcut_value, int(order_row["n"]))

    def update_label(self, label_id: int, name: str, color: str, shortcut: str) -> None:
        value = validate_label_name(name)
        shortcut_value = str(shortcut or "").strip()
        with self.transaction() as conn:
            if shortcut_value:
                conflict = conn.execute(
                    "SELECT name FROM labels WHERE shortcut=? COLLATE NOCASE "
                    "AND enabled=1 AND label_id<>?",
                    (shortcut_value, label_id),
                ).fetchone()
                if conflict:
                    raise ValueError(f"快捷键已被标签 {conflict['name']} 使用")
            conn.execute(
                "UPDATE labels SET name=?, color=?, shortcut=? WHERE label_id=?",
                (value, color or "#4caf50", shortcut_value, label_id),
            )

    def delete_label(self, label_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM labels WHERE label_id=?", (label_id,))

    def labels(self) -> list[LabelDefinition]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM labels WHERE enabled=1 ORDER BY sort_order, label_id"
            ).fetchall()
        return [
            LabelDefinition(
                int(row["label_id"]),
                row["name"],
                row["color"],
                row["shortcut"],
                int(row["sort_order"]),
                bool(row["enabled"]),
            )
            for row in rows
        ]

    def reorder_labels(self, label_ids: Iterable[int]) -> None:
        """按给定 ID 顺序持久化标签排列，要求包含全部当前标签且不重复。"""
        requested = [int(label_id) for label_id in label_ids]
        current = [label.label_id for label in self.labels()]
        if len(requested) != len(set(requested)) or set(requested) != set(current):
            raise ValueError("标签排序必须包含全部当前标签，且不能重复")
        with self.transaction() as conn:
            for position, label_id in enumerate(requested):
                conn.execute(
                    "UPDATE labels SET sort_order=? WHERE label_id=?",
                    (position, label_id),
                )

    def label_assignment_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM media_labels"
            ).fetchone()
        return int(row["n"])

    def replace_unassigned_labels(self, catalog: Sequence[dict]) -> bool:
        """用应用级模板替换标签；已有标注时拒绝替换，避免改变历史含义。"""
        prepared = []
        names = set()
        shortcuts = set()
        for position, raw in enumerate(catalog):
            name = validate_label_name(raw.get("name", ""))
            folded_name = name.casefold()
            shortcut = str(raw.get("shortcut", "") or "").strip()
            folded_shortcut = shortcut.casefold()
            if folded_name in names:
                raise ValueError(f"标签模板中名称重复: {name}")
            if folded_shortcut and folded_shortcut in shortcuts:
                raise ValueError(f"标签模板中快捷键重复: {shortcut}")
            names.add(folded_name)
            if folded_shortcut:
                shortcuts.add(folded_shortcut)
            prepared.append(
                (name, str(raw.get("color", "") or "#4caf50"), shortcut, position)
            )
        if not prepared:
            raise ValueError("标签模板不能为空")

        with self.transaction() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM media_labels").fetchone()
            if int(count["n"]) > 0:
                return False
            conn.execute("DELETE FROM labels")
            conn.executemany(
                "INSERT INTO labels(name, color, shortcut, sort_order, enabled) "
                "VALUES(?, ?, ?, ?, 1)",
                prepared,
            )
        return True

    def ensure_default_labels(self) -> None:
        if self.labels():
            return
        for name, color, shortcut in (
            ("correct", "#2e7d32", "1"),
            ("false_positive", "#c62828", "2"),
            ("uncertain", "#f9a825", "3"),
            ("invalid_media", "#616161", "4"),
        ):
            self.add_label(name, color, shortcut)

    def set_label(
        self,
        media_keys: Iterable[str],
        label_id: int,
        checked: bool,
        assignment_source: str = "manual",
    ) -> None:
        keys = list(dict.fromkeys(media_keys))
        if not keys:
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.transaction() as conn:
            if checked:
                for key in keys:
                    conn.execute(
                        """
                        INSERT INTO media_labels(media_key, label_id, assignment_source, assigned_at)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(media_key, label_id) DO UPDATE SET
                            assignment_source=excluded.assignment_source,
                            assigned_at=excluded.assigned_at
                        """,
                        (key, label_id, assignment_source, now),
                    )
            else:
                placeholders = ",".join("?" for _ in keys)
                conn.execute(
                    f"DELETE FROM media_labels WHERE label_id=? AND media_key IN ({placeholders})",
                    [label_id, *keys],
                )

    def clear_labels(self, media_keys: Iterable[str]) -> None:
        keys = list(dict.fromkeys(media_keys))
        if not keys:
            return
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            self._conn.execute(
                f"DELETE FROM media_labels WHERE media_key IN ({placeholders})", keys
            )

    def label_states(self, media_keys: Iterable[str]) -> dict[int, int]:
        keys = list(dict.fromkeys(media_keys))
        labels = self.labels()
        if not keys:
            return {label.label_id: 0 for label in labels}
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT label_id, COUNT(*) AS n FROM media_labels "
                f"WHERE media_key IN ({placeholders}) GROUP BY label_id",
                keys,
            ).fetchall()
        counts = {int(row["label_id"]): int(row["n"]) for row in rows}
        total = len(keys)
        return {
            label.label_id: 0 if counts.get(label.label_id, 0) == 0 else (
                2 if counts.get(label.label_id, 0) == total else 1
            )
            for label in labels
        }

    def label_names_by_media(self, media_keys: Iterable[str]) -> dict[str, list[str]]:
        keys = list(dict.fromkeys(media_keys))
        result = {key: [] for key in keys}
        if not keys:
            return result
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT ml.media_key, l.name
                FROM media_labels ml JOIN labels l ON l.label_id=ml.label_id
                WHERE ml.media_key IN ({placeholders}) AND l.enabled=1
                ORDER BY l.sort_order, l.label_id
                """,
                keys,
            ).fetchall()
        for row in rows:
            result[row["media_key"]].append(row["name"])
        return result

    def media_keys_with_labels(self, media_keys: Iterable[str]) -> set[str]:
        keys = list(dict.fromkeys(media_keys))
        if not keys:
            return set()
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT DISTINCT media_key FROM media_labels "
                f"WHERE media_key IN ({placeholders})",
                keys,
            ).fetchall()
        return {str(row["media_key"]) for row in rows}

    def mark_deleted(self, operation_id: str, moves: dict[str, tuple[str, str]]) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.transaction() as conn:
            for media_key, (source_path, target_path) in moves.items():
                conn.execute(
                    "UPDATE media SET deleted=1, present=0, trash_path=? WHERE media_key=?",
                    (target_path, media_key),
                )
                conn.execute(
                    """
                    INSERT INTO file_operations(
                        operation_id, operation_type, media_key, source_path,
                        target_path, created_at, undone
                    ) VALUES(?, 'delete', ?, ?, ?, ?, 0)
                    """,
                    (operation_id, media_key, source_path, target_path, now),
                )

    def latest_delete_operation(self) -> list[sqlite3.Row]:
        with self._lock:
            op = self._conn.execute(
                """
                SELECT operation_id FROM file_operations
                WHERE operation_type='delete' AND undone=0
                ORDER BY row_id DESC LIMIT 1
                """
            ).fetchone()
            if not op:
                return []
            return self._conn.execute(
                "SELECT * FROM file_operations WHERE operation_id=? ORDER BY row_id",
                (op["operation_id"],),
            ).fetchall()

    def mark_delete_undone(self, operation_id: str, media_keys: Iterable[str]) -> None:
        keys = list(media_keys)
        with self.transaction() as conn:
            conn.execute(
                "UPDATE file_operations SET undone=1 WHERE operation_id=?",
                (operation_id,),
            )
            for key in keys:
                conn.execute(
                    "UPDATE media SET deleted=0, present=1, trash_path='' WHERE media_key=?",
                    (key,),
                )
