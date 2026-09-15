"""插件协议与发现（评审第 8 节）。

三类插件：
- `model`：模型适配（把新的模型格式/后端接进推理）
- `node`：处理节点（往工作流里加一个可配置步骤）
- `io`：导入导出（新的素材来源或产物格式）

每个插件包提供一个 `plugin.json` 清单，声明 ID、版本、能力、参数 schema、
依赖、支持的数据版本、资源需求与健康检查入口。安装本地包后**不需要改主窗口
或核心注册表**：发现 → 校验 → 健康检查 → 注册，全部由本模块完成。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

#: 当前宿主支持的数据/协议版本
SUPPORTED_DATA_VERSION = 1
SUPPORTED_PROTOCOL_VERSION = 1

MODEL_PLUGIN = "model"
NODE_PLUGIN = "node"
IO_PLUGIN = "io"
PLUGIN_KINDS = (MODEL_PLUGIN, NODE_PLUGIN, IO_PLUGIN)

MANIFEST_NAME = "plugin.json"

HEALTHY = "健康"
UNHEALTHY = "异常"
INCOMPATIBLE = "不兼容"
INVALID = "清单非法"


class PluginError(ValueError):
    """插件清单或加载过程不合法。"""


@dataclass
class PluginManifest:
    """插件清单。"""

    plugin_id: str
    name: str
    version: str
    kind: str
    entry: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    param_schema: dict[str, Any] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)
    data_version: int = SUPPORTED_DATA_VERSION
    protocol_version: int = SUPPORTED_PROTOCOL_VERSION
    resources: dict[str, Any] = field(default_factory=dict)
    healthcheck: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any], source: str = "") -> "PluginManifest":
        if not isinstance(payload, dict):
            raise PluginError("清单必须是 JSON 对象")
        plugin_id = str(payload.get("id", "")).strip()
        name = str(payload.get("name", "")).strip() or plugin_id
        version = str(payload.get("version", "")).strip()
        kind = str(payload.get("kind", "")).strip()
        entry = str(payload.get("entry", "")).strip()
        if not plugin_id:
            raise PluginError("缺少 id")
        if not version:
            raise PluginError(f"{plugin_id} 缺少 version")
        if kind not in PLUGIN_KINDS:
            raise PluginError(f"{plugin_id} 的 kind 非法：{kind!r}（应为 {', '.join(PLUGIN_KINDS)}）")
        if not entry:
            raise PluginError(f"{plugin_id} 缺少 entry（入口模块路径）")
        try:
            data_version = int(payload.get("data_version", SUPPORTED_DATA_VERSION))
        except (TypeError, ValueError) as exc:
            raise PluginError(f"{plugin_id} 的 data_version 不是整数") from exc
        try:
            protocol_version = int(payload.get("protocol_version", SUPPORTED_PROTOCOL_VERSION))
        except (TypeError, ValueError) as exc:
            raise PluginError(f"{plugin_id} 的 protocol_version 不是整数") from exc
        return cls(
            plugin_id=plugin_id,
            name=name,
            version=version,
            kind=kind,
            entry=entry,
            description=str(payload.get("description", "")),
            capabilities=[str(item) for item in (payload.get("capabilities") or [])],
            param_schema=dict(payload.get("param_schema") or {}),
            requires=[str(item) for item in (payload.get("requires") or [])],
            data_version=data_version,
            protocol_version=protocol_version,
            resources=dict(payload.get("resources") or {}),
            healthcheck=str(payload.get("healthcheck", "")),
        )

    def compatibility_problem(self) -> str:
        """返回不兼容原因；空字符串表示兼容。"""
        if self.data_version > SUPPORTED_DATA_VERSION:
            return (
                f"插件数据版本 {self.data_version} 高于宿主支持的 {SUPPORTED_DATA_VERSION}，"
                "需要升级宿主"
            )
        if self.protocol_version > SUPPORTED_PROTOCOL_VERSION:
            return (
                f"插件协议版本 {self.protocol_version} 高于宿主支持的 {SUPPORTED_PROTOCOL_VERSION}"
            )
        return ""


@dataclass
class PluginStatus:
    """插件加载与健康检查结果。"""

    manifest: Optional[PluginManifest]
    path: str
    state: str
    reason: str = ""
    missing_requirements: list[str] = field(default_factory=list)
    report: str = ""

    @property
    def healthy(self) -> bool:
        return self.state == HEALTHY


def missing_requirements(requires: list[str]) -> list[str]:
    """检查依赖模块是否可导入，返回缺失清单。"""
    import importlib.util as util

    missing = []
    for name in requires:
        module = name.split("[")[0].strip()
        if not module:
            continue
        try:
            found = util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    return missing


def load_manifest(path: str | Path) -> PluginManifest:
    manifest_path = Path(path) / MANIFEST_NAME if Path(path).is_dir() else Path(path)
    if not manifest_path.exists():
        raise PluginError(f"找不到插件清单：{manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"清单无法解析：{exc}") from exc
    return PluginManifest.from_dict(payload, str(manifest_path))


def _load_entry_module(directory: Path, entry: str):
    """从插件目录加载入口模块（不安装也能用）。"""
    module_path = (directory / entry).resolve()
    if not module_path.exists():
        raise PluginError(f"入口文件不存在：{entry}")
    module_name = f"annoweave_plugin_{directory.name}_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise PluginError(f"无法加载入口模块：{entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # 让插件能 import 同目录下的兄弟模块
    directory_text = str(directory)
    if directory_text not in sys.path:
        sys.path.insert(0, directory_text)
    spec.loader.exec_module(module)
    return module


class PluginHost:
    """插件宿主：发现、校验、健康检查与注册。"""

    def __init__(self, plugin_root: str | Path | None = None):
        self.plugin_root = Path(plugin_root) if plugin_root else default_plugin_root()
        self.statuses: list[PluginStatus] = []
        #: 已注册的插件处理器：kind -> {capability/node_type: callable_or_manifest}
        self.registry: dict[str, dict[str, Any]] = {kind: {} for kind in PLUGIN_KINDS}

    # ----------------------------- 发现 ----------------------------- #
    def discover(self, root: str | Path | None = None) -> list[PluginStatus]:
        """扫描插件目录。每个子目录（或直接放置的 plugin.json）视为一个插件。"""
        base = Path(root) if root is not None else self.plugin_root
        self.statuses = []
        if not base.exists():
            return self.statuses
        candidates: list[Path] = []
        if (base / MANIFEST_NAME).exists():
            candidates.append(base)
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / MANIFEST_NAME).exists():
                candidates.append(child)
        for directory in candidates:
            self.statuses.append(self._inspect(directory))
        return self.statuses

    def _inspect(self, directory: Path) -> PluginStatus:
        try:
            manifest = load_manifest(directory)
        except PluginError as exc:
            return PluginStatus(manifest=None, path=str(directory), state=INVALID, reason=str(exc))

        problem = manifest.compatibility_problem()
        if problem:
            return PluginStatus(manifest=manifest, path=str(directory), state=INCOMPATIBLE, reason=problem)

        missing = missing_requirements(manifest.requires)
        if missing:
            return PluginStatus(
                manifest=manifest,
                path=str(directory),
                state=UNHEALTHY,
                reason="缺少依赖：" + "、".join(missing),
                missing_requirements=missing,
            )

        try:
            module = _load_entry_module(directory, manifest.entry)
        except Exception as exc:  # noqa: BLE001
            return PluginStatus(
                manifest=manifest, path=str(directory), state=UNHEALTHY,
                reason=f"入口加载失败：{type(exc).__name__}: {exc}",
            )

        check = getattr(module, manifest.healthcheck, None) if manifest.healthcheck else None
        if callable(check):
            try:
                report = check()
            except Exception as exc:  # noqa: BLE001
                return PluginStatus(
                    manifest=manifest, path=str(directory), state=UNHEALTHY,
                    reason=f"健康检查抛错：{type(exc).__name__}: {exc}",
                )
            if report is False:
                return PluginStatus(
                    manifest=manifest, path=str(directory), state=UNHEALTHY,
                    reason="健康检查返回失败",
                )
            report_text = str(report) if report not in (True, None) else ""
        else:
            report_text = ""

        entry_callable = getattr(module, "register", None)
        if not callable(entry_callable):
            return PluginStatus(
                manifest=manifest, path=str(directory), state=UNHEALTHY,
                reason="入口模块缺少 register(host) 函数",
            )
        try:
            entry_callable(self)
        except Exception as exc:  # noqa: BLE001
            return PluginStatus(
                manifest=manifest, path=str(directory), state=UNHEALTHY,
                reason=f"注册失败：{type(exc).__name__}: {exc}",
            )

        return PluginStatus(
            manifest=manifest, path=str(directory), state=HEALTHY, report=report_text
        )

    # ----------------------------- 注册接口 ----------------------------- #
    def register_model_adapter(self, name: str, factory: Callable) -> None:
        self.registry[MODEL_PLUGIN][name] = factory

    def register_node(self, type_name: str, node_class: type) -> None:
        """把插件节点注册进工作流节点表（插件不需要改核心注册表）。"""
        from ..workflow.node import NODE_REGISTRY, Node

        if not isinstance(node_class, type) or not issubclass(node_class, Node):
            raise PluginError(f"插件节点 {type_name!r} 必须是 Node 子类")
        NODE_REGISTRY[type_name] = node_class
        self.registry[NODE_PLUGIN][type_name] = node_class

    def register_io_handler(self, name: str, handler: Callable) -> None:
        self.registry[IO_PLUGIN][name] = handler

    # ----------------------------- 查询 ----------------------------- #
    def healthy_plugins(self) -> list[PluginStatus]:
        return [status for status in self.statuses if status.healthy]

    def by_kind(self, kind: str) -> list[PluginStatus]:
        return [
            status for status in self.statuses
            if status.healthy and status.manifest is not None and status.manifest.kind == kind
        ]

    def summary(self) -> str:
        if not self.statuses:
            return f"未发现插件。把插件目录放到 {self.plugin_root} 后点“重新扫描”。"
        healthy = len(self.healthy_plugins())
        broken = [status for status in self.statuses if not status.healthy]
        lines = [f"共发现 {len(self.statuses)} 个插件，健康 {healthy} 个。"]
        for status in broken:
            label = status.manifest.name if status.manifest else Path(status.path).name
            lines.append(f"✗ {label}：{status.state} — {status.reason}")
        return "\n".join(lines)


def default_plugin_root() -> Path:
    from .. import paths

    return paths.data_path("plugins")
