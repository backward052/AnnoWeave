from __future__ import annotations

import copy
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..workflow.node import NODE_REGISTRY
from ..workflow.workflow import NodeConfig, WorkflowConfig, load_workflow, save_workflow
from .workflow_editor import build_param_form

_ROWS = 72
_COL_H = 150


class BuildNodeItem(QGraphicsItem):
    """画布上的一个节点卡片。"""

    def __init__(self, node: NodeConfig, x=0, y=0):
        super().__init__()
        self.node = node
        self.setPos(x, y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self._w = 260
        self._h = 72

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isSelected():
            painter.setBrush(QBrush(QColor("#2f6fd6")))
        elif not getattr(self.node, "enabled", True):
            painter.setBrush(QBrush(QColor("#2a2e36")))
        else:
            painter.setBrush(QBrush(QColor("#3a3f4b")))
        painter.setPen(QPen(QColor("#6c7280"), 1))
        painter.drawRoundedRect(QRectF(0, 0, self._w, self._h), 8, 8)
        disabled = not getattr(self.node, "enabled", True)
        painter.setPen(QColor("#8a93a5") if disabled else QColor("white"))
        painter.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.Bold))
        title = self.node.name or self.node.type
        if disabled:
            title += "（已禁用）"
        painter.drawText(QRectF(8, 6, self._w - 16, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        painter.setPen(QColor("#a9b0bd"))
        painter.setFont(QFont("Microsoft YaHei", 8))
        meta = NODE_REGISTRY.get(self.node.type)
        subtitle = self.node.params.get('model', {})
        subtitle = subtitle.get('name', '') if isinstance(subtitle, dict) else str(subtitle)
        painter.drawText(QRectF(8, 36, self._w - 16, 20), Qt.AlignmentFlag.AlignLeft,
                         subtitle or (meta.Meta.get('display_name', self.node.type) if meta else self.node.type))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.scene().update()
        return super().itemChange(change, value)


class WorkflowBuilderView(QWidget):
    """新手“搭积木”工作流构建器：左侧节点面板，中间可视化节点+连线，右侧属性面板。

    编辑的是传入配置的**独立副本**：取消/关闭弹窗不会改动活动工作流（AR-06）。
    只有调用方在用户确认后取 `values()` 才会得到新配置。
    """

    def __init__(self, config: WorkflowConfig, model_names: list[str], parent=None):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self._source_config = config
        self.model_names = model_names
        self._items: dict[str, BuildNodeItem] = {}
        self._arrows: list[QGraphicsPathItem] = []
        self._selected_uuid: Optional[str] = None
        root = QVBoxLayout(self)
        layout = QHBoxLayout()

        # 左侧：节点面板（搜索 + 分类，评审第 8 节）
        left = QVBoxLayout()
        left.addWidget(QLabel("模块库 · 双击添加"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索节点名称、类型或标签")
        self.search_edit.textChanged.connect(self._filter_palette)
        left.addWidget(self.search_edit)
        self.palette = QListWidget()
        # 分组直接来自节点自己的 Meta['category']：新增节点不需要改这里。
        order = ('输入', '模型推理', '姿态与关键点', '关联与裁剪', '规则判定', '结果输出')
        grouped: dict[str, list[str]] = {}
        for type_name in NODE_REGISTRY:
            meta = NODE_REGISTRY[type_name].Meta
            grouped.setdefault(meta.get('category') or '其他', []).append(type_name)
        listed = set()
        for title in [name for name in order if name in grouped] + \
                [name for name in grouped if name not in order]:
            heading = QListWidgetItem(title)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            heading.setForeground(QColor('#19B99A'))
            self.palette.addItem(heading)
            for type_name in sorted(grouped[title], key=lambda key: NODE_REGISTRY[key].Meta.get('display_name', key)):
                self._add_palette_item(type_name)
                listed.add(type_name)
        for type_name in sorted(set(NODE_REGISTRY) - listed):
            self._add_palette_item(type_name)
        self.palette.setSpacing(5)
        self.palette.itemDoubleClicked.connect(self._palette_clicked)
        add_button = QPushButton("添加选中模块 →")
        add_button.clicked.connect(lambda: self._palette_clicked(self.palette.currentItem()) if self.palette.currentItem() else None)
        left.addWidget(self.palette)
        left.addWidget(add_button)
        self.palette_hint = QLabel("")
        self.palette_hint.setProperty("role", "muted")
        self.palette_hint.setWordWrap(True)
        left.addWidget(self.palette_hint)
        left_host = QWidget()
        left_host.setLayout(left)
        left_host.setFixedWidth(215)
        layout.addWidget(left_host)

        # 中间：可视化画布
        self.scene = QGraphicsScene()
        self.scene.setSceneRect(0, 0, 360, 9000)
        self.scene.selectionChanged.connect(self._on_selection)
        self.view = QGraphicsView(self.scene)
        self.view.setBackgroundBrush(QBrush(QColor("#20242b")))
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout.addWidget(self.view, 4)

        # 右侧：属性面板
        right = QVBoxLayout()
        right.addWidget(QLabel("节点属性（选中后编辑）"))
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._on_name_changed)
        right.addWidget(QLabel("名称"))
        right.addWidget(self.name_edit)
        self.enabled_check = QCheckBox("启用该节点")
        self.enabled_check.setToolTip("禁用后该节点不执行，数据按原样传给下一个节点")
        self.enabled_check.toggled.connect(self._on_enabled_changed)
        right.addWidget(self.enabled_check)
        self.param_host = QWidget()
        right.addWidget(self.param_host)
        self._param_form = QFormLayout(self.param_host)
        self._param_widgets = {}
        action_buttons = QHBoxLayout()
        for text, handler in (
            ("上移", lambda: self._move(-1)),
            ("下移", lambda: self._move(1)),
            ("复制", self._duplicate_selected),
            ("删除", self._delete_selected),
        ):
            b = QPushButton(text)
            b.clicked.connect(handler)
            action_buttons.addWidget(b)
        right.addLayout(action_buttons)
        help_label = QLabel("从上到下依次执行。用上移 / 下移调整模块顺序。")
        help_label.setWordWrap(True)
        right.addWidget(help_label)
        right.addStretch()
        right_host = QWidget()
        right_host.setLayout(right)
        right_host.setFixedWidth(320)
        layout.addWidget(right_host)

        # 顶部操作区：用 QVBoxLayout 真正置顶，避免 insertLayout 把横排挤进左列
        self._topbar = QHBoxLayout()
        for text, handler, tip in (
            ("新建空白", self._load_starter, "用空白输入与预览节点覆盖当前草稿"),
            ("打开文件", self._open_file, "从 JSON 载入工作流到草稿"),
            ("保存文件", self._save_file, "把当前草稿另存为 JSON"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            self._topbar.addWidget(b)
        self._topbar.addStretch()
        self._dirty = False
        self.dirty_label = QLabel("")
        self.dirty_label.setProperty("role", "muted")
        self._topbar.addWidget(self.dirty_label)
        validate_button = QPushButton("运行前检查")
        validate_button.setToolTip("检查未知节点、缺模型、缺参数与空工作流")
        validate_button.clicked.connect(self.run_precheck)
        self._topbar.addWidget(validate_button)

        root.addLayout(self._topbar)
        root.addLayout(layout, 1)

        self._rebuild_scene()

    def run_precheck(self) -> list[tuple[int, str]]:
        """运行前检查并向用户展示结果，返回问题列表。"""
        problems = self.validate_nodes()
        if not problems:
            QMessageBox.information(self, "运行前检查", "未发现问题，可以运行。")
            return problems
        detail = "\n".join(f"第 {index} 个节点：{message}" for index, message in problems)
        QMessageBox.warning(self, "运行前检查未通过", f"发现 {len(problems)} 个问题：\n\n{detail}")
        return problems

    # ----------------------- 节点面板 ----------------------- #
    def _add_palette_item(self, type_name: str):
        meta = NODE_REGISTRY[type_name].Meta
        tags = ",".join(meta.get("tags", []))
        item = QListWidgetItem(meta.get('display_name', type_name))
        item.setData(Qt.ItemDataRole.UserRole, type_name)
        item.setData(Qt.ItemDataRole.UserRole + 1, f"{type_name} {meta.get('display_name', '')} {tags}")
        item.setToolTip(meta.get("description", ""))
        self.palette.addItem(item)

    def _filter_palette(self, text: str):
        needle = text.strip().casefold()
        visible = 0
        for row in range(self.palette.count()):
            item = self.palette.item(row)
            haystack = str(item.data(Qt.ItemDataRole.UserRole + 1) or "").casefold()
            match = not needle or needle in haystack
            item.setHidden(not match)
            visible += int(match)
        self.palette_hint.setText("" if visible else "没有匹配的节点，试试清空搜索。")


    # ----------------------- 场景构建 ----------------------- #
    def _rebuild_scene(self):
        self.scene.clear()
        self._items.clear()
        self._arrows.clear()
        for index, node in enumerate(self.config.nodes):
            item = BuildNodeItem(node, 40, 24 + index * 104)
            self._items[str(index)] = item
            self.scene.addItem(item)
        self.view.setScene(self.scene)
        self._draw_arrows()
        self._load_props_for_selection()
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-24, -24, 24, 24))

    def fit_to_content(self):
        """首次打开即适配内容，避免固定 9000 高场景产生大片空白（评审第 8 节）。"""
        rect = self.scene.itemsBoundingRect()
        if rect.isNull() or rect.isEmpty():
            self.view.resetTransform()
            return
        rect = rect.adjusted(-40, -40, 40, 60)
        self.scene.setSceneRect(rect)
        self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        # Keep labels readable on long workflows; scroll instead of shrinking to postage stamps.
        if self.view.transform().m11() < 0.8:
            self.view.resetTransform()
            self.view.centerOn(170, 140)
        if self.view.transform().m11() > 1.0:
            self.view.resetTransform()
            self.view.centerOn(rect.center())

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            self.fit_to_content()

    def _mark_dirty(self):
        self._dirty = True
        self.dirty_label.setText("草稿已修改，保存工作流后才生效")

    # ----------------------- 运行前检查 ----------------------- #
    def validate_nodes(self) -> list[tuple[int, str]]:
        """返回 [(节点序号, 问题)]；同时检查模型与上下游图层依赖。"""
        from ..workflow.validation import validate_workflow

        return [
            (issue.node_index, issue.message)
            for issue in validate_workflow(self.config, {name: None for name in self.model_names or []})
            if issue.severity == "error"
        ]

    def _draw_arrows(self):
        for arrow in self._arrows:
            self.scene.removeItem(arrow)
        self._arrows.clear()
        nodes = sorted(self.config.nodes, key=lambda n: self.config.nodes.index(n))
        for index in range(len(nodes) - 1):
            item_a = self._items.get(str(index))
            item_b = self._items.get(str(index + 1))
            if item_a is None or item_b is None:
                continue
            x = item_a.x() + item_a.boundingRect().width() / 2
            y_a = item_a.y() + item_a.boundingRect().height()
            y_b = item_b.y()
            path_item = QGraphicsPathItem()
            from PySide6.QtGui import QPainterPath

            path = QPainterPath()
            path.moveTo(x, y_a)
            path.lineTo(x, y_b)
            path_item.setPath(path)
            pen = QPen(QColor("#8a93a5"), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            path_item.setPen(pen)
            self.scene.addItem(path_item)
            self._arrows.append(path_item)

    # ----------------------- 交互 ----------------------- #
    def _palette_clicked(self, item):
        type_name = item.data(Qt.ItemDataRole.UserRole)
        if not type_name:
            return
        idx = len(self.config.nodes)
        meta = NODE_REGISTRY[type_name].Meta
        params = {key: copy.deepcopy(spec['default']) for key, spec in meta.get('param_schema', {}).items() if 'default' in spec}
        if type_name == 'inference':
            import uuid
            params['model_key'] = 'model_' + uuid.uuid4().hex[:8]
        node = NodeConfig(type=type_name, name=meta.get("display_name", type_name), params=params)
        self.config.nodes.append(node)
        self._mark_dirty()
        self._rebuild_scene()
        self._select_index(idx)

    def _on_selection(self):
        from shiboken6 import isValid
        if not isValid(self.scene):
            return
        selected = [item for item in self.scene.selectedItems() if isinstance(item, BuildNodeItem)]
        if not selected:
            self._load_props(user_uuid=None)
            return
        item = selected[0]
        for key, it in self._items.items():
            if it is item:
                self._load_props(user_uuid=key)
                return

    def _select_index(self, index: int):
        key = str(index)
        item = self._items.get(key)
        if item is not None:
            self.scene.clearSelection()
            item.setSelected(True)
            self.view.centerOn(item)
            self._load_props(user_uuid=key)

    def _current_index(self) -> int:
        for index, it in self._items.items():
            if it.isSelected():
                return int(index)
        return -1

    def _move(self, offset: int):
        index = self._current_index()
        if index < 0:
            return
        new = index + offset
        if new < 0 or new >= len(self.config.nodes):
            return
        nodes = self.config.nodes
        nodes[index], nodes[new] = nodes[new], nodes[index]
        self._mark_dirty()
        self._rebuild_scene()
        self._select_index(new)

    def _duplicate_selected(self):
        index = self._current_index()
        if index < 0:
            return
        clone = copy.deepcopy(self.config.nodes[index])
        clone.name = (clone.name or clone.type) + " 副本"
        self.config.nodes.insert(index + 1, clone)
        self._mark_dirty()
        self._rebuild_scene()
        self._select_index(index + 1)

    def _delete_selected(self):
        index = self._current_index()
        if index < 0:
            return
        del self.config.nodes[index]
        self._mark_dirty()
        self._rebuild_scene()

    # ----------------------- 属性面板 ----------------------- #
    def _load_props(self, user_uuid: Optional[str]):
        if user_uuid is not None and int(user_uuid) >= len(self.config.nodes):
            user_uuid = None
        self._selected_uuid = user_uuid
        # 清空旧的 param 控件
        while self._param_form.count():
            item = self._param_form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._param_widgets.clear()
        self.enabled_check.blockSignals(True)
        if user_uuid is None:
            self.name_edit.setEnabled(False)
            self.name_edit.setText("")
            self.enabled_check.setEnabled(False)
            self.enabled_check.setChecked(False)
            self.enabled_check.blockSignals(False)
            return
        index = int(user_uuid)
        if index >= len(self.config.nodes):
            self.enabled_check.blockSignals(False)
            return
        node = self.config.nodes[index]
        cls = NODE_REGISTRY.get(node.type)
        meta = cls.Meta if cls else {}
        self.name_edit.setEnabled(True)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(node.name or "")
        self.name_edit.blockSignals(False)
        self.enabled_check.setEnabled(True)
        self.enabled_check.setChecked(bool(node.enabled))
        self.enabled_check.blockSignals(False)
        self._param_widgets = build_param_form(
            self._param_form, meta.get("param_schema", {}), node.params, self.model_names
        )
        for widget in self._param_widgets.values():
            if hasattr(widget, "currentIndexChanged"):
                widget.currentIndexChanged.connect(self._on_param_changed)
            elif hasattr(widget, "textChanged"):
                widget.textChanged.connect(self._on_param_changed)
            elif hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._on_param_changed)
            elif hasattr(widget, "toggled"):
                widget.toggled.connect(self._on_param_changed)

    def _on_enabled_changed(self, enabled: bool):
        if self._selected_uuid is None:
            return
        index = int(self._selected_uuid)
        if 0 <= index < len(self.config.nodes):
            self.config.nodes[index].enabled = bool(enabled)
            self._mark_dirty()
            self._rebuild_scene()

    def _load_props_for_selection(self):
        self._load_props(user_uuid=self._selected_uuid if self._selected_uuid else None)

    def _on_name_changed(self):
        if self._selected_uuid is None:
            return
        index = int(self._selected_uuid)
        if 0 <= index < len(self.config.nodes):
            self.config.nodes[index].name = self.name_edit.text().strip()
            self._mark_dirty()
            self.scene.update()

    def _on_param_changed(self):
        if self._selected_uuid is None:
            return
        index = int(self._selected_uuid)
        if index >= len(self.config.nodes):
            return
        node = self.config.nodes[index]
        meta = NODE_REGISTRY[node.type].Meta
        for key, widget in self._param_widgets.items():
            stype = meta.get("param_schema", {}).get(key, {}).get("type", "str")
            if stype == "model":
                model_name = widget.currentData()
                node.params[key] = {"name": model_name} if model_name else {}
            elif stype == "float":
                node.params[key] = float(widget.value())
            elif stype == "int":
                node.params[key] = int(widget.value())
            elif stype == "bool":
                node.params[key] = bool(widget.isChecked())
            else:
                node.params[key] = widget.text()
        self._mark_dirty()
        self._draw_arrows()

    # ----------------------- 工作流载入/保存 ----------------------- #
    def _load_starter(self):
        from ..workflow.templates import starter_workflow_config

        self.config = copy.deepcopy(starter_workflow_config())
        self._selected_uuid = None
        self._mark_dirty()
        self._rebuild_scene()
        self.fit_to_content()

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开工作流", "", "工作流 (*.json)")
        if not path:
            return
        try:
            self.config = copy.deepcopy(load_workflow(path))
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self._selected_uuid = None
        self._mark_dirty()
        self._rebuild_scene()
        self.fit_to_content()

    def _save_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存工作流", "", "工作流 (*.json)")
        if not path:
            return
        try:
            save_workflow(self.config, path)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._dirty = False
        self.dirty_label.setText(f"已保存到 {path}")

    def values(self) -> WorkflowConfig:
        """返回草稿的深拷贝；确认后才由调用方提交为活动工作流。"""
        return copy.deepcopy(self.config)
