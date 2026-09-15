"""Public workflow templates containing generic processing patterns only."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from .workflow import NodeConfig, WorkflowConfig


@dataclass(frozen=True)
class TemplateRole:
    key: str
    label: str
    hint: str = ""
    task: str = "any"
    required: bool = True


@dataclass(frozen=True)
class WorkflowTemplate:
    template_id: str
    name: str
    category: str
    summary: str
    scenario: str
    pipeline: tuple[str, ...]
    roles: tuple[TemplateRole, ...]
    shape: str
    keywords: tuple[str, ...] = ()
    defaults: dict = field(default_factory=dict)
    def role(self, key): return next((item for item in self.roles if item.key == key), None)
    def required_roles(self): return tuple(item for item in self.roles if item.required)
    def all_roles(self): return self.roles


def _source(): return NodeConfig("source_video", "媒体输入", params={"fps": 1.0})
def _draw(): return NodeConfig("draw", "结果预览", params={"show_keypoints": True})
def _text(values, key, fallback=""): return str(values.get(key, "") or fallback).strip()
def _number(values, key, fallback):
    try:
        return float(values.get(key, fallback))
    except (TypeError, ValueError):
        return float(fallback)


def _finish(nodes, values, template):
    return WorkflowConfig(
        workflow_id=uuid.uuid4().hex,
        name=_text(values, "name", template.name) or template.name,
        description=f"{template.summary}（模板：{template.name}）",
        nodes=[node for node in nodes if node.type != "save"],
    )


def starter_workflow_config():
    """Safe empty starter with no model, class, threshold, or business defaults."""
    return WorkflowConfig("starter_preview", "空白工作流", "按需添加推理、关联、裁剪和输出节点", [_source(), _draw()])


def _shape_full_multi(template, values):
    models = [str(item).strip() for item in dict.fromkeys(values.get("extra_models") or []) if str(item).strip()]
    nodes = [_source()]
    nodes.extend(NodeConfig("inference", f"整图推理 {index + 1}", params={
        "model": {"name": model}, "model_key": f"model_{index + 1}"}) for index, model in enumerate(models))
    return _finish(nodes + [_draw()], values, template)


def _shape_crop_cascade(template, values):
    detector, downstream = _text(values, "subject_detector"), _text(values, "downstream_model")
    return _finish([
        _source(),
        NodeConfig("inference", "① 主体检测", params={"model": {"name": detector}, "model_key": "subject"}),
        NodeConfig("crop", "② 裁剪主体", params={"subject_mode": "all", "subject_source": "subject",
                   "expand_ratio": _number(values, "expand_ratio", .1), "min_area": int(_number(values, "min_area", 0))}),
        NodeConfig("crop_inference", "③ 裁剪图推理", params={"route": "all", "model": {"name": downstream}}),
        _draw(),
    ], values, template)


def _shape_associate_crop(template, values):
    subject = _text(values, "subject_detector")
    candidate = _text(values, "candidate_detector")
    downstream = _text(values, "downstream_model")
    nodes = [
        _source(),
        NodeConfig("inference", "① 主体检测", params={"model": {"name": subject}, "model_key": "subject"}),
        NodeConfig("inference", "② 候选检测", params={"model": {"name": candidate}, "model_key": "candidate"}),
        NodeConfig("generic_associate", "③ 空间关联", params={
            "left_key": "subject", "right_key": "candidate",
            "left_classes": _text(values, "subject_classes"), "right_classes": _text(values, "candidate_classes"),
            "metric": _text(values, "metric", "ioa_right"), "threshold": _number(values, "threshold", .5),
            "expand_ratio": _number(values, "association_expand", 0)}),
        NodeConfig("crop", "④ 裁剪命中主体", params={"subject_mode": "associated",
                   "expand_ratio": _number(values, "crop_expand", .1), "min_area": int(_number(values, "min_area", 0))}),
    ]
    if downstream:
        nodes.append(NodeConfig("crop_inference", "⑤ 裁剪图推理", params={"route": "all", "model": {"name": downstream}}))
    return _finish(nodes + [_draw()], values, template)


def _shape_roi_count(template, values):
    detector = _text(values, "detector")
    return _finish([
        _source(),
        NodeConfig("inference", "① 目标检测", params={"model": {"name": detector}, "model_key": "subject"}),
        NodeConfig("roi_filter", "② 区域过滤", params={"source": "subject",
                   "points": _text(values, "points", "0,0 1,0 1,1 0,1"), "anchor": _text(values, "anchor", "center"), "mode": "inside"}),
        NodeConfig("count_rule", "③ 数量规则", params={"source": "subject", "classes": _text(values, "count_classes"),
                   "op": _text(values, "op", ">="), "threshold": int(_number(values, "count_threshold", 1)),
                   "label": _text(values, "count_label", "数量达到阈值")}),
        _draw(),
    ], values, template)


SHAPES: dict[str, Callable] = {"full_multi": _shape_full_multi, "crop_cascade": _shape_crop_cascade,
                               "associate_crop": _shape_associate_crop, "roi_count": _shape_roi_count}

TEMPLATES = (
    WorkflowTemplate("multi_model_full", "多模型整图复核", "基础", "多个模型处理同一帧并分别保留结果",
        "先确认每个模型能独立运行，再决定是否增加关联和裁剪。",
        ("媒体输入", "整图模型 A", "整图模型 B", "结果预览"), (), "full_multi", ("多模型", "整图", "入门")),
    WorkflowTemplate("detect_crop_infer", "检测 → 裁剪 → 下游推理", "级联", "先定位主体，再逐个裁剪并交给下游模型",
        "适用于一级模型负责定位、二级模型负责细分或确认的两阶段流程。",
        ("媒体输入", "主体检测", "裁剪", "下游推理", "结果预览"),
        (TemplateRole("subject_detector", "① 主体检测模型", "输出要被裁剪的框", "det"),
         TemplateRole("downstream_model", "② 下游模型", "处理每张裁剪图", "any")),
        "crop_cascade", ("裁剪", "级联", "两阶段"), {"expand_ratio": .1, "min_area": 0}),
    WorkflowTemplate("associate_crop_infer", "双模型关联 → 裁剪 → 下游", "关联", "关联两个全图模型的结果，裁剪命中主体并可继续推理",
        "主体类别和候选类别由用户填写；模板不含业务类别或业务阈值。",
        ("媒体输入", "主体检测", "候选检测", "空间关联", "裁剪", "可选下游", "结果预览"),
        (TemplateRole("subject_detector", "① 主体模型", "决定裁剪范围", "det"),
         TemplateRole("candidate_detector", "② 候选模型", "参与空间关系判断", "det"),
         TemplateRole("downstream_model", "③ 下游模型（可留空）", "处理关联后的裁剪图", "any", False)),
        "associate_crop", ("关联", "IoA", "IoU", "裁剪"),
        {"metric": "ioa_right", "threshold": .5, "association_expand": 0, "crop_expand": .1}),
    WorkflowTemplate("roi_count", "区域过滤与数量规则", "规则", "只统计指定多边形区域内的目标",
        "适用于区域内目标计数等通用规则；区域坐标使用 0 到 1 的归一化值。",
        ("媒体输入", "目标检测", "区域过滤", "数量规则", "结果预览"),
        (TemplateRole("detector", "检测模型", "提供待统计的目标框", "det"),), "roi_count", ("ROI", "区域", "计数"),
        {"points": "0,0 1,0 1,1 0,1", "anchor": "center", "op": ">=", "count_threshold": 1}),
)
TEMPLATE_REGISTRY = {item.template_id: item for item in TEMPLATES}
CATEGORY_ORDER = ("基础", "级联", "关联", "规则")


def template_categories(): return [name for name in CATEGORY_ORDER if any(item.category == name for item in TEMPLATES)]
def get_template(template_id: str) -> Optional[WorkflowTemplate]: return TEMPLATE_REGISTRY.get(template_id)


def build_from_template(template_id, name="", role_models=None, extra_models=None, option_values=None):
    template = get_template(template_id)
    if template is None:
        raise ValueError(f"未知工作流模板 {template_id!r}")
    values = dict(template.defaults or {})
    values.update(option_values or {})
    values.update(role_models or {})
    values["name"] = str(name or values.get("name") or "").strip()
    values["extra_models"] = [item for item in (extra_models or []) if str(item).strip()]
    if template.shape == "full_multi" and not values["extra_models"]:
        raise ValueError("至少选择一个全图模型")
    missing = [role.label for role in template.required_roles() if not _text(values, role.key)]
    if missing:
        raise ValueError("请为以下步骤选择模型：" + "、".join(missing))
    if not values["name"]:
        raise ValueError("请为工作流命名")
    return SHAPES[template.shape](template, values)


def default_workflows(): return [starter_workflow_config()]
