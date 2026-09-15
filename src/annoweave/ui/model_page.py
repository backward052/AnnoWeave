"""模型页（M8）：模型状态、依赖、执行设备与试跑。

评审第 8 节要求显示：任务类型、模型路径/哈希、类别表、输入规格、
**实际执行设备**、兼容状态和试跑结果；缺权重、缺依赖、GPU 回退都要可理解。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..inference.backends import available_providers, onnxruntime_available, requested_providers
from ..inference.config import PipelineConfig, load_model_library, save_pipeline_config

READY = "就绪"
MISSING_WEIGHTS = "缺少权重"
MISSING_DEPENDENCY = "缺少依赖"

TASK_NAMES = {
    "yolov5": "检测",
    "yolov8": "检测",
    "yolov8_cls": "分类",
    "yolov5_cls": "分类",
    "yolov8_pose": "姿态",
    "yolov5_pose": "姿态",
    "dwpose": "姿态(自顶向下)",
    "rtmpose": "姿态(自顶向下)",
}


@dataclass
class ModelStatus:
    """单个模型的可用性快照。"""

    name: str
    model_type: str
    role: str
    model_path: str
    classes: list[str] = field(default_factory=list)
    input_size: tuple[int, int] = (640, 640)
    device: str = "cpu"
    requested_provider: str = ""
    actual_provider: str = ""
    status: str = READY
    reason: str = ""
    file_size: int = 0
    sha256: str = ""
    smoke_result: str = ""

    @property
    def ready(self) -> bool:
        return self.status == READY

    def task_text(self) -> str:
        return TASK_NAMES.get(self.model_type, self.model_type or "未知")

    def path_text(self) -> str:
        if not self.model_path:
            return "—"
        name = Path(self.model_path).name
        return f"{name} ({_size_text(self.file_size)})" if self.file_size else name

    def classes_text(self) -> str:
        return "、".join(self.classes) if self.classes else "（未声明类别）"

    def input_text(self) -> str:
        return f"{self.input_size[0]}×{self.input_size[1]}"

    def device_text(self) -> str:
        if self.actual_provider:
            return f"{self.device} → {self.actual_provider.replace('ExecutionProvider', '')}"
        return self.device or "cpu"

    def detail(self) -> str:
        lines = [
            f"模型：{self.name}",
            f"类型：{self.model_type}（{self.task_text()}，角色：{self.role}）",
            f"状态：{self.status}" + (f" — {self.reason}" if self.reason else ""),
            f"路径：{self.model_path or '未设置'}",
            f"大小：{_size_text(self.file_size) if self.file_size else '—'}",
            f"SHA256（前 16 位）：{self.sha256 or '—'}",
            f"类别表（{len(self.classes)}）：{self.classes_text()}",
            f"输入规格：{self.input_text()}",
            f"设备：{self.device_text()}",
        ]
        if self.requested_provider and self.actual_provider and self.requested_provider != self.actual_provider:
            lines.append(
                f"注意：请求 {self.requested_provider}，实际回退到 {self.actual_provider}，"
                "推理仍在 CPU 上执行。"
            )
        if self.smoke_result:
            lines.append(f"试跑：{self.smoke_result}")
        return "\n".join(lines)


def _size_text(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def file_sha256(path: str | Path, limit: int = 8 * 1024 * 1024) -> str:
    """计算权重哈希（大文件只取前 8MB，避免打开模型页时长时间读盘）。"""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            remaining = limit
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
        return digest.hexdigest()[:16]
    except OSError:
        return ""


def preferred_provider(device: str) -> str:
    providers = requested_providers(device)
    return providers[0] if providers else "CPUExecutionProvider"


def describe_model(model, providers: list[str] | None = None) -> ModelStatus:
    """把一个 ModelConfig 变成可展示、可判断的可用性快照。"""
    providers = available_providers() if providers is None else providers
    status = ModelStatus(
        name=model.name,
        model_type=model.model_type,
        role=model.role,
        model_path=str(model.model_path or ""),
        classes=list(model.classes or []),
        input_size=tuple(model.input_size),
        device=str(model.device or "cpu"),
    )
    status.requested_provider = preferred_provider(status.device)
    # 实际执行设备：只有真正可用的 provider 才算数，不能把请求当成已生效
    if status.requested_provider in providers:
        status.actual_provider = status.requested_provider
    elif providers:
        status.actual_provider = "CPUExecutionProvider"

    if not status.model_path:
        status.status = MISSING_WEIGHTS
        status.reason = "未设置模型路径"
    else:
        path = Path(status.model_path)
        if not path.exists():
            status.status = MISSING_WEIGHTS
            status.reason = "权重文件不存在，请重新指定路径"
        else:
            try:
                status.file_size = path.stat().st_size
            except OSError:
                status.file_size = 0
            status.sha256 = file_sha256(path)

    if status.status == READY and not onnxruntime_available():
        status.status = MISSING_DEPENDENCY
        status.reason = "缺少 onnxruntime，无法加载 ONNX 模型"

    if status.status == READY and not status.classes:
        status.reason = "类别表为空：结果中的类别只会显示索引"

    if status.status == READY and status.requested_provider not in providers:
        status.reason = (
            f"请求的 {status.requested_provider} 不可用，推理将回退到 CPU"
            + (f"（可用：{', '.join(providers)}）" if providers else "（未检测到任何 provider）")
        )
    return status


def smoke_test(model) -> str:
    """用一张合成图试跑一次，返回可读结论。"""
    try:
        import numpy as np

        from ..inference.models import create_model

        instance = create_model(model)
        height, width = int(model.input_size[1]), int(model.input_size[0])
        image = np.zeros((max(32, height), max(32, width), 3), dtype=np.uint8)
        if getattr(instance, "top_down", False):
            # 自顶向下姿态模型必须给框；用整幅图当一个人框来验证加载与后处理。
            boxes = [(0.0, 0.0, float(width), float(height))]
            result = instance.predict(image, boxes=boxes)
        else:
            result = instance.predict(image)
        detections = len(getattr(result, "detections", []) or [])
        classification = getattr(result, "classification", None)
        label = getattr(classification, "label", "") if classification else ""
        task = getattr(result, "task_type", "")
        task = getattr(task, "value", None) or str(task)
        text = f"成功：任务={task}，检测框 {detections} 个"
        if label:
            text += f"，分类={label}"
        keypoint_count = sum(len(getattr(det, "keypoints", []) or []) for det in getattr(result, "detections", []) or [])
        if keypoint_count:
            text += f"，关键点 {keypoint_count} 个"
        return text
    except Exception as exc:  # noqa: BLE001
        return f"失败：{type(exc).__name__}: {exc}"


class ModelPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.statuses: list[ModelStatus] = []
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel("模型库")
        title.setProperty("role", "title")
        top.addWidget(title)
        manage_button = QPushButton("＋ 添加模型")
        manage_button.clicked.connect(lambda: self._edit_model(False))
        edit_button = QPushButton("编辑选中模型")
        edit_button.clicked.connect(lambda: self._edit_model(True))
        top.addWidget(manage_button)
        top.addWidget(edit_button)
        remove_button = QPushButton("移除")
        remove_button.clicked.connect(self._remove_model)
        top.addWidget(remove_button)
        help_button = QPushButton("入门教程")
        help_button.clicked.connect(self._open_guide)
        top.addWidget(help_button)
        top.addStretch()
        self.smoke_button = QPushButton("试跑选中模型")
        self.smoke_button.setToolTip("用一张合成图实际加载并推理一次，验证权重与依赖")
        self.smoke_button.clicked.connect(self.run_smoke_test)
        top.addWidget(self.smoke_button)
        self.refresh_button = QPushButton("重新检测")
        self.refresh_button.clicked.connect(self.reload)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)

        self.provider_label = QLabel("执行后端：—")
        self.provider_label.setProperty("role", "muted")
        self.provider_label.setWordWrap(True)
        layout.addWidget(self.provider_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["模型", "类型", "状态", "设备", "输入", "类别数"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.currentCellChanged.connect(lambda *_: self._show_detail())
        self.table.cellDoubleClicked.connect(lambda *_: self._edit_model(True))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setShowGrid(False)
        self.table.setMinimumWidth(600)
        content = QSplitter(Qt.Orientation.Horizontal)
        content.addWidget(self.table)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选中一个模型查看路径、哈希、类别表与试跑结果。")
        self.detail.setMinimumWidth(320)
        content.addWidget(self.detail)
        content.setSizes([800, 520])
        layout.addWidget(content, 1)

        self.status = QLabel("—")
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)

        note = QLabel(
            "运行工作流前请先在这里确认目标模型为“就绪”；缺少权重或 onnxruntime 时，"
            "工作流页会直接报出失败节点。"
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        # ---------------- 插件（评审第 8 节） ----------------
        plugin_top = QHBoxLayout()
        plugin_title = QPushButton("插件与扩展（展开）")
        plugin_title.setCheckable(True)
        plugin_title.setProperty("role", "title")
        plugin_top.addWidget(plugin_title)
        self.plugin_path_label = QLabel("")
        self.plugin_path_label.setProperty("role", "muted")
        plugin_top.addWidget(self.plugin_path_label, 1)
        scan_button = QPushButton("重新扫描插件")
        scan_button.clicked.connect(self.reload_plugins)
        plugin_top.addWidget(scan_button)
        layout.addLayout(plugin_top)

        self.plugin_table = QTableWidget(0, 5)
        self.plugin_table.setHorizontalHeaderLabels(["插件", "类型", "版本", "状态", "能力 / 原因"])
        self.plugin_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.plugin_table, 2)

        self.plugin_summary = QLabel("")
        self.plugin_summary.setProperty("role", "muted")
        self.plugin_summary.setWordWrap(True)
        layout.addWidget(self.plugin_summary)
        for widget in (self.plugin_table, self.plugin_summary, self.plugin_path_label, scan_button):
            widget.setVisible(False)
            plugin_title.toggled.connect(widget.setVisible)

        self.reload()
        self.reload_plugins()

    def _open_guide(self):
        from PySide6.QtWidgets import QTextBrowser
        guide = Path(__file__).resolve().parents[1] / "assets" / "getting-started.zh-CN.md"
        dialog = QDialog(self)
        dialog.setWindowTitle("AnnoWeave · 入门教程")
        dialog.resize(920, 720)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setSearchPaths([str(guide.parent)])
        browser.setMarkdown(guide.read_text(encoding='utf-8') if guide.exists() else '未找到内置教程，请查看项目 docs/zh-CN 目录。')
        layout.addWidget(browser)
        dialog.exec()

    def _remove_model(self):
        from ..inference.config import save_model_library
        from ..workflow.templates import default_workflows
        from ..workflow.workflow import load_workflow_catalog
        selected = self._selected_status()
        if selected is None:
            return
        used = [w.name for w in (load_workflow_catalog() or default_workflows())
                if any(isinstance(n.params.get('model'), dict) and
                       n.params['model'].get('name') == selected.name for n in w.nodes)]
        if used:
            QMessageBox.information(self, '模型仍在使用', '请先在下列工作流中替换引用，再移除：\n' + '\n'.join(used))
            return
        if QMessageBox.question(self, '移除模型', f'从模型库移除 {selected.name}？权重文件会保留。') != QMessageBox.StandardButton.Yes:
            return
        try:
            save_model_library([m for m in load_model_library() if m.name != selected.name])
        except Exception as exc:
            QMessageBox.warning(self, '移除失败', str(exc))
            return
        self.reload()

    # ----------------------------- 插件 ----------------------------- #
    def reload_plugins(self):
        from ..plugins import PluginHost, default_plugin_root

        self.plugin_host = PluginHost()
        statuses = self.plugin_host.discover()
        self.plugin_path_label.setText(f"插件目录：{default_plugin_root()}")
        self.plugin_table.setRowCount(0)
        for status in statuses:
            manifest = status.manifest
            row = self.plugin_table.rowCount()
            self.plugin_table.insertRow(row)
            label = manifest.name if manifest else Path(status.path).name
            values = (
                label,
                manifest.kind if manifest else "—",
                manifest.version if manifest else "—",
                status.state,
                ("、".join(manifest.capabilities) if manifest and manifest.capabilities else status.reason),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if not status.healthy and column == 3:
                    item.setForeground(Qt.GlobalColor.red)
                self.plugin_table.setItem(row, column, item)
        self.plugin_table.resizeColumnsToContents()
        self.plugin_summary.setText(self.plugin_host.summary())

    # ----------------------------- 检测 ----------------------------- #
    def reload(self):
        try:
            library = load_model_library()
        except Exception as exc:  # noqa: BLE001
            library = []
            self.provider_label.setText(f"读取模型库失败：{exc}")
        providers = available_providers()
        self.statuses = [describe_model(model, providers) for model in library]
        self._render()
        self._render_providers(providers)

    def _render_providers(self, providers: list[str]):
        if not onnxruntime_available():
            self.provider_label.setText(
                "执行后端：未安装 onnxruntime — 模型无法加载。"
                "请在联网环境执行 pip install onnxruntime（或 onnxruntime-gpu）。"
            )
        elif providers:
            self.provider_label.setText(
                "执行后端：ONNX Runtime，可用 provider：" + "、".join(providers)
                + "。设备列显示可用后端预估，请通过试跑验证模型兼容性。"
            )
        else:
            self.provider_label.setText(
                "执行后端：ONNX Runtime 已安装，但没有报告任何 provider（异常状态）。"
            )

    def _render(self):
        self.table.setRowCount(0)
        for status in self.statuses:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                status.name,
                status.task_text(),
                status.status,
                status.device_text(),
                status.input_text(),
                str(len(status.classes)),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if status.status != READY and column == 2:
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        if self.statuses:
            self.table.setCurrentCell(0, 0)
            ready = sum(1 for status in self.statuses if status.ready)
            self.status.setText(
                f"共 {len(self.statuses)} 个模型，就绪 {ready} 个；"
                "哈希用于标识权重版本，试跑用于验证依赖与权重是否真的可用。"
            )
        else:
            self.status.setText("模型库为空。")
            self.detail.setPlainText(
                "模型库为空。请点“＋ 添加模型”，选择本机 ONNX 权重并填写类别表。"
            )
        self._show_detail()

    def _selected_status(self) -> ModelStatus | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.statuses):
            return self.statuses[row]
        return None

    def _show_detail(self):
        status = self._selected_status()
        if status is None:
            self.detail.clear()
            return
        self.detail.setPlainText(status.detail())

    # ----------------------------- 试跑 ----------------------------- #
    def run_smoke_test(self):
        status = self._selected_status()
        if status is None:
            QMessageBox.information(self, "提示", "请先选中一个模型")
            return
        if not status.ready:
            QMessageBox.warning(
                self,
                "无法试跑",
                f"{status.name} 当前状态为“{status.status}”：{status.reason}",
            )
            return
        model = next((m for m in load_model_library() if m.name == status.name), None)
        if model is None:
            QMessageBox.warning(self, "无法试跑", "模型已不在模型库中，请重新检测。")
            return
        from ..workers import FunctionWorker
        self.smoke_button.setEnabled(False)
        self.smoke_button.setText("模型加载与试跑中……")
        self._smoke_worker = FunctionWorker(smoke_test, model)
        self._smoke_worker.signals.finished.connect(self._smoke_finished)
        self._smoke_worker.signals.failed.connect(self._smoke_finished)
        self._smoke_name = model.name
        QThreadPool.globalInstance().start(self._smoke_worker)

    def _smoke_finished(self, result):
        self.smoke_button.setEnabled(True)
        self.smoke_button.setText("试跑选中模型")
        for status in self.statuses:
            if status.name == self._smoke_name:
                status.smoke_result = str(result)
        self.status.setText(f"{self._smoke_name}：{result}")
        self._show_detail()

    def _edit_model(self, edit=False):
        from ..inference.config import save_model_library
        from ..video_inference.dialogs import ModelConfigDialog
        selected = self._selected_status() if edit else None
        if edit and selected is None:
            return
        config = PipelineConfig(models=load_model_library())
        model = next((m for m in config.models if selected and m.name == selected.name), None)
        dialog = ModelConfigDialog(self, model)
        if model:
            dialog.name_edit.setReadOnly(True)
            dialog.name_edit.setToolTip("名称是工作流引用标识；修改参数和权重不会改变已有引用。")
        while dialog.exec() == QDialog.DialogCode.Accepted:
            value = dialog.values()
            if not model and any(m.name.casefold() == value.name.casefold() for m in config.models):
                QMessageBox.warning(self, "名称重复", "请使用不同的模型名称。")
                continue
            config.models = [value if model and m.name == model.name else m for m in config.models]
            if not model:
                config.models.append(value)
            try:
                save_model_library(config.models)
            except Exception as exc:
                QMessageBox.warning(self, "保存失败", str(exc))
                return
            self.reload()
            for row, status in enumerate(self.statuses):
                if status.name == value.name:
                    self.table.setCurrentCell(row, 0)
            return

    # ----------------------------- 模型库管理 ----------------------------- #
    def _manage(self):
        from ..video_inference.dialogs import PipelineConfigDialog

        library = load_model_library()
        config = PipelineConfig(models=list(library))
        dialog = PipelineConfigDialog(config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                save_pipeline_config(dialog.values())
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "保存失败", str(exc))
                return
            self.reload()

