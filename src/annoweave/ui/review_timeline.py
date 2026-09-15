"""复核工作台的底部可折叠时间轴（评审第 6 节）。

分轨显示：原始帧 / 采样点 / 命中区间 / 人工关键帧 / 复核状态；
控制：播放暂停、前后原始帧、前后命中点、时间码、倍速。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

LANE_HEIGHT = 18
LANE_GAP = 4
LANES = (
    ("source", "原始帧"),
    ("sample", "采样点"),
    ("hit", "命中区间"),
    ("keyframe", "人工关键帧"),
    ("review", "复核状态"),
)

_COLORS = {
    "source": QColor("#2A3644"),
    "sample": QColor("#19B99A"),
    "hit": QColor("#F5A623"),
    "keyframe": QColor("#7FB2FF"),
    "review": QColor("#2FBF71"),
    "playhead": QColor("#E7EDF5"),
}


@dataclass
class TimelineData:
    """一帧时间轴所需的全部数据。"""

    source_frame_count: int = 0
    source_fps: float = 0.0
    sample_indices: list[int] = field(default_factory=list)
    #: (start_source_frame, end_source_frame, label)
    hit_intervals: list[tuple[int, int, str]] = field(default_factory=list)
    keyframes: set[int] = field(default_factory=set)
    #: source_frame -> pending/confirmed/rejected
    review_states: dict[int, str] = field(default_factory=dict)
    selected_object_label: str = ""
    #: 对象身份 -> [(start, end, label)]，选中某个对象时命中轨只画它的证据
    object_hits: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)

    def visible_hit_intervals(self) -> list[tuple[int, int, str]]:
        """按当前选中对象过滤命中区间；未选中时显示全部。"""
        if not self.selected_object_label:
            return list(self.hit_intervals)
        selected = self.object_hits.get(self.selected_object_label)
        if selected:
            return list(selected)
        # 选中的对象本帧没有命中证据：明确显示为空，而不是退回全部
        return [] if self.object_hits else list(self.hit_intervals)


def timecode(frame_index: int, fps: float) -> str:
    if fps <= 0:
        return f"#{frame_index}"
    total_seconds = frame_index / fps
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


class TimelineRuler(QWidget):
    """分轨标尺本体：绘制轨道与播放头，点击可定位。"""

    frame_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = TimelineData()
        self.current_source_frame = 0
        self.setMinimumHeight(len(LANES) * (LANE_HEIGHT + LANE_GAP) + 18)
        self.setMaximumHeight(len(LANES) * (LANE_HEIGHT + LANE_GAP) + 22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, data: TimelineData):
        self.data = data
        self.update()

    def set_current(self, source_frame: int):
        self.current_source_frame = int(source_frame)
        self.update()

    def _lane_rect(self, lane_index: int) -> QRectF:
        top = 16 + lane_index * (LANE_HEIGHT + LANE_GAP)
        return QRectF(0, top, max(1, self.width() - 1), LANE_HEIGHT)

    def _x_for(self, source_frame: int) -> float:
        total = max(1, self.data.source_frame_count)
        return min(self.width() - 1.0, max(0.0, self.width() * (source_frame / total)))

    def _frame_for(self, x: float) -> int:
        width = max(1, self.width())
        total = max(1, self.data.source_frame_count)
        return max(0, min(total - 1, int(x / width * total)))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111820"))
        painter.setFont(QFont("Microsoft YaHei", 8))
        if self.data.source_frame_count <= 0:
            painter.setPen(QColor("#A8B4C3"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "时间轴：打开视频后显示")
            painter.end()
            return

        for lane_index, (key, label) in enumerate(LANES):
            rect = self._lane_rect(lane_index)
            painter.fillRect(rect, _COLORS["source"])
            painter.setPen(QColor("#A8B4C3"))
            painter.drawText(QRectF(4, rect.top(), 80, rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            if key == "sample":
                painter.setPen(QPen(_COLORS["sample"], 3))
                center = rect.center().y()
                for frame_index in self.data.sample_indices:
                    x = self._x_for(frame_index)
                    painter.drawLine(int(x), int(center - 5), int(x), int(center + 5))
            elif key == "hit":
                painter.setPen(Qt.PenStyle.NoPen)
                for start, end, _label in self.data.visible_hit_intervals():
                    x1 = self._x_for(start)
                    x2 = max(x1 + 2, self._x_for(end + 1))
                    painter.fillRect(QRectF(x1, rect.top() + 3, x2 - x1, rect.height() - 6), _COLORS["hit"])
            elif key == "keyframe":
                painter.setBrush(_COLORS["keyframe"])
                painter.setPen(Qt.PenStyle.NoPen)
                for frame_index in sorted(self.data.keyframes):
                    x = self._x_for(frame_index)
                    painter.drawEllipse(QRectF(x - 3, rect.center().y() - 3, 6, 6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            elif key == "review":
                painter.setPen(Qt.PenStyle.NoPen)
                for frame_index, decision in self.data.review_states.items():
                    color = {
                        "confirmed": _COLORS["review"],
                        "rejected": QColor("#E05252"),
                        "pending": QColor("#F5A623"),
                    }.get(decision, QColor("#A8B4C3"))
                    x1 = self._x_for(frame_index)
                    x2 = max(x1 + 3, self._x_for(frame_index + 1))
                    painter.fillRect(QRectF(x1, rect.top() + 4, x2 - x1, rect.height() - 8), color)

        # 播放头
        x = self._x_for(self.current_source_frame)
        painter.setPen(QPen(_COLORS["playhead"], 2))
        painter.drawLine(int(x), 14, int(x), self.height())
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.data.source_frame_count > 0:
            self.frame_clicked.emit(self._frame_for(event.position().x()))


class TimelinePanel(QWidget):
    """可折叠时间轴：分轨标尺 + 播放控制。"""

    collapse_toggled = Signal(bool)
    source_frame_requested = Signal(int)
    hit_step_requested = Signal(int)
    sample_step_requested = Signal(int)
    play_state_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = TimelineData()
        self._current_source_frame = 0
        self._play_fps = 12.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        sample_controls = QHBoxLayout()
        self.sample_prev_button = QPushButton("上一采样帧  PgUp")
        self.sample_next_button = QPushButton("下一采样帧  PgDn")
        self.sample_prev_button.clicked.connect(lambda: self.sample_step_requested.emit(-1))
        self.sample_next_button.clicked.connect(lambda: self.sample_step_requested.emit(1))
        sample_controls.addWidget(self.sample_prev_button)
        sample_controls.addWidget(self.sample_next_button)
        sample_controls.addWidget(QLabel("按运行选项中的采样率跳转；← / → 微调原始帧"))
        sample_controls.addStretch()
        layout.addLayout(sample_controls)

        controls = QHBoxLayout()
        self.collapse_check = QCheckBox("时间轴")
        self.collapse_check.setChecked(True)
        self.collapse_check.toggled.connect(self._on_collapse)
        controls.addWidget(self.collapse_check)

        self.hit_prev_button = QPushButton("◀ 上一已运行命中")
        self.hit_prev_button.setToolTip("跳到本次会话中已经运行且产生裁剪的上一帧")
        self.hit_prev_button.clicked.connect(lambda: self.hit_step_requested.emit(-1))
        self.play_button = QPushButton("播放")
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._on_play_toggled)
        self.hit_next_button = QPushButton("下一已运行命中 ▶")
        self.hit_next_button.setToolTip("跳到本次会话中已经运行且产生裁剪的下一帧")
        self.hit_next_button.clicked.connect(lambda: self.hit_step_requested.emit(1))
        controls.addWidget(self.hit_prev_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.hit_next_button)

        controls.addWidget(QLabel("逐原始帧"))
        frame_prev = QPushButton("◀")
        frame_prev.setToolTip("上一个原始帧（不是上一个采样点）")
        frame_prev.clicked.connect(lambda: self.source_frame_requested.emit(-1))
        frame_next = QPushButton("▶")
        frame_next.setToolTip("下一个原始帧（不是下一个采样点）")
        frame_next.clicked.connect(lambda: self.source_frame_requested.emit(1))
        controls.addWidget(frame_prev)
        controls.addWidget(frame_next)

        controls.addWidget(QLabel("倍速"))
        self.speed_combo = QComboBox()
        for text, factor in (("0.5×", 0.5), ("1×", 1.0), ("2×", 2.0), ("4×", 4.0)):
            self.speed_combo.addItem(text, factor)
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.currentIndexChanged.connect(self._apply_speed)
        controls.addWidget(self.speed_combo)

        self.timecode_label = QLabel("--:--:--.---")
        self.timecode_label.setStyleSheet("font-family: Consolas, monospace;")
        controls.addWidget(self.timecode_label)
        self.scope_label = QLabel("")
        self.scope_label.setProperty("role", "muted")
        controls.addWidget(self.scope_label)
        controls.addStretch()
        layout.addLayout(controls)

        self.ruler = TimelineRuler()
        self.ruler.frame_clicked.connect(self.source_frame_requested.emit)
        layout.addWidget(self.ruler)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._apply_speed()

    # ----------------------------- 状态 ----------------------------- #
    def set_data(self, data: TimelineData):
        self.data = data
        self.ruler.set_data(data)
        self.timecode_label.setText(timecode(self._current_source_frame, data.source_fps))
        # 一个命中时跳转只会回到原帧；隐藏这组低频动作，避免伪装成整段视频检索。
        has_navigation = len(data.hit_intervals) > 1
        self.hit_prev_button.setVisible(has_navigation)
        self.hit_next_button.setVisible(has_navigation)
        self.hit_prev_button.setEnabled(has_navigation)
        self.hit_next_button.setEnabled(has_navigation)
        if data.source_frame_count <= 0:
            self.play_button.setEnabled(False)
        else:
            self.play_button.setEnabled(True)
        self._sync_scope_label()

    def _sync_scope_label(self):
        if self.data.selected_object_label:
            self.scope_label.setText(f"仅显示：{self.data.selected_object_label} 的证据")
        else:
            self.scope_label.setText("显示全部对象证据")

    def set_selected_object(self, label: str):
        self.data.selected_object_label = label
        self._sync_scope_label()

    def set_current_source_frame(self, source_frame: int):
        self._current_source_frame = int(source_frame)
        self.ruler.set_current(source_frame)
        self.timecode_label.setText(timecode(source_frame, self.data.source_fps))

    def current_source_frame(self) -> int:
        return self._current_source_frame

    def is_playing(self) -> bool:
        return self._timer.isActive()

    # ----------------------------- 播放 ----------------------------- #
    def _apply_speed(self, *_args):
        factor = float(self.speed_combo.currentData() or 1.0)
        base = self.data.source_fps or 12.0
        interval = max(15, int(1000.0 / max(1.0, base * factor)))
        self._timer.setInterval(interval)

    def _on_play_toggled(self, playing: bool):
        if playing:
            if self.data.source_frame_count <= 0:
                self.play_button.setChecked(False)
                return
            self.play_button.setText("暂停")
            self._apply_speed()
            self._timer.start()
        else:
            self.play_button.setText("播放")
            self._timer.stop()
        self.play_state_changed.emit(playing)

    def stop(self):
        if self.play_button.isChecked():
            self.play_button.setChecked(False)
        else:
            self._timer.stop()

    def _tick(self):
        if self.data.source_frame_count <= 0:
            self.stop()
            return
        if self._current_source_frame >= self.data.source_frame_count - 1:
            self.stop()
            return
        self.source_frame_requested.emit(1)

    def _on_collapse(self, expanded: bool):
        self.ruler.setVisible(expanded)
        for widget in (
            self.hit_prev_button,
            self.play_button,
            self.hit_next_button,
            self.speed_combo,
            self.timecode_label,
        ):
            widget.setVisible(expanded)
        self.collapse_toggled.emit(expanded)
