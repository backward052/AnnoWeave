"""复核工作台（M6，评审第 6 节）。

布局：
- 顶部：项目名、当前媒体与帧、活动工作流、主流程按钮（≤5 个）。
- 左侧 230–280：素材队列（缩略图/文件名/类型/时长/复核状态 + 过滤）。
- 中央：尽可能大的可缩放画布 + 独立窄工具条（选择/矩形/平移/缩放/显隐）。
- 右侧 280–340：对象与证据检查器（预测、置信度、人工结论、IoA、裁剪与下游）。
- 底部：可折叠时间轴（原始帧/采样点/命中区间/人工关键帧/复核状态）。
- 固定状态栏：保存状态、上次保存时间、运行状态、帧身份、“确认并下一项”。

可靠性约束（第 3、4、7 节）：
- 人工修订作为明确输入进入下游，重跑模型不覆盖人工结果；
- 选择按稳定身份保留，不因推理刷新跳回第一个；
- 切帧/改采样率不丢人工编辑（按 media_id + 原始帧号持久化）；
- 写盘失败绝不显示成功。
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..export.service import ExportService
from ..export.service import frame_tag as build_frame_tag
from ..inference.config import SAVE_TEMPLATES, load_model_library
from ..inference.datatypes import Detection, Frame, ModelResult, TaskType
from ..media import read_image
from ..video_inference.editable_canvas import EditableFrameCanvas
from ..workers import FunctionWorker
from ..workflow.inspection import TracedWorkflowRunner
from ..workflow.packet import Packet
from ..workflow.templates import starter_workflow_config
from ..workflow.workflow import Workflow, load_workflow_catalog
from .review_inspector import ReviewDecisionBar
from .review_queue import MediaQueuePanel, QueueEntry
from .review_timeline import TimelineData, TimelinePanel

#: 时间轴“命中区间”的合并窗口（原始帧）
HIT_MERGE_WINDOW = 3


@dataclass
class FrameRunState:
    """一帧的运行结果与人工修订（按 media_id + 原始帧号持久化在内存/数据库）。"""

    packet: Optional[Packet] = None
    image: object = None
    manual_persons: Optional[list[Detection]] = None
    manual_behaviors: Optional[list[Detection]] = None
    edits: dict = field(default_factory=dict)
    decision: str = "pending"
    edited: bool = False
    #: 采用版本：predicted / manual（决定导出用哪一版）
    adopted_source: str = "predicted"
    #: 最近一次运行的 run_id 与状态，供“这次导出对应哪次运行”追溯
    run_id: str = ""
    run_status: str = ""
    #: 重算期间又发生编辑时挂起的输入，完成后自动补算一次
    pending_downstream: object = None
    prediction_results: dict[str, ModelResult] = field(default_factory=dict)
    crop_edits: dict = field(default_factory=dict)
    #: 独立于确认/驳回的导出篮子；普通浏览帧不需要加入。
    selected_for_export: bool = False


@dataclass
class PrecomputeProgress:
    phase: str = "准备素材"
    total: int = 0
    done: int = 0
    media_total: int = 0
    media_done: int = 0
    failed: int = 0
    current: str = ""


class ReviewPage(QWidget):
    """复核工作台。所有数据来自共享项目上下文，不自行扫描磁盘。"""

    status_changed = Signal(str)

    def __init__(self, context=None, parent=None):
        super().__init__(parent)
        self.context = context
        self._context_initialized = False
        self.thread_pool = QThreadPool.globalInstance()
        self.workflow_config = starter_workflow_config()
        self.model_library = load_model_library()
        self.media_entries: list = []
        self.current_key = ""
        self.entry_index = -1
        self.source_frame = 0
        self.sample_indices: list[int] = []
        self.sample_fps = 1
        self._video = None
        self._image = None
        self._states: dict[tuple[str, int], FrameRunState] = {}
        self._selected_object_box = None
        self._members_by_pair = {}
        self._busy = False
        self._closing = False
        #: 节点级缓存：输入指纹未变时复用上一轮输出（评审第 8 节）
        self.node_cache: dict = {}
        self._last_saved_at = "—"
        self._precompute_cancel = False
        self._precompute_progress = None
        self._precompute_dialog = None
        self._precompute_timer = None
        self._build_ui()
        if context is not None:
            self.set_context(context)

    # =============================== UI =============================== #
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ---------------- 顶部：标题 + ≤5 个主流程按钮 ---------------- #
        header = QHBoxLayout()
        self.project_label = QLabel("未打开项目")
        self.project_label.setProperty("role", "title")
        header.addWidget(self.project_label)
        self.frame_label = QLabel("—")
        self.frame_label.setProperty("role", "muted")
        header.addWidget(self.frame_label)
        header.addStretch()
        root.addLayout(header)

        actions = QHBoxLayout()
        self.open_button = QPushButton("打开素材")
        self.open_button.setToolTip("选择图片、视频或文件夹作为复核范围")
        from PySide6.QtWidgets import QMenu
        open_menu = QMenu(self.open_button)
        open_menu.addAction("打开图片 / 视频……", self.pick_source)
        open_menu.addAction("打开文件夹……", self.pick_folder)
        self.open_button.setMenu(open_menu)
        self.workflow_button = QPushButton("工作流")
        self.workflow_button.setToolTip("选择已保存的工作流模板")
        self.workflow_button.clicked.connect(self.reload_workflows)
        self.run_button = QPushButton("运行当前帧")
        self.run_button.setProperty("accent", "true")
        self.run_button.setToolTip(
            "从头运行当前帧：全图模型 → 关联 → 裁剪 → 下游模型。\n"
            "若本帧有人工修改，运行成功后会清除大图和裁剪人工版，改用新的模型预测。"
        )
        self.run_button.clicked.connect(self.run_current_frame)
        self.export_button = QPushButton("导出")
        self.export_button.setToolTip("把当前帧或已加入保存集的帧，导出为证据图片或训练数据")
        export_menu = QMenu(self.export_button)
        export_menu.addAction("当前帧 → 带框证据图与裁剪图……", self.export_current_frame)
        export_menu.addAction("当前帧 → 训练原图与标签……", self.export_training_data)
        export_menu.addSeparator()
        export_menu.addAction("保存集 → 带框证据图与裁剪图……", self.export_selected_review_results)
        export_menu.addAction("保存集 → 训练原图与标签……", self.export_selected_training_data)
        self.export_button.setMenu(export_menu)
        self.batch_button = QPushButton("批量处理")
        self.batch_button.setToolTip("先推理全部采样帧供人工复核，或进入任务页直接批量导出")
        batch_menu = QMenu(self.batch_button)
        batch_menu.addAction("预推理全部采样帧，然后从头复核", self.precompute_all_for_review)
        batch_menu.addAction("批量推理并直接导出文件……", self._goto_tasks)
        self.batch_button.setMenu(batch_menu)
        for button in (
            self.open_button,
            self.run_button,
            self.export_button,
            self.batch_button,
        ):
            actions.addWidget(button)
        actions.addStretch()
        self.workflow_combo = QComboBox()
        self.workflow_combo.setMinimumWidth(150)
        self.workflow_combo.currentIndexChanged.connect(self._on_workflow_changed)
        actions.addWidget(QLabel("活动工作流"))
        actions.addWidget(self.workflow_combo)
        self.options_button = QPushButton("采样与导出设置")
        self.options_button.setToolTip("设置视频采样 FPS、刷新工作流和批量导出的文件范围")
        self.options_button.setCheckable(True)
        actions.addWidget(self.options_button)
        root.addLayout(actions)
        self.options_panel = QWidget()
        options = QHBoxLayout(self.options_panel)
        options.setContentsMargins(0, 0, 0, 0)
        options.addWidget(self.workflow_button)
        self.workflow_button.setText("刷新工作流")
        self.sample_fps_spin = QComboBox()
        for fps in (1, 2, 5, 10, 25):
            self.sample_fps_spin.addItem(f"{fps} FPS", fps)
        self.sample_fps_spin.currentIndexChanged.connect(self._on_sample_fps_changed)
        options.addWidget(QLabel("采样率"))
        options.addWidget(self.sample_fps_spin)
        self.export_template_combo = QComboBox()
        for key, label in SAVE_TEMPLATES.items():
            self.export_template_combo.addItem(label, key)
        options.addWidget(QLabel("导出内容"))
        options.addWidget(self.export_template_combo)
        options.addStretch()
        self.options_panel.hide()
        self.options_button.toggled.connect(self.options_panel.setVisible)
        root.addWidget(self.options_panel)

        # ---------------- 主体三栏 ---------------- #
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.queue = MediaQueuePanel()
        self.queue.entry_activated.connect(self.open_entry)
        self.splitter.addWidget(self.queue)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)
        self.canvas = EditableFrameCanvas()
        self.canvas.setMinimumSize(320, 220)
        self.canvas.boxes_changed.connect(self._on_boxes_changed)
        self.canvas.edit_committed.connect(self._on_edit_committed)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        self.canvas.status_message.connect(self._set_status)
        self.canvas.mode_changed.connect(self._sync_mode_buttons)
        canvas_row = QHBoxLayout()
        toolbar = self._build_canvas_toolbar()
        canvas_header = QHBoxLayout()
        canvas_header.addWidget(QLabel("标注图层"))
        canvas_header.addWidget(self.add_layer_combo)
        canvas_header.addWidget(QLabel("新框类别"))
        canvas_header.addWidget(self.add_class_combo)
        canvas_header.addStretch()
        canvas_header.addWidget(self.show_conf_check)
        canvas_header.addWidget(self.show_keypoints_check)
        canvas_header.addWidget(QLabel("R 编框 · Ctrl+J 编辑 · Ctrl+F 适应 · 改框后自动重算裁剪"))
        center_layout.addLayout(canvas_header)
        canvas_row.addWidget(toolbar)
        canvas_row.addWidget(self.canvas, 1)
        center_layout.addLayout(canvas_row, 1)
        self.splitter.addWidget(center)

        from .review_inspector import ObjectInspector

        self.inspector = ObjectInspector()
        self.inspector.object_selected.connect(self._on_inspector_selection)
        self.inspector.adopt_source_changed.connect(self._on_adopt_source_changed)
        self.inspector.crop_edit_requested.connect(self._edit_crop)
        self.splitter.addWidget(self.inspector)
        self.splitter.setSizes([250, 820, 310])
        root.addWidget(self.splitter, 1)

        # ---------------- 底部时间轴 ---------------- #
        self.timeline = TimelinePanel()
        self.timeline.source_frame_requested.connect(self._step_source_frame)
        self.timeline.hit_step_requested.connect(self._step_hit)
        self.timeline.sample_step_requested.connect(self._step_sample_frame)
        root.addWidget(self.timeline)

        # ---------------- 固定状态栏 ---------------- #
        self.decision_bar = ReviewDecisionBar()
        self.decision_bar.decision_requested.connect(self.set_decision)
        self.decision_bar.next_requested.connect(self._goto_next_media)
        self.decision_bar.export_selected_changed.connect(self.set_export_selected)
        root.addWidget(self.decision_bar)

        status = QHBoxLayout()
        self.save_label = QLabel("保存状态：暂无修改")
        self.save_label.setProperty("role", "muted")
        self.run_label = QLabel("运行状态：空闲")
        self.run_label.setProperty("role", "muted")
        self.identity_label = QLabel("帧身份：—")
        self.identity_label.setProperty("role", "muted")
        for widget in (self.save_label, self.run_label, self.identity_label):
            status.addWidget(widget)
        status.addStretch()
        root.addLayout(status)

        self.reload_workflows(initial=True)
        self._setup_shortcuts()
        self._update_identity()

    def _setup_shortcuts(self):
        """页面范围快捷键；文本输入（搜索框）聚焦时自动让位（评审第 4 节）。"""
        from PySide6.QtCore import Qt as _Qt

        from .shortcuts import ShortcutGuard

        self.shortcut_guard = ShortcutGuard(self)
        context = _Qt.ShortcutContext.WidgetWithChildrenShortcut
        bindings = (
            ("V", lambda: self.set_canvas_mode("select")),
            ("Ctrl+J", lambda: self.set_canvas_mode("select")),
            ("Escape", self.cancel_canvas_action),
            ("R", lambda: self.set_canvas_mode("add")),
            ("H", lambda: self.set_canvas_mode("pan")),
            ("F", self.canvas.fit_to_window),
            ("Ctrl+F", self.canvas.fit_to_window),
            ("Ctrl++", self.canvas.zoom_in),
            ("Ctrl+-", self.canvas.zoom_out),
            ("Ctrl+=", self.canvas.actual_size),
            ("1", self.canvas.actual_size),
            ("Ctrl+Z", self.canvas.undo),
            ("Ctrl+Shift+Z", self.canvas.redo),
            ("Ctrl+D", self.canvas.duplicate_selected),
            ("Ctrl+E", self.edit_selected_class),
            ("Ctrl+L", self.canvas.toggle_lock_selected),
            ("Delete", self.canvas.delete_selected),
            ("Space", self._toggle_playback),
            ("Left", lambda: self._step_source_frame(-1)),
            ("Right", lambda: self._step_source_frame(1)),
            ("PgUp", lambda: self._step_sample_frame(-1)),
            ("PgDown", lambda: self._step_sample_frame(1)),
            ("Ctrl+S", self._persist_manual_revision),
            ("Ctrl+Shift+S", self.export_current_frame),
            ("A", lambda: self._step_media(-1)),
            ("D", lambda: self._step_media(1)),
            ("Ctrl+Return", self._goto_next_media),
        )
        for key, handler in bindings:
            self.shortcut_guard.install(key, handler, context)

    def cancel_canvas_action(self):
        self.canvas.cancel_action()
        self.set_canvas_mode('select')

    def edit_selected_class(self):
        det = self.canvas.selected()
        if det is None or det.locked:
            self._set_status('请先选中一个未锁定的框')
            return
        labels = [self.add_class_combo.itemText(i) for i in range(self.add_class_combo.count())]
        if det.label not in labels:
            labels.insert(0, det.label)
        value, accepted = QInputDialog.getItem(self, '修改框类别', '类别', labels,
                                               labels.index(det.label), True)
        if accepted and value.strip():
            self.canvas.relabel_selected(value.strip())

    def _sync_mode_buttons(self, mode):
        for key, button in getattr(self, 'mode_buttons', {}).items():
            button.setChecked(key == mode)

    def _step_media(self, delta):
        index = self.entry_index + delta
        if 0 <= index < len(self.media_entries):
            self.open_entry(self._entry_key(self.media_entries[index]))

    def _toggle_playback(self):
        self.timeline.play_button.setChecked(not self.timeline.play_button.isChecked())

    def _build_canvas_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedWidth(106)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        def add_button(text: str, tip: str, handler, checkable: bool = False) -> QPushButton:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setFixedSize(100, 34)
            button.setCheckable(checkable)
            button.clicked.connect(handler)
            layout.addWidget(button)
            return button

        self.mode_buttons: dict[str, QPushButton] = {}
        for key, text, tip in (
            ("select", "选择 / 编辑", "选择并移动、调整框（Ctrl+J / V）"),
            ("add", "绘制矩形", "绘制矩形（R），拖拽完成后自动返回编辑模式"),
            ("pan", "平移画布", "平移画布（H，或按住中键）"),
        ):
            button = add_button(text, tip, lambda _checked=False, k=key: self.set_canvas_mode(k), checkable=True)
            self.mode_buttons[key] = button
        self.mode_buttons["select"].setChecked(True)

        layout.addSpacing(8)
        add_button("修改类别", "修改选中框的类别（Ctrl+E）", self.edit_selected_class)
        add_button("删除框", "删除选中的框（Delete）", self.canvas.delete_selected)
        layout.addSpacing(8)
        add_button("撤销", "撤销当前帧编辑（Ctrl+Z）", self.canvas.undo)
        add_button("重做", "重做当前帧编辑（Ctrl+Shift+Z）", self.canvas.redo)
        from PySide6.QtWidgets import QMenu
        view_button = add_button("视图设置", "缩放与适应画布", lambda: None)
        view_menu = QMenu(view_button)
        for text, handler in (
            ("放大   Ctrl++", self.canvas.zoom_in),
            ("缩小   Ctrl+-", self.canvas.zoom_out),
            ("适应窗口   Ctrl+F", self.canvas.fit_to_window),
            ("100% 原图   Ctrl+=", self.canvas.actual_size),
        ):
            view_menu.addAction(text, handler)
        view_button.setMenu(view_menu)
        layout.addStretch()

        self.show_conf_check = QCheckBox("置信")
        self.show_conf_check.setChecked(True)
        self.show_conf_check.setToolTip("在框上显示置信度")
        self.show_conf_check.toggled.connect(self.canvas.set_show_conf)

        self.show_keypoints_check = QCheckBox("关键点")
        self.show_keypoints_check.setChecked(True)
        self.show_keypoints_check.setToolTip(
            "显示人体骨架图层（只读展示，不参与编辑）。\n"
            "需要工作流里有姿态模型（整图人体姿态 / 裁剪上二次姿态）。"
        )
        self.show_keypoints_check.toggled.connect(self.canvas.set_show_keypoints)

        self.add_class_combo = QComboBox()
        self.add_class_combo.setMinimumWidth(140)
        self.add_class_combo.setMaximumWidth(220)
        self.add_class_combo.setToolTip("新框类别")
        self.add_class_combo.currentIndexChanged.connect(self._on_add_class_changed)
        self.add_layer_combo = QComboBox()
        self.add_layer_combo.setMinimumWidth(170)
        self.add_layer_combo.setToolTip("新框所属的全图模型；相同类别也能分别保存")
        self.add_layer_combo.currentIndexChanged.connect(self._on_add_layer_changed)
        self._refresh_add_classes()
        return bar

    # =============================== 项目 / 工作流 =============================== #
    def set_context(self, context):
        """接入共享项目上下文；切换项目时重新装载队列。"""
        # MainWindow 每次切回页面都会再次传入同一个上下文。这个调用必须幂等，
        # 否则直接打开的素材队列会被清掉，而右侧仍残留上一帧的对象与裁剪。
        if self._context_initialized and context is self.context:
            return
        self._context_initialized = True
        self.context = context
        self._close_video()
        self.media_entries = []
        self.current_key = ""
        self.entry_index = -1
        self.source_frame = 0
        self.sample_indices = []
        self._states.clear()
        self._members_by_pair = {}
        self._image = None
        self._selected_object_box = None
        self.canvas.set_data(None, [], [])
        self.inspector.set_frame([], [], [], [], (0, 0), predictions=[], has_prediction=False,
                                 adopted_source="manual")
        self.inspector.show_placeholder("打开素材并运行当前帧后，这里显示对象、裁剪和下游证据。")
        self.timeline.stop()
        self.timeline.set_data(TimelineData())
        self.timeline.set_current_source_frame(0)
        self.decision_bar.set_decision("pending")
        self.decision_bar.set_export_selected(False)
        self.run_label.setText("运行状态：空闲")
        self.save_label.setText("保存状态：—")
        if context is None:
            self.project_label.setText("未打开项目")
            self.queue.set_entries([])
            self._update_identity()
            return
        self.project_label.setText(f"项目：{Path(context.source_root).name}")
        self._load_persisted_decisions()
        self.reload_entries()

    def _load_persisted_decisions(self):
        """从仓储恢复既有复核结论与人工修订，避免重启后状态全变“未复核”。"""
        context = self.context
        if context is None or context.repository is None:
            return
        try:
            rows = context.repository.review_decisions_by_media()
        except Exception:  # noqa: BLE001
            rows = []
        for media_id, frame_index, decision in rows:
            self._states.setdefault((media_id, int(frame_index)), FrameRunState()).decision = decision
        # 标注框按用户真正打开的帧懒加载。这里逐媒体 load_media 会把项目规模直接
        # 放大成 N 次查询和大量 Python 对象，是“大目录进入视觉复核卡死”的主因。

    def reload_entries(self):
        if self.context is None or self.context.store is None:
            self.queue.set_entries([])
            return
        if self._closing or getattr(self.context, "closed", False):
            return  # 项目已关闭：不要再去读数据库
        # store 返回的是 MediaItem（无 present/deleted 字段），先转成领域 Media 再筛选，
        # 并沿用项目上下文里已回填的稳定 media_id。
        self.media_entries = [self._to_domain(item) for item in self.context.store.list_media()]
        self.media_entries = [
            entry for entry in self.media_entries if not getattr(entry, "deleted", False)
        ]
        self._members_by_pair = {}
        for entry in self.media_entries:
            self._members_by_pair.setdefault(entry.pair_key, []).append(entry)
        entries = [self._queue_entry(item) for item in self.media_entries]
        self.queue.set_entries(entries)
        # Keep the header tied to the same source of truth as the queue. Showing the
        # project name and count together avoids the contradictory impression that
        # navigation is active while no project is open.
        if self.context is not None:
            self.project_label.setText(
                f"项目：{Path(self.context.source_root).name} · {len(entries)} 个素材"
            )
        if entries and not self.current_key:
            self.open_entry(entries[0].key)

    def _to_domain(self, item):
        """把旧 MediaItem 转成领域 Media，并复用已落库的稳定 media_id。

        评审第 9 节：路径变化可重定位，但身份必须稳定。
        每次打开项目都重新生成 uuid 会让“重启后找回结果”失效，因此优先沿用数据库里的 id。
        """
        if self.context is not None:
            stable = self.context.media.get(getattr(item, "media_key", ""))
            if stable is not None:
                return stable
        from .project_context import to_domain_media

        record = to_domain_media(item)
        context = self.context
        if context is not None and context.repository is not None:
            existing = context.repository.media_ids_by_key().get(item.media_key)
            if existing:
                record.media_id = existing
                context.media[item.media_key] = record
        return record

    def _queue_entry(self, item) -> QueueEntry:
        members = self._group_members(item)
        media_id = str(getattr(item, "media_id", "") or item.media_key)
        reviewed, total, modified, disagreement = self._review_summary(media_id, members)
        # 扫描阶段已有元数据就使用；没有时保持未知，选中该视频后再打开一次读取。
        duration = float(getattr(item, "duration_s", 0.0) or 0.0)
        frame_count = int(getattr(item, "frame_count", 0) or 0)
        return QueueEntry(
            key=item.media_key,
            display=item.relative_path,
            media_id=media_id,
            media_type=item.media_type,
            path=item.absolute_path,
            duration_s=duration,
            frame_count=frame_count,
            reviewed_count=reviewed,
            total_frames=total,
            modified=modified,
            disagreement=disagreement,
            thumbnail_path=item.absolute_path if item.media_type == "image" else "",
            members=[member.absolute_path for member in members],
        )

    def _group_members(self, item) -> list:
        return self._members_by_pair.get(getattr(item, "pair_key", ""), [item]) or [item]

    def _probe_video(self, path: str) -> tuple[float, int]:
        try:
            from ..inference.pipeline import VideoSource

            fps, frame_count = VideoSource.probe(path)
            duration = frame_count / fps if fps > 0 else 0.0
            return duration, frame_count
        except Exception:  # noqa: BLE001
            return 0.0, 0

    def _review_summary(self, media_id: str, members: list) -> tuple[int, int, bool, bool]:
        """返回 (已复核帧数, 总采样帧数, 是否有人工修改, 是否存在模型分歧)。"""
        relevant = [
            state for (state_media, _frame), state in self._states.items() if state_media == media_id
        ]
        total = len(self.sample_indices) or max(1, len(members))
        reviewed = sum(1 for state in relevant if state.decision in ("confirmed", "rejected"))
        modified = any(state.edited for state in relevant)
        disagreement = any(
            state.manual_persons is not None
            and self._differs_from_prediction(state)
            for state in relevant
        )
        return reviewed, total, modified, disagreement

    @staticmethod
    def _differs_from_prediction(state: FrameRunState) -> bool:
        packet = state.packet
        if packet is None or state.manual_persons is None:
            return False
        predicted = [
            (round(d.x1), round(d.y1), round(d.x2), round(d.y2), d.label)
            for d in ReviewPage._packet_persons(packet)
        ]
        manual = [
            (round(d.x1), round(d.y1), round(d.x2), round(d.y2), d.label)
            for d in state.manual_persons
        ]
        return predicted != manual

    def reload_workflows(self, initial: bool = False):
        self.model_library = load_model_library()
        selected_id = self.workflow_config.workflow_id
        catalog = load_workflow_catalog()
        self._workflows = catalog or [self.workflow_config]
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        for index, config in enumerate(self._workflows):
            self.workflow_combo.addItem(config.name or f"工作流 {index + 1}", index)
        self.workflow_combo.blockSignals(False)
        index = next((i for i, w in enumerate(self._workflows) if w.workflow_id == selected_id), 0)
        self.workflow_combo.setCurrentIndex(index)
        self.workflow_config = self._workflows[index]
        self._refresh_add_classes()

    def _on_workflow_changed(self, index: int):
        if 0 <= index < len(self._workflows):
            self.workflow_config = self._workflows[index]
            self._refresh_add_classes()
            self._set_status(f"活动工作流：{self.workflow_config.name or index + 1}")

    def _goto_tasks(self):
        window = self.window()
        if hasattr(window, "show_page"):
            window.show_page("task")
            page = getattr(window, "_pages", {}).get("task")
            if page is not None and hasattr(page, "prepare_from_review"):
                paths = [self._entry_path(entry) for entry in self.media_entries]
                page.prepare_from_review(
                    [path for path in paths if path],
                    self.workflow_config.workflow_id,
                    self.sample_fps,
                )

    def precompute_all_for_review(self):
        """后台跑完当前队列的全部采样帧，完成后回到第一项开始人工复核。"""
        if self._busy or self._precompute_dialog is not None:
            QMessageBox.information(self, "整批预推理", "当前已有推理任务正在运行。")
            return
        items = [(self._entry_key(entry), self._entry_path(entry), self._entry_kind(entry),
                  str(getattr(entry, 'media_id', '') or self._entry_key(entry)))
                 for entry in self.media_entries if self._entry_path(entry)]
        if not items:
            QMessageBox.information(self, "整批预推理", "请先打开图片、视频或文件夹。")
            return
        self.model_library = load_model_library()
        from ..workflow.validation import validate_workflow
        issues = [
            issue for issue in validate_workflow(self.workflow_config, self.model_library)
            if issue.severity == "error"
        ]
        if issues:
            detail = "\n".join(
                (f"第 {issue.node_index} 个节点：" if issue.node_index else "") + issue.message
                for issue in issues
            )
            QMessageBox.warning(self, "工作流尚不能运行", detail + "\n\n请先到“工作流”页修正并运行检查。")
            return
        self._precompute_cancel = False
        progress = PrecomputeProgress(media_total=len(items))
        self._precompute_progress = progress
        dialog = QProgressDialog("正在统计采样帧……", "取消", 0, 0, self)
        dialog.setWindowTitle("整批预推理")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.canceled.connect(lambda: setattr(self, '_precompute_cancel', True))
        self._precompute_dialog = dialog
        timer = QTimer(self)
        timer.setInterval(150)
        timer.timeout.connect(self._poll_precompute)
        self._precompute_timer = timer
        timer.start()
        worker = FunctionWorker(_precompute_for_review, items, self.sample_fps,
                                copy.deepcopy(self.workflow_config), copy.deepcopy(self.model_library),
                                progress, lambda: self._precompute_cancel)
        worker.signals.finished.connect(self._precompute_finished)
        worker.signals.failed.connect(self._precompute_failed)
        self._precompute_worker = worker
        self.thread_pool.start(worker)

    def _poll_precompute(self):
        progress = self._precompute_progress
        dialog = self._precompute_dialog
        if progress is None or dialog is None:
            return
        if progress.total > 0:
            dialog.setRange(0, progress.total)
            dialog.setValue(progress.done)
        dialog.setLabelText(
            f"{progress.phase} · 素材 {progress.media_done}/{progress.media_total} · "
            f"采样帧 {progress.done}/{progress.total or '?'} · 失败 {progress.failed}\n{progress.current}"
        )

    def _close_precompute_ui(self):
        if self._precompute_timer is not None:
            self._precompute_timer.stop()
            self._precompute_timer.deleteLater()
        if self._precompute_dialog is not None:
            self._precompute_dialog.close()
            self._precompute_dialog.deleteLater()
        self._precompute_timer = None
        self._precompute_dialog = None
        self._precompute_progress = None

    def _precompute_finished(self, result):
        self._close_precompute_ui()
        failures = result.get('failures') or []
        if result.get('cancelled'):
            self._set_status(
                f"整批预推理已取消；已处理 {result.get('done', 0)} 个采样帧，"
                f"其中失败 {len(failures)} 个，成功缓存仍可复核。"
            )
            return
        keys = result.get('keys') or []
        for media_id, frame_index, _entry_key in keys:
            state = self._states.get((media_id, int(frame_index)))
            if state is not None and not state.edited:
                state.packet = None
        if not keys:
            detail = "\n".join(failures[:8])
            QMessageBox.warning(
                self, "整批预推理", "没有产生可复核的采样帧。" +
                (f"\n\n失败示例：\n{detail}" if detail else "")
            )
            return
        _media_id, first_frame, first_entry = keys[0]
        self.open_entry(first_entry)
        if self._video is not None and int(first_frame) != self.source_frame:
            self.source_frame = int(first_frame)
            self._restore_persisted_edits(self._media_identity(), self.source_frame)
            self._show_frame()
        self._set_status(
            f"整批预推理完成：成功 {len(keys)} 个，失败 {len(failures)} 个采样帧。"
            "已回到第一项，可用 PgDn 顺序复核。"
        )
        message = (
            f"成功缓存 {len(keys)} 个采样帧，并回到第一项。\n"
            "使用 PgDn 查看下一采样帧；要跳过当前视频剩余采样点，可点“下一视频 / 图片”。"
        )
        if failures:
            message += f"\n\n有 {len(failures)} 个采样帧失败，其余结果不受影响：\n" + "\n".join(failures[:8])
            QMessageBox.warning(self, "整批预推理部分完成", message)
        else:
            QMessageBox.information(self, "整批预推理完成", message)

    def _precompute_failed(self, detail):
        self._close_precompute_ui()
        QMessageBox.critical(self, "整批预推理失败", detail)

    # =============================== 素材打开 =============================== #
    def pick_source(self):
        paths = QFileDialog.getOpenFileNames(
            self,
            "选择图片/视频（可多选）",
            "",
            "媒体 (*.mp4 *.avi *.mov *.mkv *.webm *.jpg *.jpeg *.png *.bmp *.webp)",
        )[0]
        if paths:
            self.load_paths(paths)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含图片或视频的文件夹")
        if folder:
            self.load_paths([folder])

    def load_paths(self, paths):
        """直接装载一组路径（不依赖扫描器），供独立复核使用。"""
        from ..media import MediaSet

        media = MediaSet.from_paths(paths)
        if not media:
            QMessageBox.warning(self, "打开", "未找到任何图片或视频")
            return
        self.media_entries = list(media.entries)
        entries = []
        for entry in media.entries:
            media_id = entry.path
            # 只为真正选中的视频建立 VideoSource；装载目录时不逐个探测。
            duration, frame_count = 0.0, 0
            entries.append(
                QueueEntry(
                    key=entry.path,
                    display=entry.display,
                    media_id=media_id,
                    media_type=entry.kind,
                    path=entry.path,
                    duration_s=duration,
                    frame_count=frame_count,
                    thumbnail_path=entry.path if entry.kind == "image" else "",
                )
            )
        self.queue.set_entries(entries)
        self.project_label.setText(f"项目：直接打开 {len(entries)} 个素材")
        if entries:
            self.open_entry(entries[0].key)

    # =============================== 帧装载 =============================== #
    @staticmethod
    def _entry_key(entry) -> str:
        """兼容领域 Media（media_key）与直接打开的 MediaEntry（path）。"""
        for attribute in ("media_key", "key", "path"):
            value = getattr(entry, attribute, None)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _entry_path(entry) -> str:
        for attribute in ("absolute_path", "path"):
            value = getattr(entry, attribute, None)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _entry_kind(entry) -> str:
        for attribute in ("media_type", "kind"):
            value = getattr(entry, attribute, None)
            if value:
                return str(value)
        return "image"

    @staticmethod
    def _entry_display(entry) -> str:
        for attribute in ("relative_path", "display"):
            value = getattr(entry, attribute, None)
            if value:
                return str(value)
        return ReviewPage._entry_path(entry)

    def _entry_by_key(self, key: str):
        for entry in self.media_entries:
            if self._entry_key(entry) == key:
                return entry
        return None

    def open_entry(self, key: str):
        entry = self._entry_by_key(key)
        if entry is None:
            return
        self.entry_index = next(
            (index for index, candidate in enumerate(self.media_entries)
             if self._entry_key(candidate) == key),
            -1,
        )
        self._close_video()
        self.current_key = key
        self.timeline.stop()
        path = self._entry_path(entry)
        kind = self._entry_kind(entry)
        if kind == "video":
            try:
                from ..inference.pipeline import VideoSource

                self._video = VideoSource(path)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "无法打开视频", f"{path}\n{exc}")
                self._video = None
                return
            self.sample_indices = self._video.sample_indices(self.sample_fps)
            self.source_frame = self.sample_indices[0] if self.sample_indices else 0
            self._image = None
        else:
            self._video = None
            self.sample_indices = [0]
            self.source_frame = 0
            try:
                self._image = read_image(path)
            except Exception as exc:  # noqa: BLE001
                self._image = None
                # 文件被删/被占用时不要弹模态框：可能发生在 teardown 或批量刷新期间，
                # 阻塞对话框会让调用方永久卡住。
                self._set_status(f"无法读取图片（{Path(path).name}）：{exc}")
        self.queue.select_key(key)
        self._restore_persisted_edits(self._media_identity(), self.source_frame)
        self._show_frame()
        # 当前帧的人工标注是按需恢复的，只刷新这一行；不能为显示“已修改”重新扫描整个项目。
        media_id = self._media_identity()
        queue_entry = next((value for value in self.queue.entries() if value.key == key), None)
        if queue_entry is not None:
            members = self._group_members(entry) if self.context is not None else []
            reviewed, total, modified, disagreement = self._review_summary(media_id, members)
            self.queue.update_entry_status(
                key, reviewed_count=reviewed, total_frames=total,
                modified=modified, disagreement=disagreement,
            )

    def _close_video(self):
        if self._video is not None:
            try:
                self._video.close()
            except Exception:  # noqa: BLE001
                pass
            self._video = None

    def closeEvent(self, event):
        self._closing = True
        self.flush_pending_edits()
        self._close_video()
        super().closeEvent(event)

    def flush_pending_edits(self):
        """供外部（关项目/切页）在丢弃页面之前主动落库。"""
        try:
            self._persist_manual_revision()
        except Exception:  # noqa: BLE001
            pass

    def _media_identity(self) -> str:
        entry = self._entry_by_key(self.current_key)
        if entry is None:
            return self.current_key or "unknown"
        # 领域 Media 优先用稳定 media_id；直接打开的 MediaEntry 用路径
        media_id = str(getattr(entry, "media_id", "") or "")
        return media_id or self._entry_key(entry) or "unknown"

    def _state_key(self) -> Optional[tuple[str, int]]:
        if not self.current_key:
            return None
        return (self._media_identity(), int(self.source_frame))

    def _state(self) -> FrameRunState:
        key = self._state_key()
        if key is None:
            return FrameRunState()
        return self._states.setdefault(key, FrameRunState())

    def _read_frame_image(self):
        key = self._state_key()
        if key is not None and key in self._states and self._states[key].image is not None:
            return self._states[key].image
        if self._video is not None:
            frame = self._video.read(self.source_frame)
            if frame is None:
                return None
            image = frame.image
        else:
            image = self._image
        if key is not None and image is not None:
            self._states.setdefault(key, FrameRunState()).image = image
        return image

    def _show_frame(self):
        key = self._state_key()
        same_frame = key == getattr(self, '_canvas_frame_key', None)
        self._canvas_frame_key = key
        image = self._read_frame_image()
        state = self._state()
        if state.packet is not None and image is not None and (
                state.packet.frame is None or state.packet.frame.image is None):
            from ..services.precompute_cache import hydrate_packet
            hydrate_packet(state.packet, image)
        if image is None:
            self.canvas.set_data(None, [], [])
        else:
            persons = state.manual_persons
            behaviors = state.manual_behaviors
            if persons is None:
                persons = self._packet_persons(state.packet)
                behaviors = self._packet_behaviors(state.packet)
            self.canvas.set_data(image, copy.deepcopy(persons or []), copy.deepcopy(behaviors or []), keep_selection=same_frame)
        self._refresh_inspector()
        self._refresh_timeline()
        self._update_identity()
        self.decision_bar.set_decision(state.decision)
        self.decision_bar.set_export_selected(state.selected_for_export)
        if state.pending_downstream and not self._busy:
            pending = state.pending_downstream
            state.pending_downstream = None
            self._request_downstream_recompute(state, *pending)

    # ------------------------------ 包 <-> 画布 ------------------------------ #
    @staticmethod
    def _packet_persons(packet) -> list[Detection]:
        if packet is None:
            return []
        if packet.subjects:
            return list(packet.subjects)
        first = next(iter(packet.full_results.values()), None)
        return list(first.detections) if first else []

    @staticmethod
    def _packet_behaviors(packet) -> list[Detection]:
        if packet is None:
            return []
        if packet.candidates:
            return list(packet.candidates)
        layers = list(packet.full_results.values())[1:]
        return [d for result in layers for d in result.detections]

    # =============================== 运行 =============================== #
    def run_current_frame(self):
        self.model_library = load_model_library()
        if not self.current_key:
            QMessageBox.information(self, "提示", "请先打开素材")
            return
        if self._busy:
            return
        state = self._state()
        # “运行当前帧”表示以模型为准重新运行。人工修订只在本次运行成功后清除，
        # 推理失败时仍保留，避免模型故障导致用户标注丢失。
        replace_manual = bool(
            state.edited or state.manual_persons is not None
            or state.manual_behaviors is not None or state.crop_edits
        )
        inherited = {} if replace_manual else self._inherit_results(state)
        if replace_manual:
            self.node_cache.clear()
        image = self._read_frame_image()
        if image is None:
            QMessageBox.warning(self, "无法读取帧", "当前帧读取失败，可能视频已损坏或帧号越界。")
            return
        self._busy = True
        self.run_label.setText("运行状态：正在运行……")
        frame = Frame(
            index=self.source_frame,
            timestamp=self.source_frame / self._video.fps if self._video and self._video.fps else 0.0,
            image=image,
        )
        run_id = uuid.uuid4().hex[:12]
        state.run_id = run_id
        state.run_status = "running"
        self._register_run(state, run_id, "running", source="manual")
        worker = FunctionWorker(
            _run_workflow,
            frame,
            copy.deepcopy(self.workflow_config),
            copy.deepcopy(self.model_library),
            self._media_identity(),
            run_id,
            inherited,
            None,
            self.node_cache,
        )
        origin, context = self._state_key(), self.context
        worker.signals.finished.connect(
            lambda result: self._deliver_result(result, origin, context, False, replace_manual)
        )
        worker.signals.failed.connect(lambda detail: self._deliver_failure(detail, origin, context, False))
        self._worker = worker  # 持有引用，避免信号送达前被回收
        self.thread_pool.start(worker)

    def _deliver_result(self, packet, origin, context, downstream, replace_manual=False):
        self._busy = False
        if self._closing or context is not self.context:
            return
        if replace_manual:
            state = self._states.get(origin)
            if state is not None:
                self._clear_manual_edits(state, origin)
        if origin != self._state_key():
            state = self._states.get(origin)
            if state is not None:
                state.packet = packet
                state.prediction_results = copy.deepcopy(packet.full_results)
                state.run_status = 'success'
            service = getattr(context, 'run_service', None)
            if service and not downstream:
                service.finish_run(packet.run_id, 'success')
            self.run_label.setText('运行状态：后台帧已完成')
            self._drain_pending_edits()
            return
        (self._downstream_finished if downstream else self._run_finished)(packet)

    def _deliver_failure(self, detail, origin, context, downstream):
        self._busy = False
        if self._closing or context is not self.context:
            return
        if origin != self._state_key():
            state = self._states.get(origin)
            if state:
                state.run_status = 'failed'
                service = getattr(context, 'run_service', None)
                if service and not downstream:
                    service.finish_run(state.run_id, 'failed')
            self.run_label.setText('运行状态：后台帧失败，请返回查看')
            self._drain_pending_edits()
            return
        (self._downstream_failed if downstream else self._run_failed)(detail)

    def _register_run(self, state: FrameRunState, run_id: str, status: str, source: str = "manual"):
        """把一次运行登记到 run 表，回答“这次结果/导出对应哪次运行”。"""
        context = self.context
        service = getattr(context, "run_service", None) if context is not None else None
        if service is None or context.repository is None or not self.current_key:
            return
        try:
            frame_ref = context.repository.get_or_create_frame_ref(
                self._media_identity(), int(self.source_frame)
            )
            if status == "running":
                service.start_run(
                    self.workflow_config,
                    list(self.model_library),
                    media_id=self._media_identity(),
                    frame_ref_id=frame_ref.frame_ref_id,
                    source=source,
                    run_id=run_id,
                    meta={"frame_index": int(self.source_frame)},
                )
            else:
                service.finish_run(run_id, status)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"运行记录未落库：{exc}")

    def _inherit_results(self, state: FrameRunState) -> dict:
        """把已有的人工修订生成的内容作为下游输入（AR-02），不因重跑丢失。"""
        inherited: dict = {}
        if state.manual_persons is not None or state.manual_behaviors is not None:
            persons = state.manual_persons or []
            behaviors = state.manual_behaviors or []
            inherited["subject"] = ModelResult(task_type=TaskType.DETECTION, detections=list(persons))
            inherited["candidate"] = ModelResult(task_type=TaskType.DETECTION, detections=list(behaviors))
            inherited["skip_full_inference"] = True
        return inherited

    def _run_finished(self, result):
        self._busy = False
        packet = result
        state = self._state()
        if not state.prediction_results:
            state.prediction_results = copy.deepcopy(packet.full_results)
        state.packet = packet
        # 自动下游重算仍保留人工版；完整“运行当前帧”会在到达这里前清除人工版。
        self._reapply_edits(state, packet)
        self.canvas.set_data(
            packet.frame.image,
            state.manual_persons if state.manual_persons is not None else self._packet_persons(packet),
            state.manual_behaviors if state.manual_behaviors is not None else self._packet_behaviors(packet),
            keep_selection=True,
        )
        state.run_status = "success"
        self._register_run(state, state.run_id, "success")
        trace = packet.meta.get("trace")
        verdict = self._verdict_text(packet)
        if trace is not None and trace.reports:
            cached = sum(1 for report in trace.reports if report.cached)
            self.run_label.setText(
                f"运行状态：完成（{len(trace.reports)} 个节点，复用缓存 {cached} 个，"
                f"{trace.elapsed_ms:.0f}ms）{verdict}"
            )
        else:
            self.run_label.setText("运行状态：完成" + verdict)
        self._refresh_inspector()
        self._refresh_timeline()
        self._refresh_queue_status()
        from ..services.review_session import save_state
        save_state(self._state_key(), state)
        self._drain_pending_edits()

    @staticmethod
    def _verdict_text(packet) -> str:
        """把规则节点（计数判定等）的结论拼到运行状态里，一眼看到"命中没命中"。"""
        verdicts = list((packet.meta or {}).get("verdicts") or [])
        if not verdicts:
            return ""
        parts = []
        for item in verdicts:
            mark = "命中" if item.get("passed") else "未命中"
            parts.append(f"{item.get('label', '规则')}={mark}({item.get('detail', '')})")
        return " · 判定：" + "；".join(parts)

    def _run_failed(self, detail: str):
        self._busy = False
        state = self._state()
        state.run_status = "failed"
        self._register_run(state, state.run_id, "failed")
        self.run_label.setText("运行状态：失败")
        QMessageBox.critical(self, "运行失败", detail)
        self._drain_pending_edits()

    def _reapply_edits(self, state: FrameRunState, packet):
        """把保存的人工编辑重新套用到新结果上（按裁剪序号与对象身份）。"""
        edits = state.edits or {}
        for index, slot in enumerate(packet.crops):
            saved = edits.get(index)
            if not saved:
                continue
            slot.user_result.update(saved.get("user_result") or {})
            slot.edited = True
            slot.edited_source = "modified"
        from ..services.review_session import apply_crop_edits
        apply_crop_edits(packet, state.crop_edits)

    def _edit_crop(self, index: int):
        state = self._state()
        packet = state.packet
        if packet is None or not 0 <= index < len(packet.crops):
            return
        from ..services.review_session import crop_edit, save_state, update_crop_business
        from .crop_editor import CropEditorDialog
        entry = self._entry_by_key(self.current_key)
        dialog = CropEditorDialog(packet.crops[index], self._entry_display(entry) if entry else '',
                                  self.source_frame, index, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        slot = packet.crops[index]
        slot.results = dialog.results()
        slot.edited = True
        slot.edited_source = 'modified'
        slot.needs_review = False
        update_crop_business(slot)
        state.crop_edits[slot.crop_id] = crop_edit(slot)
        state.edited = True
        self._set_export_selected(True, persist=False)
        from ..workflow.nodes import DrawNode
        DrawNode({}, '人工修订重绘').run(packet)
        save_state(self._state_key(), state)
        self.save_label.setText('保存状态：裁剪标注已保存')
        self._refresh_inspector()
        self._refresh_queue_status()

    # =============================== 编辑 =============================== #
    def _on_edit_committed(self, summary: str):
        """一次编辑已提交（拖动只在松手时提交一条命令），随后落库人工修订。"""
        self._set_status(f"已提交：{summary}")
        self._set_export_selected(True, persist=False)
        self._persist_manual_revision()

    def _persist_manual_revision(self):
        """把人工修订写入 object_annotation（三版本），保证重启可恢复。"""
        from ..services.review_session import save_state
        if self.current_key:
            save_state(self._state_key(), self._state())
        context = self.context
        if context is None or context.repository is None or not self.current_key:
            return
        if getattr(context.repository, "closed", False):
            return  # 项目已关闭：静默跳过，不要污染日志或阻塞退出
        state = self._state()
        if state.manual_persons is None and state.manual_behaviors is None:
            return
        try:
            frame_ref = context.repository.get_or_create_frame_ref(
                self._media_identity(), int(self.source_frame)
            )
            store = context.annotation_store
            written = store.save_frame(
                frame_ref.frame_ref_id,
                state.manual_persons,
                state.manual_behaviors,
                predictions=self._prediction_detections(state),
                adopted_source="manual",
            )
            self.save_label.setText(f"保存状态：人工修订已保存（{written} 个对象）")
        except Exception as exc:  # noqa: BLE001
            self.save_label.setText("保存状态：人工修订保存失败")
            self._set_status(f"人工修订未落库：{exc}")

    @staticmethod
    def _prediction_detections(state: FrameRunState) -> list[Detection]:
        """本次运行的模型预测对象（用于并排对比，不与人工版本混用）。"""
        if state.prediction_results:
            detections = [d for result in state.prediction_results.values() for d in result.detections]
        elif state.packet is not None:
            detections = ReviewPage._packet_persons(state.packet) + ReviewPage._packet_behaviors(state.packet)
        else:
            detections = []
        return [ReviewPage._clone(d) for d in detections]

    def _clear_manual_edits(self, state: FrameRunState, key) -> None:
        """完整模型重跑成功后清除大图/裁剪人工版，并同步删除持久化人工框。"""
        state.manual_persons = None
        state.manual_behaviors = None
        state.crop_edits = {}
        state.edited = False
        state.adopted_source = "predicted"
        state.prediction_results = {}
        from ..services.review_session import save_state
        save_state(key, state)
        context = self.context
        if context is None or context.repository is None or context.annotation_store is None:
            return
        if getattr(context.repository, "closed", False):
            return
        try:
            media_id, frame_index = key
            frame_ref = context.repository.get_or_create_frame_ref(media_id, int(frame_index))
            # 空列表具有“删除本帧既有人工对象”的明确语义；None 则表示不改数据库。
            context.annotation_store.save_frame(
                frame_ref.frame_ref_id, [], [], predictions=[], adopted_source="predicted"
            )
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"模型结果已恢复，但旧人工版本清理失败：{exc}")

    def _restore_persisted_edits(self, media_id: str, frame_index: int):
        """从数据库恢复该帧人工修订；已经有内存状态时不覆盖当前编辑。"""
        context = self.context
        key = (media_id, int(frame_index))
        state = self._states.setdefault(key, FrameRunState())
        if state.packet is None:
            from ..services.precompute_cache import load_packet
            state.packet = load_packet(media_id, frame_index, self.workflow_config.workflow_id)
            if state.packet is not None and not state.prediction_results:
                state.prediction_results = copy.deepcopy(state.packet.full_results)
        from ..services.review_session import load_export_selected
        state.selected_for_export = load_export_selected(key)
        if context is None or context.annotation_store is None:
            from ..services.review_session import load_state
            load_state(key, state)
            return
        if key in self._states and (
            self._states[key].manual_persons is not None or self._states[key].manual_behaviors is not None
        ):
            return
        try:
            frame = context.annotation_store.load_frame(media_id, int(frame_index))
        except Exception:  # noqa: BLE001
            return
        if not frame.has_manual:
            return
        persons = [d for d in frame.manual_objects if d.label in ("person", "head")]
        behaviors = [d for d in frame.manual_objects if d.label not in ("person", "head")]
        state.manual_persons = persons
        state.manual_behaviors = behaviors
        state.edited = True

    def _on_boxes_changed(self, mark_selected: bool = True):
        state = self._state()
        persons = [self._clone(d) for d in self.canvas.persons()]
        behaviors = [self._clone(d) for d in self.canvas.behaviors()]
        state.manual_persons = persons
        state.manual_behaviors = behaviors
        state.edited = True
        if mark_selected:
            self._set_export_selected(True, persist=False)
        # 先落库并立刻反映“已修改”，不依赖下游重算是否已经跑完
        self._persist_manual_revision()
        self.save_label.setText("保存状态：有人工修改（未导出）")
        self._refresh_inspector()
        self._refresh_timeline()
        self._refresh_queue_status()
        self._request_downstream_recompute(state, persons, behaviors)

    def recompute_current_boxes(self):
        """用画布上当前的全图框重算关联/裁剪/下游，不重新跑全图模型。

        2026-09-15：原来的「按当前标注更新下游」按钮已移除（与「运行当前帧」+
        编辑后自动重算重复，且不写 run 记录、丢失可追溯性）。这个方法保留作为
        自动重算与自动化/测试的单一入口。
        """
        if not self.current_key:
            self._set_status("请先打开素材并绘制或检测出对象")
            return
        self.model_library = load_model_library()
        self._on_boxes_changed(mark_selected=False)

    def _drain_pending_edits(self):
        if self._busy or self._closing:
            return
        state = self._state()
        if state.pending_downstream is not None:
            pending = state.pending_downstream
            state.pending_downstream = None
            self._request_downstream_recompute(state, *pending)

    def _request_downstream_recompute(self, state: FrameRunState, persons, behaviors):
        """异步重算下游：拖动/编辑后 UI 不再阻塞等待工作流。"""
        if self._busy:
            # 已有一次重算在跑：把最新输入记为待处理，完成后自动补一次
            state.pending_downstream = (persons, behaviors)
            self.run_label.setText("运行状态：已记录最新框，当前任务结束后自动更新下游")
            return
        image = state.image if state.image is not None else self._read_frame_image()
        if image is None:
            return
        self._busy = True
        self.run_label.setText("运行状态：正在重算下游……")
        frame = Frame(index=self.source_frame, timestamp=0.0, image=image)
        base_results = {
            "subject": ModelResult(task_type=TaskType.DETECTION, detections=list(persons)),
            "candidate": ModelResult(task_type=TaskType.DETECTION, detections=list(behaviors)),
        }
        packet = copy.deepcopy(state.packet) if state.packet else Packet(frame=frame, media_identity=self._media_identity())
        packet.frame = frame
        packet.full_results = base_results
        packet.meta["skip_full_inference"] = True
        worker = FunctionWorker(
            _run_workflow,
            frame,
            copy.deepcopy(self.workflow_config),
            copy.deepcopy(self.model_library),
            self._media_identity(),
            state.run_id or uuid.uuid4().hex[:12],
            {**base_results, "skip_full_inference": True},
            packet,
            self.node_cache,
        )
        origin, context = self._state_key(), self.context
        worker.signals.finished.connect(lambda result: self._deliver_result(result, origin, context, True))
        worker.signals.failed.connect(lambda detail: self._deliver_failure(detail, origin, context, True))
        self._downstream_worker = worker
        self.thread_pool.start(worker)

    def _downstream_finished(self, result):
        self._busy = False
        packet = result
        state = self._state()
        state.packet = packet
        self._reapply_edits(state, packet)
        persons = state.manual_persons if state.manual_persons is not None else self._packet_persons(packet)
        behaviors = state.manual_behaviors if state.manual_behaviors is not None else self._packet_behaviors(packet)
        self.canvas.set_data(packet.frame.image, copy.deepcopy(persons), copy.deepcopy(behaviors), keep_selection=True)
        self.run_label.setText(
            f"运行状态：已按当前框更新 · {len(packet.associations)} 个人体关联 · "
            f"{len(packet.crops)} 张裁剪{self._verdict_text(packet)}"
        )
        self.save_label.setText("保存状态：有人工修改（未导出）")
        self._refresh_inspector()
        self._refresh_timeline()
        self._refresh_queue_status()
        from ..services.review_session import save_state
        save_state(self._state_key(), state)
        pending = getattr(state, "pending_downstream", None)
        if pending:
            state.pending_downstream = None
            self._request_downstream_recompute(state, *pending)

    def _downstream_failed(self, detail: str):
        self._busy = False
        self.run_label.setText("运行状态：下游重算失败")
        self.save_label.setText("保存状态：有人工修改（下游重算失败）")
        self._set_status(f"下游重算失败：{detail.strip().splitlines()[-1] if detail else ''}")
        self._refresh_inspector()
        self._refresh_timeline()
        self._refresh_queue_status()
        self._drain_pending_edits()

    def _rerun_downstream(self, state: FrameRunState, persons, behaviors):
        """同步重算（测试与无事件循环场景用）；正常交互走 `_request_downstream_recompute`。"""
        image = state.image if state.image is not None else self._read_frame_image()
        if image is None:
            return
        frame = Frame(index=self.source_frame, timestamp=0.0, image=image)
        packet = state.packet or Packet(frame=frame, media_identity=self._media_identity())
        packet.frame = frame
        packet.full_results["subject"] = ModelResult(task_type=TaskType.DETECTION, detections=list(persons))
        packet.full_results["candidate"] = ModelResult(task_type=TaskType.DETECTION, detections=list(behaviors))
        packet.meta["models"] = {model.name: model for model in self.model_library}
        packet.meta["skip_full_inference"] = True
        try:
            packet = Workflow.from_config(self.workflow_config).run(packet)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"下游重算失败：{exc}")
            self.save_label.setText("保存状态：有人工修改（下游重算失败）")
            self._refresh_inspector()
            self._refresh_timeline()
            self._refresh_queue_status()
            return
        state.packet = packet
        self._reapply_edits(state, packet)
        self.canvas.set_data(image, list(persons), list(behaviors), keep_selection=True)
        self._refresh_inspector()
        self._refresh_timeline()
        self.save_label.setText("保存状态：有人工修改（未导出）")
        self._refresh_queue_status()

    @staticmethod
    def _clone(det: Detection) -> Detection:
        return copy.deepcopy(det)

    def set_canvas_mode(self, mode: str):
        self.canvas.set_mode(mode)
        for key, button in self.mode_buttons.items():
            button.setChecked(key == mode)
        self._set_status({"select": "选择/移动", "add": "绘制矩形", "pan": "平移画布"}.get(mode, mode))

    def _on_add_class_changed(self, _index: int):
        self.canvas.set_add_class(self.add_class_combo.currentText())

    def _full_model_layers(self):
        models = {model.name: model for model in self.model_library}
        layers = []
        for node in self.workflow_config.nodes:
            if node.type != 'inference' or not node.enabled:
                continue
            raw = node.params.get('model', {})
            name = raw.get('name', '') if isinstance(raw, dict) else str(raw)
            key = str(node.params.get('model_key', node.name))
            model = models.get(name)
            layers.append((key, name or node.name, list(getattr(model, 'classes', []) or [])))
        return layers

    def _on_add_layer_changed(self, _index: int):
        key = str(self.add_layer_combo.currentData() or '')
        classes = next((values for layer, _name, values in self._full_model_layers() if layer == key), [])
        current = self.add_class_combo.currentText()
        self.add_class_combo.blockSignals(True)
        self.add_class_combo.clear()
        self.add_class_combo.addItems(classes)
        if current in classes:
            self.add_class_combo.setCurrentText(current)
        self.add_class_combo.blockSignals(False)
        self.canvas.set_add_model_key(key)
        self.canvas.set_add_class(self.add_class_combo.currentText())

    def _refresh_add_classes(self):
        if not hasattr(self, "add_class_combo"):
            return
        current = str(self.add_layer_combo.currentData() or '')
        self.add_layer_combo.blockSignals(True)
        self.add_layer_combo.clear()
        for key, name, _classes in self._full_model_layers():
            self.add_layer_combo.addItem(f'{name}（{key}）', key)
        self.add_layer_combo.setCurrentIndex(max(0, self.add_layer_combo.findData(current)))
        self.add_layer_combo.blockSignals(False)
        self._on_add_layer_changed(self.add_layer_combo.currentIndex())
        self.add_class_combo.setToolTip("类别来自当前全图模型图层；人工新增框保留模型来源，便于分别导出训练标注。")

    # =============================== 选择 / 检查器 =============================== #
    def _on_selection_changed(self, det):
        if det is None:
            self.timeline.set_selected_object("")
            self._refresh_timeline()
            return
        label = f"{det.label} {det.score:.2f}"
        self.timeline.set_selected_object(label)
        if det in self.canvas.persons():
            index = self.canvas.persons().index(det)
            self.inspector.select_object("person", index)
        elif det in self.canvas.behaviors():
            index = self.canvas.behaviors().index(det)
            self.inspector.select_object("behavior", index)
        # 时间轴命中轨按选中对象过滤
        self._selected_object_box = (det.x1, det.y1, det.x2, det.y2)
        self._refresh_timeline()

    def _on_inspector_selection(self, kind: str, index: int):
        self.canvas.select_detection(kind, index)

    def _on_adopt_source_changed(self, source: str):
        state = self._state()
        state.adopted_source = source
        self._persist_manual_revision()
        self._set_status(
            "采用版本：" + ("人工修订版" if source == "manual" else "模型预测版")
        )
        self._refresh_queue_status()

    def _refresh_inspector(self):
        state = self._state()
        packet = state.packet
        classifications = []
        if packet:
            for key, result in packet.full_results.items():
                if result.classification:
                    c = result.classification
                    classifications.append(f'{key} · {c.label}  {c.score:.0%}')
        self.inspector.classification_label.setText('\n'.join(classifications))
        self.inspector.classification_label.setVisible(bool(classifications))
        persons = state.manual_persons if state.manual_persons is not None else self._packet_persons(packet)
        behaviors = state.manual_behaviors if state.manual_behaviors is not None else self._packet_behaviors(packet)
        slots = packet.crops if packet is not None else []
        associations = packet.associations if packet is not None else []
        image = state.image if state.image is not None else self._image
        size = image.shape[:2][::-1] if image is not None else (0, 0)
        self.inspector.set_frame(
            list(persons or []),
            list(behaviors or []),
            list(associations or []),
            list(slots or []),
            size,
            predictions=self._prediction_detections(state),
            has_prediction=bool(state.prediction_results),
            adopted_source=state.adopted_source,
        )

    # =============================== 时间轴 =============================== #
    def _refresh_timeline(self):
        data = TimelineData()
        if self._video is not None:
            data.source_frame_count = self._video.frame_count
            data.source_fps = self._video.fps
            data.sample_indices = list(self.sample_indices)
        else:
            data.source_frame_count = 1
            data.source_fps = 0.0
            data.sample_indices = [0]
        media_id = self._media_identity()
        for (state_media, frame_index), state in self._states.items():
            if state_media != media_id:
                continue
            if state.manual_persons is not None or state.packet is not None:
                data.keyframes.add(frame_index)
            data.review_states[frame_index] = state.decision
            if state.packet is not None:
                for start, end in self._hit_ranges(state.packet):
                    data.hit_intervals.append((start, end, "hit"))
                self._collect_object_hits(data, state, frame_index)
        data.hit_intervals = _merge_ranges(data.hit_intervals, HIT_MERGE_WINDOW)
        for key, ranges in data.object_hits.items():
            data.object_hits[key] = _merge_ranges(ranges, HIT_MERGE_WINDOW)
        if self._selected_object_box is None:
            data.selected_object_label = ""
        self.timeline.set_data(data)
        self.timeline.set_current_source_frame(self.source_frame)

    @staticmethod
    def _object_key(det) -> str:
        return f"{det.label}@{det.x1:.0f},{det.y1:.0f},{det.x2:.0f},{det.y2:.0f}"

    def _collect_object_hits(self, data: TimelineData, state: FrameRunState, frame_index: int):
        """把每条命中关联到具体人身对象，供时间轴按选中对象过滤。"""
        packet = state.packet
        if packet is None:
            return
        hit_people = {self._object_key(getattr(slot, 'source_bbox', None) or slot.person_bbox) for slot in packet.crops}
        pool = state.manual_persons if state.manual_persons is not None else self._packet_persons(packet)
        for det in pool:
            key = self._object_key(det)
            if key not in hit_people:
                continue
            data.object_hits.setdefault(f"{det.label} {det.score:.2f}", []).append(
                (frame_index, frame_index, "hit")
            )
            data.object_hits.setdefault(key, []).append((frame_index, frame_index, "hit"))

    @staticmethod
    def _hit_ranges(packet) -> list[tuple[int, int]]:
        index = packet.frame.index if packet.frame is not None else 0
        return [(index, index)] if packet.crops else []

    def _step_source_frame(self, delta: int):
        if delta == 0:
            return
        if self._video is None:
            return
        target = min(max(0, self.source_frame + delta), max(0, self._video.frame_count - 1))
        if target != self.source_frame:
            # 离开当前帧前先落库，避免切帧丢人工修订
            self._persist_manual_revision()
            self.timeline.set_current_source_frame(target)
            self.source_frame = target
            self._restore_persisted_edits(self._media_identity(), target)
            self._show_frame()

    def _step_sample_frame(self, direction: int):
        from bisect import bisect_left, bisect_right
        if direction == 0:
            return
        if self._video is None:
            if direction > 0:
                if self._goto_next_entry(wrap=False):
                    self._set_status("已进入下一视频 / 图片的开始位置")
                else:
                    self._set_status("已是文件夹最后一个文件")
            else:
                if self._goto_previous_entry(wrap=False):
                    self._set_status("已进入上一视频的开始位置 / 上一图片")
                else:
                    self._set_status("已是文件夹首个文件")
            return
        if not self.sample_indices:
            moved = (self._goto_next_entry(wrap=False) if direction > 0
                     else self._goto_previous_entry(wrap=False))
            if not moved:
                self._set_status("已是文件夹最后一个文件" if direction > 0 else "已是文件夹首个文件")
            return
        index = (bisect_right(self.sample_indices, self.source_frame) if direction > 0
                 else bisect_left(self.sample_indices, self.source_frame) - 1)
        if not 0 <= index < len(self.sample_indices):
            if direction > 0:
                if self._goto_next_entry(wrap=False):
                    self._set_status("当前视频采样帧已结束，进入下一视频 / 图片的开始位置")
                else:
                    self._set_status("已是文件夹最后一个文件")
            else:
                if self._goto_previous_entry(wrap=False):
                    self._set_status("当前视频位于开始位置，进入上一视频的开始位置 / 上一图片")
                else:
                    self._set_status("已是文件夹首个文件")
            return
        self.timeline.stop()
        self._step_source_frame(self.sample_indices[index] - self.source_frame)

    def _step_hit(self, direction: int):
        hits = sorted({start for start, _end, _label in self._timeline_hits()})
        if not hits:
            self._set_status("当前媒体没有命中区间")
            return
        if direction > 0:
            candidates = [value for value in hits if value > self.source_frame]
            target = candidates[0] if candidates else hits[0]
        else:
            candidates = [value for value in hits if value < self.source_frame]
            target = candidates[-1] if candidates else hits[-1]
        self._persist_manual_revision()
        self.source_frame = target
        self._restore_persisted_edits(self._media_identity(), target)
        self._show_frame()

    def _timeline_hits(self) -> list[tuple[int, int, str]]:
        media_id = self._media_identity()
        hits = []
        for (state_media, frame_index), state in self._states.items():
            if state_media == media_id and state.packet is not None and state.packet.crops:
                hits.append((frame_index, frame_index, "hit"))
        return _merge_ranges(hits, HIT_MERGE_WINDOW)

    def _on_sample_fps_changed(self, _index: int):
        fps = int(self.sample_fps_spin.currentData() or 1)
        if fps == self.sample_fps:
            return
        self.sample_fps = fps
        if self._video is None:
            self._set_status(f"采样率：{fps} FPS（图片不受影响）")
            return
        self.sample_indices = self._video.sample_indices(fps)
        # 重建采样计划后，停在不超过当前原始帧的最后一个采样点
        nearest = 0
        for index in self.sample_indices:
            if index <= self.source_frame:
                nearest = index
            else:
                break
        self.source_frame = nearest
        self._set_status(f"采样率已改为 {fps} FPS，共 {len(self.sample_indices)} 个采样点")
        self._show_frame()
        self._refresh_queue_status()

    # =============================== 复核结论 =============================== #
    def set_export_selected(self, selected: bool):
        self._set_export_selected(selected, persist=True)

    def _set_export_selected(self, selected: bool, persist: bool):
        if not self.current_key:
            self.decision_bar.set_export_selected(False)
            return
        state = self._state()
        state.selected_for_export = bool(selected)
        self.decision_bar.set_export_selected(state.selected_for_export)
        if persist:
            from ..services.review_session import save_state
            save_state(self._state_key(), state)
        count = sum(1 for item in self._states.values() if item.selected_for_export)
        action = "加入" if selected else "移出"
        self._set_status(f"当前帧已{action}保存集 · 保存集共 {count} 帧")

    def set_decision(self, decision: str):
        if decision not in ("pending", "confirmed", "rejected"):
            raise ValueError(f"非法复核结论: {decision}")
        state = self._state()
        state.decision = decision
        # 结论落地前先把人工修订保存，保证“采用的版本”确实可取回
        self._persist_manual_revision()
        self.decision_bar.set_decision(decision)
        self._persist_decision(decision)
        self._refresh_timeline()
        self._refresh_queue_status()
        self._set_status(f"已记录结论：{ {'confirmed':'确认','rejected':'驳回','pending':'待定'}[decision] }")

    def _persist_decision(self, decision: str):
        """写入互斥结论表；每帧一条，天然不会出现矛盾结论（评审第 7 节）。"""
        context = self.context
        if context is None or context.repository is None or not self.current_key:
            return
        try:
            frame_ref = context.repository.get_or_create_frame_ref(
                self._media_identity(), int(self.source_frame)
            )
            service = context.save_service or _fallback_save_service(context.repository)
            service.set_review_decision(
                frame_ref.frame_ref_id,
                decision,
                adopting_source="manual" if self._state().edited else "predicted",
            )
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"结论未写入数据库：{exc}")

    def confirm_and_next(self):
        self.set_decision("confirmed")
        self._goto_next_review_item()

    def _goto_next_review_item(self):
        """视频内优先走下一采样帧；当前视频结束后再进入下一素材。"""
        if self._video is not None:
            candidates = [index for index in self.sample_indices if index > self.source_frame]
            if candidates:
                self._persist_manual_revision()
                self.source_frame = candidates[0]
                self._restore_persisted_edits(self._media_identity(), self.source_frame)
                self._show_frame()
                return
        self._goto_next_entry(wrap=False)

    def _goto_next_media(self):
        """跳过当前素材的剩余采样帧，直接进入队列中的下一视频或图片。"""
        if self._goto_next_entry(wrap=False):
            self._set_status("已跳到下一视频 / 图片")
        else:
            self._set_status("已到全部素材末尾")

    def _goto_next_entry(self, wrap: bool = True):
        entries = [entry for entry in self.queue.entries()]
        if not entries:
            return False
        self._persist_manual_revision()
        current = self.queue.current_key()
        keys = [entry.key for entry in entries]
        if current in keys:
            index = keys.index(current) + 1
            if index >= len(keys):
                if not wrap:
                    return False
                index = 0
        else:
            index = 0
        self.open_entry(keys[index])
        return True

    def _goto_previous_entry(self, wrap: bool = True):
        """进入队列上一素材；视频由 open_entry 统一打开到首个采样点。"""
        entries = [entry for entry in self.queue.entries()]
        if not entries:
            return False
        self._persist_manual_revision()
        current = self.queue.current_key()
        keys = [entry.key for entry in entries]
        if current in keys:
            index = keys.index(current) - 1
            if index < 0:
                if not wrap:
                    return False
                index = len(keys) - 1
        else:
            index = 0
        self.open_entry(keys[index])
        return True

    # =============================== 导出 =============================== #
    def _selected_export_states(self):
        return [(key, state) for key, state in self._states.items()
                if state.selected_for_export and state.packet is not None]

    @staticmethod
    def _state_image(state):
        if state.image is not None:
            return state.image
        packet = state.packet
        return packet.frame.image if packet is not None and packet.frame is not None else None

    @staticmethod
    def _review_crops(packet, media_id, frame_index):
        if packet is None:
            return []
        from ..inference.skeletons import keypoints_payload

        payload = []
        for slot in packet.crops:
            meta = {
                "crop_id": slot.crop_id, "offset_x": slot.offset_x, "offset_y": slot.offset_y,
                "crop_rect": list(slot.crop_rect) if slot.crop_rect else None,
                "result_layers": sorted(slot.results),
                "user_result": dict(slot.user_result), "edited": slot.edited,
                "media_id": media_id, "source_frame": frame_index,
            }
            if slot.keypoints:
                meta["keypoints"] = keypoints_payload(slot.keypoints, slot.keypoint_format)
            payload.append({"image": slot.crop_image, "meta": meta})
        return payload

    def export_selected_review_results(self):
        selected = self._selected_export_states()
        if not selected:
            QMessageBox.information(self, "导出保存集", "保存集为空。请在需要保留的帧上点击“加入保存集”。")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择保存集复核产物目录")
        if not output_dir:
            return
        exporter = ExportService(output_dir, str(self.export_template_combo.currentData()))
        succeeded = failed = skipped = 0
        failures = []
        for (media_id, frame_index), state in selected:
            packet = state.packet
            image = self._state_image(state)
            if image is None:
                skipped += 1
                continue
            result = exporter.export_frame(
                frame_tag=build_frame_tag(media_id, frame_index,
                    run_id=getattr(packet, "run_id", ""), revision="selected"),
                frame_image=image,
                rendered=packet.meta.get("rendered", image),
                crops=self._review_crops(packet, media_id, frame_index),
            )
            succeeded += result.succeeded
            failed += result.failed
            skipped += result.skipped
            failures.extend(result.failures)
        self.save_label.setText(f"保存状态：保存集已导出 {len(selected)} 帧")
        message = f"保存集 {len(selected)} 帧：成功 {succeeded}，失败 {failed}，跳过 {skipped}。\n{output_dir}"
        if failures:
            message += "\n\n" + "\n".join(f"{path}: {error}" for path, error in failures[:8])
            QMessageBox.warning(self, "保存集导出未完全成功", message)
        else:
            QMessageBox.information(self, "保存集导出完成", message)

    def export_selected_training_data(self):
        selected = self._selected_export_states()
        if not selected:
            QMessageBox.information(self, "导出保存集", "保存集为空。修改漏检/错检后会自动加入，也可手动点击“加入保存集”。")
            return
        full_seen, crop_seen = {}, {}
        names = {key: name for key, name, _classes in self._full_model_layers()}
        for _key, state in selected:
            for key in self._effective_full_results(state):
                full_seen[key] = names.get(key, key)
            for slot in state.packet.crops:
                for key in slot.results:
                    crop_seen[key] = slot.model_names.get(key, key)
        from .training_export_dialog import TrainingExportDialog
        dialog = TrainingExportDialog(list(full_seen.items()), list(crop_seen.items()), self,
            title=f"导出保存集训练数据（{len(selected)} 帧）",
            intro="只导出保存集中的当前有效标注；普通浏览帧不会进入训练数据。")
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values['output']:
            QMessageBox.warning(self, "训练数据导出", "请选择输出目录。")
            return
        if not values['images'] and not values['labels']:
            QMessageBox.warning(self, "训练数据导出", "请至少选择保存图片或标注。")
            return
        classes = {key: values for key, _name, values in self._full_model_layers()}
        from ..export.training import export_training_frame
        exported, failures = 0, []
        for (media_id, frame_index), state in selected:
            packet = state.packet
            image = self._state_image(state)
            if image is None:
                failures.append(f"{media_id} #{frame_index}: 图像不可用")
                continue
            try:
                export_training_frame(values['output'], build_frame_tag(media_id, frame_index,
                    run_id=packet.run_id, revision='selected'), image,
                    self._effective_full_results(state), classes, packet.crops,
                    values['full'], values['crop'], bool(values['full']), bool(values['crop']),
                    values['images'], values['labels'])
                exported += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{media_id} #{frame_index}: {exc}")
        self.save_label.setText(f"保存状态：保存集训练数据已导出 {exported}/{len(selected)} 帧")
        message = f"保存集共 {len(selected)} 帧，成功导出 {exported} 帧。\n{values['output']}"
        if failures:
            QMessageBox.warning(self, "保存集训练数据未完全导出", message + "\n\n" + "\n".join(failures[:8]))
        else:
            QMessageBox.information(self, "保存集训练数据导出完成", message)

    def _effective_full_results(self, state):
        packet = state.packet
        results = copy.deepcopy(state.prediction_results or (packet.full_results if packet else {}))
        if state.manual_persons is None and state.manual_behaviors is None:
            return results
        current = list(state.manual_persons or []) + list(state.manual_behaviors or [])
        layers = self._full_model_layers()
        for key, _name, _classes in layers:
            previous = results.get(key)
            task = previous.task_type if previous else TaskType.DETECTION
            results[key] = ModelResult(task_type=task, detections=[])
        for det in current:
            key = det.model_key
            if not key:
                matches = [layer for layer, _name, classes in layers if det.label in classes]
                key = matches[0] if len(matches) == 1 else ''
            if key in results:
                results[key].detections.append(copy.deepcopy(det))
        return results

    def export_training_data(self):
        state = self._state()
        packet = state.packet
        if packet is None:
            QMessageBox.information(self, '训练数据导出', '当前帧尚无模型或裁剪结果，请先运行当前帧。')
            return
        full = self._effective_full_results(state)
        names = {key: name for key, name, _classes in self._full_model_layers()}
        full_models = [(key, names.get(key, key)) for key in full]
        crop_seen = {}
        for slot in packet.crops:
            for key in slot.results:
                crop_seen[key] = slot.model_names.get(key, key)
        from .training_export_dialog import TrainingExportDialog
        dialog = TrainingExportDialog(full_models, list(crop_seen.items()), self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values['output']:
            QMessageBox.warning(self, '训练数据导出', '请选择输出目录。')
            return
        if not values['images'] and not values['labels']:
            QMessageBox.warning(self, '训练数据导出', '请至少选择保存图片或标注。')
            return
        image = state.image if state.image is not None else self._read_frame_image()
        classes = {key: model_classes for key, _name, model_classes in self._full_model_layers()}
        from ..export.training import export_training_frame
        try:
            manifest = export_training_frame(
                values['output'], build_frame_tag(self._media_identity(), self.source_frame,
                run_id=packet.run_id, revision='current'), image, full, classes, packet.crops,
                values['full'], values['crop'], bool(values['full']), bool(values['crop']),
                values['images'], values['labels'])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, '训练数据导出失败', str(exc))
            return
        self.save_label.setText('保存状态：当前帧训练数据已导出')
        QMessageBox.information(self, '训练数据导出完成',
                                f"全图模型 {len(manifest['full_models'])} 个，裁剪 {len(manifest['crops'])} 张。\n{values['output']}")

    def export_current_frame(self):
        state = self._state()
        if state.packet is None and state.manual_persons is None:
            QMessageBox.information(self, "提示", "当前帧还没有可导出的结果，请先运行工作流。")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not output_dir:
            return
        template = str(self.export_template_combo.currentData())
        image = state.image if state.image is not None else self._read_frame_image()
        if image is None:
            QMessageBox.warning(self, "导出失败", "当前帧图像不可用。")
            return
        packet = state.packet
        crops = []
        if packet is not None:
            crops = [
                {
                    "image": slot.crop_image,
                    "meta": {
                        "crop_id": slot.crop_id,
                        "offset_x": slot.offset_x,
                        "offset_y": slot.offset_y,
                        "crop_rect": list(slot.crop_rect) if slot.crop_rect else None,
                        "result_layers": sorted(slot.results),
                        "user_result": dict(slot.user_result),
                        "edited": slot.edited,
                        "media_id": self._media_identity(),
                        "source_frame": self.source_frame,
                    },
                }
                for slot in packet.crops
            ]
        exporter = ExportService(output_dir, template)
        result = exporter.export_frame(
            frame_tag=build_frame_tag(
                self._media_identity(),
                self.source_frame,
                run_id=getattr(packet, "run_id", "") if packet is not None else "",
                revision="manual" if state.edited else "",
            ),
            frame_image=image,
            rendered=packet.meta.get("rendered", image) if packet is not None else image,
            crops=crops,
        )
        if result.failed:
            detail = "\n".join(f"{path}\n  {error}" for path, error in result.failures[:10])
            QMessageBox.warning(
                self,
                "导出未完全成功",
                f"成功 {result.succeeded}，失败 {result.failed}，跳过 {result.skipped}。\n\n{detail}",
            )
        else:
            self._last_saved_at = _now_text()
            self.save_label.setText(f"保存状态：已导出 {result.succeeded} 个文件（{self._last_saved_at}）")
            QMessageBox.information(self, "导出完成", f"已导出 {result.succeeded} 个文件。\n{output_dir}")

    # =============================== 状态 =============================== #
    def _update_identity(self):
        if not self.current_key:
            self.frame_label.setText("—")
            self.identity_label.setText("帧身份：—")
            return
        entry = self._entry_by_key(self.current_key)
        name = self._entry_display(entry) if entry is not None else self.current_key
        kind = self._entry_kind(entry) if entry is not None else "?"
        if kind == "video" and self._video is not None:
            position = self.sample_indices.index(self.source_frame) + 1 if self.source_frame in self.sample_indices else 0
            self.frame_label.setText(
                f"{name} · 原始帧 {self.source_frame + 1}/{self._video.frame_count}"
                + (f" · 采样点 {position}/{len(self.sample_indices)}" if position else "")
            )
        else:
            self.frame_label.setText(str(name))
        self.identity_label.setText(
            f"帧身份：media={self._media_identity()[:12]}… frame={self.source_frame}"
        )

    def _refresh_queue_status(self):
        """只刷新当前媒体状态；编辑一帧不能重新扫描含数千项的项目。"""
        if self._closing:
            return
        if self.context is not None and getattr(self.context, "closed", False):
            return
        self._update_identity()
        if not self.media_entries or not self.current_key:
            return
        domain_entry = self._entry_by_key(self.current_key)
        queue_entry = next(
            (entry for entry in self.queue.entries() if entry.key == self.current_key), None
        )
        if domain_entry is None or queue_entry is None:
            return
        members = self._group_members(domain_entry) if self.context is not None else []
        reviewed, total, modified, disagreement = self._review_summary(
            queue_entry.media_id, members
        )
        self.queue.update_entry_status(
            self.current_key,
            reviewed_count=reviewed,
            total_frames=total,
            modified=modified,
            disagreement=disagreement,
        )

    def _set_status(self, text: str):
        self.status_changed.emit(text)
        self.run_label.setText(f"运行状态：{text}" if text else "运行状态：空闲")


def _fallback_save_service(repository):
    from ..services.save import SaveService

    return SaveService(repository)


def _merge_ranges(ranges: list[tuple[int, int, str]], window: int) -> list[tuple[int, int, str]]:
    if not ranges:
        return []
    ordered = sorted((start, end) for start, end, _label in ranges)
    merged: list[list[int]] = []
    for start, end in ordered:
        if merged and start - merged[-1][1] <= window:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end, "hit") for start, end in merged]


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%H:%M:%S")


def _run_workflow(
    frame: Frame,
    workflow_config,
    model_library,
    media_identity: str,
    run_id: str,
    inherited: dict,
    base_packet: Packet | None = None,
    cache: dict | None = None,
):
    """后台线程：跑一次工作流。继承的人工结果作为输入，不重跑全图模型。

    `base_packet` 用于“仅重算下游”：沿用已有 Packet，避免丢掉裁剪槽与人工编辑。
    `cache` 为节点级缓存：输入指纹没变时复用上一轮输出（评审第 8 节）。
    """
    packet = base_packet or Packet(
        frame=frame,
        media_identity=media_identity,
        run_id=run_id,
        meta={},
    )
    packet.frame = frame
    packet.media_identity = media_identity
    packet.run_id = run_id
    packet.meta["models"] = {model.name: model for model in model_library}
    packet.meta["skip_full_inference"] = bool(inherited.get("skip_full_inference"))
    for key, value in inherited.items():
        if key == "skip_full_inference":
            continue
        packet.full_results[key] = value
    runner = TracedWorkflowRunner(workflow_config, cache if cache is not None else {})
    packet, trace = runner.run(packet)
    if trace.status == '失败':
        raise RuntimeError(trace.summary() + '\n' + '\n'.join(report.error for report in trace.reports if report.error))
    packet.meta["trace"] = trace
    return packet


def _precompute_for_review(items, fps, workflow_config, model_library,
                           progress: PrecomputeProgress, cancel_flag):
    """逐素材预推理并流式写轻量缓存；返回的仅是索引，不把图片堆进内存。"""
    from ..inference.pipeline import VideoSource
    from ..media import read_image
    from ..services.precompute_cache import save_packet

    plans = []
    failures = []
    progress.phase = "统计采样帧"
    for entry_key, path, kind, media_id in items:
        if cancel_flag():
            return {'cancelled': True, 'done': progress.done, 'keys': []}
        progress.current = path
        try:
            if kind == 'video':
                source = VideoSource(path)
                try:
                    indices = source.sample_indices(fps)
                finally:
                    source.close()
            else:
                indices = [0]
        except Exception as exc:  # 单个损坏素材不能中断整个文件夹
            failures.append(f"{Path(path).name}：无法读取（{exc}）")
            progress.failed += 1
            progress.media_done += 1
            continue
        plans.append((entry_key, path, kind, media_id, indices))
        progress.total += len(indices)

    workflow = Workflow.from_config(workflow_config)
    models = {model.name: model for model in model_library}
    run_id = uuid.uuid4().hex[:12]
    keys = []
    progress.phase = "运行工作流"
    for entry_key, path, kind, media_id, indices in plans:
        if cancel_flag():
            break
        source = VideoSource(path) if kind == 'video' else None
        try:
            for frame_index in indices:
                if cancel_flag():
                    break
                progress.current = f"{Path(path).name} · 帧 {frame_index}"
                try:
                    frame = source.read(frame_index) if source is not None else Frame(
                        index=0, timestamp=0.0, image=read_image(path))
                    if frame is None or frame.image is None:
                        raise RuntimeError("未读取到图像")
                    packet = workflow.run(Packet(frame=frame, media_identity=media_id, run_id=run_id,
                                                 meta={'models': models}))
                    save_packet(media_id, frame.index, workflow_config.workflow_id, packet)
                    keys.append((media_id, int(frame.index), entry_key))
                except Exception as exc:  # 失败隔离到采样帧，保留整批其余结果
                    failures.append(f"{Path(path).name} · 帧 {frame_index}：{exc}")
                    progress.failed += 1
                finally:
                    progress.done += 1
        finally:
            if source is not None:
                source.close()
        progress.media_done += 1
    return {'cancelled': bool(cancel_flag()), 'done': progress.done, 'keys': keys,
            'failures': failures,
            'workflow_id': workflow_config.workflow_id, 'run_id': run_id}
