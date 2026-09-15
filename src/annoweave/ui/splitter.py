"""带"抓取点"的分隔条：让左右/上下的拖动区更好点中、也更好看懂。

默认的 QSplitter 分隔条只有 1~5 像素宽、没有任何视觉提示，在深色界面上几乎
看不见，鼠标要很精准才能按到 —— 这也是"用起来非常生硬"的来源之一。

GripSplitter 做了三件事：
1. 分隔条默认加宽到 9 像素，命中面积变大；
2. 中间画三个抓取点（横分隔条为竖排、竖分隔条为横排），悬停/拖动时换成强调色；
3. 悬停时把鼠标指针换成左右（或上下）调整光标，并在整个分隔条上生效。

> 实现注意：`QSplitterHandle` **没有** `isSliderDown()`（那是 `QAbstractSlider` 的 API）。
> 拖动状态必须自己用鼠标事件跟踪；`paintEvent` 里也要保证 `QPainter` 一定会 `end()`，
> 否则一旦抛异常就会连锁刷出 `QBackingStore::endPaint() called with active painter`。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSplitter, QSplitterHandle

from .theme import TOKENS

#: 抓取点的直径与间距（像素）
_DOT = 3
_GAP = 4
_DEFAULT_HANDLE_WIDTH = 9


class GripSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        #: 自己跟踪"正在拖动"——QSplitterHandle 没有 isSliderDown/isPressed 之类的查询接口。
        self._pressed = False
        horizontal = orientation == Qt.Orientation.Horizontal
        self.setCursor(
            Qt.CursorShape.SplitHCursor if horizontal else Qt.CursorShape.SplitVCursor
        )

    # ---------------------------------------------------------------- 状态
    @property
    def dragging(self) -> bool:
        return self._pressed

    def is_active(self) -> bool:
        """悬停或正在拖动时高亮。"""
        return bool(self._pressed) or self.underMouse()

    def _colors(self) -> tuple[QColor, QColor]:
        base = QColor(TOKENS["border"])
        if self.is_active():
            accent = QColor(TOKENS["brand_accent"])
            return accent, accent
        return base, QColor("#4C687D")

    def mousePressEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            self.update()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):  # noqa: N802 (Qt 命名)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt 命名)
        self.update()
        super().leaveEvent(event)

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect()
            track, dot = self._colors()
            painter.fillRect(rect, track)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            count = 3
            span = count * _DOT + (count - 1) * _GAP
            if self.orientation() == Qt.Orientation.Horizontal:
                cx = rect.center().x() + 0.5
                top = rect.center().y() + 0.5 - span / 2.0
                for index in range(count):
                    y = top + index * (_DOT + _GAP)
                    painter.drawEllipse(QRectF(cx - _DOT / 2.0, y, _DOT, _DOT))
            else:
                cy = rect.center().y() + 0.5
                left = rect.center().x() + 0.5 - span / 2.0
                for index in range(count):
                    x = left + index * (_DOT + _GAP)
                    painter.drawEllipse(QRectF(x, cy - _DOT / 2.0, _DOT, _DOT))
        finally:
            # 无论画成什么样都要结束 painter，否则后面所有绘制都会刷错误。
            if painter.isActive():
                painter.end()


class GripSplitter(QSplitter):
    """分隔条带抓取点的 QSplitter。用法与 QSplitter 完全一致。"""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None, *, handle_width: int = _DEFAULT_HANDLE_WIDTH):
        super().__init__(orientation, parent)
        self.setHandleWidth(max(4, int(handle_width)))
        self.setOpaqueResize(True)

    def createHandle(self) -> QSplitterHandle:  # noqa: N802 (Qt 命名)
        return GripSplitterHandle(self.orientation(), self)
