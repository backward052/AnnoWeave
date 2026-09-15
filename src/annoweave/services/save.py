"""保存服务（M2）：区分三种语义——自动保存草稿 / 确认复核 / 导出。

- 自动保存：保存可重新编辑的草稿与人工修订，不等于导出图片。
- 确认复核：改变审核状态（确认/驳回/待定，互斥），记录采用版本。
- 导出：按范围与模板生成新产物（委托 ExportService），并登记 Artifact。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..domain.entities import Artifact, ObjectAnnotation, ReviewDecision
from ..export.service import ExportResult, ExportService


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SaveService:
    def __init__(self, repository):
        self.repo = repository

    # ------------------------------- 自动保存（草稿/人工修订） ------------------------------- #
    def save_manual_revision(self, annotation: ObjectAnnotation) -> ObjectAnnotation:
        """保存人工修订：递增 revision，采用人工版本；不触发导出。"""
        annotation.manual_revision = int(annotation.manual_revision or 0) + 1
        annotation.manual_updated_at = _now()
        if annotation.manual is not None:
            annotation.adopted = annotation.manual
            annotation.adopted_label = annotation.manual_label or annotation.class_label
            annotation.adopted_source = "manual"
        self.repo.upsert_annotation(annotation)
        return annotation

    # ------------------------------- 确认复核（互斥结论） ------------------------------- #
    def set_review_decision(
        self,
        frame_ref_id: str,
        decision: str,
        adopting_source: str = "manual",
        note: str = "",
    ) -> ReviewDecision:
        if decision not in ("pending", "confirmed", "rejected"):
            raise ValueError(f"非法复核结论: {decision}")
        result = ReviewDecision(
            decision_id=f"dec_{frame_ref_id}",  # 每帧一条，天然互斥
            frame_ref_id=frame_ref_id,
            decision=decision,
            adopting_source=adopting_source,
            note=note,
            decided_at=_now(),
        )
        self.repo.upsert_review_decision(result)
        return result

    def get_review_decision(self, frame_ref_id: str) -> Optional[ReviewDecision]:
        return self.repo.get_review_decision(frame_ref_id)

    # ------------------------------- 导出（委托 ExportService + 登记 Artifact） ------------------------------- #
    def export_frame(
        self,
        output_dir: str | Path,
        template: str,
        frame_tag: str,
        frame_image,
        rendered,
        crops,
        run_id: str = "",
        frame_ref_id: str = "",
        selected_crop_index: Optional[int] = None,
        include_meta: bool = True,
    ) -> ExportResult:
        service = ExportService(output_dir, template)
        result = service.export_frame(
            frame_tag=frame_tag,
            frame_image=frame_image,
            rendered=rendered,
            crops=crops,
            selected_crop_index=selected_crop_index,
            include_meta=include_meta,
        )
        for path in result.paths:
            name = Path(path).name
            if name.endswith(".json"):
                kind = "result"
            elif "_crop_" in name:
                kind = "crop"
            else:
                kind = "full_image"
            self.repo.insert_artifact(
                Artifact(
                    artifact_id=uuid.uuid4().hex,
                    run_id=run_id,
                    frame_ref_id=frame_ref_id,
                    kind=kind,
                    file_path=path,
                    rel_path=name,
                )
            )
        return result
