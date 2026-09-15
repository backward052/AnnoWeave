"""人工修订落库服务（评审第 7、9 节）。

把画布上的对象保存为 `object_annotation` 的三版本行：
- `predicted_*`：模型预测原值（重跑后可更新，不覆盖人工）
- `manual_*`：人工修订框与改类，带 `manual_revision`
- `adopted_*`：最终采用版本（人工优先）

稳定身份：同一 (frame_ref, layer, class, 量化坐标) 得出同一个 `annotation_id`，
因此重复保存是幂等的，拖动后保存是更新而不是新增一行。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..domain.entities import ObjectAnnotation
from ..inference.datatypes import Detection

#: 坐标量化精度（像素）。用于稳定身份，避免浮点抖动产生新行。
QUANTUM = 0.5

PERSON_LABELS = frozenset({"person", "head"})


def layer_for(label: str) -> str:
    return "person" if label in PERSON_LABELS else "behavior"


def quantize(value: float) -> int:
    return int(round(float(value) / QUANTUM))


def annotation_key(frame_ref_id: str, label: str, box: tuple[float, float, float, float]) -> str:
    """稳定的标注身份：帧 + 图层 + 类别 + 量化坐标。"""
    layer = layer_for(label)
    payload = "|".join(
        [frame_ref_id, layer, label, *(str(quantize(v)) for v in box)]
    )
    return "ann_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


@dataclass
class PersistedFrame:
    """一帧从数据库恢复出来的人工修订与结论。"""

    manual_objects: list[Detection] = field(default_factory=list)
    predictions: list[Detection] = field(default_factory=list)
    adopted_source: str = "predicted"

    @property
    def has_manual(self) -> bool:
        return bool(self.manual_objects)


def box_of(det: Detection) -> tuple[float, float, float, float]:
    return (det.x1, det.y1, det.x2, det.y2)


def box_distance(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """两个框的最大边偏移（像素）。用于判断“还是同一个对象”。"""
    return max(abs(a[i] - b[i]) for i in range(4))


def best_match(
    box: tuple[float, float, float, float],
    candidates: list[ObjectAnnotation],
    layer: str,
    tolerance: float,
    ignore_layer: bool = False,
):
    """在已有标注里找最近的同图层对象。

    单纯坐标量化无法覆盖拖动（框整体位移可能远超量化粒度），
    因此增量保存前先做一次空间匹配：容差内视为同一对象，就地更新而不是新增行。

    `ignore_layer=True` 时跨图层匹配，可识别人工跨类别修改操作
    仍然是同一个对象，必须就地更新（否则会删旧行建新行，丢掉关联与修订历史）。
    """
    best = None
    best_distance = None
    for candidate in candidates:
        if not ignore_layer and (candidate.layer or "") != layer:
            continue
        reference = candidate.manual or candidate.predicted
        if reference is None:
            continue
        distance = box_distance(box, reference)
        if distance > tolerance:
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = candidate, distance
    return best


class AnnotationStore:
    """在仓储之上做“画布对象 <-> 三版本标注”的转换与存取。"""

    def __init__(self, repository, match_tolerance: float = 4.0):
        self.repo = repository
        #: 判定“还是同一个对象”的最大边偏移（像素）
        self.match_tolerance = float(match_tolerance)

    # ----------------------------- 保存 ----------------------------- #
    def save_frame(
        self,
        frame_ref_id: str,
        manual_persons: list[Detection] | None,
        manual_behaviors: list[Detection] | None,
        predictions: list[Detection] | None = None,
        adopted_source: str = "manual",
    ) -> int:
        """保存一帧的人工修订，返回写入行数。

        `manual_*` 传 None 表示“本帧没有人工改动”，此时不写人工版本；
        预测版本仍会按需记录，便于“并排查看预测版/人工版”。
        """
        if not frame_ref_id:
            return 0
        manual_objects: list[Detection] = []
        if manual_persons is not None:
            manual_objects.extend(manual_persons)
        if manual_behaviors is not None:
            manual_objects.extend(manual_behaviors)

        existing = self.repo.annotations_for_frame(frame_ref_id)
        # 未被匹配到的旧人工行：本次画布上已经不存在，删除以免留下幽灵对象
        consumed: set[str] = set()

        written = 0
        for det in manual_objects:
            layer = layer_for(det.label)
            box = box_of(det)
            # 先按同图层找；找不到再跨图层找一次（改类会换图层，但仍是同一对象）
            match = best_match(box, existing, layer, self.match_tolerance)
            if match is None:
                match = best_match(
                    box, existing, layer, self.match_tolerance, ignore_layer=True
                )
            if match is not None:
                consumed.add(match.annotation_id)
                annotation_id = match.annotation_id
            else:
                annotation_id = annotation_key(frame_ref_id, det.label, box)
                # 极端情况下量化身份撞上未匹配的行：退回带后缀的唯一身份
                if any(row.annotation_id == annotation_id for row in existing):
                    annotation_id = f"{annotation_id}_{written}"
            prediction = self._nearest_prediction(box, predictions or [])
            annotation = self._build(
                annotation_id, frame_ref_id, det, match, prediction, adopted_source
            )
            self.repo.upsert_annotation(annotation)
            written += 1

        for row in existing:
            if row.annotation_id in consumed:
                continue
            # 画布上已不存在的旧人工对象：删除，避免留下幽灵框
            if row.manual is not None:
                self.repo.delete_annotation(row.annotation_id)
        return written

    def _nearest_prediction(self, box: tuple[float, float, float, float], predictions: list[Detection]):
        best = None
        best_distance = None
        for det in predictions:
            distance = box_distance(box, box_of(det))
            if distance > self.match_tolerance:
                continue
            if best_distance is None or distance < best_distance:
                best, best_distance = det, distance
        return best

    def _build(
        self,
        annotation_id: str,
        frame_ref_id: str,
        det: Detection,
        existing: ObjectAnnotation | None,
        prediction: Detection | None,
        adopted_source: str,
    ) -> ObjectAnnotation:
        annotation = existing or ObjectAnnotation(annotation_id=annotation_id, frame_ref_id=frame_ref_id)
        annotation.layer = layer_for(det.label)
        annotation.class_label = det.label
        # 预测版本：只在本次运行确实产生过同一对象的预测时更新
        if prediction is not None:
            annotation.predicted = box_of(prediction)
            annotation.predicted_conf = float(prediction.score)
        annotation.manual = box_of(det)
        annotation.manual_label = det.label
        annotation.manual_revision = int(annotation.manual_revision or 0) + 1
        from datetime import datetime

        annotation.manual_updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        annotation.adopted = annotation.manual
        annotation.adopted_label = annotation.manual_label
        annotation.adopted_source = adopted_source
        return annotation

    # ----------------------------- 恢复 ----------------------------- #
    def load_frame(self, media_id: str, frame_index: int) -> PersistedFrame:
        """按媒体与原始帧号恢复人工修订（需要调用方先取得 frame_ref）。"""
        frame_ref = self._frame_ref(media_id, frame_index)
        if frame_ref is None:
            return PersistedFrame()
        rows = self.repo.annotations_for_frame(frame_ref.frame_ref_id)
        return self._to_frame(rows)

    def load_media(self, media_id: str) -> dict[int, PersistedFrame]:
        """一次恢复整个媒体的人工修订，避免逐帧查询。

        返回 {frame_index: PersistedFrame}；仅包含有人工修订的帧。
        """
        grouped = self.repo.latest_annotations_for_media(media_id)
        result: dict[int, PersistedFrame] = {}
        for frame_index, rows in grouped.items():
            frame = self._to_frame(rows)
            if frame.has_manual or frame.predictions:
                result[int(frame_index)] = frame
        return result

    def _frame_ref(self, media_id: str, frame_index: int):
        frame_refs = getattr(self.repo, "frame_refs_for_media", None)
        if frame_refs is None:
            return None
        for ref in frame_refs(media_id):
            if int(ref.frame_index) == int(frame_index):
                return ref
        return None

    @staticmethod
    def _to_frame(rows: list[ObjectAnnotation]) -> PersistedFrame:
        frame = PersistedFrame()
        for row in rows:
            if row.manual is not None:
                box = row.manual
                frame.manual_objects.append(
                    Detection(box[0], box[1], box[2], box[3], row.manual_label or row.class_label, 1.0)
                )
            if row.predicted is not None:
                box = row.predicted
                frame.predictions.append(
                    Detection(box[0], box[1], box[2], box[3], row.class_label, row.predicted_conf)
                )
            if row.adopted_source:
                frame.adopted_source = row.adopted_source
        return frame

    def delete_annotation(self, annotation_id: str) -> None:
        self.repo.delete_annotation(annotation_id)
