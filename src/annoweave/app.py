from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .brand import (
    PRODUCT_NAME,
    PRODUCT_SUBTITLE_EN,
    WINDOW_TITLE_EN,
    WINDOW_TITLE_ZH,
    BrandLockup,
    application_icon,
)
from .file_ops import DeleteService, export_labeled
from .i18n import LANG_EN, LANG_ZH, LanguageManager
from .models import LabelDefinition, MediaItem, ScanResult
from .paths import data_path
from .scanner import flat_collisions, scan_media
from .store import ReviewStore
from .ui.project_context import open_project, project_database_path, scan_into_project
from .ui.smooth_list import SmoothListWidget
from .ui.splitter import GripSplitter
from .workers import FunctionWorker

DEFAULT_ACTION_SHORTCUTS = {
    "previous": "Left",
    "next": "Right",
    "delete": "Delete",
    "undo_delete": "Ctrl+Shift+Z",
    "play_pause": "Space",
    "clear_labels": "Backspace",
    "next_unlabeled": "N",
}

ACTION_NAMES = {
    "previous": "上一项",
    "next": "下一项",
    "delete": "移动到 _delete",
    "undo_delete": "撤销上次删除",
    "play_pause": "播放/暂停",
    "clear_labels": "清除当前全部标签",
    "next_unlabeled": "下一个未标记项",
}


@dataclass
class ReviewTarget:
    target_key: str
    display_path: str
    members: list[MediaItem]
    preview_images: list[MediaItem]
    primary: MediaItem


class ScaledImageLabel(QLabel):
    def __init__(self):
        super().__init__("请选择告警文件夹")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(480, 320)
        self.setStyleSheet("background:#171717;color:#ddd;border:1px solid #444;")
        self._source_pixmap: Optional[QPixmap] = None

    def set_image(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._source_pixmap = None
            self.setText(f"图片无法读取\n{path}")
            return
        self._source_pixmap = pixmap
        self._apply_scaled()

    def clear_image(self, text: str = "") -> None:
        self._source_pixmap = None
        self.clear()
        self.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scaled()

    def _apply_scaled(self) -> None:
        if not self._source_pixmap:
            return
        self.setPixmap(
            self._source_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class LabelEditDialog(QDialog):
    def __init__(self, parent=None, label: LabelDefinition | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑标签" if label else "新增标签")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(label.name if label else "")
        self.color_edit = QLineEdit(label.color if label else "#4caf50")
        self.color_button = QPushButton("选择颜色")
        self.color_button.clicked.connect(self._pick_color)
        color_row = QHBoxLayout()
        color_row.addWidget(self.color_edit)
        color_row.addWidget(self.color_button)
        self.shortcut_edit = QKeySequenceEdit(QKeySequence(label.shortcut if label else ""))
        form.addRow("标签名", self.name_edit)
        form.addRow("颜色", color_row)
        form.addRow("快捷键", self.shortcut_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self.color_edit.text()), self)
        if color.isValid():
            self.color_edit.setText(color.name())

    def values(self) -> tuple[str, str, str]:
        return (
            self.name_edit.text().strip(),
            self.color_edit.text().strip(),
            self.shortcut_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
        )


class LabelManagerDialog(QDialog):
    labels_changed = Signal()

    def __init__(self, store: ReviewStore, action_shortcuts: dict[str, str], parent=None):
        super().__init__(parent)
        self.store = store
        self.action_shortcuts = action_shortcuts
        self.setWindowTitle("标签管理")
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "拖动左侧行号可调整标签顺序；也可以选中后使用上移/下移。"
            "快捷键可留空，只给常用标签设置即可。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "颜色", "快捷键", "ID"])
        self.table.setColumnHidden(3, True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setSectionsMovable(True)
        self.table.verticalHeader().sectionMoved.connect(self._row_moved)
        self.table.itemChanged.connect(self._inline_edit)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for text, handler in (
            ("新增", self._add),
            ("编辑", self._edit),
            ("删除", self._delete),
            ("上移", lambda: self._move_selected(-1)),
            ("下移", lambda: self._move_selected(1)),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self._reload()

    def _reload(self):
        labels = self.store.labels()
        self.table.blockSignals(True)
        header = self.table.verticalHeader()
        header.blockSignals(True)
        try:
            self.table.setRowCount(len(labels))
            # 拖动表头只改变视觉顺序；重新装载前先恢复逻辑行顺序，
            # 再按数据库中的 sort_order 填充，保证行号始终从 1 连续显示。
            for logical_index in range(header.count()):
                visual_index = header.visualIndex(logical_index)
                if visual_index != logical_index:
                    header.moveSection(visual_index, logical_index)
            for row, label in enumerate(labels):
                self.table.setItem(row, 0, QTableWidgetItem(label.name))
                color_item = QTableWidgetItem(label.color)
                color_item.setBackground(QColor(label.color))
                self.table.setItem(row, 1, color_item)
                self.table.setItem(row, 2, QTableWidgetItem(label.shortcut))
                id_item = QTableWidgetItem(str(label.label_id))
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 3, id_item)
        finally:
            header.blockSignals(False)
            self.table.blockSignals(False)
        self.table.resizeColumnsToContents()

    def _row_moved(self, logical_index: int, _old_visual: int, _new_visual: int):
        """将左侧行号拖动形成的视觉顺序保存为正式标签顺序。"""
        header = self.table.verticalHeader()
        moved_item = self.table.item(logical_index, 3)
        moved_label_id = int(moved_item.text()) if moved_item else None
        ordered_ids = []
        for visual_index in range(header.count()):
            row = header.logicalIndex(visual_index)
            id_item = self.table.item(row, 3)
            if id_item:
                ordered_ids.append(int(id_item.text()))
        try:
            self.store.reorder_labels(ordered_ids)
        except Exception as exc:
            QMessageBox.warning(self, "无法调整标签顺序", str(exc))
        self._reload()
        if moved_label_id is not None:
            self._select_label_id(moved_label_id)
        self.labels_changed.emit()

    def _select_label_id(self, label_id: int) -> None:
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 3)
            if id_item and int(id_item.text()) == label_id:
                self.table.setCurrentCell(row, 0)
                return

    def _move_selected(self, offset: int) -> None:
        label = self._selected_label()
        if not label:
            return
        ordered_ids = [value.label_id for value in self.store.labels()]
        old_index = ordered_ids.index(label.label_id)
        new_index = old_index + offset
        if new_index < 0 or new_index >= len(ordered_ids):
            return
        ordered_ids[old_index], ordered_ids[new_index] = (
            ordered_ids[new_index],
            ordered_ids[old_index],
        )
        self.store.reorder_labels(ordered_ids)
        self._reload()
        self._select_label_id(label.label_id)
        self.labels_changed.emit()

    def _inline_edit(self, item: QTableWidgetItem):
        """将表格内直接修改立即写入数据库，避免重载后恢复旧值。"""
        if item.column() == 3:
            return
        row = item.row()
        id_item = self.table.item(row, 3)
        name_item = self.table.item(row, 0)
        color_item = self.table.item(row, 1)
        shortcut_item = self.table.item(row, 2)
        if not all((id_item, name_item, color_item, shortcut_item)):
            return

        label_id = int(id_item.text())
        name = name_item.text().strip()
        color = color_item.text().strip()
        shortcut_text = shortcut_item.text().strip()
        shortcut = QKeySequence(shortcut_text).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        try:
            if not QColor(color).isValid():
                raise ValueError("颜色无效，请输入如 #4caf50 的颜色值")
            if shortcut_text and not shortcut:
                raise ValueError("快捷键格式无效")
            conflict = self._shortcut_conflict(shortcut)
            if conflict:
                raise ValueError(f"快捷键已被操作“{conflict}”使用")
            self.store.update_label(label_id, name, color, shortcut)
        except Exception as exc:
            QMessageBox.warning(self, "无法保存标签修改", str(exc))
            self._reload()
            return

        self.table.blockSignals(True)
        try:
            name_item.setText(name)
            color_item.setText(color)
            color_item.setBackground(QColor(color))
            shortcut_item.setText(shortcut)
        finally:
            self.table.blockSignals(False)
        self.labels_changed.emit()

    def _shortcut_conflict(self, shortcut: str) -> str:
        folded = shortcut.casefold()
        if not folded:
            return ""
        for action, value in self.action_shortcuts.items():
            if value.casefold() == folded:
                return ACTION_NAMES.get(action, action)
        return ""

    def _add(self):
        dialog = LabelEditDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, color, shortcut = dialog.values()
        conflict = self._shortcut_conflict(shortcut)
        if conflict:
            QMessageBox.warning(self, "快捷键冲突", f"快捷键已被操作“{conflict}”使用")
            return
        try:
            self.store.add_label(name, color, shortcut)
        except Exception as exc:
            QMessageBox.warning(self, "无法新增标签", str(exc))
            return
        self._reload()
        self.labels_changed.emit()

    def _selected_label(self) -> LabelDefinition | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        label_id = int(self.table.item(row, 3).text())
        return next((label for label in self.store.labels() if label.label_id == label_id), None)

    def _edit(self):
        label = self._selected_label()
        if not label:
            return
        dialog = LabelEditDialog(self, label)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, color, shortcut = dialog.values()
        conflict = self._shortcut_conflict(shortcut)
        if conflict:
            QMessageBox.warning(self, "快捷键冲突", f"快捷键已被操作“{conflict}”使用")
            return
        try:
            self.store.update_label(label.label_id, name, color, shortcut)
        except Exception as exc:
            QMessageBox.warning(self, "无法编辑标签", str(exc))
            return
        self._reload()
        self.labels_changed.emit()

    def _delete(self):
        label = self._selected_label()
        if not label:
            return
        answer = QMessageBox.question(
            self,
            "删除标签",
            f"删除标签“{label.name}”？所有媒体上的该标签也会被删除。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_label(label.label_id)
        self._reload()
        self.labels_changed.emit()


class ShortcutDialog(QDialog):
    def __init__(self, values: dict[str, str], label_shortcuts: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("操作快捷键")
        self._edits: dict[str, QKeySequenceEdit] = {}
        self.label_shortcuts = label_shortcuts
        form = QFormLayout(self)
        for action, title in ACTION_NAMES.items():
            edit = QKeySequenceEdit(QKeySequence(values.get(action, "")))
            self._edits[action] = edit
            form.addRow(title, edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate(self):
        values = self.values()
        used: dict[str, str] = {}
        for action, shortcut in values.items():
            folded = shortcut.casefold()
            if not folded:
                continue
            if folded in used:
                QMessageBox.warning(
                    self,
                    "快捷键冲突",
                    f"“{ACTION_NAMES[action]}”与“{used[folded]}”使用了相同快捷键",
                )
                return
            used[folded] = ACTION_NAMES[action]
        for label_name, shortcut in self.label_shortcuts.items():
            if shortcut and shortcut.casefold() in used:
                QMessageBox.warning(
                    self,
                    "快捷键冲突",
                    f"标签“{label_name}”与操作“{used[shortcut.casefold()]}”快捷键冲突",
                )
                return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            action: edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            for action, edit in self._edits.items()
        }


class ExportDialog(QDialog):
    def __init__(self, has_collisions: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成标签分类目录")
        form = QFormLayout(self)
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("保留原目录层级（推荐）", "preserve")
        if not has_collisions:
            self.layout_combo.addItem("平铺；检测确认无同名冲突", "flat")
        self.layout_combo.addItem("平铺；同名冲突自动追加短后缀", "flat_rename")
        note = QLabel(
            "多标签文件会复制到每个标签目录；未标记文件进入 unlabeled。\n"
            + ("已检测到同名文件，纯平铺选项已禁用。" if has_collisions else "未检测到同名文件，可以使用纯平铺。")
        )
        note.setWordWrap(True)
        form.addRow("输出布局", self.layout_combo)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def layout_mode(self) -> str:
        return str(self.layout_combo.currentData())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.language_manager = LanguageManager(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self.language_manager)
        self.setWindowTitle(WINDOW_TITLE_ZH)
        self.resize(1380, 860)
        self.thread_pool = QThreadPool.globalInstance()
        self.source_root: Optional[Path] = None
        self.store: Optional[ReviewStore] = None
        self.delete_service: Optional[DeleteService] = None
        self.scan_result: Optional[ScanResult] = None
        self.targets: list[ReviewTarget] = []
        self.current_index = -1
        self.label_checks: dict[int, QCheckBox] = {}
        self.shortcuts: list[QShortcut] = []
        self.project_context = None
        self._setting_label_states = False
        self._busy_cursor_active = False
        self._build_ui()
        self.language_manager.changed.connect(self._apply_language)
        self._apply_language(self.language_manager.language)
        self._register_shortcuts()

    def _build_ui(self):
        self.main_stack = QStackedWidget()
        self.inference_workspace = None
        self._pages: dict = {}

        container = QWidget()
        container_layout = QVBoxLayout(container)
        nav = QHBoxLayout()
        nav.addWidget(BrandLockup(self))
        nav.addSpacing(20)
        self._nav_buttons: dict = {}
        for key, title in (
            ("review", "文件分类"),
            ("workbench", "视觉复核"),
            ("workflow", "工作流"),
            ("model", "模型"),
            ("task", "任务"),
        ):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setChecked(key == 'review')
            button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
            nav.addWidget(button)
            self._nav_buttons[key] = button
        nav.addStretch()
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("LanguageSelector")
        self.language_combo.setToolTip("语言 / Language")
        self.language_combo.addItem("中文", LANG_ZH)
        self.language_combo.addItem("English", LANG_EN)
        language_index = self.language_combo.findData(self.language_manager.language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        self.language_combo.currentIndexChanged.connect(self._language_selected)
        nav.addWidget(self.language_combo)
        container_layout.addLayout(nav)
        container_layout.addWidget(self.main_stack, 1)
        self.setCentralWidget(container)

        root_widget = QWidget()
        root_layout = QVBoxLayout(root_widget)
        self.main_stack.addWidget(root_widget)

        toolbar = QHBoxLayout()
        self.open_button = QPushButton("打开文件夹")
        self.open_button.clicked.connect(self.open_folder)
        self.rescan_button = QPushButton("重新扫描")
        self.rescan_button.clicked.connect(self.rescan)
        self.rescan_button.setEnabled(False)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("所有图片和视频（逐文件）", "all")
        self.mode_combo.addItem("仅图片（同名组共享标签和删除）", "images")
        self.mode_combo.currentIndexChanged.connect(self._rebuild_targets)
        self.label_manager_button = QPushButton("定义标签")
        self.label_manager_button.clicked.connect(self.manage_labels)
        self.label_manager_button.setEnabled(False)
        self.shortcut_button = QPushButton("快捷键")
        self.shortcut_button.clicked.connect(self.manage_shortcuts)
        self.shortcut_button.setEnabled(False)
        self.export_button = QPushButton("生成分类目录")
        self.export_button.clicked.connect(self.export_files)
        self.export_button.setEnabled(False)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.rescan_button)
        toolbar.addWidget(QLabel("遍历模式"))
        toolbar.addWidget(self.mode_combo)
        toolbar.addWidget(self.label_manager_button)
        toolbar.addWidget(self.shortcut_button)
        toolbar.addStretch()
        toolbar.addWidget(self.export_button)
        root_layout.addLayout(toolbar)

        splitter = GripSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(9)
        splitter.setChildrenCollapsible(False)
        # 左侧文件列表：单行条目 + 像素级横向/纵向平滑滚动。
        # 长文件名不再需要"够"滚动条 —— 滚轮直接滚，Shift+滚轮/触控板横扫左右移动。
        # 注意：这里不能开 uniformItemSizes —— Qt 在统一行高模式下会按"平均宽度"
        # 估算内容宽度，长文件名会被裁掉且横向滚动条不再出现（实测 hmax=0）。
        # 行高改由主题里的 QListWidget#MediaList::item padding 控制。
        self.media_list = SmoothListWidget(
            allow_horizontal=True, word_wrap=False
        )
        self.media_list.setObjectName("MediaList")
        self.media_list.setMinimumWidth(310)
        self.media_list.currentRowChanged.connect(self._select_index)
        splitter.addWidget(self.media_list)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.path_label = QLabel("尚未打开目录")
        self.path_label.setWordWrap(True)
        center_layout.addWidget(self.path_label)
        self.variant_combo = QComboBox()
        self.variant_combo.currentIndexChanged.connect(self._load_selected_variant)
        self.variant_combo.hide()
        center_layout.addWidget(self.variant_combo)
        self.preview_stack = QStackedWidget()
        self.image_view = ScaledImageLabel()
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(480, 320)
        self.video_widget.setStyleSheet("background:#111;")
        self.video_message = QLabel("视频无法播放")
        self.video_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_message.setStyleSheet("background:#171717;color:#ddd;")
        self.preview_stack.addWidget(self.image_view)
        self.preview_stack.addWidget(self.video_widget)
        self.preview_stack.addWidget(self.video_message)
        center_layout.addWidget(self.preview_stack, 1)
        video_controls = QHBoxLayout()
        self.play_button = QPushButton("播放/暂停")
        self.play_button.clicked.connect(self.toggle_play_pause)
        self.restart_button = QPushButton("从头播放")
        self.restart_button.clicked.connect(self.restart_video)
        self.mute_check = QCheckBox("静音")
        self.mute_check.setChecked(True)
        self.mute_check.toggled.connect(self._set_muted)
        video_controls.addWidget(self.play_button)
        video_controls.addWidget(self.restart_button)
        video_controls.addWidget(self.mute_check)
        video_controls.addStretch()
        center_layout.addLayout(video_controls)
        navigation = QHBoxLayout()
        previous_button = QPushButton("上一项")
        previous_button.clicked.connect(self.previous_item)
        next_button = QPushButton("下一项")
        next_button.clicked.connect(self.next_item)
        delete_button = QPushButton("移动到 _delete")
        delete_button.clicked.connect(self.delete_current)
        undo_delete_button = QPushButton("撤销上次删除")
        undo_delete_button.clicked.connect(self.undo_delete)
        navigation.addWidget(previous_button)
        navigation.addWidget(next_button)
        navigation.addStretch()
        navigation.addWidget(delete_button)
        navigation.addWidget(undo_delete_button)
        center_layout.addLayout(navigation)
        splitter.addWidget(center)

        label_frame = QFrame()
        label_frame.setMinimumWidth(260)
        label_layout = QVBoxLayout(label_frame)
        label_layout.addWidget(QLabel("当前项标签（可多选，勾选即保存）"))
        self.label_scroll = QScrollArea()
        self.label_scroll.setWidgetResizable(True)
        self.label_container = QWidget()
        self.label_container_layout = QVBoxLayout(self.label_container)
        self.label_container_layout.addStretch()
        self.label_scroll.setWidget(self.label_container)
        label_layout.addWidget(self.label_scroll, 1)
        self.clear_labels_button = QPushButton("清除当前全部标签")
        self.clear_labels_button.clicked.connect(self.clear_current_labels)
        label_layout.addWidget(self.clear_labels_button)
        self.label_summary = QLabel("未选择媒体")
        self.label_summary.setWordWrap(True)
        label_layout.addWidget(self.label_summary)
        splitter.addWidget(label_frame)
        splitter.setSizes([320, 800, 280])
        root_layout.addWidget(splitter, 1)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setMuted(True)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._media_error)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_text = QLabel("请选择告警文件夹")
        status.addWidget(self.status_text, 1)

    def _language_selected(self, index: int) -> None:
        language = self.language_combo.itemData(index)
        if language:
            self.language_manager.set_language(str(language))

    def _apply_language(self, language: str) -> None:
        """Switch visible UI text without rebuilding pages or losing edits."""
        self.setWindowTitle(WINDOW_TITLE_EN if language == LANG_EN else WINDOW_TITLE_ZH)
        self.language_manager.apply(self)
        # Window title has an explicit bilingual form and must not be overwritten by
        # the generic source-text translator on subsequent switches.
        self.setWindowTitle(WINDOW_TITLE_EN if language == LANG_EN else WINDOW_TITLE_ZH)
        index = self.language_combo.findData(language)
        if index >= 0 and index != self.language_combo.currentIndex():
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(index)
            self.language_combo.blockSignals(False)

    def _project_database_path(self, source_root: Path) -> Path:
        # One canonical layout for the review database, shared with the project
        # context so the legacy and current paths can never diverge.
        return project_database_path(source_root)

    def _label_catalog_path(self) -> Path:
        return data_path("label_catalog.json")

    def _load_label_catalog(self) -> list[dict]:
        path = self._label_catalog_path()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            labels = payload.get("labels", []) if isinstance(payload, dict) else []
            return labels if isinstance(labels, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_label_catalog(self) -> None:
        if not self.store:
            return
        labels = [
            {
                "name": label.name,
                "color": label.color,
                "shortcut": label.shortcut,
            }
            for label in self.store.labels()
        ]
        if not labels:
            return
        path = self._label_catalog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "labels": labels}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _apply_catalog_or_defaults(self) -> None:
        assert self.store is not None
        catalog = self._load_label_catalog()
        if catalog:
            # 新项目或尚未产生标注的项目可安全同步应用级标签模板。
            # 已有标注的旧项目保留自己的标签定义，避免历史含义错位。
            try:
                self.store.replace_unassigned_labels(catalog)
            except (TypeError, ValueError):
                # 模板文件即使被手工改坏，也不能阻止项目打开；回退后重建模板。
                catalog = []
        self.store.ensure_default_labels()
        if not catalog:
            self._save_label_catalog()

    def open_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "选择告警根目录")
        if not selected:
            return
        source = Path(selected).resolve()
        if source.name == "_delete":
            answer = QMessageBox.question(
                self,
                "确认打开 _delete",
                "当前选择的是删除目录本身。仍然作为新的源目录打开吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._open_source(source)

    def _open_source(self, source: Path):
        source = Path(source).resolve(strict=True)
        self._save_label_catalog()
        self._close_project()
        self.source_root = source
        try:
            self.store = ReviewStore(self._project_database_path(source), source)
            self._apply_catalog_or_defaults()
            self.delete_service = DeleteService(source, self.store)
            self.project_context = open_project(source)
        except Exception as exc:
            QMessageBox.critical(self, "无法打开项目", str(exc))
            self._close_project()
            return
        self.rescan()

    def rescan(self):
        if not self.source_root or not self.store:
            return
        self._set_busy(True, "正在递归扫描图片和视频……")
        worker = FunctionWorker(scan_media, self.source_root)
        worker.signals.finished.connect(self._scan_finished)
        worker.signals.failed.connect(self._worker_failed)
        self._worker = worker
        self.thread_pool.start(worker)

    def _scan_finished(self, result: ScanResult):
        assert self.store is not None
        self.store.sync_scan(result.items)
        if self.project_context is not None:
            try:
                scan_into_project(self.project_context, result)
            except Exception as exc:  # noqa: BLE001
                self.status_text.setText(f"素材稳定身份写入失败：{exc}")
        self.scan_result = result
        self._set_busy(False)
        self.rescan_button.setEnabled(True)
        self.label_manager_button.setEnabled(True)
        self.shortcut_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self._refresh_labels()
        self._rebuild_targets()
        collision_note = (
            f"；发现 {len(result.flat_collisions)} 组同名冲突，纯平铺将禁用"
            if result.has_flat_collisions
            else "；未发现平铺同名冲突"
        )
        self.status_text.setText(
            f"扫描完成：图片 {result.image_count}，视频 {result.video_count}{collision_note}"
        )

    def _worker_failed(self, detail: str):
        self._set_busy(False)
        QMessageBox.critical(self, "操作失败", detail)

    def _set_busy(self, busy: bool, message: str = ""):
        self.open_button.setEnabled(not busy)
        self.rescan_button.setEnabled(not busy and self.source_root is not None)
        self.export_button.setEnabled(not busy and self.source_root is not None)
        if message:
            self.status_text.setText(message)
        if busy and not self._busy_cursor_active:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._busy_cursor_active = True
        elif not busy and self._busy_cursor_active:
            QApplication.restoreOverrideCursor()
            self._busy_cursor_active = False

    def _rebuild_targets(self):
        if not self.store:
            return
        media = self.store.list_media()
        mode = str(self.mode_combo.currentData())
        targets: list[ReviewTarget] = []
        if mode == "all":
            for item in media:
                targets.append(
                    ReviewTarget(
                        target_key=item.media_key,
                        display_path=item.relative_path,
                        members=[item],
                        preview_images=[item] if item.media_type == "image" else [],
                        primary=item,
                    )
                )
        else:
            groups: dict[str, list[MediaItem]] = {}
            for item in media:
                groups.setdefault(item.pair_key, []).append(item)
            for pair_key, members in groups.items():
                images = sorted(
                    [item for item in members if item.media_type == "image"],
                    key=lambda value: value.relative_path.casefold(),
                )
                if not images:
                    continue
                targets.append(
                    ReviewTarget(
                        target_key=pair_key,
                        # The row represents the primary image. Keep its real suffix so
                        # users can distinguish JPG/PNG/BMP variants at a glance.
                        display_path=images[0].relative_path,
                        members=sorted(members, key=lambda value: value.relative_path.casefold()),
                        preview_images=images,
                        primary=images[0],
                    )
                )
            targets.sort(key=lambda value: value.display_path.casefold())
        self.targets = targets
        saved_index = int(self.store.get_meta(f"current_index_{mode}", "0") or 0)
        self.current_index = min(max(saved_index, 0), len(targets) - 1) if targets else -1
        self.media_list.blockSignals(True)
        # 大批量填充时先关闭重绘，避免逐条刷新导致的卡顿（X-AnyLabeling 同款做法）。
        self.media_list.setUpdatesEnabled(False)
        try:
            self.media_list.clear()
            labels_map = self.store.label_names_by_media(
                target.primary.media_key for target in targets
            )
            for target in targets:
                labels = labels_map.get(target.primary.media_key, [])
                prefix = "✓ " if labels else "○ "
                item = QListWidgetItem(prefix + target.display_path)
                item.setToolTip(
                    f"{target.display_path}\n标签："
                    + (", ".join(labels) if labels else "未标记")
                    + "\n提示：Shift+滚轮 或 触控板横扫 可左右查看完整路径"
                )
                self.media_list.addItem(item)
        finally:
            self.media_list.setUpdatesEnabled(True)
            self.media_list.blockSignals(False)
        # 批量插入后必须重新布局，否则横向滚动范围会停留在旧值（0）。
        self.media_list.doItemsLayout()
        if self.current_index >= 0:
            # Clearing/refilling a QListWidget while its signals are blocked can leave
            # the numerical row unchanged. Qt then emits no currentRowChanged signal,
            # so a video preview from the previous mode remains on screen. Select and
            # load explicitly to make a mode switch deterministic.
            self.media_list.blockSignals(True)
            self.media_list.setCurrentRow(self.current_index)
            self.media_list.blockSignals(False)
            self._select_index(self.current_index)
        else:
            self._clear_preview("当前模式下没有可复核媒体")
        self._update_progress()

    def _select_index(self, index: int):
        if index < 0 or index >= len(self.targets):
            return
        self.current_index = index
        target = self.targets[index]
        mode = str(self.mode_combo.currentData())
        if self.store:
            self.store.set_meta(f"current_index_{mode}", str(index))
        self.path_label.setText(
            f"{target.display_path}\n组内文件：{len(target.members)}；"
            f"图片 {len(target.preview_images)}；视频 {sum(m.media_type == 'video' for m in target.members)}"
        )
        self._stop_player()
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        if target.preview_images:
            for image in target.preview_images:
                self.variant_combo.addItem(image.filename, image.absolute_path)
        self.variant_combo.setVisible(len(target.preview_images) > 1)
        self.variant_combo.blockSignals(False)
        if mode == "all" and target.primary.media_type == "video":
            self._load_video(target.primary.absolute_path)
        elif target.preview_images:
            self._load_image(target.preview_images[0].absolute_path)
        self._refresh_label_states()
        self._update_progress()

    def _load_selected_variant(self, index: int):
        if index >= 0:
            path = self.variant_combo.itemData(index)
            if path:
                self._load_image(str(path))

    def _load_image(self, path: str):
        self._stop_player()
        self.image_view.set_image(path)
        self.preview_stack.setCurrentWidget(self.image_view)

    def _load_video(self, path: str):
        self.player.setSource(QUrl.fromLocalFile(path))
        self.preview_stack.setCurrentWidget(self.video_widget)
        self.player.play()

    def _clear_preview(self, message: str):
        self._stop_player()
        self.image_view.clear_image(message)
        self.preview_stack.setCurrentWidget(self.image_view)
        self.path_label.setText(message)
        self.label_summary.setText("未选择媒体")

    def _stop_player(self):
        self.player.stop()
        self.player.setSource(QUrl())

    def _media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
            self.player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.video_message.setText("视频无法播放\n" + self.player.errorString())
            self.preview_stack.setCurrentWidget(self.video_message)

    def _media_error(self, _error, error_string: str):
        if error_string:
            self.video_message.setText("视频播放错误\n" + error_string)
            self.preview_stack.setCurrentWidget(self.video_message)

    def toggle_play_pause(self):
        if self.preview_stack.currentWidget() is not self.video_widget:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def restart_video(self):
        if self.preview_stack.currentWidget() is self.video_widget:
            self.player.setPosition(0)
            self.player.play()

    def _set_muted(self, muted: bool):
        self.audio_output.setMuted(muted)

    def _refresh_labels(self):
        while self.label_container_layout.count() > 1:
            item = self.label_container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.label_checks.clear()
        if not self.store:
            return
        for label in self.store.labels():
            checkbox = QCheckBox(
                f"{label.name}" + (f"    [{label.shortcut}]" if label.shortcut else "")
            )
            checkbox.setTristate(True)
            checkbox.setStyleSheet(
                f"QCheckBox{{padding:7px;border-left:8px solid {label.color};}}"
            )
            checkbox.stateChanged.connect(
                lambda state, label_id=label.label_id: self._label_toggled(label_id, state)
            )
            self.label_container_layout.insertWidget(
                self.label_container_layout.count() - 1, checkbox
            )
            self.label_checks[label.label_id] = checkbox
        self._refresh_label_states()
        self._register_shortcuts()

    def _current_keys(self) -> list[str]:
        if self.current_index < 0 or self.current_index >= len(self.targets):
            return []
        return [item.media_key for item in self.targets[self.current_index].members]

    def _refresh_label_states(self):
        if not self.store:
            return
        keys = self._current_keys()
        states = self.store.label_states(keys)
        self._setting_label_states = True
        try:
            for label_id, checkbox in self.label_checks.items():
                state = states.get(label_id, 0)
                qt_state = (
                    Qt.CheckState.Unchecked
                    if state == 0
                    else Qt.CheckState.PartiallyChecked
                    if state == 1
                    else Qt.CheckState.Checked
                )
                checkbox.setCheckState(qt_state)
        finally:
            self._setting_label_states = False
        names = []
        if keys:
            labels_by_media = self.store.label_names_by_media(keys)
            names = sorted({name for values in labels_by_media.values() for name in values})
        self.label_summary.setText("当前标签：" + (", ".join(names) if names else "未标记"))

    def _label_toggled(self, label_id: int, state: int):
        if self._setting_label_states or not self.store:
            return
        keys = self._current_keys()
        checked = Qt.CheckState(state) != Qt.CheckState.Unchecked
        assignment = "image_group" if self.mode_combo.currentData() == "images" else "manual"
        self.store.set_label(keys, label_id, checked, assignment)
        self._refresh_label_states()
        self._update_current_list_item()
        self._update_progress()

    def _toggle_label_shortcut(self, label_id: int):
        checkbox = self.label_checks.get(label_id)
        if not checkbox:
            return
        checkbox.setCheckState(
            Qt.CheckState.Unchecked
            if checkbox.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def clear_current_labels(self):
        if self.store:
            self.store.clear_labels(self._current_keys())
            self._refresh_label_states()
            self._update_current_list_item()
            self._update_progress()

    def _update_current_list_item(self):
        if not self.store or self.current_index < 0:
            return
        keys = self._current_keys()
        names = sorted(
            {
                name
                for values in self.store.label_names_by_media(keys).values()
                for name in values
            }
        )
        target = self.targets[self.current_index]
        item = self.media_list.item(self.current_index)
        item.setText(("✓ " if names else "○ ") + target.display_path)
        item.setToolTip("标签：" + (", ".join(names) if names else "未标记"))

    def _update_progress(self):
        if not self.store:
            return
        labeled_keys = self.store.media_keys_with_labels(
            item.media_key for target in self.targets for item in target.members
        )
        labeled = sum(
            any(item.media_key in labeled_keys for item in target.members)
            for target in self.targets
        )
        position = self.current_index + 1 if self.current_index >= 0 else 0
        self.status_text.setText(
            f"进度 {position}/{len(self.targets)}；已标记 {labeled}；未标记 {len(self.targets) - labeled}"
        )

    def previous_item(self):
        if self.targets:
            self.media_list.setCurrentRow(max(0, self.current_index - 1))

    def next_item(self):
        if self.targets:
            self.media_list.setCurrentRow(min(len(self.targets) - 1, self.current_index + 1))

    def next_unlabeled(self):
        if not self.store or not self.targets:
            return
        labeled_keys = self.store.media_keys_with_labels(
            item.media_key for target in self.targets for item in target.members
        )
        for offset in range(1, len(self.targets) + 1):
            index = (self.current_index + offset) % len(self.targets)
            target = self.targets[index]
            if not any(item.media_key in labeled_keys for item in target.members):
                self.media_list.setCurrentRow(index)
                return

    def delete_current(self):
        if not self.delete_service or self.current_index < 0:
            return
        target = self.targets[self.current_index]
        items = target.members if self.mode_combo.currentData() == "images" else [target.primary]
        answer = QMessageBox.question(
            self,
            "移动到 _delete",
            f"将 {len(items)} 个文件移动到：\n{self.delete_service.batch_root}\n\n此操作不会永久删除，可以撤销。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._stop_player()
        try:
            result = self.delete_service.delete(items)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.status_text.setText(f"已移动 {len(result.moved)} 个文件到 {self.delete_service.batch_root}")
        old_index = self.current_index
        self._rebuild_targets()
        if self.targets:
            self.media_list.setCurrentRow(min(old_index, len(self.targets) - 1))

    def undo_delete(self):
        if not self.delete_service:
            return
        self._stop_player()
        try:
            count = self.delete_service.undo_latest()
        except Exception as exc:
            QMessageBox.critical(self, "撤销删除失败", str(exc))
            return
        if not count:
            QMessageBox.information(self, "撤销删除", "没有可以撤销的删除操作")
            return
        self.status_text.setText(f"已恢复 {count} 个文件")
        self._rebuild_targets()

    def manage_labels(self):
        if not self.store:
            return
        dialog = LabelManagerDialog(self.store, self._action_shortcuts(), self)
        dialog.labels_changed.connect(self._labels_changed)
        dialog.exec()
        self._save_label_catalog()
        self._refresh_labels()
        self._rebuild_targets()

    def _labels_changed(self):
        self._save_label_catalog()
        self._refresh_labels()

    def _action_shortcuts(self) -> dict[str, str]:
        if not self.store:
            return dict(DEFAULT_ACTION_SHORTCUTS)
        stored = self.store.get_json_meta("action_shortcuts", {})
        return {**DEFAULT_ACTION_SHORTCUTS, **stored}

    def manage_shortcuts(self):
        if not self.store:
            return
        label_shortcuts = {label.name: label.shortcut for label in self.store.labels()}
        dialog = ShortcutDialog(self._action_shortcuts(), label_shortcuts, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.set_json_meta("action_shortcuts", dialog.values())
            self._register_shortcuts()

    def _register_shortcuts(self):
        for shortcut in self.shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.shortcuts.clear()
        actions = {
            "previous": self.previous_item,
            "next": self.next_item,
            "delete": self.delete_current,
            "undo_delete": self.undo_delete,
            "play_pause": self.toggle_play_pause,
            "clear_labels": self.clear_current_labels,
            "next_unlabeled": self.next_unlabeled,
        }
        # 只在旧复核列表页生效：进入推理复核台/其他页面时不与应用内快捷键冲突
        review_context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for action, key in self._action_shortcuts().items():
            if key and action in actions:
                shortcut = QShortcut(QKeySequence(key), self.centralWidget())
                shortcut.setContext(review_context)
                shortcut.activated.connect(actions[action])
                self.shortcuts.append(shortcut)
        if self.store:
            for label in self.store.labels():
                if label.shortcut:
                    shortcut = QShortcut(QKeySequence(label.shortcut), self.centralWidget())
                    shortcut.setContext(review_context)
                    shortcut.activated.connect(
                        lambda label_id=label.label_id: self._toggle_label_shortcut(label_id)
                    )
                    self.shortcuts.append(shortcut)
        self._install_shortcut_guard()

    def _install_shortcut_guard(self):
        """文本输入时禁用标注类快捷键，避免空格/退格误触发（评审第 4 节）。"""
        if getattr(self, "_shortcut_guard", None) is None:
            from .ui.shortcuts import ShortcutGuard

            self._shortcut_guard = ShortcutGuard(self.centralWidget())
        for widget in self.findChildren(QWidget):
            if widget.property("dshShortcutGuarded"):
                continue
            widget.installEventFilter(self._shortcut_guard)
            widget.setProperty("dshShortcutGuarded", True)

    def show_inference_workspace(self):
        # 统一入口，视频推理与视觉复核使用同一套工作流、模型库和画布。
        self.show_page("workbench")

    def show_review(self):
        self.main_stack.setCurrentIndex(0)

    def show_page(self, key: str):
        for nav_key, button in self._nav_buttons.items():
            button.setChecked(nav_key == key)
        if key == "review":
            self.main_stack.setCurrentIndex(0)
            return
        if key not in self._pages:
            page = self._create_page(key)
            if page is None:
                return
            self._pages[key] = page
            self.main_stack.addWidget(page)
            self.language_manager.apply(page)
        page = self._pages[key]
        # Show first: the global language event filter translates newly visible
        # widgets. Context is applied afterwards so a real project title cannot be
        # replaced by the construction-time "未打开项目" placeholder.
        self.main_stack.setCurrentWidget(page)
        if hasattr(page, "set_context"):
            try:
                page.set_context(self.project_context)
            except Exception as exc:  # noqa: BLE001
                self.status_text.setText(f"页面初始化失败：{exc}")
        if hasattr(page, "reload_workflows"):
            page.reload_workflows()
        self.language_manager.apply(page)

    def _create_page(self, key: str):
        if key == "workbench":
            from .ui.review_page import ReviewPage

            page = ReviewPage()
            page.status_changed.connect(lambda text: self.status_text.setText(text))
            return page
        if key == "workflow":
            from .ui.workflow_page import WorkflowPage

            return WorkflowPage()
        if key == "model":
            from .ui.model_page import ModelPage

            return ModelPage()
        if key == "task":
            from .ui.task_page import TaskPage

            return TaskPage()
        return None

    def export_files(self):
        if not self.source_root or not self.store:
            return
        active_items = self.store.list_media()
        has_collisions = bool(flat_collisions(active_items))
        dialog = ExportDialog(has_collisions, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        layout_mode = dialog.layout_mode()
        self._set_busy(True, "正在复制文件并生成分类目录……")
        worker = FunctionWorker(export_labeled, self.source_root, self.store, layout_mode)
        worker.signals.finished.connect(self._export_finished)
        worker.signals.failed.connect(self._worker_failed)
        self._worker = worker
        self.thread_pool.start(worker)

    def _export_finished(self, result):
        self._set_busy(False)
        if result.complete:
            QMessageBox.information(
                self,
                "导出完成",
                f"已复制 {result.copied_files} 个标签文件项。\n输出目录：\n{result.output_directory}",
            )
        else:
            QMessageBox.warning(
                self,
                "导出未完全成功",
                f"成功复制 {result.copied_files} 项，失败 {len(result.errors)} 项。\n"
                f"结果保留在：\n{result.output_directory}\n\n"
                + "\n".join(result.errors[:10]),
            )

    def _close_project(self):
        self._stop_player()
        # 先让页面把未落库的人工修订写入数据库，再断开连接
        for page in self._pages.values():
            flush = getattr(page, "flush_pending_edits", None)
            if callable(flush):
                try:
                    flush()
                except Exception:  # noqa: BLE001
                    pass
        if self.project_context is not None:
            self.project_context.close()
            self.project_context = None
        if self.store:
            self.store.close()
        self.store = None
        self.delete_service = None
        self.scan_result = None
        self.targets = []
        self.current_index = -1
        self.media_list.clear()

    def closeEvent(self, event):
        self._close_project()
        super().closeEvent(event)


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationDisplayName(f"{PRODUCT_NAME} · {PRODUCT_SUBTITLE_EN}")
    app.setOrganizationName("LocalTools")
    icon = application_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    try:
        from .ui.theme import apply_theme

        apply_theme(app)
    except Exception:
        pass
    # 启动时发现并注册本地插件（评审第 8 节）：插件节点此后可在工作流页直接使用
    try:
        from .plugins import PluginHost

        host = PluginHost()
        host.discover()
        app.setProperty("dshPluginSummary", host.summary())
    except Exception:
        pass
    window = MainWindow()
    window.show()
    return app.exec()
