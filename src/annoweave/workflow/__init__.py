from __future__ import annotations

import importlib

from .node import NODE_REGISTRY, Node, NodeError, create_node, register_node
from .packet import Association, CropSlot, Packet
from .workflow import (
    NodeConfig,
    Workflow,
    WorkflowConfig,
    WorkflowError,
    load_workflow,
    load_workflow_catalog,
    save_workflow,
    save_workflow_catalog,
    workflows_catalog_path,
)

# 导入各节点定义模块，触发 register_node 装饰器注册节点类型。
for _module in ("association", "nodes"):
    importlib.import_module(f"{__name__}.{_module}")

from .inspection import (  # noqa: E402
    NodeReport,
    RunTrace,
    TracedWorkflowRunner,
    packet_fingerprint,
)
from .templates import default_workflows, starter_workflow_config  # noqa: E402
from .validation import WorkflowIssue, error_messages, validate_workflow  # noqa: E402

__all__ = [
    "Association",
    "CropSlot",
    "Packet",
    "NODE_REGISTRY",
    "Node",
    "NodeError",
    "create_node",
    "register_node",
    "NodeConfig",
    "Workflow",
    "WorkflowConfig",
    "WorkflowError",
    "load_workflow",
    "load_workflow_catalog",
    "save_workflow",
    "save_workflow_catalog",
    "workflows_catalog_path",
    "starter_workflow_config",
    "default_workflows",
    "NodeReport",
    "RunTrace",
    "TracedWorkflowRunner",
    "packet_fingerprint",
    "WorkflowIssue",
    "validate_workflow",
    "error_messages",
]
