from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from .node import Node, create_node
from .packet import Packet


@dataclass
class NodeConfig:
    type: str
    name: str = ""
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NodeConfig":
        return cls(
            type=str(payload.get("type", "")),
            name=str(payload.get("name", "")),
            enabled=bool(payload.get("enabled", True)),
            params=dict(payload.get("params") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "enabled": self.enabled,
            "params": dict(self.params),
        }


class WorkflowError(ValueError):
    pass


@dataclass
class WorkflowConfig:
    workflow_id: str = ""
    name: str = ""
    description: str = ""
    nodes: list[NodeConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkflowConfig":
        nodes = []
        for item in payload.get("nodes") or []:
            if isinstance(item, dict):
                nodes.append(NodeConfig.from_dict(item))
        return cls(
            workflow_id=str(payload.get("workflow_id", "")),
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            nodes=nodes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    def validate(self) -> None:
        if not self.nodes:
            raise WorkflowError("工作流没有节点")
        for node in self.nodes:
            if not node.type:
                raise WorkflowError("存在缺少 type 的节点")


class Workflow:
    """运行时：按顺序执行 enabled 节点，返回处理后的 Packet。"""

    def __init__(self, nodes: list[Node]):
        self.nodes = nodes

    @classmethod
    def from_config(cls, config: WorkflowConfig) -> "Workflow":
        nodes = [create_node(node.type, node.params, node.name) for node in config.nodes if node.enabled]
        return cls(nodes)

    def run(self, packet: Packet) -> Packet:
        for node in self.nodes:
            packet = node.run(packet) or packet
        return packet


def workflows_catalog_path() -> Path:
    return paths.data_path("workflows", "workflows.json")


def save_workflow(config: WorkflowConfig, path: str | Path | None = None) -> Path:
    path = Path(path) if path else workflows_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def load_workflow(path: str | Path) -> WorkflowConfig:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WorkflowConfig.from_dict(payload)


def save_workflow_catalog(workflows: list[WorkflowConfig]) -> Path:
    path = workflows_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    payload = {"version": 1, "workflows": [w.to_dict() for w in workflows]}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def load_workflow_catalog() -> list[WorkflowConfig]:
    path = workflows_catalog_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [WorkflowConfig.from_dict(item) for item in payload.get("workflows", []) if isinstance(item, dict)]
