"""独立复核会话：JSON 标注存档，不保存图片或执行模型对象。"""
import hashlib
import json
import os
from dataclasses import asdict

from ..inference.config import inference_config_path
from ..inference.datatypes import Classification, Detection, ModelResult, TaskType


def result_dict(result):
    return {'task_type': result.task_type.value, 'detections': [asdict(d) for d in result.detections],
            'classification': asdict(result.classification) if result.classification else None}


def result_from(data):
    return ModelResult(TaskType(data['task_type']), [Detection(**d) for d in data.get('detections', [])],
                       Classification(**data['classification']) if data.get('classification') else None)


def session_path(media_id, frame):
    digest = hashlib.sha256(str(media_id).encode()).hexdigest()
    return inference_config_path().parent / 'sessions' / digest / f'{int(frame)}.json'


def save_state(key, state):
    if key is None:
        return
    payload = {'version': 2, 'media': key[0], 'frame': key[1], 'decision': state.decision,
               'selected_for_export': bool(getattr(state, 'selected_for_export', False)),
               'manual_persons': None if state.manual_persons is None else [asdict(d) for d in state.manual_persons],
               'manual_behaviors': None if state.manual_behaviors is None else [asdict(d) for d in state.manual_behaviors],
               'predictions': {k: result_dict(v) for k, v in state.prediction_results.items()},
               'crop_edits': state.crop_edits}
    path = session_path(*key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def load_state(key, state):
    path = session_path(*key)
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding='utf-8'))
    for field in ('manual_persons', 'manual_behaviors'):
        value = data.get(field)
        setattr(state, field, None if value is None else [Detection(**d) for d in value])
    state.prediction_results = {k: result_from(v) for k, v in data.get('predictions', {}).items()}
    state.crop_edits = data.get('crop_edits', {})
    state.decision = data.get('decision', 'pending')
    state.selected_for_export = bool(data.get('selected_for_export', False))
    state.edited = state.manual_persons is not None or state.manual_behaviors is not None or bool(state.crop_edits)
    return True


def load_export_selected(key) -> bool:
    """只读取保存集标记，避免项目数据库标注被 JSON 会话覆盖。"""
    path = session_path(*key)
    if not path.exists():
        return False
    try:
        return bool(json.loads(path.read_text(encoding='utf-8')).get('selected_for_export', False))
    except (OSError, ValueError, TypeError):
        return False


def crop_edit(slot):
    return {'rect': list(slot.crop_rect), 'results': {k: result_dict(v) for k, v in slot.results.items()}}


def update_crop_business(slot):
    """Normalize edited crop results without applying product-specific rules."""
    slot.results = {key: value for key, value in slot.results.items() if value is not None}


def apply_crop_edits(packet, edits):
    for slot in packet.crops:
        saved = edits.get(slot.crop_id)
        if not saved:
            continue
        old = saved['rect']
        changed = list(slot.crop_rect) != old
        slot.needs_review = changed
        height, width = slot.crop_image.shape[:2]
        for key, value in saved['results'].items():
            if key not in slot.results:
                continue
            result = result_from(value)
            if changed:
                valid = []
                for d in result.detections:
                    d.x1 += old[0] - slot.offset_x
                    d.x2 += old[0] - slot.offset_x
                    d.y1 += old[1] - slot.offset_y
                    d.y2 += old[1] - slot.offset_y
                    d.x1 = min(max(0., d.x1), float(width))
                    d.x2 = min(max(0., d.x2), float(width))
                    d.y1 = min(max(0., d.y1), float(height))
                    d.y2 = min(max(0., d.y2), float(height))
                    if d.x1 < d.x2 and d.y1 < d.y2:
                        valid.append(d)
                result.detections = valid
            slot.results[key] = result
        slot.edited = True
        update_crop_business(slot)
