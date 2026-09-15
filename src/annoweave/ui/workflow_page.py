"""工作流页（M7）：顺序流水线编辑器 + 节点执行检查。

构建器内部编辑独立草稿，取消不改变活动工作流（AR-06）。
下方“节点执行”面板展示每个节点的输入/输出摘要、耗时与状态，
并支持“运行至此”和“仅重算下游”（依赖输入版本缓存，不复用过期输出）。
"""

from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..inference.config import load_model_library
from ..inference.datatypes import Frame
from ..video_inference.workflow_builder import WorkflowBuilderView
from ..workflow.inspection import TracedWorkflowRunner
from ..workflow.packet import Packet
from ..workflow.templates import starter_workflow_config
from ..workflow.workflow import load_workflow_catalog, save_workflow_catalog


class WorkflowPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.node_cache: dict = {}
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        new_button = QPushButton("＋ 创建工作流")
        new_button.setProperty("accent", "true")
        new_button.clicked.connect(self._create_workflow)
        top.addWidget(new_button)
        top.addWidget(QLabel("模板"))
        self.template_combo = QComboBox()
        self._catalog = load_workflow_catalog()
        if not self._catalog:
            self._catalog = [starter_workflow_config()]
            self.template_combo.addItem("空白工作流", 0)
        else:
            for index, config in enumerate(self._catalog):
                self.template_combo.addItem(config.name or f"工作流 {index + 1}", index)
        self.template_combo.currentIndexChanged.connect(self._switch_template)
        self._active_index = 0
        top.addWidget(self.template_combo)
        reload_button = QPushButton("重新载入模板")
        reload_button.clicked.connect(self._reload_templates)
        top.addWidget(reload_button)
        precheck_button = QPushButton("运行前检查")
        precheck_button.clicked.connect(lambda: self.builder.run_precheck())
        top.addWidget(precheck_button)
        top.addStretch()
        save_button = QPushButton("保存工作流")
        save_button.setProperty("accent", "true")
        save_button.clicked.connect(self._save)
        top.addWidget(save_button)
        layout.addLayout(top)

        model_names = [m.name for m in load_model_library()]
        self.builder = WorkflowBuilderView(self._catalog[0], model_names)
        # This page owns file actions; do not show a second competing toolbar.
        for index in range(self.builder._topbar.count()):
            widget = self.builder._topbar.itemAt(index).widget()
            if widget:
                widget.hide()
        files = QPushButton('导入 / 导出')
        menu = QMenu(files)
        menu.addAction('导入工作流 JSON', self.builder._open_file)
        menu.addAction('导出草稿 JSON', self.builder._save_file)
        files.setMenu(menu)
        top.insertWidget(1, files)

        # 节点执行面板
        lower = QWidget()
        self.execution_panel = lower
        lower.setVisible(False)
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        run_row = QHBoxLayout()
        self.probe_image = None
        probe_button = QPushButton('选择试跑图片')
        probe_button.clicked.connect(self._pick_probe_image)
        run_row.addWidget(probe_button)
        self.run_all_button = QPushButton("运行整条流水线")
        self.run_all_button.clicked.connect(lambda: self.run_workflow(stop_after=None))
        self.run_here_button = QPushButton("运行至此")
        self.run_here_button.setToolTip("只运行到当前选中的节点，用缓存跳过未变化的输入")
        self.run_here_button.clicked.connect(self._run_to_selected)
        self.run_downstream_button = QPushButton("仅重算下游")
        self.run_downstream_button.setToolTip("从选中节点开始重算，前面未变化的节点复用缓存")
        self.run_downstream_button.clicked.connect(self._run_from_selected)
        run_row.addWidget(self.run_all_button)
        run_row.addWidget(self.run_here_button)
        run_row.addWidget(self.run_downstream_button)
        run_row.addStretch()
        self.run_label = QLabel("尚未运行")
        self.run_label.setProperty("role", "muted")
        run_row.addWidget(self.run_label)
        lower_layout.addLayout(run_row)

        self.node_table = QTableWidget(0, 5)
        self.node_table.setHorizontalHeaderLabels(["节点", "类型", "状态", "耗时", "输出预览"])
        self.node_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.node_table.currentCellChanged.connect(lambda *_: self._show_selected_detail())
        lower_layout.addWidget(self.node_table, 1)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(110)
        self.detail.setPlaceholderText("选中一个节点查看它的输入/输出摘要与耗时。")
        lower_layout.addWidget(self.detail)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.builder)
        splitter.addWidget(lower)
        splitter.setSizes([440, 260])
        layout.addWidget(splitter, 1)
        debug_button = QPushButton("展开节点调试")
        debug_button.setCheckable(True)
        debug_button.toggled.connect(lower.setVisible)
        debug_button.toggled.connect(lambda checked: debug_button.setText("收起节点调试" if checked else "展开节点调试"))
        layout.addWidget(debug_button)

        self.status = QLabel("编辑的是草稿；点“保存工作流”才会写入工作流目录。")
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)

    # ----------------------------- 模板 ----------------------------- #
    def _create_workflow(self):
        from .workflow_wizard import WorkflowWizard
        wizard = WorkflowWizard(load_model_library(), self)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        if self.builder._dirty and QMessageBox.question(
            self, "替换当前草稿？", "当前草稿尚未保存，是否用新工作流替换？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.builder.model_names = [m.name for m in load_model_library()]
        self.builder.config = wizard.values()
        self.builder._selected_uuid = None
        self.builder._rebuild_scene()
        self.builder._mark_dirty()
        self.builder.fit_to_content()
        self.status.setText("新工作流已生成，检查节点后点击「保存工作流」。")

    def _switch_template(self, _index: int):
        if self.builder._dirty:
            answer = QMessageBox.question(
                self,
                "放弃未保存的草稿？",
                "当前草稿有未保存的修改，切换模板会丢弃它们。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.template_combo.blockSignals(True)
                self.template_combo.setCurrentIndex(self._active_index)
                self.template_combo.blockSignals(False)
                return
        index = int(self.template_combo.currentData() or 0)
        if 0 <= index < len(self._catalog):
            self._active_index = index
            self.builder.config = copy.deepcopy(self._catalog[index])
            self.builder._selected_uuid = None
            self.builder._dirty = False
            self.builder.dirty_label.setText("")
            self.builder._rebuild_scene()
            self.builder.fit_to_content()
        self.node_cache.clear()
        self.node_table.setRowCount(0)
        self.run_label.setText("尚未运行")

    def _reload_templates(self):
        self._catalog = load_workflow_catalog() or [starter_workflow_config()]
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        for index, config in enumerate(self._catalog):
            self.template_combo.addItem(config.name or f"工作流 {index + 1}", index)
        self.template_combo.blockSignals(False)
        self.status.setText(f"已重新载入 {len(self._catalog)} 个工作流模板。")

    # ----------------------------- 执行 ----------------------------- #
    def _probe_frame(self) -> Frame:
        """无素材时用一个空帧做结构检查，让用户能先看清节点行为。"""
        return Frame(index=0, timestamp=0.0, image=self.probe_image if self.probe_image is not None else np.zeros((64, 64, 3), dtype=np.uint8))

    def _pick_probe_image(self):
        from ..media import read_image
        path, _ = QFileDialog.getOpenFileName(self, '选择用于试跑的图片', '', '图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if path:
            try:
                self.probe_image = read_image(path)
            except ValueError as exc:
                self.status.setText(str(exc))
                return
            self.node_cache.clear()
            self.status.setText('试跑图片：' + path)

    def run_workflow(self, stop_after=None, start_at: int = 0, reuse_cache: bool = True):
        config = self.builder.values()
        problems = self.builder.validate_nodes()
        if problems:
            detail = "\n".join(f"第 {i} 个节点：{m}" for i, m in problems)
            self.status.setText(f"运行前检查发现 {len(problems)} 个问题：{detail}")
        models = {model.name: model for model in load_model_library()}
        packet = Packet(frame=self._probe_frame(), meta={"models": models})
        runner = TracedWorkflowRunner(config, self.node_cache)
        packet, trace = runner.run(
            packet, stop_after=stop_after, reuse_cache=reuse_cache, start_at=start_at
        )
        self._render_trace(trace)
        self.status.setText(('空白帧结构预演（非真实素材推理） · ' if self.probe_image is None else '') + trace.summary())
        return packet, trace

    def _run_to_selected(self):
        index = self._selected_node_index()
        if index < 0:
            QMessageBox.information(self, "提示", "请先在画布或表格里选中一个节点")
            return
        self.run_workflow(stop_after=index)

    def _run_from_selected(self):
        index = self._selected_node_index()
        if index < 0:
            QMessageBox.information(self, "提示", "请先选中一个节点")
            return
        # 仅重算下游：选中节点之前的部分沿用已有结果/缓存，从选中节点开始重算
        self.run_workflow(start_at=index)

    def _selected_node_index(self) -> int:
        row = self.node_table.currentRow()
        if row >= 0:
            return row
        return self.builder._current_index()

    # ----------------------------- 展示 ----------------------------- #
    def _render_trace(self, trace):
        self.node_table.setRowCount(0)
        for report in trace.reports:
            row = self.node_table.rowCount()
            self.node_table.insertRow(row)
            values = (
                report.label,
                report.type_name,
                report.status_text(),
                f"{report.elapsed_ms:.0f}ms",
                report.output_summary,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if report.status == "失败":
                    item.setForeground(Qt.GlobalColor.red)
                self.node_table.setItem(row, column, item)
        self.node_table.resizeColumnsToContents()
        cached = sum(1 for report in trace.reports if report.cached)
        self.run_label.setText(
            f"{trace.status} · {len(trace.reports)} 个节点 · 缓存 {cached} 个 · {trace.elapsed_ms:.0f}ms"
        )
        if trace.reports:
            self.node_table.setCurrentCell(0, 0)
            self._show_selected_detail()
        _ = trace

    def _show_selected_detail(self):
        row = self.node_table.currentRow()
        if row < 0:
            self.detail.clear()
            return
        label_item = self.node_table.item(row, 0) or QTableWidgetItem("")
        type_item = self.node_table.item(row, 2) or QTableWidgetItem("")
        input_item = self.node_table.item(row, 4) or QTableWidgetItem("")
        self.detail.setPlainText(
            f"节点 {row + 1}：{label_item.text()}\n"
            f"状态：{type_item.text()}\n"
            f"输出预览：{input_item.text()}\n\n"
            "提示：输入未变化的节点会复用缓存（状态显示“缓存”），"
            "因此“运行至此/仅重算下游”不会沿用过期输出。"
        )

    # ----------------------------- 保存 ----------------------------- #
    def _save(self):
        problems = self.builder.validate_nodes()
        if problems:
            detail = "\n".join(f"第 {i} 个节点：{m}" for i, m in problems)
            answer = QMessageBox.question(
                self,
                "工作流可能无法运行",
                f"发现 {len(problems)} 个问题：\n\n{detail}\n\n仍然保存吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            config = self.builder.values()
            if not config.workflow_id:
                import uuid
                config.workflow_id = uuid.uuid4().hex
            catalog = load_workflow_catalog()
            for index, existing in enumerate(catalog):
                if existing.workflow_id == config.workflow_id:
                    catalog[index] = config
                    break
            else:
                catalog.append(config)
            save_workflow_catalog(catalog)
            self.builder.config.workflow_id = config.workflow_id
            self._catalog = catalog
            self.template_combo.blockSignals(True)
            self.template_combo.clear()
            for index, item in enumerate(catalog):
                self.template_combo.addItem(item.name, index)
                if item.workflow_id == config.workflow_id:
                    self._active_index = index
            self.template_combo.setCurrentIndex(self._active_index)
            self.template_combo.blockSignals(False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.builder._dirty = False
        self.builder.dirty_label.setText("")
        self.status.setText("已保存到工作流目录。")
        QMessageBox.information(self, "工作流", "已保存到工作流目录")
