from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .node import NODE_REGISTRY
from .workflow import WorkflowConfig


@dataclass(frozen=True)
class WorkflowIssue:
    """工作流静态检查结果；error 会阻止运行，warning 只提示优化。"""

    node_index: int
    message: str
    severity: str = "error"


def _model_names(models) -> set[str]:
    if models is None:
        return set()
    if isinstance(models, dict):
        return {str(name) for name in models}
    return {str(getattr(model, "name", "")) for model in models if getattr(model, "name", "")}


def validate_workflow(config: WorkflowConfig, models=None) -> list[WorkflowIssue]:
    """检查节点、模型和节点间的数据依赖。

    当前执行器仍是线性流水线。这层检查把隐含在 ``Packet`` 字段和字符串 key 中的
    端口关系显式化，避免流程能够保存、运行时却悄悄得到空结果。
    """

    issues: list[WorkflowIssue] = []
    if not config.nodes:
        return [WorkflowIssue(0, "工作流没有任何节点")]
    enabled = [(index, node) for index, node in enumerate(config.nodes, 1) if node.enabled]
    if not enabled:
        return [WorkflowIssue(0, "所有节点都被禁用，运行不会产生结果")]

    available_models = _model_names(models)
    check_model_existence = models is not None
    result_layers: set[str] = set()
    semantic_layers: set[str] = set()
    has_associations = False
    has_crops = False

    for index, node in enabled:
        if node.type not in NODE_REGISTRY:
            issues.append(WorkflowIssue(index, f"未知节点类型 {node.type!r}"))
            continue
        meta = NODE_REGISTRY[node.type].Meta
        schema = meta.get("param_schema", {})
        for key, spec in schema.items():
            if spec.get("required") and not node.params.get(key):
                issues.append(WorkflowIssue(index, f"必填参数“{spec.get('label', key)}”未设置"))
            if spec.get("type") != "model":
                continue
            value = node.params.get(key) or {}
            name = value.get("name") if isinstance(value, dict) else value
            if not name:
                issues.append(WorkflowIssue(index, "尚未选择模型"))
            elif check_model_existence and str(name) not in available_models:
                issues.append(WorkflowIssue(index, f"模型库中不存在模型 {name!r}"))

        if node.type in ("inference", "pose_inference"):
            output = str(node.params.get("model_key") or node.name or "").strip()
            if not output:
                issues.append(WorkflowIssue(index, "请填写节点名称或 model_key，作为结果图层名"))
            elif output in result_layers:
                issues.append(WorkflowIssue(index, f"结果图层 {output!r} 与前面的节点重名，会覆盖已有结果"))
            else:
                result_layers.add(output)
            continue

        if node.type == "generic_associate":
            left = str(node.params.get("left_key", "model_1"))
            right = str(node.params.get("right_key", "model_2"))
            missing = [key for key in (left, right) if key not in result_layers]
            if missing:
                issues.append(WorkflowIssue(index, f"通用关联引用了尚未生成的图层：{', '.join(missing)}"))
            has_associations = True
            semantic_layers.update(("subjects", "candidates"))
            continue

        if node.type == "crop":
            mode = str(node.params.get("subject_mode", "associated")).strip().lower()
            if mode in ("all", "全部", "every"):
                source = str(node.params.get("subject_source", "subject"))
                if source not in result_layers and source not in semantic_layers:
                    issues.append(WorkflowIssue(index, f"裁剪主体图层 {source!r} 尚未生成"))
            elif not has_associations:
                issues.append(WorkflowIssue(index, "“仅关联命中”裁剪前需要一个关联节点"))
            has_crops = True
            continue

        if node.type == "crop_inference" and not has_crops:
            issues.append(WorkflowIssue(index, "裁剪级联推理前需要一个裁剪节点"))

        if node.type in ("class_filter", "roi_filter", "count_rule"):
            source = str(node.params.get("source", "subject"))
            builtins = {"subjects", "subject", "主体", "candidates", "candidate", "候选",
                        "associated", "关联主体"}
            if source not in result_layers and source not in semantic_layers and source not in builtins:
                issues.append(WorkflowIssue(index, f"规则引用了尚未生成的图层 {source!r}"))

    if not any(node.type in ("draw", "save") for _, node in enabled):
        issues.append(WorkflowIssue(0, "建议在末尾加入“绘制”节点，复核界面才能直接显示叠加结果", "warning"))
    return issues


def error_messages(issues: Iterable[WorkflowIssue]) -> list[str]:
    return [issue.message for issue in issues if issue.severity == "error"]
