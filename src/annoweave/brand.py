"""AnnoWeave brand constants and scalable Qt logo widgets."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

PRODUCT_NAME = "AnnoWeave"
PRODUCT_SUBTITLE_EN = "Local Visual AI Review & Workflow"
PRODUCT_SUBTITLE_ZH = "本地视觉推理与复核工作台"
WINDOW_TITLE_ZH = f"{PRODUCT_NAME} · {PRODUCT_SUBTITLE_ZH}"
WINDOW_TITLE_EN = f"{PRODUCT_NAME} · {PRODUCT_SUBTITLE_EN}"


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / relative


def application_icon() -> QIcon:
    path = resource_path("annoweave/assets/annoweave-mark.svg")
    return QIcon(str(path)) if path.exists() else QIcon()


class AnnoWeaveMark(QWidget):
    """Compact logo: annotation frame with two interlaced model routes."""

    def __init__(self, size: int = 30, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width(), self.height()) / 32.0
        painter.scale(scale, scale)
        painter.setPen(QPen(QColor("#8FA4BC"), 2.2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        # Four open annotation-frame corners.
        for points in (
            [(3, 10), (3, 3), (10, 3)], [(22, 3), (29, 3), (29, 10)],
            [(3, 22), (3, 29), (10, 29)], [(22, 29), (29, 29), (29, 22)],
        ):
            path = QPainterPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            painter.drawPath(path)
        # Two routes weave through the frame and converge at the verified output.
        painter.setPen(QPen(QColor("#5B8CFF"), 2.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        upper = QPainterPath()
        upper.moveTo(8, 10)
        upper.lineTo(15, 10)
        upper.lineTo(19, 16)
        upper.lineTo(25, 16)
        painter.drawPath(upper)
        painter.setPen(QPen(QColor("#22C7B8"), 2.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        lower = QPainterPath()
        lower.moveTo(8, 22)
        lower.lineTo(14, 22)
        lower.lineTo(18, 16)
        lower.lineTo(25, 16)
        painter.drawPath(lower)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, color in ((8, 10, "#5B8CFF"), (8, 22, "#22C7B8"),
                            (25, 16, "#22C7B8")):
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(x - 2.2, y - 2.2, 4.4, 4.4))
        painter.end()


class BrandLockup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(AnnoWeaveMark(30, self))
        label = QLabel(PRODUCT_NAME)
        label.setObjectName("BrandWordmark")
        label.setProperty("role", "title")
        row.addWidget(label)
