"""像素级平滑滚动的列表控件（纵向 + 横向）。

评审反馈：文件列表里横向拖动查看长文件名时"非常生硬"。原因有三个，
都在 Qt 的默认行为里：

1. 默认 `ScrollPerItem`：横向滚动条一格跳一个条目宽度，没有中间位置；
2. 主题只定义了纵向滚动条样式，横向滚动条回退到 Windows 原生外观，
   在深色界面上又细又亮，抓不住；
3. 滚轮事件只处理纵向，长列表里想左右看只能去够滚动条。

本模块统一解决：像素级滚动 + 140ms OutCubic 平滑动画 + 横向滚轮/Shift+滚轮
接管 + 拖动滑块时立即停止动画（不抢手）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import QAbstractItemView, QListWidget

#: 一次鼠标滚轮"格"（120 单位）对应的像素位移
DEFAULT_WHEEL_STEP = 72
#: 平滑动画时长（毫秒）。太长会觉得拖沓，太短就没有平滑感。
SCROLL_DURATION_MS = 140


class SmoothListWidget(QListWidget):
    """带平滑滚动的列表。

    参数
    ----
    allow_horizontal:
        True 时保留横向滚动条（长文件名场景），并让 Shift+滚轮、触控板横扫、
        横向滚轮都能平滑地左右移动；False（默认）则关闭横向滚动条，靠 wordWrap
        或调用方自己控制换行。
    wheel_step:
        鼠标滚轮一格的像素步长。
    scroll_duration:
        平滑动画时长，单位毫秒；传 0 表示关闭动画（完全跟手）。
    min_row_height:
        统一的最小行高。设置后开启 `setUniformItemSizes`，长列表布局不再抖动。
    word_wrap:
        是否允许条目文字折行。长文件名的列表建议关掉（配合横向滚动条），
        缩略图/多行说明的列表保留开启。
    """

    def __init__(
        self,
        parent=None,
        *,
        allow_horizontal: bool = False,
        wheel_step: int = DEFAULT_WHEEL_STEP,
        scroll_duration: int = SCROLL_DURATION_MS,
        min_row_height: Optional[int] = None,
        word_wrap: bool = True,
    ):
        super().__init__(parent)
        self._allow_horizontal = bool(allow_horizontal)
        self._wheel_step = max(8, int(wheel_step))
        self._scroll_duration = max(0, int(scroll_duration))
        self._min_row_height = min_row_height

        # 像素级滚动：滚轮一格=若干像素，而不是跳一整个条目。
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSpacing(4)
        self.setWordWrap(bool(word_wrap))
        # 悬停反馈需要开启鼠标跟踪，否则要按下一次才高亮。
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        if min_row_height:
            self.setUniformItemSizes(True)
        if self._allow_horizontal:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._vertical_animation = self._make_animation(self.verticalScrollBar(), 20)
        self._horizontal_animation = self._make_animation(self.horizontalScrollBar(), 24)
        # 兼容旧调用方/测试使用的属性名。
        self._scroll_animation = self._vertical_animation

    # ------------------------------------------------------------------ #
    def _make_animation(self, bar, single_step: int) -> QPropertyAnimation:
        bar.setSingleStep(single_step)
        animation = QPropertyAnimation(bar, b"value", self)
        animation.setDuration(self._scroll_duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # 用户一旦直接抓住滑块，动画必须立刻让位，否则会与手动拖动打架（"生硬"的主因之一）。
        bar.sliderPressed.connect(animation.stop)
        bar.actionTriggered.connect(lambda _action, a=animation: a.stop())
        return animation

    def row_height(self) -> Optional[int]:
        return self._min_row_height

    # ------------------------------------------------------------------ #
    def _animate(self, animation: QPropertyAnimation, bar, delta: float) -> None:
        """按"滚动条数值增量"做平滑动画。

        delta 已经带好方向：纵向滚轮向上一格 = value 减小，横向滚轮向左一格
        = value 减小（与 Qt 原生横向滚动条一致）。
        """
        if not delta:
            return
        step = int(round(delta))
        if step == 0:
            return
        if self._scroll_duration == 0:
            animation.stop()
            bar.setValue(bar.value() + step)
            return
        previous = (
            int(animation.endValue())
            if animation.state() == QAbstractAnimation.State.Running
            else bar.value()
        )
        # 反向滚动立即换向；同向连续滚动累加目标，避免"每格重新起步"。
        if (previous - bar.value()) * step < 0:
            previous = bar.value()
        target = min(bar.maximum(), max(bar.minimum(), previous + step))
        animation.stop()
        animation.setStartValue(bar.value())
        animation.setEndValue(target)
        animation.start()

    def wheelEvent(self, event):
        pixel = event.pixelDelta()
        angle = event.angleDelta()
        horizontal = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # 触控板：已经有连续像素，直接跟手移动，不叠加惯性。
        if not pixel.isNull() and (pixel.x() or pixel.y()):
            self._stop_animations()
            if self._allow_horizontal and (horizontal or not pixel.y()):
                bar = self.horizontalScrollBar()
                bar.setValue(bar.value() - pixel.x())
            else:
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() - pixel.y())
            event.accept()
            return

        dx = float(angle.x())
        dy = float(angle.y())
        if not dx and not dy:
            super().wheelEvent(event)
            return

        if self._allow_horizontal and (horizontal or (dx and not dy)):
            # Shift+滚轮 ⇒ 横向；横向滚轮本身也直接走横向。
            delta = dx if dx else dy
            self._animate(self._horizontal_animation, self.horizontalScrollBar(),
                          -delta / 120.0 * self._wheel_step)
        else:
            self._animate(self._vertical_animation, self.verticalScrollBar(),
                          -dy / 120.0 * self._wheel_step)
        event.accept()

    def _stop_animations(self) -> None:
        self._vertical_animation.stop()
        self._horizontal_animation.stop()

    # ------------------------------------------------------------------ #
    def keyPressEvent(self, event):
        self._stop_animations()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self._stop_animations()
        super().mousePressEvent(event)

    def hideEvent(self, event):
        self._stop_animations()
        super().hideEvent(event)

    def clear(self):
        self._stop_animations()
        super().clear()
