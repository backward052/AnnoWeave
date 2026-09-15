"""Generic geometry helpers and association node.

This module deliberately contains no product-specific class names, model names,
thresholds, or routing rules. Workflows supply those values as configuration.
"""

from __future__ import annotations

from ..inference.datatypes import Detection
from .node import Node, register_node
from .packet import Association, Packet


def intersection_over_area(container: Detection, candidate: Detection) -> float:
    """Return intersection area divided by ``candidate`` area."""
    ix1 = max(container.x1, candidate.x1)
    iy1 = max(container.y1, candidate.y1)
    ix2 = min(container.x2, candidate.x2)
    iy2 = min(container.y2, candidate.y2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = max(0.0, candidate.x2 - candidate.x1) * max(0.0, candidate.y2 - candidate.y1)
    return intersection / area if area > 0 else 0.0


def expand_detection(det: Detection, image_width: float, image_height: float, ratio: float = 0.1) -> Detection:
    """Expand a box on every side and clamp it to the image."""
    delta_x = (det.x2 - det.x1) * ratio
    delta_y = (det.y2 - det.y1) * ratio
    return Detection(
        max(0.0, det.x1 - delta_x),
        max(0.0, det.y1 - delta_y),
        min(image_width, det.x2 + delta_x),
        min(image_height, det.y2 + delta_y),
        det.label,
        det.score,
    )


@register_node("generic_associate")
class GenericAssociateNode(Node):
    """Associate two detection layers and mark matching subjects for cropping."""

    Meta = {
        "type_name": "generic_associate",
        "display_name": "空间关联",
        "description": "按类别及 IoA、IoU 或中心点规则关联两个检测结果，命中的主体可进入裁剪节点",
        "param_schema": {
            "left_key": {"label": "主体结果键", "type": "str", "default": "subject"},
            "right_key": {"label": "候选结果键", "type": "str", "default": "candidate"},
            "left_classes": {"label": "主体类别（逗号分隔，可空）", "type": "str", "default": ""},
            "right_classes": {"label": "候选类别（逗号分隔，可空）", "type": "str", "default": ""},
            "metric": {"label": "关联方式 ioa_right/iou/ioa_left/center_inside", "type": "str", "default": "ioa_right"},
            "threshold": {"label": "阈值", "type": "float", "default": 0.5},
            "expand_ratio": {"label": "主体扩边比例", "type": "float", "default": 0.0},
            "min_subject_area": {"label": "主体最小面积(px²)", "type": "int", "default": 0},
            "min_candidate_score": {"label": "候选最低分", "type": "float", "default": 0.0},
        },
        "tags": ["logic", "geometry"],
        "category": "关联与裁剪",
    }

    @staticmethod
    def _iou(left: Detection, right: Detection) -> float:
        ix1, iy1 = max(left.x1, right.x1), max(left.y1, right.y1)
        ix2, iy2 = min(left.x2, right.x2), min(left.y2, right.y2)
        intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        union = left.area + right.area - intersection
        return intersection / union if union > 0 else 0.0

    def _score(self, metric: str, subject: Detection, candidate: Detection) -> float:
        if metric == "iou":
            return self._iou(subject, candidate)
        if metric == "ioa_left":
            return intersection_over_area(candidate, subject)
        if metric == "center_inside":
            center_x = (candidate.x1 + candidate.x2) / 2
            center_y = (candidate.y1 + candidate.y2) / 2
            return float(subject.x1 <= center_x <= subject.x2 and subject.y1 <= center_y <= subject.y2)
        return intersection_over_area(subject, candidate)

    def run(self, packet: Packet) -> Packet:
        left_key = str(self.get("left_key", "subject"))
        right_key = str(self.get("right_key", "candidate"))
        left = packet.full_results.get(left_key)
        right = packet.full_results.get(right_key)
        if left is None or right is None or packet.frame is None:
            return packet
        left_classes = {value.strip() for value in str(self.get("left_classes", "")).split(",") if value.strip()}
        right_classes = {value.strip() for value in str(self.get("right_classes", "")).split(",") if value.strip()}
        subjects = [item for item in left.detections if not left_classes or item.label in left_classes]
        candidates = [item for item in right.detections if not right_classes or item.label in right_classes]
        minimum_score = float(self.get("min_candidate_score", 0.0) or 0.0)
        candidates = [item for item in candidates if item.score >= minimum_score]
        metric = str(self.get("metric", "ioa_right"))
        threshold = float(self.get("threshold", 0.5))
        expand_ratio = float(self.get("expand_ratio", 0.0) or 0.0)
        minimum_area = float(self.get("min_subject_area", 0) or 0)
        image_height, image_width = packet.frame.image.shape[:2]
        associations: list[Association] = []
        for subject in subjects:
            if minimum_area and subject.area < minimum_area:
                associations.append(Association(subject=subject, rejections=["主体面积小于最小面积"]))
                continue
            probe = expand_detection(subject, image_width, image_height, expand_ratio) if expand_ratio else subject
            details = []
            matches = []
            for candidate in candidates:
                score = self._score(metric, probe, candidate)
                passed = score >= threshold
                details.append((candidate.label, score, threshold, passed, candidate))
                if passed:
                    matches.append(candidate)
            reason = [] if matches else ["没有候选框达到关联阈值"]
            associations.append(Association(
                subject=subject,
                matched_candidates=matches,
                crop_hit=bool(matches),
                route="all" if matches else "",
                candidates=details,
                rejections=reason,
            ))
        packet.subjects = subjects
        packet.candidates = candidates
        packet.associations = associations
        return packet
