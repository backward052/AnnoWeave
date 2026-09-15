"""节点执行检查与缓存（评审第 8 节）。

- 逐节点记录：输入摘要、输出预览、耗时、成功/失败/跳过状态；
- 选中节点可查看该帧的中间结果；
- “运行至此”与“仅重算下游”依赖**输入版本**：输入没变直接复用缓存，
  输入变了必须重算，绝不复用过期输出。
"""

from __future__ import annotations

import copy
import hashlib
import json
import pickle
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..inference.datatypes import Detection
from .node import NODE_REGISTRY, NodeError, create_node
from .packet import Packet

SUCCESS = "成功"
FAILED = "失败"
SKIPPED = "跳过"


def _summarise_detections(detections: list[Detection], limit: int = 5) -> str:
    if not detections:
        return "无"
    parts = []
    for det in detections[:limit]:
        parts.append(
            f"{det.label}@{det.score:.2f}({det.x1:.0f},{det.y1:.0f},{det.x2:.0f},{det.y2:.0f})"
        )
    if len(detections) > limit:
        parts.append(f"…共 {len(detections)} 个")
    return "、".join(parts)


def packet_fingerprint(packet: Packet) -> str:
    """计算 Packet 的输入指纹，用于判断缓存是否还有效。"""
    # Include pixels and media identity: frame 0 with the same dimensions is not the same input.
    try:
        model_signature = {name: repr(getattr(model, 'config', model))
                           for name, model in packet.meta.get('models', {}).items()}
        semantic = (packet.media_identity, packet.frame, packet.full_results,
                    packet.associations, packet.crops, packet.output_dir, packet.save_template,
                    {key: value for key, value in packet.meta.items() if key not in ('models', 'trace')},
                    model_signature)
        return hashlib.sha256(pickle.dumps(semantic, protocol=5)).hexdigest()
    except (TypeError, pickle.PickleError, AttributeError):
        return uuid.uuid4().hex  # An opaque plugin value must never cause a false cache hit.


def output_fingerprint(packet: Packet) -> str:
    """计算节点输出的指纹，作为下游节点的输入版本。"""
    payload = {
        "full_results": {
            key: [
                (round(d.x1, 1), round(d.y1, 1), round(d.x2, 1), round(d.y2, 1), d.label, round(d.score, 3))
                for d in result.detections
            ]
            for key, result in sorted(packet.full_results.items())
            if result is not None
        },
        "associations": len(packet.associations),
        "crops": [
            (
                slot.crop_id,
                slot.offset_x,
                slot.offset_y,
                sorted(slot.results),
            )
            for slot in packet.crops
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class NodeReport:
    """单个节点的一次执行记录。"""

    index: int
    type_name: str
    name: str
    status: str = SUCCESS
    elapsed_ms: float = 0.0
    input_summary: str = ""
    output_summary: str = ""
    error: str = ""
    cached: bool = False
    input_fingerprint: str = ""

    @property
    def label(self) -> str:
        return self.name or self.type_name

    def status_text(self) -> str:
        prefix = "缓存" if self.cached else self.status
        return f"{prefix} · {self.elapsed_ms:.0f}ms"

    def detail(self) -> str:
        lines = [
            f"节点 {self.index + 1}：{self.label} [{self.type_name}]",
            f"状态：{self.status_text()}",
        ]
        if self.input_summary:
            lines.append(f"输入：{self.input_summary}")
        if self.output_summary:
            lines.append(f"输出：{self.output_summary}")
        if self.error:
            lines.append(f"失败原因：{self.error}")
        return "\n".join(lines)


@dataclass
class RunTrace:
    """一次工作流运行的完整轨迹。"""

    reports: list[NodeReport] = field(default_factory=list)
    status: str = SUCCESS
    elapsed_ms: float = 0.0

    def failed_index(self) -> int:
        for report in self.reports:
            if report.status == FAILED:
                return report.index
        return -1

    def summary(self) -> str:
        if not self.reports:
            return "没有执行任何节点。"
        parts = [f"{report.label}:{report.status_text()}" for report in self.reports]
        return f"{self.status} · 共 {len(self.reports)} 个节点 · 总耗时 {self.elapsed_ms:.0f}ms\n" + "；".join(parts)


def node_input_summary(packet: Packet, type_name: str) -> str:
    """按节点类型给出可读的输入摘要。"""
    if type_name in ("inference", "crop_inference"):
        if type_name == "crop_inference":
            return f"待推理裁剪 {len(packet.crops)} 个"
        return f"帧 {packet.frame.image.shape[1]}×{packet.frame.image.shape[0]}" if packet.frame is not None else "无帧"
    if type_name == "generic_associate":
        return f"已有整图结果 {len(packet.full_results)} 层"
    if type_name == "crop":
        return f"关联 {len(packet.associations)} 个（待裁剪 {sum(1 for a in packet.associations if a.should_crop)} 个）"
    if type_name == "source_video":
        return f"帧 {packet.frame.index}" if packet.frame is not None else "无帧"
    if type_name == "save":
        return f"裁剪 {len(packet.crops)} 个、输出目录 {packet.output_dir or '未设置'}"
    return "—"


def node_output_summary(packet: Packet, type_name: str) -> str:
    """按节点类型给出可读的输出预览。

    未知类型（含第三方插件节点）退回通用预览：优先展示检测结果与帧变化，
    这样插件节点也能在“节点执行”面板里看清自己产出了什么。
    """
    if type_name == "inference":
        return _result_preview(packet)
    if type_name == "generic_associate":
        hits = sum(1 for a in packet.associations if a.should_crop)
        return f"关联 {len(packet.associations)} 个，命中 {hits} 个"
    if type_name == "crop":
        if not packet.crops:
            return "裁剪 0 个"
        return "裁剪 {} 个：".format(len(packet.crops)) + "、".join(
            f"{slot.crop_id}({slot.crop_image.shape[1]}×{slot.crop_image.shape[0]})"
            for slot in packet.crops[:3]
        )
    if type_name == "crop_inference":
        layers = sum(len(slot.results) for slot in packet.crops)
        detections = sum(len(result.detections) for slot in packet.crops for result in slot.results.values())
        return f"裁剪结果 {layers} 层、检测框 {detections} 个"
    if type_name == "draw":
        rendered = packet.meta.get("rendered")
        return f"渲染图 {rendered.shape[1]}×{rendered.shape[0]}" if rendered is not None else "未渲染"
    if type_name == "save":
        export = packet.meta.get("export") or {}
        if export.get("skipped_reason"):
            return f"未写盘：{export['skipped_reason']}"
        return f"成功 {export.get('succeeded', 0)}、失败 {export.get('failed', 0)}、跳过 {export.get('skipped', 0)}"
    if type_name == "source_video":
        return f"帧 {packet.frame.index}" if packet.frame is not None else "无帧"
    return _result_preview(packet)


def _result_preview(packet: Packet) -> str:
    """通用预览：检测结果优先，其次裁剪，最后退回帧信息。"""
    if packet.full_results:
        parts = []
        for key, result in packet.full_results.items():
            detections = list(getattr(result, "detections", []) or []) if result else []
            if detections:
                parts.append(f"{key}:{_summarise_detections(detections)}")
        if parts:
            return "；".join(parts)
    if packet.crops:
        return f"裁剪 {len(packet.crops)} 个"
    if packet.frame is not None and packet.frame.image is not None:
        shape = packet.frame.image.shape
        return f"帧 {shape[1]}×{shape[0]}"
    return "—"


class TracedWorkflowRunner:
    """带检查与缓存的工作流执行器。

    缓存键 = (节点身份, 节点参数指纹, 输入指纹)。输入没变就复用上一次的输出，
    因此“运行至此”“仅重算下游”不会沿用过期结果。
    """

    def __init__(self, config, cache: Optional[dict] = None):
        self.config = config
        self.cache: dict[str, tuple[str, Packet]] = cache if cache is not None else {}

    def _node_cache_key(self, index: int, node) -> str:
        params = json.dumps(node.params, sort_keys=True, ensure_ascii=False, default=str)
        return f"{index}:{node.type}:{node.name}:{hashlib.sha1(params.encode('utf-8')).hexdigest()[:12]}"

    def run(
        self,
        packet: Packet,
        stop_after: Optional[int] = None,
        reuse_cache: bool = True,
        start_at: int = 0,
    ) -> tuple[Packet, RunTrace]:
        """执行工作流。

        `stop_after`：只运行到该序号（含），用于“运行至此”。
        `start_at`：该序号之前的节点即使缓存未命中也不执行（直接沿用当前 Packet），
        用于“仅重算下游”——上游结果由调用方提供或来自缓存。
        `reuse_cache`：输入指纹相同则复用缓存输出（标记为缓存）。
        """
        trace = RunTrace()
        started = time.perf_counter()
        current = packet
        for index, node_config in enumerate(self.config.nodes):
            if stop_after is not None and index > stop_after:
                break
            report = NodeReport(
                index=index,
                type_name=node_config.type,
                name=node_config.name,
                input_summary=node_input_summary(current, node_config.type),
            )
            if not node_config.enabled:
                report.status = SKIPPED
                report.output_summary = "已禁用：数据按原样传给下一个节点"
                trace.reports.append(report)
                continue

            input_fp = packet_fingerprint(current)
            report.input_fingerprint = input_fp
            key = self._node_cache_key(index, node_config)
            if reuse_cache and node_config.type != 'save':
                cached = self.cache.get(key)
                if cached is not None and cached[0] == input_fp:
                    run_id = current.run_id
                    models = current.meta.get('models', {})
                    current = _snapshot_packet(cached[1])
                    current.run_id = run_id
                    current.meta['models'] = models
                    report.cached = True
                    report.output_summary = node_output_summary(current, node_config.type)
                    trace.reports.append(report)
                    continue

            # A cache miss upstream must recompute its input, even for run-from-here.
            if node_config.type not in NODE_REGISTRY:
                report.status = FAILED
                report.error = f"未知节点类型 {node_config.type!r}"
                trace.status = FAILED
                trace.reports.append(report)
                trace.elapsed_ms = (time.perf_counter() - started) * 1000
                return current, trace

            node_started = time.perf_counter()
            try:
                node = create_node(node_config.type, node_config.params, node_config.name)
                produced = node.run(current) or current
            except NodeError as exc:
                report.status = FAILED
                report.error = str(exc)
                report.elapsed_ms = (time.perf_counter() - node_started) * 1000
                trace.status = FAILED
                trace.reports.append(report)
                trace.elapsed_ms = (time.perf_counter() - started) * 1000
                return current, trace
            except Exception as exc:  # noqa: BLE001
                report.status = FAILED
                report.error = f"{type(exc).__name__}: {exc}"
                report.elapsed_ms = (time.perf_counter() - node_started) * 1000
                trace.status = FAILED
                trace.reports.append(report)
                trace.elapsed_ms = (time.perf_counter() - started) * 1000
                return current, trace

            current = produced
            report.elapsed_ms = (time.perf_counter() - node_started) * 1000
            report.output_summary = node_output_summary(current, node_config.type)
            trace.reports.append(report)
            if node_config.type != 'save':
                self.cache[key] = (input_fp, _snapshot_packet(current))

        trace.elapsed_ms = (time.perf_counter() - started) * 1000
        return current, trace


def _snapshot_packet(packet):
    models = packet.meta.get('models', {})
    return copy.deepcopy(packet, {id(model): model for model in models.values()})
