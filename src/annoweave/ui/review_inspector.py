"""复核工作台的右侧“对象与证据”检查器（评审第 6 节）。

显示：模型预测、置信度、人工结论、关联阈值、实际关联分数、裁剪与下游结果，
并明确回答“为什么裁这个人 / 为什么没裁另一个人”。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..inference.datatypes import Detection
from ..video_inference.editable_canvas import _to_qimage
from .smooth_list import SmoothListWidget


class CropImageView(QGraphicsView):
    """只读裁剪图，滚轮缩放、拖拽平移。"""
    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        scale = self.transform().m11() * factor
        if 0.02 <= scale <= 32:
            self.scale(factor, factor)
        event.accept()

#: 图层名称映射
LAYER_NAMES = {"person": "人身", "head": "人头", "behavior": "行为"}


def _fmt(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _box_text(det: Detection) -> str:
    return f"({det.x1:.0f}, {det.y1:.0f}) – ({det.x2:.0f}, {det.y2:.0f})"


class ObjectInspector(QWidget):
    """对象列表 + 选中对象证据面板。"""

    object_selected = Signal(str, int)  # (kind, index)
    adopt_source_changed = Signal(str)  # predicted | manual
    crop_edit_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self._persons: list[Detection] = []
        self._behaviors: list[Detection] = []
        self._predictions: list[Detection] = []
        self._has_prediction = False
        self._associations: list = []
        self._slots: list = []
        self._image_size = (0, 0)
        self._adopted_source = "manual"
        self._switching = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("对象与证据")
        title.setProperty("role", "title")
        layout.addWidget(title)

        # 采用版本：并排对比预测版与人工版，并明确导出用哪一版
        adopt_row = QHBoxLayout()
        adopt_row.addWidget(QLabel("当前标注"))
        self.adopt_combo = QComboBox()
        self.adopt_combo.addItem("人工修订版", "manual")
        self.adopt_combo.addItem("模型预测版", "predicted")
        self.adopt_combo.setToolTip("决定复核结论与导出使用哪一版对象坐标")
        self.adopt_combo.currentIndexChanged.connect(self._on_adopt_changed)
        adopt_row.addWidget(self.adopt_combo, 1)
        self.adopt_combo.hide()
        layout.addLayout(adopt_row)
        self.diff_label = QLabel("")
        self.diff_label.setProperty("role", "muted")
        self.diff_label.setWordWrap(True)
        layout.addWidget(self.diff_label)
        self.classification_label = QLabel()
        self.classification_label.setWordWrap(True)
        self.classification_label.hide()
        layout.addWidget(self.classification_label)

        self.result_tabs = QTabWidget()
        self.result_tabs.setMinimumHeight(140)
        self.object_list = SmoothListWidget()
        self.object_list.setStyleSheet("QListWidget::item { padding: 9px 6px; border-radius: 4px; } QListWidget::item:hover { background: #223243; }")
        self.object_list.currentRowChanged.connect(self._on_row_changed)
        self.result_tabs.addTab(self.object_list, "对象（0）")

        crop_panel = QWidget()
        crop_layout = QVBoxLayout(crop_panel)
        crop_layout.setContentsMargins(0, 4, 0, 0)

        self.crop_label = QLabel("裁剪图（0）")
        self.crop_label.setWordWrap(True)
        crop_layout.addWidget(self.crop_label)
        self.crop_list = SmoothListWidget()
        self.crop_list.setIconSize(QSize(96, 80))
        self.crop_list.setStyleSheet("QListWidget::item { padding: 8px 4px; border-radius: 4px; } QListWidget::item:hover { background: #223243; }")
        self.crop_list.setToolTip("双击裁剪图查看大图；大图支持滚轮缩放、拖拽平移")
        self.crop_list.itemDoubleClicked.connect(self._open_crop)
        crop_layout.addWidget(self.crop_list, 1)
        self.crop_open_button = QPushButton("打开并编辑选中裁剪")
        self.crop_open_button.clicked.connect(lambda: self._open_crop(self.crop_list.currentItem()))
        self.crop_open_button.setEnabled(False)
        crop_layout.addWidget(self.crop_open_button)
        self.result_tabs.addTab(crop_panel, "裁剪图（0）")

        self.evidence_scroll = QScrollArea()
        self.evidence_scroll.setWidgetResizable(True)
        self.evidence_host = QWidget()
        self.evidence_form = QFormLayout(self.evidence_host)
        self.evidence_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.evidence_scroll.setWidget(self.evidence_host)
        self.evidence_scroll.setMinimumHeight(90)
        self.evidence_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.evidence_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self.panel_splitter.setChildrenCollapsible(False)
        self.panel_splitter.setHandleWidth(7)
        self.panel_splitter.setToolTip("拖动分隔线调整列表与证据详情的高度")
        self.panel_splitter.addWidget(self.result_tabs)
        self.panel_splitter.addWidget(self.evidence_scroll)
        self.panel_splitter.setSizes([300, 210])
        layout.addWidget(self.panel_splitter, 1)

    # ----------------------------- 数据装载 ----------------------------- #
    def set_frame(
        self,
        persons: list[Detection],
        behaviors: list[Detection],
        associations: list,
        slots: list,
        image_size: tuple[int, int],
        predictions: list[Detection] | None = None,
        has_prediction: bool = False,
        adopted_source: str = "manual",
    ):
        self._persons = list(persons)
        self._behaviors = list(behaviors)
        self._predictions = list(predictions or [])
        self._has_prediction = bool(has_prediction or predictions)
        self._associations = list(associations)
        self._slots = list(slots)
        self._reload_crops()
        self._image_size = image_size
        self._set_adopted_source(adopted_source)
        self._reload_list()
        self._refresh_diff()

    def _reload_crops(self):
        self.crop_list.clear()
        self.result_tabs.setTabText(1, f"裁剪图（{len(self._slots)}）")
        self.crop_label.setText(f"裁剪图（{len(self._slots)}） · 双击放大")
        for index, slot in enumerate(self._slots):
            image = slot.crop_image
            if image is None or not image.size:
                continue
            pixmap = QPixmap.fromImage(_to_qimage(image))
            thumb = pixmap.scaled(96, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            item = QListWidgetItem(QIcon(thumb), f"裁剪 #{index + 1}\n{image.shape[1]} × {image.shape[0]}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.crop_list.addItem(item)
        available = self.crop_list.count() > 0
        self.crop_list.setVisible(available)
        self.crop_open_button.setVisible(available)
        self.crop_open_button.setEnabled(available)
        if available:
            self.crop_list.setCurrentRow(0)
        else:
            self.crop_label.setText("裁剪图（0） · 当前帧没有裁剪结果")

    def _open_crop(self, item):
        if item is None:
            return
        index = int(item.data(Qt.ItemDataRole.UserRole))
        if not 0 <= index < len(self._slots):
            return
        self.crop_edit_requested.emit(index)

    def _set_adopted_source(self, source: str):
        self._adopted_source = "predicted" if source == "predicted" else "manual"
        self._switching = True
        index = self.adopt_combo.findData(self._adopted_source)
        if index >= 0:
            self.adopt_combo.setCurrentIndex(index)
        self._switching = False

    def adopted_source(self) -> str:
        return self._adopted_source

    def _on_adopt_changed(self, _index: int):
        if self._switching:
            return
        self._adopted_source = str(self.adopt_combo.currentData() or "manual")
        self._refresh_diff()
        self.adopt_source_changed.emit(self._adopted_source)

    def _refresh_diff(self):
        """并排比较预测版与人工版对象数量；没有预测版时明确说明。"""
        manual_count = len(self._persons) + len(self._behaviors)
        predicted_count = len(self._predictions)
        def signature(items):
            return sorted(
                (round(det.x1, 3), round(det.y1, 3), round(det.x2, 3), round(det.y2, 3), det.label)
                for det in items
            )
        differs = self._has_prediction and signature(
            self._persons + self._behaviors
        ) != signature(self._predictions)
        if not self._has_prediction and manual_count:
            self.diff_label.setText("当前标注尚无模型预测来源；可以直接人工标注。")
            return
        if not differs and manual_count:
            self.diff_label.setText(f"当前 {manual_count} 个对象 · 与原始预测一致")
            return
        self.diff_label.setText(
            f"当前 {manual_count} 个对象 · 原始预测 {predicted_count} 个 · 已人工修改"
            if differs else f"当前 {manual_count} 个对象 · 尚无结果"
        )

    def _reload_list(self):
        self.result_tabs.setTabText(0, f"对象（{len(self._persons) + len(self._behaviors)}）")
        self.object_list.blockSignals(True)
        self.object_list.clear()
        for index, det in enumerate(self._persons):
            self.object_list.addItem(self._item_text("person", index, det))
        for index, det in enumerate(self._behaviors):
            self.object_list.addItem(self._item_text("behavior", index, det))
        self.object_list.blockSignals(False)
        if self.object_list.count():
            self.object_list.setCurrentRow(0)
        else:
            self.show_placeholder("本帧没有对象。运行工作流后这里会列出人身与行为框。")

    def _item_text(self, kind: str, index: int, det: Detection) -> str:
        layer = "人身" if kind == "person" else "行为"
        flags = []
        if det.locked:
            flags.append("锁定")
        if det.hidden:
            flags.append("隐藏")
        suffix = f"  [{'/'.join(flags)}]" if flags else ""
        return f"{layer} #{index + 1}  {det.label}  {det.score:.2f}{suffix}"

    def select_object(self, kind: str, index: int):
        offset = index if kind == "person" else len(self._persons) + index
        if 0 <= offset < self.object_list.count():
            self.object_list.setCurrentRow(offset)

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        if row < len(self._persons):
            kind, index = "person", row
        else:
            kind, index = "behavior", row - len(self._persons)
        self._show_evidence(kind, index)
        self.object_selected.emit(kind, index)

    # ----------------------------- 证据面板 ----------------------------- #
    def _clear_form(self):
        while self.evidence_form.count():
            item = self.evidence_form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_row(self, label: str, value: str, accent: str = ""):
        widget = QLabel(value)
        widget.setWordWrap(True)
        if accent:
            widget.setStyleSheet(f"color:{accent};")
        self.evidence_form.addRow(label, widget)
        return widget

    def show_placeholder(self, text: str):
        self._clear_form()
        self._add_row("", text)

    def _show_evidence(self, kind: str, index: int):
        self._clear_form()
        pool = self._persons if kind == "person" else self._behaviors
        if not (0 <= index < len(pool)):
            self.show_placeholder("没有可显示的选中对象。")
            return
        det = pool[index]
        self._add_row("图层", LAYER_NAMES.get(kind, kind))
        self._add_row("类别", det.label)
        self._add_row("置信度", f"{det.score:.3f}")
        self._add_row("坐标（原图像素）", _box_text(det))
        self._add_row("尺寸", f"{det.x2 - det.x1:.0f} × {det.y2 - det.y1:.0f}")
        keypoints = list(getattr(det, "keypoints", []) or [])
        if keypoints:
            from ..inference.skeletons import resolve_skeleton

            visible = [point for point in keypoints if point.score > 0.3 and point.visible]
            layout = resolve_skeleton(getattr(det, "keypoint_format", ""), len(keypoints))
            self._add_row(
                "人体关键点",
                f"{len(keypoints)} 个（可见 {len(visible)}）"
                + (f" · {layout.display_name}" if layout else ""),
            )
            preview = "、".join(
                f"{layout.point_name(i) if layout else f'kp{i}'} {point.score:.2f}"
                for i, point in enumerate(visible[:6])
            )
            if preview:
                self._add_row("关键点预览", preview + ("…" if len(visible) > 6 else ""))
        if det.locked:
            self._add_row("状态", "已锁定：不会随拖动改变")
        if det.hidden:
            self._add_row("状态", "已隐藏：不参与命中与显示")

        slot = self._slot_for(det) if kind == "person" else None
        if slot is not None:
            self._add_decision_section(det, slot)
        elif kind == "person":
            self._add_row("裁剪决定", "未生成裁剪，具体原因见下方。")
            self._add_row("为什么没裁", self._explain_no_crop(det))
        else:
            self._add_row("角色", "行为框：作为关联客体，本身不裁剪。")
            owners = self._owners_of(det)
            self._add_row("关联主体", "、".join(owners) if owners else "没有人身与之关联")

        if kind == "person":
            slot_index = self._slot_index(slot) if slot is not None else -1
            if slot_index >= 0:
                self._add_row("裁剪序号", f"裁剪 #{slot_index + 1}")

    def _add_decision_section(self, det: Detection, slot):
        self._add_row("裁剪决定", "已裁剪" + ("（人工修改过）" if slot.edited else ""))
        self._add_row("真实裁剪范围", self._crop_rect_text(slot))
        self._add_row(
            "裁剪偏移",
            f"offset=({slot.offset_x}, {slot.offset_y})  "
            f"尺寸={slot.crop_image.shape[1]}×{slot.crop_image.shape[0]}"
            if slot.crop_image is not None
            else "—",
        )
        self._add_row("关联状态", self._matched_text(slot))
        if slot.keypoints:
            from ..inference.skeletons import resolve_skeleton

            visible = [point for point in slot.keypoints if point.score > 0.3 and point.visible]
            layout = resolve_skeleton(getattr(slot, "keypoint_format", ""), len(slot.keypoints))
            self._add_row(
                "裁剪主体关键点",
                f"{len(slot.keypoints)} 个（可见 {len(visible)}）"
                + (f" · {layout.display_name}" if layout else ""),
            )
        if slot.results:
            for key, result in slot.results.items():
                summary = f"检测框 {len(result.detections)} 个"
                if result.classification:
                    summary = f"{result.classification.label} ({result.classification.score:.2f})"
                self._add_row(f"下游 · {slot.model_names.get(key, key)}", summary)
        else:
            self._add_row("下游结果", "无")

    def _crop_rect_text(self, slot) -> str:
        if not slot.crop_rect:
            return "—"
        x1, y1, x2, y2 = slot.crop_rect
        return f"({x1:.0f}, {y1:.0f}) – ({x2:.0f}, {y2:.0f})"

    def _matched_text(self, slot) -> str:
        return f"路由：{slot.route}" if slot.route else "人工新增"

    def _owners_of(self, behavior: Detection) -> list[str]:
        owners = []
        for index, assoc in enumerate(self._associations):
            if any(item is behavior for item in getattr(assoc, "matched_candidates", [])):
                owners.append(f"主体 #{index + 1}")
        return owners

    def _explain_no_crop(self, det: Detection) -> str:
        """用运行结果里记录的候选 IoA/阈值解释“为什么没裁”，不在 UI 侧重算。"""
        assoc = self._association_for(det)
        if assoc is not None and getattr(assoc, "rejections", None):
            return "；\n".join(assoc.rejections) + "。"
        if not self._behaviors:
            return "本帧没有候选框，因此没有任何关联。"
        if assoc is not None and getattr(assoc, "candidates", None):
            lines = []
            for label, ioa, threshold, passed, _behavior in assoc.candidates:
                verdict = "达到阈值" if passed else "低于阈值"
                lines.append(f"{label}：IoA {ioa:.3f} / 阈值 {threshold:.2f}（{verdict}）")
            if lines:
                return "\n".join(lines)
        return "本帧没有可关联的候选框。"

    def _association_for(self, det: Detection):
        for assoc in self._associations:
            person = getattr(assoc, "person", None)
            if person is None:
                continue
            if (abs(person.x1 - det.x1) < 1.0 and abs(person.y1 - det.y1) < 1.0
                    and abs(person.x2 - det.x2) < 1.0 and abs(person.y2 - det.y2) < 1.0):
                return assoc
        return None

    def _slot_for(self, det: Detection):
        for slot in self._slots:
            source = getattr(slot, 'source_bbox', None) or slot.person_bbox
            if abs(source.x1 - det.x1) < 1.0 and abs(source.y1 - det.y1) < 1.0 \
                    and abs(source.x2 - det.x2) < 1.0 and abs(source.y2 - det.y2) < 1.0:
                return slot
        return None

    def _slot_index(self, slot) -> int:
        for index, candidate in enumerate(self._slots):
            if candidate is slot:
                return index
        return -1


class ReviewDecisionBar(QFrame):
    """保存集选择与顺序复核导航；保留历史结论 API 供旧项目读取。"""

    decision_requested = Signal(str)  # confirmed | rejected | pending
    next_requested = Signal()
    export_selected_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.state_label = QLabel("待确认")
        self.state_label.hide()
        self.export_select_button = QPushButton("加入保存集")
        self.export_select_button.setCheckable(True)
        self.export_select_button.setToolTip("保存集只记录你要最终导出的帧；普通浏览帧无需加入")
        self.export_select_button.toggled.connect(self._on_export_selected)
        layout.addWidget(self.export_select_button)
        layout.addStretch()
        self.next_button = QPushButton("下一视频 / 图片 →")
        self.next_button.setProperty("accent", "true")
        self.next_button.setToolTip("跳过当前视频剩余采样帧，直接打开队列中的下一视频或图片")
        self.next_button.clicked.connect(self.next_requested.emit)
        layout.addWidget(self.next_button)

    def set_decision(self, decision: str):
        text = {"confirmed": "已确认", "rejected": "已驳回", "pending": "待定"}.get(decision, "待确认")
        self.state_label.setText(text)

    def set_export_selected(self, selected: bool):
        self.export_select_button.blockSignals(True)
        self.export_select_button.setChecked(bool(selected))
        self.export_select_button.setText("已加入保存集 ✓" if selected else "加入保存集")
        self.export_select_button.blockSignals(False)

    def _on_export_selected(self, selected: bool):
        self.export_select_button.setText("已加入保存集 ✓" if selected else "加入保存集")
        self.export_selected_changed.emit(bool(selected))
