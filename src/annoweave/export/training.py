"""把当前有效标注导出为可训练的 YOLO 检测或分类数据。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2


def _safe(value):
    return ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in str(value)) or 'data'


def _write_image(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode('.jpg', image)
    if not ok:
        raise OSError(f'无法编码图片: {path}')
    temp = path.with_suffix('.tmp.jpg')
    encoded.tofile(str(temp))
    os.replace(temp, path)


def _yolo_lines(detections, classes, width, height):
    lines = []
    for det in detections:
        if det.hidden or det.label not in classes or width <= 0 or height <= 0:
            continue
        x1, y1 = max(0., det.x1), max(0., det.y1)
        x2, y2 = min(float(width), det.x2), min(float(height), det.y2)
        if x2 <= x1 or y2 <= y1:
            continue
        lines.append(f'{classes.index(det.label)} {(x1+x2)/2/width:.6f} {(y1+y2)/2/height:.6f} '
                     f'{(x2-x1)/width:.6f} {(y2-y1)/height:.6f}')
    return lines


def export_training_frame(output_dir, frame_tag, frame_image, full_results, full_classes,
                          crops, selected_full=None, selected_crop=None,
                          save_full=True, save_crops=True, save_images=True, save_labels=True):
    root = Path(output_dir)
    tag = _safe(frame_tag)
    manifest = {'version': 1, 'frame': tag, 'full_models': {}, 'crops': []}
    height, width = frame_image.shape[:2]
    for key, result in full_results.items():
        if selected_full is not None and key not in selected_full:
            continue
        classes = list(full_classes.get(key, []))
        if not classes or not save_full:
            continue
        model_dir = root / 'full' / _safe(key)
        if save_images:
            _write_image(model_dir / 'images' / f'{tag}.jpg', frame_image)
        if save_labels:
            label = model_dir / 'labels' / f'{tag}.txt'
            label.parent.mkdir(parents=True, exist_ok=True)
            label.write_text('\n'.join(_yolo_lines(result.detections, classes, width, height)), encoding='utf-8')
            (model_dir / 'classes.txt').write_text('\n'.join(classes), encoding='utf-8')
        manifest['full_models'][key] = {'classes': classes, 'objects': len(result.detections)}

    if save_crops:
        for index, slot in enumerate(crops):
            crop_tag = f'{tag}_{_safe(slot.crop_id)}'
            item = {'crop_id': slot.crop_id, 'source_rect': list(slot.crop_rect or []), 'models': {}}
            ch, cw = slot.crop_image.shape[:2]
            for key, result in slot.results.items():
                if selected_crop is not None and key not in selected_crop:
                    continue
                classes = list(slot.model_classes.get(key, []))
                model_dir = root / 'crops' / _safe(slot.model_names.get(key, key))
                if result.task_type.value == 'detection':
                    if save_images:
                        _write_image(model_dir / 'images' / f'{crop_tag}.jpg', slot.crop_image)
                    if save_labels:
                        label = model_dir / 'labels' / f'{crop_tag}.txt'
                        label.parent.mkdir(parents=True, exist_ok=True)
                        label.write_text('\n'.join(_yolo_lines(result.detections, classes, cw, ch)), encoding='utf-8')
                        (model_dir / 'classes.txt').write_text('\n'.join(classes), encoding='utf-8')
                elif result.task_type.value == 'classification' and result.classification:
                    class_name = _safe(result.classification.label)
                    if save_images:
                        _write_image(model_dir / class_name / f'{crop_tag}.jpg', slot.crop_image)
                    if save_labels:
                        label_dir = model_dir / 'labels'
                        label_dir.mkdir(parents=True, exist_ok=True)
                        (label_dir / f'{crop_tag}.txt').write_text(result.classification.label, encoding='utf-8')
                        (model_dir / 'classes.txt').write_text('\n'.join(classes), encoding='utf-8')
                item['models'][key] = {'classes': classes, 'objects': len(result.detections),
                                       'classification': result.classification.label if result.classification else None}
            manifest['crops'].append(item)
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{tag}_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
