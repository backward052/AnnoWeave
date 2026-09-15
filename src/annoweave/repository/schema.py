"""SQLite 表结构与迁移辅助（M1）。

只包含 DDL 与“加列/建表”的幂等辅助，不含业务。
"""

from __future__ import annotations

# 基础表：与旧 store.py 的 _create_schema 保持一致，仓库也能在全新库上建，实现双轨自足。
BASE_TABLE_SQL = [
    """
    CREATE TABLE IF NOT EXISTS meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    """,
    """
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
    """,
    """
    CREATE TABLE IF NOT EXISTS labels (
      label_id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE COLLATE NOCASE,
      color TEXT NOT NULL DEFAULT '#4caf50',
      shortcut TEXT NOT NULL DEFAULT '',
      sort_order INTEGER NOT NULL DEFAULT 0,
      enabled INTEGER NOT NULL DEFAULT 1
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS media_labels (
      media_key TEXT NOT NULL,
      label_id INTEGER NOT NULL,
      assignment_source TEXT NOT NULL DEFAULT 'manual',
      assigned_at TEXT NOT NULL,
      PRIMARY KEY(media_key, label_id)
    );
    """,
    """
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
    """,
]

NEW_TABLE_SQL = [
    """
    CREATE TABLE IF NOT EXISTS project (
      project_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      source_root TEXT,
      schema_version INTEGER NOT NULL DEFAULT 1,
      meta TEXT,
      created_at TEXT,
      updated_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS frame_ref (
      frame_ref_id TEXT PRIMARY KEY,
      media_id TEXT NOT NULL,
      frame_index INTEGER NOT NULL,
      source_frame_index INTEGER,
      timestamp_s REAL,
      created_at TEXT,
      UNIQUE(media_id, frame_index)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS object_annotation (
      annotation_id TEXT PRIMARY KEY,
      frame_ref_id TEXT NOT NULL,
      object_id TEXT,
      layer TEXT,
      class_label TEXT,
      predicted_x1 REAL, predicted_y1 REAL, predicted_x2 REAL, predicted_y2 REAL,
      predicted_conf REAL,
      predicted_run_id TEXT,
      manual_x1 REAL, manual_y1 REAL, manual_x2 REAL, manual_y2 REAL,
      manual_label TEXT,
      manual_revision INTEGER DEFAULT 0,
      manual_updated_at TEXT,
      adopted_x1 REAL, adopted_y1 REAL, adopted_x2 REAL, adopted_y2 REAL,
      adopted_label TEXT,
      adopted_source TEXT,
      created_at TEXT, updated_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS association (
      association_id TEXT PRIMARY KEY,
      frame_ref_id TEXT NOT NULL,
      subject_annotation_id TEXT,
      relation TEXT,
      object_annotation_id TEXT,
      ioa_score REAL,
      threshold REAL,
      passed INTEGER,
      status TEXT,
      revision INTEGER DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS review_decision (
      decision_id TEXT PRIMARY KEY,
      frame_ref_id TEXT NOT NULL,
      decision TEXT,
      adopting_source TEXT,
      note TEXT,
      decided_at TEXT,
      decided_by TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_snapshot (
      snapshot_id TEXT PRIMARY KEY,
      workflow_id TEXT,
      workflow_config_json TEXT,
      models_json TEXT,
      created_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS run (
      run_id TEXT PRIMARY KEY,
      project_id TEXT,
      workflow_snapshot_id TEXT,
      media_id TEXT,
      frame_ref_id TEXT,
      batch_id TEXT,
      status TEXT,
      source TEXT,
      started_at TEXT, finished_at TEXT,
      meta TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact (
      artifact_id TEXT PRIMARY KEY,
      run_id TEXT,
      frame_ref_id TEXT,
      annotation_id TEXT,
      kind TEXT,
      rel_path TEXT,
      file_path TEXT,
      offset_x INTEGER, offset_y INTEGER,
      crop_x1 REAL, crop_y1 REAL, crop_x2 REAL, crop_y2 REAL,
      meta TEXT,
      created_at TEXT
    );
    """,
]

NEW_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_media_media_id ON media(media_id);",
    "CREATE INDEX IF NOT EXISTS idx_media_project ON media(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_obj_annot_frame ON object_annotation(frame_ref_id);",
    "CREATE INDEX IF NOT EXISTS idx_assoc_frame ON association(frame_ref_id);",
    "CREATE INDEX IF NOT EXISTS idx_review_frame ON review_decision(frame_ref_id);",
    "CREATE INDEX IF NOT EXISTS idx_run_frame ON run(frame_ref_id);",
    "CREATE INDEX IF NOT EXISTS idx_run_batch ON run(batch_id);",
    "CREATE INDEX IF NOT EXISTS idx_artifact_frame ON artifact(frame_ref_id);",
    "CREATE INDEX IF NOT EXISTS idx_media_pair ON media(pair_key);",
]

# media 表新增列（旧表不动，只补充）
MEDIA_NEW_COLUMNS = [
    ("media_id", "text"),
    ("project_id", "text"),
    ("kind", "text"),
    ("width", "integer"),
    ("height", "integer"),
    ("duration_s", "real"),
    ("frame_count", "integer"),
    ("source_path", "text"),
]
