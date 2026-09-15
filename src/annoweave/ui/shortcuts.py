"""快捷键路由工具（评审第 4 节）。

主窗口与工作台都定义了窗口范围快捷键，`Space`/`Delete` 在不同页面含义不同。
这里提供统一的焦点判定：焦点在文本输入控件时必须禁用标注类快捷键，
避免用户在搜索框里按空格/退格就触发运行、删除等破坏性动作。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QTextEdit,
    QWidget,
)

#: 在这些控件里打字时，标注类快捷键必须让位
TEXT_INPUT_TYPES = (QLineEdit, QTextEdit, QAbstractSpinBox)


def focus_is_text_input(widget: QWidget | None = None) -> bool:
    """当前焦点是否落在可输入文本的控件上。"""
    from PySide6.QtWidgets import QApplication

    target = widget if widget is not None else QApplication.focusWidget()
    if target is None:
        return False
    if isinstance(target, QComboBox):
        # 可编辑下拉框才需要让位；只读下拉用空格展开是合理的
        return bool(target.isEditable())
    if isinstance(target, QAbstractItemView):
        # 列表/表格在编辑单元格时也算文本输入
        return bool(target.state() == QAbstractItemView.State.EditingState)
    return isinstance(target, TEXT_INPUT_TYPES)


def should_block_shortcut(event, widget: QWidget | None = None) -> bool:
    """判断一次按键是否应该被标注类快捷键忽略。"""
    if event is None:
        return False
    if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
        return False
    if event.modifiers() & (
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    ):
        # Ctrl/Alt 组合键（撤销、复制等）不属于“打字”，不拦截
        return False
    return focus_is_text_input(widget)


class ShortcutGuard(QObject):
    """安装到页面上的守卫：文本输入时拒绝标注类快捷键。

    用法：`guard = ShortcutGuard(page)`，随后 `guard.install(widget, key, handler)`。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._bindings: list[tuple[str, object]] = []

    def install(self, key: str, handler, context=Qt.ShortcutContext.WidgetWithChildrenShortcut):
        from PySide6.QtGui import QKeySequence, QShortcut

        shortcut = QShortcut(QKeySequence(key), self.parent())
        shortcut.setContext(context)
        shortcut.activated.connect(handler)
        self._bindings.append((key, shortcut))
        return shortcut

    def set_enabled(self, enabled: bool):
        for _key, shortcut in self._bindings:
            shortcut.setEnabled(enabled)

    def keys(self) -> list[str]:
        return [key for key, _shortcut in self._bindings]

    def eventFilter(self, watched, event):
        if should_block_shortcut(event, watched if isinstance(watched, QWidget) else None):
            # 明确吃掉这次按键，避免它继续冒泡成快捷键
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
        return super().eventFilter(watched, event)
