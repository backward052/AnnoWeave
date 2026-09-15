"""复核工作台左侧素材队列（评审第 6 节）。

缩略图、文件名、类型、时长、复核状态；搜索与“未复核 / 模型分歧 / 已修改”过滤；
同名组可展开查看成员与操作范围。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

THUMB = 56

FILTERS = (
    ("all", "全部素材"),
    ("disagreement", "模型分歧"),
    ("modified", "已修改"),
    ("video", "仅视频"),
    ("image", "仅图片"),
)


@dataclass
class QueueEntry:
    """队列中的一个素材（同名组的代表项）。"""

    key: str
    display: str
    media_id: str
    media_type: str
    path: str
    duration_s: float = 0.0
    frame_count: int = 0
    reviewed_count: int = 0
    total_frames: int = 0
    modified: bool = False
    disagreement: bool = False
    thumbnail_path: str = ""
    members: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.disagreement:
            return "分歧"
        if self.modified:
            return "已修改"
        if self.reviewed_count:
            return "有历史结论"
        return "未修改"

    @property
    def type_text(self) -> str:
        return "视频" if self.media_type == "video" else "图片"

    def subtitle(self) -> str:
        parts = [self.type_text]
        if self.media_type == "video" and self.duration_s:
            parts.append(_duration_text(self.duration_s))
        parts.append(self.status)
        return " · ".join(parts)


def _duration_text(seconds: float) -> str:
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class MediaQueuePanel(QWidget):
    """素材队列面板。"""

    entry_activated = Signal(str)  # queue key
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(230)
        self.setMaximumWidth(280)
        self._entries: list[QueueEntry] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("素材队列")
        title.setProperty("role", "title")
        layout.addWidget(title)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文件名或路径")
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        row = QHBoxLayout()
        self.filter_combo = QComboBox()
        for key, label in FILTERS:
            self.filter_combo.addItem(label, key)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        row.addWidget(self.filter_combo, 1)
        layout.addLayout(row)

        self.group_check = QCheckBox("同名组展开成员")
        self.group_check.toggled.connect(self._apply_filter)
        layout.addWidget(self.group_check)

        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setUniformItemSizes(False)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.verticalScrollBar().valueChanged.connect(self._schedule_visible_icons)
        layout.addWidget(self.list, 1)

        self.summary = QLabel("尚未打开素材")
        self.summary.setProperty("role", "muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

    # ----------------------------- 数据 ----------------------------- #
    def set_entries(self, entries: list[QueueEntry]):
        self._entries = list(entries)
        # 重建状态列表不是用户切换素材，恢复选中项不能再次打开视频。
        previous = self.blockSignals(True)
        try:
            self._apply_filter()
        finally:
            self.blockSignals(previous)
        self._schedule_visible_icons()

    def entries(self) -> list[QueueEntry]:
        return list(self._entries)

    def current_key(self) -> str:
        item = self.list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def select_key(self, key: str) -> bool:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) == key:
                self.list.setCurrentRow(row)
                return True
        return False

    def update_entry_status(self, key: str, *, reviewed_count: int, total_frames: int,
                            modified: bool, disagreement: bool) -> None:
        """只更新一个可见条目的轻量状态，避免切帧时重建整个项目队列。"""
        entry = next((value for value in self._entries if value.key == key), None)
        if entry is None:
            return
        entry.reviewed_count = int(reviewed_count)
        entry.total_frames = max(int(entry.total_frames), int(total_frames))
        entry.modified = bool(modified)
        entry.disagreement = bool(disagreement)
        for row in range(self.list.count()):
            item = self.list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") != key or not (
                    item.flags() & Qt.ItemFlag.ItemIsSelectable):
                continue
            display = Path(entry.display).name if Path(entry.display).is_absolute() else entry.display
            item.setText(f"{display}\n{entry.subtitle()}")
            item.setToolTip(f"{entry.path}\n状态：{entry.status}")
            color = self._color_for(entry)
            item.setForeground(color if color is not None else QColor())
            break

    # ----------------------------- 过滤/渲染 ----------------------------- #
    def _matches(self, entry: QueueEntry, needle: str, mode: str) -> bool:
        if needle and needle not in entry.display.casefold() and needle not in entry.path.casefold():
            return False
        if mode == "disagreement":
            return entry.disagreement
        if mode == "modified":
            return entry.modified
        if mode == "video":
            return entry.media_type == "video"
        if mode == "image":
            return entry.media_type == "image"
        return True

    def _apply_filter(self, *_args):
        needle = self.search_edit.text().strip().casefold()
        mode = str(self.filter_combo.currentData())
        previous = self.current_key()
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        for entry in self._entries:
            if not self._matches(entry, needle, mode):
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry.key)
            display = Path(entry.display).name if Path(entry.display).is_absolute() else entry.display
            item.setText(f"{display}\n{entry.subtitle()}")
            item.setToolTip(f"{entry.path}\n状态：{entry.status}")
            # 图片解码放到列表布局完成后，仅加载可见行；大目录不能在这里逐张同步解码。
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.thumbnail_path)
            color = self._color_for(entry)
            if color is not None:
                item.setForeground(color)
            self.list.addItem(item)
            shown += 1
            if self.group_check.isChecked() and len(entry.members) > 1:
                for member in entry.members:
                    child = QListWidgetItem(f"    └ {Path(member).name}")
                    child.setData(Qt.ItemDataRole.UserRole, entry.key)
                    child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    child.setForeground(QColor("#A8B4C3"))
                    self.list.addItem(child)
        self.list.blockSignals(False)
        self.summary.setText(f"显示 {shown} / {len(self._entries)} 个素材")
        if previous and not self.select_key(previous) and self.list.count():
            for row in range(self.list.count()):
                if self.list.item(row).flags() & Qt.ItemFlag.ItemIsSelectable:
                    self.list.setCurrentRow(row)
                    break
        self._schedule_visible_icons()

    def _schedule_visible_icons(self, *_args):
        QTimer.singleShot(0, self._load_visible_icons)

    def _load_visible_icons(self):
        """只解码视口附近的缩略图，避免打开含大量图片的目录时卡住界面。"""
        viewport_rect = self.list.viewport().rect().adjusted(0, -THUMB * 2, 0, THUMB * 2)
        loaded = 0
        for row in range(self.list.count()):
            item = self.list.item(row)
            path = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
            if not path or not self.list.visualItemRect(item).intersects(viewport_rect):
                continue
            entry = next((value for value in self._entries if value.thumbnail_path == path), None)
            if entry is None:
                continue
            icon = self._icon_for(entry)
            if icon is not None:
                item.setIcon(icon)
            loaded += 1
            if loaded >= 32:
                break

    def _icon_for(self, entry: QueueEntry):
        if not entry.thumbnail_path:
            return None
        path = Path(entry.thumbnail_path)
        if not path.exists():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        return QIcon(pixmap.scaled(
            THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))

    def _color_for(self, entry: QueueEntry):
        return {
            "未复核": QColor("#E7EDF5"),
            "复核中": QColor("#F5A623"),
            "已复核": QColor("#2FBF71"),
            "已驳回": QColor("#E05252"),
            "分歧": QColor("#F5A623"),
            "已修改": QColor("#7FB2FF"),
        }.get(entry.status)

    def _on_current_changed(self, current, _previous):
        if current is None:
            return
        if not (current.flags() & Qt.ItemFlag.ItemIsSelectable):
            return
        self.entry_activated.emit(str(current.data(Qt.ItemDataRole.UserRole)))
