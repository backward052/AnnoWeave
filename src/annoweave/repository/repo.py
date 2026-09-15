"""SQLite 仓储（M1）：领域实体存取 + 幂等建表/加列/回填。

“打开项目时惰性回填 media_id + 确保新表/新列”，旧 store.py 保持不动（双轨过渡）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from ..domain.entities import (
    Artifact,
    Association,
    FrameRef,
    Media,
    ObjectAnnotation,
    Project,
    ReviewDecision,
    Run,
    WorkflowSnapshot,
)
from .schema import BASE_TABLE_SQL, MEDIA_NEW_COLUMNS, NEW_INDEX_SQL, NEW_TABLE_SQL

_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ReviewRepository:
    """项目级仓储。每个项目一个 SQLite 文件。"""

    def __init__(
        self,
        database_path: str | Path,
        project_id: str | None = None,
        project_name: str = "AnnoWeave",
        source_root: str = "",
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_id = project_id or uuid.uuid4().hex
        self.project_name = project_name
        self.source_root = source_root
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.database_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._closed = False
        self._init_schema()
        self._ensure_project()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    @property
    def closed(self) -> bool:
        return self._closed

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

    # ------------------------------------------------ schema / 迁移 ------------------------------------------------ #
    def _init_schema(self) -> None:
        with self._lock:
            for ddl in BASE_TABLE_SQL:
                self._conn.executescript(ddl)
            for ddl in NEW_TABLE_SQL:
                self._conn.executescript(ddl)
            self._ensure_media_columns(self._conn)
            for ddl in NEW_INDEX_SQL:
                self._conn.execute(ddl)
            self._backfill_media_ids(self._conn)
            self._conn.execute(
                "INSERT INTO project(project_id, name, source_root, schema_version, meta, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id) DO NOTHING",
                (self.project_id, self.project_name, self.source_root, _SCHEMA_VERSION, "{}", _now(), _now()),
            )

    @staticmethod
    def _ensure_media_columns(conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(media)").fetchall()}
        for column, ddl_type in MEDIA_NEW_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE media ADD COLUMN {column} {ddl_type}")

    @staticmethod
    def _backfill_media_ids(conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT rowid FROM media WHERE media_id IS NULL OR media_id=''").fetchall()
        for row in rows:
            conn.execute("UPDATE media SET media_id=? WHERE rowid=?", (uuid.uuid4().hex, row["rowid"]))

    def _ensure_project(self) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE project SET name=? WHERE project_id=?",
                (self.project_name, self.project_id),
            )

    # ------------------------------------------------ Project ------------------------------------------------ #
    def get_project(self) -> Optional[Project]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM project WHERE project_id=?", (self.project_id,)).fetchone()
        return self._row_to_project(row) if row else None

    @staticmethod
    def _row_to_project(row) -> Project:
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            source_root=_rv(row, "source_root") or "",
            schema_version=int(_rv(row, "schema_version") or 1),
            meta=json.loads(row["meta"]) if _rv(row, "meta") else {},
            created_at=_rv(row, "created_at") or "",
            updated_at=_rv(row, "updated_at") or "",
        )

    # ------------------------------------------------ Media ------------------------------------------------ #
    def upsert_media(self, item: Media) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO media(
                  media_key, relative_path, absolute_path, parent_relative_path, filename,
                  stem, extension, media_type, pair_key, file_size, modified_ns, present, deleted, trash_path, last_scan_token,
                  media_id, project_id, kind, width, height, duration_s, frame_count, source_path
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                  present=excluded.present,
                  media_id=excluded.media_id,
                  kind=excluded.kind,
                  width=excluded.width, height=excluded.height,
                  duration_s=excluded.duration_s, frame_count=excluded.frame_count,
                  source_path=excluded.source_path
                """,
                (
                    item.media_key, item.relative_path, item.absolute_path, item.parent_relative_path,
                    item.filename, item.stem, item.extension, item.media_type, item.pair_key,
                    item.file_size, item.modified_ns, 1 if item.present else 0,
                    1 if item.deleted else 0, item.trash_path, "",
                    item.media_id or uuid.uuid4().hex, item.project_id or self.project_id,
                    item.kind or item.media_type, item.width, item.height, item.duration_s,
                    item.frame_count, item.source_path or item.absolute_path,
                ),
            )

    def list_media_rows(self) -> list:
        """返回 media 原始行，供批量读取稳定身份等新增列。"""
        with self._lock:
            return [
                dict(row)
                for row in self._conn.execute(
                    "SELECT media_key, media_id, project_id, kind, width, height, duration_s, "
                    "frame_count, source_path FROM media"
                ).fetchall()
            ]

    # ------------------------------------------------ FrameRef（惰性落库） ------------------------------------------------ #
    def get_or_create_frame_ref(self, media_id: str, frame_index: int, source_frame_index: int = 0, timestamp_s: float = 0.0) -> FrameRef:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM frame_ref WHERE media_id=? AND frame_index=?",
                (media_id, frame_index),
            ).fetchone()
            if row is not None:
                return self._row_to_frame_ref(row)
        frame_ref_id = uuid.uuid4().hex
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO frame_ref(frame_ref_id, media_id, frame_index, source_frame_index, timestamp_s, created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(media_id, frame_index) DO NOTHING",
                (frame_ref_id, media_id, frame_index, source_frame_index, timestamp_s, _now()),
            )
            row = conn.execute(
                "SELECT * FROM frame_ref WHERE media_id=? AND frame_index=?",
                (media_id, frame_index),
            ).fetchone()
        return self._row_to_frame_ref(row)

    @staticmethod
    def _row_to_frame_ref(row) -> FrameRef:
        return FrameRef(
            frame_ref_id=row["frame_ref_id"],
            media_id=row["media_id"],
            frame_index=int(row["frame_index"]),
            source_frame_index=int(_rv(row, "source_frame_index") or 0),
            timestamp_s=float(_rv(row, "timestamp_s") or 0.0),
            created_at=_rv(row, "created_at") or "",
        )

    # ------------------------------------------------ ObjectAnnotation ------------------------------------------------ #
    def upsert_annotation(self, obj: ObjectAnnotation) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO object_annotation(
                  annotation_id, frame_ref_id, object_id, layer, class_label,
                  predicted_x1, predicted_y1, predicted_x2, predicted_y2, predicted_conf, predicted_run_id,
                  manual_x1, manual_y1, manual_x2, manual_y2, manual_label, manual_revision, manual_updated_at,
                  adopted_x1, adopted_y1, adopted_x2, adopted_y2, adopted_label, adopted_source,
                  created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(annotation_id) DO UPDATE SET
                  layer=excluded.layer, class_label=excluded.class_label, object_id=excluded.object_id,
                  predicted_x1=excluded.predicted_x1, predicted_y1=excluded.predicted_y1,
                  predicted_x2=excluded.predicted_x2, predicted_y2=excluded.predicted_y2,
                  predicted_conf=excluded.predicted_conf, predicted_run_id=excluded.predicted_run_id,
                  manual_x1=excluded.manual_x1, manual_y1=excluded.manual_y1,
                  manual_x2=excluded.manual_x2, manual_y2=excluded.manual_y2,
                  manual_label=excluded.manual_label, manual_revision=excluded.manual_revision,
                  manual_updated_at=excluded.manual_updated_at,
                  adopted_x1=excluded.adopted_x1, adopted_y1=excluded.adopted_y1,
                  adopted_x2=excluded.adopted_x2, adopted_y2=excluded.adopted_y2,
                  adopted_label=excluded.adopted_label, adopted_source=excluded.adopted_source,
                  updated_at=excluded.updated_at
                """,
                (
                    obj.annotation_id, obj.frame_ref_id, obj.object_id, obj.layer, obj.class_label,
                    *(_box4(obj.predicted)), obj.predicted_conf, obj.predicted_run_id,
                    *(_box4(obj.manual)), obj.manual_label, obj.manual_revision, obj.manual_updated_at,
                    *(_box4(obj.adopted)), obj.adopted_label, obj.adopted_source,
                    obj.created_at or _now(), _now(),
                ),
            )

    def annotations_for_frame(self, frame_ref_id: str) -> list[ObjectAnnotation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM object_annotation WHERE frame_ref_id=? ORDER BY created_at",
                (frame_ref_id,),
            ).fetchall()
        return [self._row_to_annotation(row) for row in rows]

    def delete_annotation(self, annotation_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM object_annotation WHERE annotation_id=?", (annotation_id,))

    def annotations_by_ids(self, annotation_ids: list[str]) -> dict[str, ObjectAnnotation]:
        """按 annotation_id 批量读取，避免逐条查询。"""
        if not annotation_ids:
            return {}
        result: dict[str, ObjectAnnotation] = {}
        with self._lock:
            for start in range(0, len(annotation_ids), 200):
                chunk = annotation_ids[start:start + 200]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._conn.execute(
                    f"SELECT * FROM object_annotation WHERE annotation_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    item = self._row_to_annotation(row)
                    result[item.annotation_id] = item
        return result

    @staticmethod
    def _row_to_annotation(row) -> ObjectAnnotation:
        return ObjectAnnotation(
            annotation_id=row["annotation_id"],
            frame_ref_id=row["frame_ref_id"],
            object_id=_rv(row, "object_id") or "",
            layer=_rv(row, "layer") or "",
            class_label=_rv(row, "class_label") or "",
            predicted=_box_from_row(row, "predicted"),
            predicted_conf=float(_rv(row, "predicted_conf") or 0.0),
            predicted_run_id=_rv(row, "predicted_run_id") or "",
            manual=_box_from_row(row, "manual"),
            manual_label=_rv(row, "manual_label") or "",
            manual_revision=int(_rv(row, "manual_revision") or 0),
            manual_updated_at=_rv(row, "manual_updated_at") or "",
            adopted=_box_from_row(row, "adopted"),
            adopted_label=_rv(row, "adopted_label") or "",
            adopted_source=_rv(row, "adopted_source") or "",
            created_at=_rv(row, "created_at") or "",
            updated_at=_rv(row, "updated_at") or "",
        )

    # ------------------------------------------------ Association / ReviewDecision ------------------------------------------------ #
    def upsert_association(self, a: Association) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO association(
                  association_id, frame_ref_id, subject_annotation_id, relation, object_annotation_id,
                  ioa_score, threshold, passed, status, revision
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(association_id) DO UPDATE SET
                  subject_annotation_id=excluded.subject_annotation_id, relation=excluded.relation,
                  object_annotation_id=excluded.object_annotation_id, ioa_score=excluded.ioa_score,
                  threshold=excluded.threshold, passed=excluded.passed, status=excluded.status,
                  revision=excluded.revision
                """,
                (a.association_id, a.frame_ref_id, a.subject_annotation_id, a.relation, a.object_annotation_id,
                 a.ioa_score, a.threshold, 1 if a.passed else 0, a.status, a.revision),
            )

    def upsert_review_decision(self, d: ReviewDecision) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO review_decision(
                  decision_id, frame_ref_id, decision, adopting_source, note, decided_at, decided_by
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(decision_id) DO UPDATE SET
                  decision=excluded.decision, adopting_source=excluded.adopting_source,
                  note=excluded.note, decided_at=excluded.decided_at, decided_by=excluded.decided_by
                """,
                (d.decision_id, d.frame_ref_id, d.decision, d.adopting_source, d.note, d.decided_at or _now(), d.decided_by),
            )

    def get_review_decision(self, frame_ref_id: str) -> Optional[ReviewDecision]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM review_decision WHERE frame_ref_id=? ORDER BY decided_at DESC LIMIT 1",
                (frame_ref_id,),
            ).fetchone()
        if row is None:
            return None
        return ReviewDecision(
            decision_id=row["decision_id"],
            frame_ref_id=row["frame_ref_id"],
            decision=row["decision"] or "pending",
            adopting_source=row["adopting_source"] or "predicted",
            note=row["note"] or "",
            decided_at=row["decided_at"] or "",
            decided_by=row["decided_by"] or "local",
        )

    def review_decisions_by_media(self) -> list[tuple[str, int, str]]:
        """一次取出全部 (media_id, frame_index, decision)，供队列状态批量渲染。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.media_id AS media_id, f.frame_index AS frame_index, d.decision AS decision "
                "FROM review_decision d JOIN frame_ref f ON f.frame_ref_id = d.frame_ref_id"
            ).fetchall()
        return [(row["media_id"], int(row["frame_index"]), row["decision"] or "pending") for row in rows]

    def frame_refs_for_media(self, media_id: str) -> list[FrameRef]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM frame_ref WHERE media_id=? ORDER BY frame_index",
                (media_id,),
            ).fetchall()
        return [self._row_to_frame_ref(row) for row in rows]

    def media_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT media_id FROM media WHERE media_id IS NOT NULL AND media_id<>''"
            ).fetchall()
        return [row["media_id"] for row in rows]

    def media_ids_by_key(self) -> dict[str, str]:
        """{media_key: media_id}，用于在打开项目时沿用既有稳定身份。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT media_key, media_id FROM media "
                "WHERE media_id IS NOT NULL AND media_id<>''"
            ).fetchall()
        return {row["media_key"]: row["media_id"] for row in rows}

    def media_id_for_key(self, media_key: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT media_id FROM media WHERE media_key=?", (media_key,)
            ).fetchone()
        if row is None or not row["media_id"]:
            return ""
        return str(row["media_id"])

    def latest_annotations_for_media(self, media_id: str) -> dict[int, list[ObjectAnnotation]]:
        """按帧号返回该媒体的全部对象标注，用于恢复人工修订。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.frame_index AS frame_index, a.* FROM object_annotation a "
                "JOIN frame_ref f ON f.frame_ref_id = a.frame_ref_id WHERE f.media_id=?",
                (media_id,),
            ).fetchall()
        grouped: dict[int, list[ObjectAnnotation]] = {}
        for row in rows:
            grouped.setdefault(int(row["frame_index"]), []).append(self._row_to_annotation(row))
        return grouped

    # ------------------------------------------------ Run / Artifact / Snapshot ------------------------------------------------ #
    def insert_run(self, run: Run) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO run(run_id, project_id, workflow_snapshot_id, media_id, frame_ref_id, batch_id, status, source, started_at, finished_at, meta)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (run.run_id, run.project_id or self.project_id, run.workflow_snapshot_id, run.media_id,
                 run.frame_ref_id, run.batch_id, run.status, run.source, run.started_at or _now(), run.finished_at,
                 json.dumps(run.meta, ensure_ascii=False)),
            )

    def update_run_status(self, run_id: str, status: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE run SET status=?, finished_at=? WHERE run_id=?", (status, _now(), run_id))

    def runs_for_media(self, media_id: str) -> list[Run]:
        """按时间倒序返回该媒体的运行记录，供“这次导出对应哪次运行”追溯。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM run WHERE media_id=? ORDER BY started_at DESC, rowid DESC",
                (media_id,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def run_batches(self) -> list[tuple[str, int]]:
        """返回 (batch_id, 运行条数)，供任务页展示历史批次。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT batch_id, COUNT(*) AS n FROM run "
                "WHERE batch_id IS NOT NULL AND batch_id<>'' GROUP BY batch_id "
                "ORDER BY MAX(started_at) DESC"
            ).fetchall()
        return [(row["batch_id"], int(row["n"])) for row in rows]

    @staticmethod
    def _row_to_run(row) -> Run:
        meta = _rv(row, "meta")
        try:
            parsed = json.loads(meta) if meta else {}
        except (TypeError, ValueError):
            parsed = {}
        return Run(
            run_id=row["run_id"],
            project_id=_rv(row, "project_id") or "",
            workflow_snapshot_id=_rv(row, "workflow_snapshot_id") or "",
            media_id=_rv(row, "media_id") or "",
            frame_ref_id=_rv(row, "frame_ref_id") or "",
            batch_id=_rv(row, "batch_id") or "",
            status=_rv(row, "status") or "running",
            source=_rv(row, "source") or "manual",
            started_at=_rv(row, "started_at") or "",
            finished_at=_rv(row, "finished_at") or "",
            meta=parsed if isinstance(parsed, dict) else {},
        )

    def insert_artifact(self, art: Artifact) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifact(artifact_id, run_id, frame_ref_id, annotation_id, kind, rel_path, file_path, offset_x, offset_y, crop_x1, crop_y1, crop_x2, crop_y2, meta, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (art.artifact_id, art.run_id, art.frame_ref_id, art.annotation_id, art.kind, art.rel_path,
                 art.file_path, art.offset_x, art.offset_y, *(_box4(art.crop)),
                 json.dumps(art.meta, ensure_ascii=False), art.created_at or _now()),
            )

    def insert_workflow_snapshot(self, s: WorkflowSnapshot) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO workflow_snapshot(snapshot_id, workflow_id, workflow_config_json, models_json, created_at)
                VALUES(?,?,?,?,?)
                """,
                (s.snapshot_id, s.workflow_id, s.workflow_config_json, s.models_json, s.created_at or _now()),
            )


def _box4(box) -> tuple:
    if box is None:
        return (None, None, None, None)
    return tuple(box)


def _box_from_row(row, prefix: str):
    x1 = _rv(row, f"{prefix}_x1")
    if x1 is None:
        return None
    return (float(row[f"{prefix}_x1"]), float(row[f"{prefix}_y1"]), float(row[f"{prefix}_x2"]), float(row[f"{prefix}_y2"]))


def _rv(row, key: str, default=None):
    """sqlite3.Row 安全取值（缺列返回默认值）。"""
    try:
        value = row[key]
        return default if value is None else value
    except (KeyError, IndexError, TypeError):
        return default
