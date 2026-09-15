from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .packet import Packet


class Node(ABC):
    """节点基类。子类声明 Meta（类型名/展示名/描述/参数 schema）并实现 run。

    参数 schema 用于在软件里动态生成参数表单，使工作流可配置。
    """

    Meta: dict[str, Any] = {
        "type_name": "",
        "display_name": "",
        "description": "",
        "param_schema": {},
        "tags": [],
    }

    def __init__(self, params: dict[str, Any] | None = None, name: str = ""):
        self.params: dict[str, Any] = dict(params or {})
        self.name = name or self.Meta.get("display_name", self.Meta.get("type_name", "node"))

    @abstractmethod
    def run(self, packet: Packet) -> Packet:
        raise NotImplementedError

    def get(self, key: str, default=None):
        return self.params.get(key, default)


class NodeError(RuntimeError):
    pass


NODE_REGISTRY: dict[str, type[Node]] = {}


def register_node(type_name: str):
    def decorator(cls: type[Node]):
        cls.Meta = {**cls.Meta, "type_name": type_name}
        NODE_REGISTRY[type_name] = cls
        return cls
    return decorator


def create_node(type_name: str, params=None, name: str = "") -> Node:
    cls = NODE_REGISTRY.get(type_name)
    if cls is None:
        raise NodeError(f"未知节点类型 {type_name!r}，可选: {sorted(NODE_REGISTRY)}")
    return cls(params, name)
