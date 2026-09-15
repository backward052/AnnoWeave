"""推理运行服务（M2）：不可变运行快照 + run_id 生命周期。

- 每次运行创建 WorkflowSnapshot（工作流配置 + 模型清单快照）与 Run。
- 结果按 run_id / frame_ref_id 归属，便于筛选迟到结果、区分预测版本。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Iterable, Optional

from ..domain.entities import Run, WorkflowSnapshot


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _to_dict(item) -> dict:
    if item is None:
        return {}
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if isinstance(item, dict):
        return item
    return {}


class InferenceRunService:
    def __init__(self, repository):
        self.repo = repository

    def snapshot(self, workflow_config, model_configs: Iterable) -> WorkflowSnapshot:
        snapshot = WorkflowSnapshot(
            snapshot_id=uuid.uuid4().hex,
            workflow_id=str(getattr(workflow_config, "workflow_id", "")),
            workflow_config_json=json.dumps(_to_dict(workflow_config), ensure_ascii=False),
            models_json=json.dumps([_to_dict(m) for m in (model_configs or [])], ensure_ascii=False),
            created_at=_now(),
        )
        self.repo.insert_workflow_snapshot(snapshot)
        return snapshot

    def start_run(
        self,
        workflow_config,
        model_configs: Iterable,
        media_id: str = "",
        frame_ref_id: str = "",
        source: str = "manual",
        batch_id: str = "",
        meta: Optional[dict] = None,
        run_id: str = "",
    ) -> Run:
        snapshot = self.snapshot(workflow_config, model_configs)
        run = Run(
            run_id=run_id or uuid.uuid4().hex,
            workflow_snapshot_id=snapshot.snapshot_id,
            media_id=media_id,
            frame_ref_id=frame_ref_id,
            batch_id=batch_id,
            status="running",
            source=source,
            started_at=_now(),
            meta=meta or {},
        )
        self.repo.insert_run(run)
        return run

    def finish_run(self, run_id: str, status: str = "success") -> None:
        if status not in ("success", "cancelled", "partial_fail", "failed"):
            raise ValueError(f"非法运行状态: {status}")
        self.repo.update_run_status(run_id, status)
