"""领域模型实体（M1）。只含数据与纯逻辑，不依赖 Qt。

对齐《M1-领域模型与表结构》设计的字段与“同表三列 / FrameRef 惰性 / Project 轻量表”等决策。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Project:
    project_id: str
    name: str
    source_root: str = ""
    schema_version: int = 1
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Media:
    media_id: str
    media_key: str
    relative_path: str
    absolute_path: str
    parent_relative_path: str
    filename: str
    stem: str
    extension: str
    media_type: str  # image | video
    pair_key: str
    file_size: int
    modified_ns: int
    project_id: str = ""
    kind: str = ""  # image | video
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    frame_count: int = 0
    source_path: str = ""
    present: bool = True
    deleted: bool = False
    trash_path: str = ""

    @property
    def is_video(self) -> bool:
        return self.kind == "video" or self.media_type == "video"


@dataclass
class FrameRef:
    frame_ref_id: str
    media_id: str
    frame_index: int
    source_frame_index: int = 0
    timestamp_s: float = 0.0
    created_at: str = ""


@dataclass
class ObjectAnnotation:
    annotation_id: str
    frame_ref_id: str
    layer: str = ""
    class_label: str = ""
    object_id: str = ""
    predicted: Optional[tuple[float, float, float, float]] = None
    predicted_conf: float = 0.0
    predicted_run_id: str = ""
    manual: Optional[tuple[float, float, float, float]] = None
    manual_label: str = ""
    manual_revision: int = 0
    manual_updated_at: str = ""
    adopted: Optional[tuple[float, float, float, float]] = None
    adopted_label: str = ""
    adopted_source: str = ""  # predicted | manual
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Association:
    association_id: str
    frame_ref_id: str
    subject_annotation_id: str = ""
    relation: str = ""
    object_annotation_id: str = ""
    ioa_score: float = 0.0
    threshold: float = 0.0
    passed: bool = False
    status: str = "auto"  # auto | manually_added | manually_removed
    revision: int = 0


@dataclass
class ReviewDecision:
    decision_id: str
    frame_ref_id: str
    decision: str = "pending"  # pending | confirmed | rejected
    adopting_source: str = "predicted"
    note: str = ""
    decided_at: str = ""
    decided_by: str = "local"


@dataclass
class WorkflowSnapshot:
    snapshot_id: str
    workflow_id: str = ""
    workflow_config_json: str = "{}"
    models_json: str = "{}"
    created_at: str = ""


@dataclass
class Run:
    run_id: str
    project_id: str = ""
    workflow_snapshot_id: str = ""
    media_id: str = ""
    frame_ref_id: str = ""
    batch_id: str = ""
    status: str = "running"
    source: str = "manual"
    started_at: str = ""
    finished_at: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Artifact:
    artifact_id: str
    run_id: str = ""
    frame_ref_id: str = ""
    annotation_id: str = ""
    kind: str = ""
    rel_path: str = ""
    file_path: str = ""
    offset_x: int = 0
    offset_y: int = 0
    crop: Optional[tuple[float, float, float, float]] = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
