from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class FunctionWorker(QRunnable):
    """在 QThreadPool 中运行一个函数的通用 worker。

    注意：`QRunnable` 在 Qt 侧不归 Python 管理，只保留局部引用时可能在
    排队信号送达前就被回收，表现为“任务跑完了但 UI 永远收不到结果”。
    调用方应把 worker 存到实例属性上（例如 `self._worker = worker`）。
    """

    def __init__(self, function: Callable, *args, **kwargs):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        #: 让信号对象随 worker 一起存活，避免被提前回收
        self._signals_ref = self.signals

    @Slot()
    def run(self):
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception:
            self.signals.failed.emit(traceback.format_exc())
        else:
            self.signals.finished.emit(result)
