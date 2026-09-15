"""裁剪图标注工作台：编辑下游检测框和分类结果。"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..inference.datatypes import Classification, TaskType
from ..video_inference.editable_canvas import EditableFrameCanvas


class CropEditorDialog(QDialog):
    """一次编辑一个裁剪，确定后才写回主工作区。"""
    def __init__(self, slot, media_name='', frame_index=0, crop_index=0, parent=None):
        super().__init__(parent)
        self.slot = slot
        self._results = copy.deepcopy(slot.results or {})
        self._classes = copy.deepcopy(slot.model_classes or {})
        self._model_names = dict(slot.model_names or {})
        self._active_key = ''
        self._dirty = False
        self.setWindowTitle(f'裁剪标注 · {media_name} · 帧 {frame_index} · 裁剪 #{crop_index + 1}')
        self.resize(1100, 760)
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        header.addWidget(QLabel(f'裁剪 #{crop_index + 1}  {slot.crop_image.shape[1]}×{slot.crop_image.shape[0]}'))
        if slot.needs_review:
            warning = QLabel('原裁剪范围已变化，请确认迁移后的标注')
            warning.setStyleSheet('color:#F5A623;')
            header.addWidget(warning)
        header.addStretch()
        header.addWidget(QLabel('下游模型'))
        self.model_combo = QComboBox()
        header.addWidget(self.model_combo)
        root.addLayout(header)

        body = QHBoxLayout()
        tools = QWidget()
        tools.setFixedWidth(118)
        tool_layout = QVBoxLayout(tools)
        def button(text, tip, action, checkable=False):
            item = QPushButton(text)
            item.setToolTip(tip)
            item.setCheckable(checkable)
            item.clicked.connect(action)
            tool_layout.addWidget(item)
            return item
        self.edit_button = button('选择 / 编辑', 'Ctrl+J', lambda: self.canvas.set_mode('select'), True)
        self.draw_button = button('绘制矩形', 'R', lambda: self.canvas.set_mode('add'), True)
        button('修改类别', 'Ctrl+E', self._change_label)
        button('删除框', 'Delete', self.canvas_delete)
        button('清空当前模型', '清空当前模型的当前人工标注，原始预测仍可恢复', self._clear)
        button('恢复模型预测', '恢复当前模型最初运行结果', self._restore_prediction)
        button('撤销', 'Ctrl+Z', lambda: self.canvas.undo())
        button('重做', 'Ctrl+Shift+Z', lambda: self.canvas.redo())
        button('适应窗口', 'Ctrl+F', lambda: self.canvas.fit_to_window())
        tool_layout.addStretch()
        body.addWidget(tools)

        middle = QVBoxLayout()
        bar = QHBoxLayout()
        bar.addWidget(QLabel('新框类别'))
        self.class_combo = QComboBox()
        self.class_combo.currentTextChanged.connect(self._set_add_label)
        bar.addWidget(self.class_combo)
        bar.addStretch()
        self.result_label = QLabel('')
        bar.addWidget(self.result_label)
        middle.addLayout(bar)
        self.canvas = EditableFrameCanvas()
        self.canvas.mode_changed.connect(self._mode_changed)
        self.canvas.boxes_changed.connect(self._canvas_changed)
        middle.addWidget(self.canvas, 1)
        classification = QHBoxLayout()
        self.classification_title = QLabel('分类结果')
        self.classification_combo = QComboBox()
        self.classification_combo.currentTextChanged.connect(self._classification_changed)
        classification.addWidget(self.classification_title)
        classification.addWidget(self.classification_combo, 1)
        self.restore_class_button = QPushButton('恢复预测分类')
        self.restore_class_button.clicked.connect(self._restore_prediction)
        classification.addWidget(self.restore_class_button)
        middle.addLayout(classification)
        body.addLayout(middle, 1)
        root.addLayout(body, 1)

        note = QLabel('当前窗口编辑的是裁剪局部坐标。保存后同步更新业务结果；不会重新运行模型覆盖人工修改。')
        note.setProperty('role', 'muted')
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存裁剪标注')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for key, result in self._results.items():
            label = self._model_names.get(key, key)
            self.model_combo.addItem(f'{label} · {result.task_type.value}', key)
        if not self._results:
            self.model_combo.addItem('没有下游模型结果', '')
            self.model_combo.setEnabled(False)
        self.model_combo.currentIndexChanged.connect(self._switch_model)
        self._install_shortcuts()
        self._switch_model()

    def _install_shortcuts(self):
        for key, action in [('R', lambda: self.canvas.set_mode('add')),
                            ('Ctrl+J', lambda: self.canvas.set_mode('select')),
                            ('Ctrl+E', self._change_label), ('Delete', self.canvas_delete),
                            ('Ctrl+Z', self.canvas.undo), ('Ctrl+Shift+Z', self.canvas.redo),
                            ('Ctrl+F', self.canvas.fit_to_window)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)

    def _save_active(self):
        if not self._active_key or self._active_key not in self._results:
            return
        result = self._results[self._active_key]
        if result.task_type == TaskType.DETECTION:
            result.detections = copy.deepcopy(self.canvas.persons() + self.canvas.behaviors())

    def _switch_model(self, *_args):
        self._save_active()
        self._active_key = str(self.model_combo.currentData() or '')
        result = self._results.get(self._active_key)
        classes = self._classes.get(self._active_key, [])
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItems(classes)
        self.class_combo.blockSignals(False)
        self.classification_combo.blockSignals(True)
        self.classification_combo.clear()
        self.classification_combo.addItems(classes)
        self.classification_combo.blockSignals(False)
        is_detection = bool(result and result.task_type == TaskType.DETECTION)
        is_pose = bool(result and result.task_type == TaskType.POSE)
        self.canvas.setVisible(is_detection)
        self.class_combo.setEnabled(is_detection)
        self.classification_title.setVisible(not is_detection and not is_pose)
        self.classification_combo.setVisible(not is_detection and not is_pose)
        self.restore_class_button.setVisible(not is_detection and not is_pose)
        if is_detection:
            detections = copy.deepcopy(result.detections)
            self.canvas.set_add_class(classes[0] if classes else '')
            self.canvas.set_data(self.slot.crop_image, [], detections)
            self.result_label.setText(f'{len(detections)} 个框 · 模型原始类别完整显示')
        elif is_pose:
            # 关键点是只读展示：这里不提供编辑，避免和"人工修订框"的语义混在一起。
            points = list(self.slot.keypoints or [])
            visible = [point for point in points if point.score > 0.3 and point.visible]
            self.result_label.setText(
                f'人体关键点：{len(points)} 个（可见 {len(visible)}） · 只读展示，不可编辑'
            )
        elif result:
            label = result.classification.label if result.classification else ''
            self.classification_combo.setCurrentText(label)
            score = result.classification.score if result.classification else 0
            self.result_label.setText(f'模型结果：{label or "—"} {score:.1%}')
        else:
            self.result_label.setText('当前裁剪没有下游结果')

    def _set_add_label(self, label):
        self.canvas.set_add_class(label)

    def _mode_changed(self, mode):
        self.edit_button.setChecked(mode == 'select')
        self.draw_button.setChecked(mode == 'add')

    def _canvas_changed(self):
        self._dirty = True
        self._save_active()
        result = self._results.get(self._active_key)
        self.result_label.setText(f'{len(result.detections) if result else 0} 个框 · 已人工修改')

    def canvas_delete(self):
        self.canvas.delete_selected()

    def _change_label(self):
        selected = self.canvas.selected()
        if selected is None:
            return
        classes = self._classes.get(self._active_key, [])
        if not classes:
            return
        # 使用现有类别下拉作为明确来源，避免自由文本破坏训练类别索引。
        label, ok = QInputDialog.getItem(self, '修改类别', '类别', classes,
                                         max(0, classes.index(selected.label) if selected.label in classes else 0), False)
        if ok:
            self.canvas.relabel_selected(label)

    def _classification_changed(self, label):
        result = self._results.get(self._active_key)
        if not label or result is None or result.task_type != TaskType.CLASSIFICATION:
            return
        if result.classification is None or result.classification.label != label:
            result.classification = Classification(label, 1.0)
            self._dirty = True
            self.result_label.setText(f'人工分类：{label}')

    def _clear(self):
        result = self._results.get(self._active_key)
        if result is None:
            return
        if result.task_type == TaskType.DETECTION:
            self.canvas.set_data(self.slot.crop_image, [], [])
            result.detections = []
        else:
            result.classification = None
            self.classification_combo.setCurrentIndex(-1)
        self._dirty = True
        self.result_label.setText('当前模型标注已清空')

    def _restore_prediction(self):
        prediction = self.slot.predictions.get(self._active_key)
        if prediction is None:
            QMessageBox.information(self, '恢复预测', '当前裁剪没有可恢复的原始预测。')
            return
        self._results[self._active_key] = copy.deepcopy(prediction)
        self._dirty = True
        self._active_key = ''
        self._switch_model()

    def _accept(self):
        self._save_active()
        self.accept()

    def results(self):
        self._save_active()
        return copy.deepcopy(self._results)
