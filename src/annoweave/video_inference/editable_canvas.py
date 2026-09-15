from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..inference.datatypes import Detection

_BEHAVIOR_COLOR = (255, 0, 255)
_PERSON_COLOR = (0, 165, 255)
_LOCKED_COLOR = (140, 150, 160)
_SELECTED_COLOR = (25, 185, 154)
_MIN_BOX = 8.0  # 最小框边长（原图像素）
_HANDLE_TOL = 12.0  # 手柄命中容差（显示像素）


def _to_qimage(image: np.ndarray) -> QImage:
    height, width = image.shape[:2]
    contiguous = np.ascontiguousarray(image)
    return QImage(contiguous.data, width, height, contiguous.strides[0], QImage.Format.Format_BGR888)


class EditableFrameCanvas(QWidget):
    """大图画布：可编辑 person/behavior 框。

    - 新增：选类别后拖拽画框（最小框阈值）。
    - 选择/移动/八方向缩放：拖动框或角点手柄。
    - 命中优先级：句柄 > 小面积 > 大面积；隐藏框不参与；Tab/Alt 循环候选。
    - 删除：Del 或右键菜单；复制：Ctrl+D；锁定/显隐：Ctrl+L / Ctrl+H。
    - 撤销/重做：Ctrl+Z / Ctrl+Shift+Z，一次拖拽算一步。
    - 视图：适应窗口 / 1:1 / 滚轮缩放 / 中键或 H 平移。
    - `set_data(..., keep_selection=True)` 让推理刷新后选择不跳回第一个。
    """

    boxes_changed = Signal()
    selection_changed = Signal(object)
    status_message = Signal(str)
    #: 一次拖拽提交一条命令后发出，携带本次变更摘要（评审第 4 节：拖动不落盘）
    edit_committed = Signal(str)
    mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: Optional[np.ndarray] = None
        self._persons: list[Detection] = []
        self._behaviors: list[Detection] = []
        self._selected: Optional[Detection] = None
        self._selected_kind = ""
        self._drag_start = None
        self._drag_current = None
        self._moving = False
        self._resizing = False
        self._resize_handle = ""
        self._resize_orig = None
        self.show_conf = True
        #: 只读关键点图层：默认开启，可在复核页用“关键点”勾选框关掉。
        self.show_keypoints = True
        self.keypoint_score = 0.3
        self.mode = "select"  # select | add | pan
        self.add_label = "person"
        self.add_model_key = ""
        self._history: list = []
        self._redo: list = []
        self._pushed_this_drag = False
        #: 一次拖拽期间是否真的改动了几何（决定松手时是否提交下游重算）
        self._drag_dirty = False
        self._zoom = 1.0  # 相对“适应窗口”的倍率
        self._pan = [0.0, 0.0]
        self._panning = False
        self._pan_anchor = None
        self._selected_id: str = ""
        self._last_mouse = None
        self._cycle_lock = False
        # 1366×768 逻辑视口下画布必须能与侧栏共存（评审第 6 节）
        self.setMinimumSize(320, 220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    # ----------------------------- 公共 API ----------------------------- #
    def set_data(
        self,
        image,
        persons: list[Detection],
        behaviors: list[Detection],
        keep_selection: bool = False,
    ):
        """装载一帧的数据；image 传 None 表示清空画面。

        keep_selection=True 时按稳定身份重新绑定选中对象，
        推理刷新或编辑回填后选择不会跳回第一个。
        """
        self._image = image if image is not None and getattr(image, "size", 0) else None
        if not keep_selection:
            self._history.clear()
            self._redo.clear()
            self._drag_start = self._drag_current = None
            self._drag_dirty = self._pushed_this_drag = False
            self._resize_handle = ""
            self._resize_orig = None
            self._panning = False
            self._pan_anchor = None
        self._persons = list(persons)
        self._behaviors = list(behaviors)
        if keep_selection and self._selected is not None:
            restored = self._find_replacement(self._selected, self._selected_kind)
            self._selected = restored
            if restored is None:
                self._selected_kind = ""
        else:
            self._selected = None
            self._selected_kind = ""
        self._moving = False
        self._resizing = False
        self.update()

    @staticmethod
    def _same_box(a: Detection, b: Detection) -> bool:
        return (
            abs(a.x1 - b.x1) < 1e-6
            and abs(a.y1 - b.y1) < 1e-6
            and abs(a.x2 - b.x2) < 1e-6
            and abs(a.y2 - b.y2) < 1e-6
        )

    def _find_replacement(self, target: Detection, kind: str) -> Optional[Detection]:
        pool = self._behaviors if kind == "behavior" else self._persons
        for det in pool:
            if det is target:
                return det
        for det in pool:
            if det.label == target.label and self._same_box(det, target):
                return det
        for det in pool:
            if self._same_box(det, target):
                return det
        return None

    def selected(self) -> Optional[Detection]:
        return self._selected

    def selected_kind(self) -> str:
        return self._selected_kind

    def select_detection(self, kind: str, index: int) -> bool:
        """按图层与序号选中对象，供时间轴/检查器联动。"""
        pool = self._behaviors if kind == "behavior" else self._persons
        if 0 <= index < len(pool):
            self._selected = pool[index]
            self._selected_kind = kind
            self.selection_changed.emit(self._selected)
            self.update()
            return True
        return False

    def persons(self) -> list[Detection]:
        return self._persons

    def behaviors(self) -> list[Detection]:
        return self._behaviors

    def set_mode(self, mode: str):
        self.mode = mode
        self.setCursor(
            Qt.CursorShape.OpenHandCursor
            if mode == "pan"
            else Qt.CursorShape.CrossCursor
            if mode == "add"
            else Qt.CursorShape.ArrowCursor
        )
        self.update()
        self.mode_changed.emit(mode)

    def cancel_action(self):
        """Cancel the uncommitted gesture without creating an annotation command."""
        if self._drag_dirty and self._pushed_this_drag and self._history:
            self._restore(self._history.pop())
        self._drag_start = self._drag_current = None
        self._moving = self._resizing = self._panning = False
        self._drag_dirty = self._pushed_this_drag = False
        self._resize_handle = ''
        self.set_mode('select')
        self.update()

    def set_add_class(self, label: str):
        self.add_label = label or "person"

    def set_add_model_key(self, model_key: str):
        self.add_model_key = str(model_key or '')
        self.update()

    def set_show_conf(self, show: bool):
        self.show_conf = show
        self.update()

    def set_show_keypoints(self, show: bool):
        """关键点图层开关（只读展示，不参与编辑）。"""
        self.show_keypoints = bool(show)
        self.update()

    def set_keypoint_score(self, score: float):
        self.keypoint_score = float(score)
        self.update()

    # ----------------------------- 视图（缩放/平移） ----------------------------- #
    def zoom(self) -> float:
        return self._zoom

    def fit_to_window(self):
        self._zoom = 1.0
        self._pan = [0.0, 0.0]
        self.update()
        self.status_message.emit("视图：适应窗口")

    def actual_size(self):
        base = self._base_scale()
        if base <= 0:
            return
        self._zoom = 1.0 / base
        self._pan = [0.0, 0.0]
        self.update()
        self.status_message.emit("视图：100%")

    def zoom_in(self):
        self._apply_zoom(1.25)

    def zoom_out(self):
        self._apply_zoom(1 / 1.25)

    def _apply_zoom(self, factor: float, anchor=None):
        if self._image is None:
            return
        old_scale, offset_x, offset_y = self._view()
        if old_scale <= 0:
            return
        new_zoom = max(0.05, min(40.0, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        if anchor is None:
            anchor = self.rect().center()
        image_x = (anchor.x() - offset_x) / old_scale
        image_y = (anchor.y() - offset_y) / old_scale
        self._zoom = new_zoom
        self._pan = [0.0, 0.0]
        new_scale, base_off_x, base_off_y = self._view()
        # 解出新 pan，使 image_(x,y) 仍落在同一锚点上
        self._pan[0] = anchor.x() - base_off_x - image_x * new_scale
        self._pan[1] = anchor.y() - base_off_y - image_y * new_scale
        self.update()
        self.status_message.emit(f"视图：{int(round(new_scale * 100))}%")

    def _base_scale(self) -> float:
        if self._image is None:
            return 1.0
        h, w = self._image.shape[:2]
        if w <= 0 or h <= 0:
            return 1.0
        return min(self.width() / w, self.height() / h)

    def _view(self) -> tuple[float, float, float]:
        """返回 (scale, offset_x, offset_y)：图像坐标 -> 控件坐标。"""
        if self._image is None:
            return 1.0, 0.0, 0.0
        h, w = self._image.shape[:2]
        scale = self._base_scale() * self._zoom
        offset_x = (self.width() - w * scale) / 2 + self._pan[0]
        offset_y = (self.height() - h * scale) / 2 + self._pan[1]
        return scale, offset_x, offset_y

    # ----------------------------- 撤销/重做 ----------------------------- #
    @staticmethod
    def _clone(det: Detection) -> Detection:
        import copy
        return copy.deepcopy(det)

    def _snapshot(self):
        return ([self._clone(d) for d in self._persons], [self._clone(d) for d in self._behaviors])

    def _push_history(self):
        self._history.append(self._snapshot())
        if len(self._history) > 200:
            self._history.pop(0)
        self._redo.clear()

    def _restore(self, snap):
        persons, behaviors = snap
        previous = self._selected
        previous_kind = self._selected_kind
        self._persons = [self._clone(d) for d in persons]
        self._behaviors = [self._clone(d) for d in behaviors]
        if previous is not None:
            # 撤销/重做后尽量保持选择，不跳回“无选择”
            restored = self._find_replacement(previous, previous_kind)
            self._selected = restored
            if restored is None:
                self._selected_kind = ""
        self.selection_changed.emit(self._selected)

    def undo(self):
        if not self._history:
            return
        self._redo.append(self._snapshot())
        self._restore(self._history.pop())
        self._commit_edit("撤销")

    def redo(self):
        if not self._redo:
            return
        self._history.append(self._snapshot())
        self._restore(self._redo.pop())
        self._commit_edit("重做")

    # ----------------------------- 编辑动作 ----------------------------- #
    def duplicate_selected(self):
        if self._selected is None or self._selected.locked:
            return
        self._push_history()
        clone = self._clone(self._selected)
        import uuid
        clone.object_id = uuid.uuid4().hex
        clone.x1 += 12
        clone.x2 += 12
        clone.y1 += 12
        clone.y2 += 12
        target = self._behaviors if self._selected_kind == "behavior" else self._persons
        target.append(clone)
        self._selected = clone
        self._commit_edit("复制框")
        self.update()

    def relabel_selected(self, label: str):
        if self._selected is None or self._selected.locked or not label:
            return
        self._push_history()
        det = self._selected
        det.label = label
        # 依据类别重新归层
        is_person = label in ("person", "head")
        current = self._behaviors if self._selected_kind == "behavior" else self._persons
        target_kind = "person" if is_person else "behavior"
        if (self._selected_kind == "behavior") != (not is_person):
            if det in current:
                current.remove(det)
            (self._persons if is_person else self._behaviors).append(det)
            self._selected_kind = target_kind
        self._commit_edit(f"改类为 {label}")
        self.update()

    def toggle_lock_selected(self):
        if self._selected is None:
            return
        self._push_history()
        self._selected.locked = not self._selected.locked
        # 锁定/显隐只影响显示与命中，不需要重算下游
        self.boxes_changed.emit()
        self.update()

    def toggle_hidden_selected(self):
        if self._selected is None:
            return
        self._push_history()
        self._selected.hidden = not self._selected.hidden
        if self._selected.hidden:
            self._selected = None
        self.boxes_changed.emit()
        self.update()

    # ----------------------------- 坐标映射 ----------------------------- #
    def _scale(self) -> float:
        return self._view()[0]

    def _to_image(self, pos) -> tuple[float, float]:
        scale, offset_x, offset_y = self._view()
        if scale <= 0:
            return 0.0, 0.0
        return (pos.x() - offset_x) / scale, (pos.y() - offset_y) / scale

    # ----------------------------- 绘制 ----------------------------- #
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is None:
            painter.setPen(Qt.GlobalColor.gray)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "打开素材或运行当前帧后显示画面")
            painter.end()
            return
        h, w = self._image.shape[:2]
        scale, offset_x, offset_y = self._view()
        target_w = max(1, int(w * scale))
        target_h = max(1, int(h * scale))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pixmap = QPixmap.fromImage(_to_qimage(self._image))
        painter.drawPixmap(
            int(offset_x), int(offset_y),
            pixmap.scaled(target_w, target_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation),
        )
        for det in self._persons:
            self._draw_box(painter, det, offset_x, offset_y, scale, _PERSON_COLOR, 2, solid=True)
        for det in self._behaviors:
            self._draw_box(painter, det, offset_x, offset_y, scale, _BEHAVIOR_COLOR, 2, solid=False)
        if self.show_keypoints:
            for det in self._persons:
                self._draw_skeleton(painter, det, offset_x, offset_y, scale)
        if self._selected is not None and not self._selected.hidden:
            self._draw_box(painter, self._selected, offset_x, offset_y, scale, _SELECTED_COLOR, 3,
                           solid=True, handle=True)
        if self._drag_start is not None and self._drag_current is not None:
            x1, y1 = self._drag_start
            x2, y2 = self._drag_current
            painter.setPen(Qt.GlobalColor.green)
            painter.drawRect(int(offset_x + min(x1, x2) * scale), int(offset_y + min(y1, y2) * scale),
                             int(abs(x2 - x1) * scale), int(abs(y2 - y1) * scale))
        painter.end()

    def _draw_box(self, painter, det, offset_x, offset_y, scale, color, thickness, solid=True, handle=False):
        if det.hidden:
            return
        x1 = offset_x + det.x1 * scale
        y1 = offset_y + det.y1 * scale
        x2 = offset_x + det.x2 * scale
        y2 = offset_y + det.y2 * scale
        if det.locked:
            color = _LOCKED_COLOR
        painter.setPen(QPen(QColor(*color), thickness))
        if solid:
            painter.drawRect(int(x1), int(y1), int(x2 - x1), int(y2 - y1))
        else:
            painter.setPen(QPen(QColor(*color), thickness, Qt.PenStyle.DashLine))
            painter.drawRect(int(x1), int(y1), int(x2 - x1), int(y2 - y1))
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        label = det.label if not self.show_conf else f"{det.label} {det.score:.2f}"
        if det.locked:
            label = "🔒 " + label
        painter.drawText(int(x1), int(max(0, y1 - 4)), label)
        if handle and not det.locked:
            painter.setPen(QPen(QColor(0, 255, 0), 2))
            for hx, hy in self._handle_points(det).values():
                px = offset_x + hx * scale
                py = offset_y + hy * scale
                painter.drawRect(int(px - 3), int(py - 3), 6, 6)

    def _draw_skeleton(self, painter, det, offset_x, offset_y, scale):
        """只读骨架图层：按关键点排列画连线 + 圆点（不参与命中与编辑）。"""
        points = list(getattr(det, "keypoints", []) or [])
        if not points:
            return
        from ..inference.skeletons import resolve_skeleton

        layout = resolve_skeleton(getattr(det, "keypoint_format", ""), len(points))
        threshold = float(getattr(self, "keypoint_score", 0.3) or 0.0)
        radius = max(2.0, min(4.0, 3.0 * scale))

        def position(index: int):
            point = points[index]
            score = float(getattr(point, "score", 0.0))
            if score <= threshold:
                return None
            return (offset_x + point.x * scale, offset_y + point.y * scale)

        if layout is not None:
            for edge_index, (start, end) in enumerate(layout.edges):
                if start >= len(points) or end >= len(points):
                    continue
                a = position(start)
                b = position(end)
                if a is None or b is None:
                    continue
                color = layout.edge_color(edge_index)
                painter.setPen(QPen(QColor(*color), 2))
                painter.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))

        painter.setPen(Qt.PenStyle.NoPen)
        for index, _point in enumerate(points):
            location = position(index)
            if location is None:
                continue
            color = layout.point_color(index) if layout is not None else (0, 255, 255)
            painter.setBrush(QColor(*color))
            painter.drawEllipse(
                int(location[0] - radius), int(location[1] - radius),
                int(radius * 2), int(radius * 2),
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    # ----------------------------- 命中 ----------------------------- #
    @staticmethod
    def _handle_points(det: Detection) -> dict:
        cx = (det.x1 + det.x2) / 2
        cy = (det.y1 + det.y2) / 2
        return {
            "nw": (det.x1, det.y1), "n": (cx, det.y1), "ne": (det.x2, det.y1),
            "e": (det.x2, cy), "se": (det.x2, det.y2), "s": (cx, det.y2),
            "sw": (det.x1, det.y2), "w": (det.x1, cy),
        }

    def _handle_at(self, pos) -> Optional[str]:
        if self._selected is None or self._image is None or self._selected.locked:
            return None
        ix, iy = self._to_image(pos)
        tol = _HANDLE_TOL / max(self._scale(), 1e-6)
        for name, (hx, hy) in self._handle_points(self._selected).items():
            if abs(ix - hx) <= tol and abs(iy - hy) <= tol:
                return name
        return None

    def _hit_box(self, pos):
        """命中优先级：句柄(按下时单独判断) > 小面积 > 大面积；隐藏框不参与。"""
        candidates = self._hit_candidates(pos)
        if not candidates:
            return None, ""
        return candidates[0]

    def _hit_candidates(self, pos) -> list[tuple[Detection, str]]:
        """全部命中候选，按“小面积优先”排序，供 Tab/Alt 循环选择。"""
        if self._image is None:
            return []
        ix, iy = self._to_image(pos)
        candidates = []
        for det in self._persons:
            if not det.hidden and det.x1 <= ix <= det.x2 and det.y1 <= iy <= det.y2:
                candidates.append((det, "person"))
        for det in self._behaviors:
            if not det.hidden and det.x1 <= ix <= det.x2 and det.y1 <= iy <= det.y2:
                candidates.append((det, "behavior"))
        candidates.sort(key=lambda item: max(1.0, item[0].area))
        return candidates

    def cycle_selection(self, forward: bool = True):
        """在当前鼠标位置的所有候选框之间循环；嵌套框不再永远只能选到大框。"""
        if self._image is None:
            return
        anchor = getattr(self, "_last_mouse", None)
        if anchor is None:
            return
        candidates = self._hit_candidates(anchor)
        if not candidates:
            return
        ids = [(id(det), kind) for det, kind in candidates]
        current = (id(self._selected), self._selected_kind) if self._selected is not None else None
        if current in ids:
            step = 1 if forward else -1
            index = (ids.index(current) + step) % len(ids)
        else:
            index = 0
        det, kind = candidates[index]
        self._selected = det
        self._selected_kind = kind
        self._cycle_lock = True
        self.selection_changed.emit(det)
        self.update()
        self.status_message.emit(f"候选 {index + 1}/{len(candidates)}：{det.label}")

    # ----------------------------- 鼠标交互 ----------------------------- #
    def mousePressEvent(self, event):
        if self._image is None:
            return
        self._pushed_this_drag = False
        self._drag_dirty = False
        self._last_mouse = event.position()
        # 中键或平移模式：拖动画布，不改数据
        if event.button() == Qt.MouseButton.MiddleButton or self.mode == "pan":
            self._panning = True
            self._pan_anchor = (event.position().x(), event.position().y(), self._pan[0], self._pan[1])
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.position())
            return
        if self.mode == "select":
            handle = self._handle_at(event.position())
            if handle:
                self._resizing = True
                self._resize_handle = handle
                det = self._selected
                self._resize_orig = (det.x1, det.y1, det.x2, det.y2)
            else:
                self._cycle_lock = False
                det, kind = self._hit_box(event.position())
                if det is not None:
                    self._selected = det
                    self._selected_kind = kind
                    self._moving = not det.locked
                    self._drag_start = self._to_image(event.position())
                    self.selection_changed.emit(det)
                else:
                    self._selected = None
                    self._selected_kind = ""
                    self.selection_changed.emit(None)
            self.update()
        else:
            self._drag_start = self._to_image(event.position())
            self._drag_current = self._drag_start
            self.update()

    def mouseMoveEvent(self, event):
        self._last_mouse = event.position()
        if self._panning and self._pan_anchor is not None:
            ax, ay, ox, oy = self._pan_anchor
            self._pan = [ox + (event.position().x() - ax), oy + (event.position().y() - ay)]
            self.update()
            return
        if self._drag_start is None and not self._resizing:
            return
        pos = self._to_image(event.position())
        # 拖动过程中只更新几何并重绘预览，不发出 boxes_changed：
        # 下游重算留到 mouseReleaseEvent，避免每个鼠标事件都跑一次工作流。
        if self.mode == "select":
            if self._resizing and self._selected is not None and self._resize_orig is not None:
                if not self._pushed_this_drag:
                    self._push_history()
                    self._pushed_this_drag = True
                x1, y1, x2, y2 = self._resize_orig
                if "w" in self._resize_handle:
                    x1 = min(pos[0], x2 - _MIN_BOX)
                if "e" in self._resize_handle:
                    x2 = max(pos[0], x1 + _MIN_BOX)
                if "n" in self._resize_handle:
                    y1 = min(pos[1], y2 - _MIN_BOX)
                if "s" in self._resize_handle:
                    y2 = max(pos[1], y1 + _MIN_BOX)
                self._selected.x1, self._selected.y1, self._selected.x2, self._selected.y2 = x1, y1, x2, y2
                self._drag_dirty = True
                self.update()
            elif self._selected is not None and self._moving:
                if not self._pushed_this_drag:
                    self._push_history()
                    self._pushed_this_drag = True
                dx = pos[0] - self._drag_start[0]
                dy = pos[1] - self._drag_start[1]
                self._selected.x1 += dx
                self._selected.y1 += dy
                self._selected.x2 += dx
                self._selected.y2 += dy
                self._drag_start = pos
                self._drag_dirty = True
                self.update()
        else:
            self._drag_current = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self._pan_anchor = None
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self.mode == "pan" else Qt.CursorShape.ArrowCursor
            )
            return
        if self.mode != "select" and self._drag_start is not None:
            x1, y1 = self._drag_start
            x2, y2 = self._drag_current or self._to_image(event.position())
            self._drag_start = self._drag_current = None
            if abs(x2 - x1) >= _MIN_BOX and abs(y2 - y1) >= _MIN_BOX:
                label = self.add_label
                det = Detection(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), label, 1.0)
                det.model_key = self.add_model_key
                self._push_history()
                (self._behaviors if label not in ("person", "head") else self._persons).append(det)
                self._selected = det
                self._selected_kind = "person" if label in ("person", "head") else "behavior"
                self.selection_changed.emit(det)
                self._commit_edit(f"新增 {label} 框")
                self.set_mode('select')
                self.update()
            else:
                self.status_message.emit(f"框太小，已忽略（最小边长 {_MIN_BOX:.0f} 像素）")
        elif self._drag_dirty:
            # 一次拖拽 = 一步：到这里才提交，下游只重算一次
            verb = "缩放" if self._resize_handle else "移动"
            self._commit_edit(f"{verb}完成")
        self._moving = False
        self._resizing = False
        self._resize_handle = ""
        self._resize_orig = None
        self._drag_dirty = False

    def _commit_edit(self, summary: str):
        """提交一条编辑命令：入栈已完成，这里统一通知下游重算一次。"""
        self._drag_dirty = False
        self.boxes_changed.emit()
        self.edit_committed.emit(summary)

    def wheelEvent(self, event):
        if self._image is None:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self._apply_zoom(1.2 if delta > 0 else 1 / 1.2, anchor=event.position())
        event.accept()

    def _show_context_menu(self, pos):
        """右键弹上下文菜单（评审第 4 节）；删除进入命令历史，可撤销。"""
        from PySide6.QtWidgets import QMenu

        det, _kind = self._hit_box(pos)
        if det is not None:
            self._selected = det
            self._selected_kind = _kind
            self.selection_changed.emit(det)
            self.update()
        menu = QMenu(self)
        act_delete = menu.addAction("删除该框 (Delete)")
        act_delete.setEnabled(self._selected is not None and not self._selected.locked)
        act_duplicate = menu.addAction("复制该框 (Ctrl+D)")
        act_duplicate.setEnabled(self._selected is not None and not self._selected.locked)
        menu.addSeparator()
        act_lock = menu.addAction("锁定/解锁 (Ctrl+L)")
        act_lock.setEnabled(self._selected is not None)
        act_hide = menu.addAction("显示/隐藏 (Ctrl+H)")
        act_hide.setEnabled(self._selected is not None)
        menu.addSeparator()
        act_undo = menu.addAction("撤销 (Ctrl+Z)")
        act_undo.setEnabled(bool(self._history))
        act_redo = menu.addAction("重做 (Ctrl+Shift+Z)")
        act_redo.setEnabled(bool(self._redo))
        menu.addSeparator()
        act_cycle = menu.addAction("循环选择嵌套框 (Tab)")
        act_cycle.setEnabled(bool(self._hit_candidates(pos)))
        menu.addSeparator()
        act_fit = menu.addAction("适应窗口")
        act_actual = menu.addAction("实际大小 (1:1)")

        chosen = menu.exec(self.mapToGlobal(pos.toPoint()))
        if chosen is None:
            return
        if chosen is act_delete:
            self.delete_selected()
        elif chosen is act_duplicate:
            self.duplicate_selected()
        elif chosen is act_lock:
            self.toggle_lock_selected()
        elif chosen is act_hide:
            self.toggle_hidden_selected()
        elif chosen is act_undo:
            self.undo()
        elif chosen is act_redo:
            self.redo()
        elif chosen is act_cycle:
            self.cycle_selection()
        elif chosen is act_fit:
            self.fit_to_window()
        elif chosen is act_actual:
            self.actual_size()

    def delete_selected(self):
        if self._selected is None or self._selected.locked:
            return
        self._push_history()
        (self._behaviors if self._selected_kind == "behavior" else self._persons).remove(self._selected)
        self._selected = None
        self._selected_kind = ""
        self.selection_changed.emit(None)
        self._commit_edit("删除框")
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_action()
        elif event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
        elif event.matches(QKeySequence.StandardKey.Redo):
            self.redo()
        elif event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.cycle_selection(forward=event.key() == Qt.Key.Key_Tab)
            event.accept()
        elif (
            event.key() == Qt.Key.Key_Alt
            and self._last_mouse is not None
            and self._hit_candidates(self._last_mouse)
        ):
            self.cycle_selection()
            event.accept()
        elif event.key() == Qt.Key.Key_Delete:
            self.delete_selected()
        elif event.key() == Qt.Key.Key_F:
            self.fit_to_window()
        elif event.key() == Qt.Key.Key_1:
            self.actual_size()
        else:
            super().keyPressEvent(event)
