from __future__ import annotations

from ..inference.datatypes import Detection, Keypoint, TaskType
from .association import expand_detection
from .node import Node, NodeError, register_node
from .packet import Association, CropSlot, Packet

_COLORS = ((58, 183, 255), (86, 214, 151), (255, 181, 71), (174, 126, 255), (255, 104, 139))


def _layer_color(index: int):
    return _COLORS[index % len(_COLORS)]

#: 节点在“模块库”里的分组（新手按业务顺序找模块，而不是按实现找）
CAT_SOURCE = "输入"
CAT_MODEL = "模型推理"
CAT_POSE = "姿态与关键点"
CAT_GEOMETRY = "关联与裁剪"
CAT_RULE = "规则判定"
CAT_OUTPUT = "结果输出"


class _ModelResolver:
    """按名称从 packet.meta['models'] 解析模型实例，并按名称缓存。

    packet.meta['models'] 形如 {name: ModelConfig | 模型实例}。
    支持 params['model'] 为 {name: str}（从模型库选）、完整 ModelConfig dict、
    或直接注入 '_model_instance'（测试用）。
    """

    def __init__(self, params: dict):
        self.params = params
        self._cache: dict[str, object] = {}

    def resolve(self, packet: Packet):
        if "_model_instance" in self.params:
            return self.params["_model_instance"]
        registry = packet.meta.get("models", {}) or {}
        raw = self.params.get("model") or {}
        name = ""
        if isinstance(raw, dict):
            name = str(raw.get("name", ""))
            if not name and len(raw) > 1:
                # 完整 ModelConfig dict
                from ..inference.config import ModelConfig
                from ..inference.models import create_model

                return create_model(ModelConfig.from_dict(raw))
        elif isinstance(raw, str):
            name = raw
        if not name:
            raise NodeError("推理节点未配置模型")
        entry = registry.get(name)
        if entry is None:
            raise NodeError(f"模型库中不存在模型 {name!r}")
        if hasattr(entry, "predict"):
            return entry
        if name not in self._cache:
            from ..inference.config import ModelConfig
            from ..inference.models import create_model

            mc = entry if isinstance(entry, ModelConfig) else ModelConfig.from_dict(entry)
            self._cache[name] = create_model(mc)
        return self._cache[name]


@register_node("source_video")
class SourceVideoNode(Node):
    Meta = {
        "type_name": "source_video",
        "display_name": "视频源",
        "description": "读取当前采样帧到 Packet.frame",
        "param_schema": {"fps": {"label": "采样帧率", "type": "float", "default": 1.0}},
        "tags": ["source"],
        "category": CAT_SOURCE,
    }

    def run(self, packet: Packet) -> Packet:
        # 帧已在工作区准备好（packet.frame）；此节点仅占位/可在未来自行抽帧。
        return packet


@register_node("inference")
class InferenceNode(Node):
    Meta = {
        "type_name": "inference",
        "display_name": "全图推理",
        "description": "在全图上跑一个模型，写入 full_results[model_key]；姿态模型会附上关键点",
        "param_schema": {
            "model": {"label": "模型", "type": "model", "default": {}},
            "model_key": {"label": "结果键（留空用节点名）", "type": "str", "default": ""},
        },
        "tags": ["inference"],
        "category": CAT_MODEL,
    }

    def __init__(self, params=None, name=""):
        super().__init__(params, name)
        self._resolver = _ModelResolver(self.params)

    def run(self, packet: Packet) -> Packet:
        if packet.frame is None:
            return packet
        if packet.meta.get("skip_full_inference"):
            return packet
        model = self._resolver.resolve(packet)
        result = model.predict(packet.frame.image)
        key = self.get("model_key", self.name)
        for index, det in enumerate(result.detections):
            import hashlib
            det.model_key = str(key)
            det.object_id = hashlib.sha256(f'{packet.media_identity}:{packet.frame.index}:{key}:{index}'.encode()).hexdigest()[:24]
        packet.full_results[key] = result
        packet.meta["rendered_needs"] = True
        return packet


@register_node("pose_inference")
class PoseInferenceNode(InferenceNode):
    """全图姿态推理：一次拿到"人框 + 每个人的人体关键点"。

    与「全图推理」是同一个节点，只是换个名字，让新手一眼知道
    "想要关键点就把这个模块拖进来，然后选一个姿态模型"。
    """

    Meta = {
        "type_name": "pose_inference",
        "display_name": "整图人体姿态",
        "description": "在全图上跑姿态模型（YOLOv8-pose 等），输出人框 + 每个人的人体关键点",
        "param_schema": {"model": {"label": "姿态模型", "type": "model", "default": {}}},
        "tags": ["inference", "pose"],
        "category": CAT_POSE,
    }



@register_node("crop")
class CropNode(Node):
    Meta = {
        "type_name": "crop",
        "display_name": "裁剪",
        "description": (
            "把主体裁出来：既可只裁「关联命中」的主体，也可以裁「全部」主体；"
            "主体自带的关键点会随裁剪一起带走"
        ),
        "param_schema": {
            "subject_mode": {"label": "裁谁 associated(仅关联命中)/all(全部主体)", "type": "str", "default": "associated"},
            "subject_source": {"label": "主体图层（all 模式用）", "type": "str", "default": "subject"},
            "expand_px": {"label": "扩边像素", "type": "int", "default": 0},
            "expand_ratio": {"label": "扩边比例", "type": "float", "default": 0.0},
            "min_area": {"label": "裁剪最小面积", "type": "int", "default": 7200},
        },
        "tags": ["geometry"],
        "category": CAT_GEOMETRY,
    }

    def _subjects(self, packet: Packet) -> list:
        """返回要裁剪的主体列表（元素是 Association）。"""
        mode = str(self.get("subject_mode", "associated") or "associated").strip().lower()
        if mode in ("all", "全部", "every"):
            source = str(self.get("subject_source", "subject"))
            detections, label = _select_layer(packet, source)
            if detections:
                # 没有关联步骤时也要能裁：为每个主体合成一条"全部裁剪"的关联，
                # 这样检查器仍然能解释"为什么裁了它"。
                packet.associations = [
                    Association(det, matched_candidates=[], crop_hit=True, route="all",
                                candidates=[], rejections=[])
                    for det in detections
                ]
                packet.meta.setdefault("filter_notes", []).append(
                    f"裁剪：全部主体模式，共 {len(detections)} 个（来自 {label}）"
                )
        return list(packet.associations)

    def run(self, packet: Packet) -> Packet:
        if packet.frame is None:
            return packet
        from ..inference.crop import crop_region  # noqa: PLC0415

        image_h, image_w = packet.frame.image.shape[:2]
        expand_ratio = float(self.get("expand_ratio", 0.0))
        expand_px = int(self.get("expand_px", 0))
        min_area = float(self.get("min_area", 7200))
        crops: list[CropSlot] = []
        for index, assoc in enumerate(self._subjects(packet)):
            if not assoc.should_crop:
                continue
            # 扩边只做一次：先按比例扩，再按像素扩（都裁剪到帧内）
            exp = expand_detection(assoc.person, image_w, image_h, expand_ratio)
            if expand_px:
                exp = Detection(
                    max(0.0, exp.x1 - expand_px),
                    max(0.0, exp.y1 - expand_px),
                    min(float(image_w), exp.x2 + expand_px),
                    min(float(image_h), exp.y2 + expand_px),
                    exp.label,
                    exp.score,
                )
            # crop_region 不再二次扩边（expand_px/ratio 传 0）
            crop = crop_region(
                packet.frame.image,
                exp,
                expand_px=0,
                expand_ratio=0.0,
                min_size=0,
                clip_to_frame=True,
                source_frame_index=packet.frame.index,
            )
            if crop is None:
                continue
            area = crop.width * crop.height
            if min_area > 0 and area <= min_area:
                assoc.rejections.append(f"关联已通过，但实际裁剪面积 {area} 未超过裁剪节点最小面积 {min_area:.0f}；可调整裁剪节点的 min_area")
                continue
            # 主体自带的关键点（整图姿态模型的结果）跟着裁剪一起带走，
            # 这样"裁剪出来的人"和"他的骨架"永远成对出现。
            subject_keypoints = [
                Keypoint(point.x, point.y, point.score, point.name)
                for point in getattr(assoc.person, "keypoints", []) or []
            ]
            crops.append(
                CropSlot(
                    crop_id=f"{self.name}:{assoc.person.object_id}",
                    person_bbox=exp,
                    source_bbox=assoc.person,
                    crop_image=crop.image,
                    route=assoc.route or "all",
                    offset_x=crop.offset_x,
                    offset_y=crop.offset_y,
                    crop_rect=(
                        float(crop.offset_x),
                        float(crop.offset_y),
                        float(crop.offset_x + crop.width),
                        float(crop.offset_y + crop.height),
                    ),
                    keypoints=subject_keypoints,
                    keypoint_format=getattr(assoc.person, "keypoint_format", "") or "",
                )
            )
        packet.crops = crops
        return packet


@register_node("crop_inference")
class CropInferenceNode(Node):
    Meta = {
        "type_name": "crop_inference",
        "display_name": "裁剪级联推理",
        "description": "对路由匹配的裁剪图跑下游模型（检测→回填框；分类→回填结论；姿态→回填关键点）",
        "param_schema": {"route": {"label": "路由", "type": "str", "default": "all"}, "model": {"label": "模型", "type": "model", "default": {}}},
        "tags": ["inference"],
        "category": CAT_MODEL,
    }

    def __init__(self, params=None, name=""):
        super().__init__(params, name)
        self._resolver = _ModelResolver(self.params)

    def _matches_route(self, route: str, slot: CropSlot) -> bool:
        """路由决定这个裁剪要不要跑本节点。

        `all` 或 `crop` 处理所有裁剪；其他值只处理带相同路由标记的裁剪。
        """
        if route in ("", "all", "crop"):
            return True
        return route == slot.route or route in {str(value) for value in (slot.routes or {}).values()}

    def run(self, packet: Packet) -> Packet:
        route = str(self.get("route", "all"))
        model = None
        for slot in packet.crops:
            if not self._matches_route(route, slot):
                continue
            if model is None:
                model = self._resolver.resolve(packet)
            raw_model = self.params.get('model', {})
            key = self.name or route
            task = getattr(model, "task_type", TaskType.DETECTION)
            if task == TaskType.POSE:
                self._run_pose(slot, model, key, route, raw_model)
                continue
            result = model.predict(slot.crop_image)
            import copy
            slot.results[key] = copy.deepcopy(result)
            slot.predictions[key] = copy.deepcopy(result)
            slot.model_classes[key] = list(getattr(getattr(model, 'config', None), 'classes', [])) or list(dict.fromkeys(d.label for d in result.detections))
            slot.model_names[key] = raw_model.get('name', key) if isinstance(raw_model, dict) else str(raw_model)
            slot.routes[key] = route
        return packet

    def _run_pose(self, slot: CropSlot, model, key: str, route: str, raw_model) -> None:
        """在裁剪图上跑自顶向下姿态模型，并把关键点平移回原图坐标。"""
        import copy

        subject = slot.source_bbox or slot.person_bbox
        local_box = (
            float(subject.x1 - slot.offset_x),
            float(subject.y1 - slot.offset_y),
            float(subject.x2 - slot.offset_x),
            float(subject.y2 - slot.offset_y),
        )
        result = model.predict(slot.crop_image, boxes=[local_box])
        slot.results[key] = copy.deepcopy(result)
        slot.predictions[key] = copy.deepcopy(result)
        slot.model_classes[key] = list(getattr(getattr(model, 'config', None), 'classes', [])) or ["person"]
        slot.model_names[key] = raw_model.get('name', key) if isinstance(raw_model, dict) else str(raw_model)
        slot.routes[key] = route
        if not result.detections:
            return
        points = list(result.detections[0].keypoints or [])
        slot.keypoints = [
            Keypoint(point.x + slot.offset_x, point.y + slot.offset_y, point.score, point.name)
            for point in points
        ]
        slot.keypoint_format = getattr(result.detections[0], "keypoint_format", "") or ""


@register_node("draw")
class DrawNode(Node):
    Meta = {
        "type_name": "draw",
        "display_name": "绘制",
        "description": "把多图层框叠加到大图，写回 packet.meta['rendered']；可叠加人体骨架",
        "param_schema": {
            "show_keypoints": {"label": "绘制人体骨架", "type": "bool", "default": True},
            "keypoint_score": {"label": "关键点最低分", "type": "float", "default": 0.3},
            "keypoint_radius": {"label": "关键点半径", "type": "int", "default": 3},
            "keypoint_line_width": {"label": "骨架线宽", "type": "int", "default": 2},
        },
        "tags": ["out"],
        "category": CAT_OUTPUT,
    }

    def run(self, packet: Packet) -> Packet:
        if packet.frame is None:
            return packet
        from ..inference.skeletons import draw_skeleton  # noqa: PLC0415

        image = packet.frame.image.copy()
        show_keypoints = bool(self.get("show_keypoints", True))
        kp_threshold = float(self.get("keypoint_score", 0.3))
        kp_radius = int(self.get("keypoint_radius", 3))
        kp_width = int(self.get("keypoint_line_width", 2))
        # 每个整图结果键使用稳定的图层颜色。
        for layer_index, (_key, result) in enumerate(packet.full_results.items()):
            for det in result.detections:
                image = _rect(image, det, _layer_color(layer_index), 2)
        # 裁剪范围与裁剪图上的检测结果。
        for crop_index, slot in enumerate(packet.crops):
            rect = _slot_rect(slot)
            _rect(image, rect, _layer_color(crop_index + len(packet.full_results)), 2)
            for result in slot.results.values():
                for detection in result.detections:
                    translated = Detection(
                        detection.x1 + slot.offset_x, detection.y1 + slot.offset_y,
                        detection.x2 + slot.offset_x, detection.y2 + slot.offset_y,
                        detection.label, detection.score,
                    )
                    image = _rect(image, translated, _layer_color(crop_index + 1), 3)
            if show_keypoints and slot.keypoints:
                draw_skeleton(image, slot.keypoints, keypoint_format=slot.keypoint_format,
                              score_threshold=kp_threshold, radius=kp_radius,
                              line_width=kp_width)
        # 整图姿态模型的骨架（没有裁剪时也能直接看到）
        if show_keypoints:
            for key, result in packet.full_results.items():
                for det in result.detections:
                    if det.keypoints:
                        draw_skeleton(image, det.keypoints,
                                      keypoint_format=det.keypoint_format,
                                      score_threshold=kp_threshold, radius=kp_radius,
                                      line_width=kp_width)
        packet.meta["rendered"] = image
        return packet


def _slot_rect(slot) -> Detection:
    if slot.crop_rect is not None:
        x1, y1, x2, y2 = slot.crop_rect
        return Detection(x1, y1, x2, y2, slot.person_bbox.label, slot.person_bbox.score)
    return slot.person_bbox


# --------------------------------------------------------------------------- #
# 规则判定节点：把"业务条件"和"模型"分开，新手改阈值不用懂模型
# --------------------------------------------------------------------------- #
def _parse_points(text: str) -> list[tuple[float, float]]:
    """解析 "x1,y1 x2,y2 ..." 或 "x1,y1;x2,y2" 形式的归一化点串。"""
    points: list[tuple[float, float]] = []
    for chunk in str(text or "").replace(";", " ").replace("|", " ").split():
        if not chunk.strip():
            continue
        parts = chunk.replace("，", ",").split(",")
        if len(parts) != 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return points


def _anchor_point(det: Detection, anchor: str) -> tuple[float, float]:
    """按锚点模式取判定点：中心 / 底边中点 / 底部 20% 处。"""
    cx = (det.x1 + det.x2) / 2.0
    mode = (anchor or "center").strip().lower()
    if mode in ("bottom_center", "bottom", "foot"):
        return cx, det.y2
    if mode in ("bottom20", "bottom_20", "lower"):
        return cx, det.y1 + det.height * 0.8
    if mode in ("top_center", "head"):
        return cx, det.y1
    return cx, (det.y1 + det.y2) / 2.0


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """射线法（与 subway/safe_school 的 isPoiWithinPoly 同源）。"""
    if len(polygon) < 3:
        return True
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            x_cross = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
            if x < x_cross:
                inside = not inside
    return inside


def _select_layer(packet: Packet, source: str) -> tuple[list[Detection], str]:
    """按来源名字取出一组框，并返回给用户看的名字。"""
    key = str(source or "subject").strip()
    if key in ("subjects", "subject", "主体"):
        return packet.subjects, "主体"
    if key in ("candidates", "candidate", "候选"):
        return packet.candidates, "候选"
    result = packet.full_results.get(key)
    if result is not None:
        return list(result.detections), key
    # 也允许按"关联主体"取：关联里已经通过的主体
    if key in ("associated", "subjects", "关联主体"):
        return [assoc.subject for assoc in packet.associations], "关联主体"
    return [], key


def _store_layer(packet: Packet, source: str, detections: list[Detection]) -> None:
    key = str(source or "subject").strip()
    if key in ("subjects", "subject", "主体"):
        packet.subjects = detections
    elif key in ("candidates", "candidate", "候选"):
        packet.candidates = detections
    else:
        from ..inference.datatypes import ModelResult  # noqa: PLC0415

        existing = packet.full_results.get(key)
        packet.full_results[key] = ModelResult(
            task_type=getattr(existing, "task_type", TaskType.DETECTION),
            detections=detections,
        )


@register_node("class_filter")
class ClassFilterNode(Node):
    """只保留（或剔除）指定类别，把"我关心什么"从模型里独立出来。"""

    Meta = {
        "type_name": "class_filter",
        "display_name": "类别筛选",
        "description": "只保留指定类别的框（也支持反过来剔除）；不重新推理，只做条件过滤",
        "param_schema": {
            "source": {"label": "作用图层", "type": "str", "default": "subject"},
            "classes": {"label": "类别（逗号分隔）", "type": "str", "default": ""},
            "mode": {"label": "模式 keep/drop", "type": "str", "default": "keep"},
        },
        "tags": ["logic"],
        "category": CAT_RULE,
    }

    def run(self, packet: Packet) -> Packet:
        source = str(self.get("source", "subject"))
        detections, label = _select_layer(packet, source)
        wanted = {item.strip() for item in str(self.get("classes", "")).split(",") if item.strip()}
        if not wanted or not detections:
            return packet
        keep = str(self.get("mode", "keep")).strip().lower() != "drop"
        kept = [det for det in detections if (det.label in wanted) == keep]
        dropped = len(detections) - len(kept)
        _store_layer(packet, source, kept)
        packet.meta.setdefault("filter_notes", []).append(
            f"{label}：按类别 {'保留' if keep else '剔除'} {sorted(wanted)}，"
            f"{len(detections)} → {len(kept)}（去掉 {dropped}）"
        )
        return packet


@register_node("roi_filter")
class RoiFilterNode(Node):
    """区域过滤：按判定点是否落在多边形内保留目标。

    坐标是**归一化的 0~1**（相对整帧宽高），所以同一套区域可以换分辨率继续用。
    """

    Meta = {
        "type_name": "roi_filter",
        "display_name": "区域过滤(ROI)",
        "description": "只保留判定点落在多边形区域内的目标；坐标用 0~1 归一化（如 0.1,0.2 0.9,0.2 0.9,0.9 0.1,0.9）",
        "param_schema": {
            "source": {"label": "作用图层", "type": "str", "default": "subject"},
            "points": {"label": "区域顶点", "type": "str", "default": "0,0 1,0 1,1 0,1"},
            "anchor": {"label": "判定点 center/bottom_center/bottom20", "type": "str", "default": "center"},
            "mode": {"label": "模式 inside/outside", "type": "str", "default": "inside"},
        },
        "tags": ["logic", "roi"],
        "category": CAT_RULE,
    }

    def run(self, packet: Packet) -> Packet:
        if packet.frame is None:
            return packet
        source = str(self.get("source", "subject"))
        detections, label = _select_layer(packet, source)
        polygon = _parse_points(self.get("points", ""))
        if not detections or len(polygon) < 3:
            return packet
        height, width = packet.frame.image.shape[:2]
        pixels = [(x * width, y * height) for x, y in polygon]
        anchor = str(self.get("anchor", "center"))
        inside_mode = str(self.get("mode", "inside")).strip().lower() != "outside"
        kept: list[Detection] = []
        for det in detections:
            x, y = _anchor_point(det, anchor)
            inside = _point_in_polygon(x, y, pixels)
            if inside == inside_mode:
                kept.append(det)
        _store_layer(packet, source, kept)
        packet.meta.setdefault("filter_notes", []).append(
            f"{label}：区域过滤（{anchor} / {'区域内' if inside_mode else '区域外'}），"
            f"{len(detections)} → {len(kept)}"
        )
        return packet


@register_node("count_rule")
class CountRuleNode(Node):
    """计数判定：区域内目标数达到阈值就给出结论。

    结论写在 `packet.meta['verdict']`（单条）与 `packet.meta['verdicts']`（可叠加多条），
    界面会把它显示在运行状态里，导出清单也会带上。
    """

    Meta = {
        "type_name": "count_rule",
        "display_name": "计数判定",
        "description": "统计某个图层的目标数量，达到阈值就判定命中（可叠加多条规则）",
        "param_schema": {
            "source": {"label": "统计图层", "type": "str", "default": "subject"},
            "classes": {"label": "只统计类别（可空）", "type": "str", "default": ""},
            "op": {"label": "比较 >= / > / == / < / <=", "type": "str", "default": ">="},
            "threshold": {"label": "阈值", "type": "int", "default": 1},
            "label": {"label": "结论名称", "type": "str", "default": "人数达标"},
        },
        "tags": ["logic", "rule"],
        "category": CAT_RULE,
    }

    def run(self, packet: Packet) -> Packet:
        source = str(self.get("source", "subject"))
        detections, layer_label = _select_layer(packet, source)
        wanted = {item.strip() for item in str(self.get("classes", "")).split(",") if item.strip()}
        counted = [det for det in detections if not wanted or det.label in wanted]
        value = len(counted)
        op = str(self.get("op", ">=")).strip()
        threshold = float(self.get("threshold", 1))
        passed = {
            ">=": value >= threshold,
            ">": value > threshold,
            "==": value == threshold,
            "<": value < threshold,
            "<=": value <= threshold,
        }.get(op, value >= threshold)
        name = str(self.get("label", "计数规则"))
        verdict = {
            "label": name,
            "value": value,
            "threshold": threshold,
            "op": op,
            "passed": bool(passed),
            "source": layer_label,
            "detail": f"{layer_label} {value} {op} {threshold:.0f} → {'命中' if passed else '未命中'}",
        }
        packet.meta.setdefault("verdicts", []).append(verdict)
        packet.meta["verdict"] = verdict
        return packet


@register_node("save")
class SaveNode(Node):
    Meta = {
        "type_name": "save",
        "display_name": "保存",
        "description": "按模板保存大图/指定裁剪/全部裁剪，写盘结果记入 packet.meta['export']",
        "param_schema": {"template": {"label": "保存模板", "type": "str", "default": "full_and_crops"}},
        "tags": ["out"],
        "category": CAT_OUTPUT,
    }

    def run(self, packet: Packet) -> Packet:
        if packet.frame is None:
            return packet
        output_dir_text = str(packet.output_dir or "").strip()
        if not output_dir_text:
            # 未设置输出目录时不写盘（避免误写入当前目录），但必须让调用方看见"没有导出"
            packet.meta["export"] = {
                "skipped_reason": "未设置输出目录",
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
                "paths": [],
                "failures": [],
            }
            return packet
        template = str(self.get("template", packet.save_template))
        # 统一走导出服务：模板语义严格、原子写、默认不覆盖、检查写盘返回值（AR-03/07/08）
        from ..export.service import ExportService
        from ..export.service import frame_tag as build_frame_tag

        exporter = ExportService(output_dir_text, template)
        from ..inference.skeletons import keypoints_payload  # noqa: PLC0415

        crop_payload = []
        for slot in packet.crops:
            meta = {
                "crop_id": slot.crop_id,
                "offset_x": slot.offset_x,
                "offset_y": slot.offset_y,
                "crop_rect": list(slot.crop_rect) if slot.crop_rect else None,
                "result_layers": sorted(slot.results),
            }
            if slot.keypoints:
                meta["keypoints"] = keypoints_payload(slot.keypoints, slot.keypoint_format)
            crop_payload.append({"image": slot.crop_image, "meta": meta})
        result = exporter.export_frame(
            frame_tag=build_frame_tag(
                packet.media_identity or "unknown",
                packet.frame.index,
                run_id=packet.run_id,
            ),
            frame_image=packet.frame.image,
            rendered=packet.meta.get("rendered", packet.frame.image),
            crops=crop_payload,
        )
        packet.meta["saved"] = list(result.paths)
        packet.meta["export"] = {
            "output_dir": str(output_dir_text),
            "template": template,
            "succeeded": result.succeeded,
            "failed": result.failed,
            "skipped": result.skipped,
            "paths": list(result.paths),
            "failures": [list(item) for item in result.failures],
        }
        return packet


def _rect(image, det: Detection, color, thickness: int):
    import cv2  # noqa: PLC0415

    x1, y1 = int(round(det.x1)), int(round(det.y1))
    x2, y2 = int(round(det.x2)), int(round(det.y2))
    cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)
    label = f"{det.label} {det.score:.2f}"
    cv2.putText(image, label, (x1, max(0, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return image


def _rect_dashed(image, det: Detection, color, thickness: int, gap=8, seg=6):
    import cv2  # noqa: PLC0415

    x1, y1 = int(round(det.x1)), int(round(det.y1))
    x2, y2 = int(round(det.x2)), int(round(det.y2))
    for (sx, sy, ex, ey) in (
        (x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1),
    ):
        length = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
        if length == 0:
            continue
        step = gap + seg
        t = 0.0
        while t < length:
            t2 = min(t + seg, length)
            ax = sx + (ex - sx) * t / length
            ay = sy + (ey - sy) * t / length
            bx = sx + (ex - sx) * t2 / length
            by = sy + (ey - sy) * t2 / length
            cv2.line(image, (int(ax), int(ay)), (int(bx), int(by)), color, thickness)
            t += step
    return image
