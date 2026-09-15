"""共享项目上下文 + 项目装载服务。

评审第 6/9 节要求所有页面共用同一项目上下文与素材身份：
项目名、源目录、仓储、复核存储、素材稳定 ID、活动工作流与模型库。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import paths
from ..domain.entities import Media
from ..inference.config import load_model_library
from ..models import MediaItem, ScanResult
from ..repository.repo import ReviewRepository
from ..services.annotations import AnnotationStore
from ..services.inference_run import InferenceRunService
from ..services.save import SaveService
from ..store import ReviewStore
from ..workflow.templates import starter_workflow_config
from ..workflow.workflow import WorkflowConfig, load_workflow_catalog


def app_data_root() -> Path:
    """Kept for callers that only need the root; follows ``ANNOWEAVE_CONFIG_DIR``."""
    return paths.data_path()


def project_database_path(source_root: str | Path) -> Path:
    digest = hashlib.sha256(str(source_root).casefold().encode("utf-8")).hexdigest()[:16]
    return app_data_root() / "projects" / digest / "review.db"


@dataclass
class ProjectContext:
    """一个打开项目的全部共享状态。UI 各页只读它，不各自扫描磁盘。"""

    source_root: str
    store: ReviewStore
    repository: ReviewRepository
    save_service: SaveService = None
    run_service: InferenceRunService = None
    annotation_store: object = None
    media: dict[str, Media] = field(default_factory=dict)
    workflow: Optional[WorkflowConfig] = None
    model_library: list = field(default_factory=list)
    media_items: list[MediaItem] = field(default_factory=list)

    def __post_init__(self):
        if self.save_service is None:
            self.save_service = SaveService(self.repository)
        if self.run_service is None:
            self.run_service = InferenceRunService(self.repository)
        if self.annotation_store is None:
            self.annotation_store = AnnotationStore(self.repository)
        if not self.model_library:
            self.model_library = load_model_library()
        if self.workflow is None:
            catalog = load_workflow_catalog()
            self.workflow = catalog[0] if catalog else starter_workflow_config()

    def media_id_for(self, media_key: str) -> str:
        item = self.media.get(media_key)
        return item.media_id if item and item.media_id else media_key

    def close(self):
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.repository.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def closed(self) -> bool:
        return bool(getattr(self.repository, "closed", False))


def open_project(source_root: str | Path) -> ProjectContext:
    """打开（或创建）一个项目：复核存储 + 领域仓储共用同一个数据库文件。"""
    source = Path(source_root).resolve()
    database = project_database_path(source)
    store = ReviewStore(database, source)
    repository = ReviewRepository(
        database, project_id=None, project_name=source.name, source_root=str(source)
    )
    return ProjectContext(source_root=str(source), store=store, repository=repository)


def scan_into_project(context: ProjectContext, scan_result: ScanResult) -> dict[str, Media]:
    """把扫描结果同步进项目：旧表沿用现有语义，新表补稳定身份与元数据。

    稳定身份必须**沿用**已落库的 media_id：重新生成会让“重启后找回结果”失效
    （评审第 9 节：路径可重定位，身份保持稳定）。
    """
    context.store.sync_scan(scan_result.items)
    media: dict[str, Media] = {}
    for item in scan_result.items:
        record = to_domain_media(item)
        existing = context.repository.media_id_for_key(item.media_key)
        record.media_id = existing or uuid.uuid4().hex
        media[item.media_key] = record
    for record in media.values():
        context.repository.upsert_media(record)
    context.media = media
    context.media_items = list(scan_result.items)
    return media


def to_domain_media(item: MediaItem) -> Media:
    """旧 MediaItem -> 领域 Media，保留 media_key 兼容键。"""
    return Media(
        media_id="",
        media_key=item.media_key,
        relative_path=item.relative_path,
        absolute_path=item.absolute_path,
        parent_relative_path=item.parent_relative_path,
        filename=item.filename,
        stem=item.stem,
        extension=item.extension,
        media_type=item.media_type,
        pair_key=item.pair_key,
        file_size=item.file_size,
        modified_ns=item.modified_ns,
        kind=item.media_type,
        source_path=item.absolute_path,
    )
