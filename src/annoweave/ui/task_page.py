"""任务页（M9）：批处理任务的状态机、进度、取消与运行快照。

评审第 9 节要求：
- 状态：排队 / 预检 / 运行 / 取消请求 / 已取消 / 成功 / 部分失败 / 失败；
- 显示媒体数、帧数、节点、耗时、速度与预计剩余；
- 运行计划包含**不可变**的工作流与模型配置快照（落库到 workflow_snapshot / run）；
- 取消在安全边界结束，已有结果保持完整；
- 结果流式追加，不把所有帧结果堆在内存里。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..export.service import ExportService
from ..inference.config import SAVE_TEMPLATES, load_model_library
from ..media import MediaSet
from ..workers import FunctionWorker
from ..workflow.packet import Packet
from ..workflow.templates import starter_workflow_config
from ..workflow.workflow import Workflow

#: 任务状态机
QUEUED = "排队"
PRECHECK = "预检"
RUNNING = "运行中"
CANCEL_REQUESTED = "正在取消"
CANCELLED = "已取消"
SUCCESS = "成功"
PARTIAL_FAIL = "部分失败"
FAILED = "失败"

TERMINAL_STATES = {CANCELLED, SUCCESS, PARTIAL_FAIL, FAILED}


@dataclass
class Progress:
    """后台线程写入、UI 线程读取的进度快照（只做简单赋值，避免锁竞争）。"""

    state: str = QUEUED
    media_total: int = 0
    media_done: int = 0
    frames_total: int = 0
    frames_done: int = 0
    saved: int = 0
    failed: int = 0
    skipped: int = 0
    nodes: int = 0
    started_at: float = 0.0
    message: str = ""
    snapshot_id: str = ""
    failures: list = field(default_factory=list)
    rows: list = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def speed(self) -> float:
        elapsed = self.elapsed
        if elapsed <= 0:
            return 0.0
        return self.frames_done / elapsed

    @property
    def eta(self) -> float:
        speed = self.speed
        if speed <= 0 or self.frames_total <= 0:
            return 0.0
        return max(0.0, (self.frames_total - self.frames_done) / speed)


class TaskPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread_pool = QThreadPool.globalInstance()
        self.context = None
        self.media = MediaSet()
        self.progress = Progress()
        self._cancel = False
        #: 正在进行的运行标识；迟到的完成信号必须被忽略，避免重复弹窗/重复渲染
        self._run_token = ""
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.source_label = QLabel("未选择来源")
        self.source_label.setProperty("role", "muted")
        pick_button = QPushButton("选择文件")
        pick_button.clicked.connect(self._pick_files)
        pick_folder_button = QPushButton("选择文件夹")
        pick_folder_button.clicked.connect(self._pick_folder)
        self.use_project_button = QPushButton("使用当前项目素材")
        self.use_project_button.setToolTip("直接使用“复核”页已打开项目的素材列表")
        self.use_project_button.clicked.connect(self._use_project_media)
        row.addWidget(pick_button)
        row.addWidget(pick_folder_button)
        row.addWidget(self.use_project_button)
        row.addWidget(self.source_label, 1)
        layout.addLayout(row)

        workflow_row = QHBoxLayout()
        workflow_row.addWidget(QLabel("工作流"))
        self.workflow_combo = QComboBox()
        self._workflows = []
        self.reload_workflows()
        self.workflow_combo.setToolTip("本次批处理固定使用这里选择的工作流快照")
        workflow_row.addWidget(self.workflow_combo, 1)
        layout.addLayout(workflow_row)
        scope_note = QLabel("批处理会按采样率对全部素材运行所选工作流，并把全图/裁剪产物和运行清单写入输出目录。")
        scope_note.setProperty("role", "muted")
        scope_note.setWordWrap(True)
        layout.addWidget(scope_note)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("采样FPS"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(1)
        row2.addWidget(self.fps_spin)
        row2.addWidget(QLabel("保存模板"))
        self.template_combo = QComboBox()
        for key, label in SAVE_TEMPLATES.items():
            self.template_combo.addItem(label, key)
        row2.addWidget(self.template_combo)
        row2.addWidget(QLabel("输出目录"))
        self.output_edit = QLineEdit()
        browse = QPushButton("浏览")
        browse.clicked.connect(self._browse_output)
        row2.addWidget(self.output_edit, 1)
        row2.addWidget(browse)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self.start_button = QPushButton("开始批量推理并导出")
        self.start_button.setProperty("accent", "true")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.request_cancel)
        self.cancel_button.setEnabled(False)
        row3.addWidget(self.start_button)
        row3.addWidget(self.cancel_button)
        row3.addStretch()
        layout.addLayout(row3)

        self.state_label = QLabel(QUEUED)
        self.state_label.setProperty("role", "title")
        layout.addWidget(self.state_label)
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        self.metrics_label = QLabel("媒体 0/0 · 帧 0/0 · 节点 0 · 耗时 0.0s · 速度 0.0 帧/秒 · 预计剩余 —")
        self.metrics_label.setProperty("role", "muted")
        layout.addWidget(self.metrics_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["媒体", "帧", "裁剪", "状态", "失败原因"])
        layout.addWidget(self.table, 1)
        self.status = QLabel("—")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # ----------------------------- 上下文 ----------------------------- #
    def set_context(self, context):
        self.context = context
        self._refresh_source_label()

    def reload_workflows(self):
        """刷新批处理可选流程，并尽量保留用户当前选择。"""
        from ..workflow.workflow import load_workflow_catalog
        selected_id = ''
        current = self.workflow_combo.currentIndex()
        if 0 <= current < len(self._workflows):
            selected_id = self._workflows[current].workflow_id
        self._workflows = load_workflow_catalog() or [starter_workflow_config()]
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        selected_index = 0
        for index, workflow in enumerate(self._workflows):
            self.workflow_combo.addItem(workflow.name or f'工作流 {index + 1}', index)
            if workflow.workflow_id == selected_id:
                selected_index = index
        self.workflow_combo.setCurrentIndex(selected_index)
        self.workflow_combo.blockSignals(False)

    def prepare_from_review(self, paths: list[str], workflow_id: str, fps: int):
        """从复核页进入批处理时继承素材、工作流和采样率。"""
        if self.progress.state in (RUNNING, PRECHECK, CANCEL_REQUESTED):
            self.status.setText("当前批处理仍在运行，未替换它的素材和工作流。")
            return
        if paths:
            self.media = MediaSet.from_paths(paths)
        self.reload_workflows()
        selected = next((index for index, item in enumerate(self._workflows)
                         if item.workflow_id == workflow_id), -1)
        if selected >= 0:
            self.workflow_combo.setCurrentIndex(selected)
        self.fps_spin.setValue(min(max(int(fps), self.fps_spin.minimum()), self.fps_spin.maximum()))
        self._refresh_source_label()
        workflow_name = self.workflow_combo.currentText() or "未选择"
        self.status.setText(f"已从视觉复核继承 {len(self.media)} 个素材 · {workflow_name} · {self.fps_spin.value()} FPS")

    def _refresh_source_label(self):
        if self.media:
            self.source_label.setText(f"已选 {len(self.media)} 个媒体")
        elif self.context is not None and getattr(self.context, "media", None):
            self.source_label.setText(f"项目素材可用（{len(self.context.media)} 个），点“使用当前项目素材”")
        else:
            self.source_label.setText("未选择来源")

    # ----------------------------- 输入 ----------------------------- #
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择媒体", "",
            "媒体 (*.mp4 *.avi *.mov *.mkv *.webm *.jpg *.jpeg *.png *.bmp *.webp)",
        )
        if not files:
            return
        self.media = MediaSet.from_paths(files)
        self._refresh_source_label()

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹（含视频/图片）")
        if not folder:
            return
        self.media = MediaSet.from_folder(folder)
        self._refresh_source_label()

    def _use_project_media(self):
        if self.context is None or not getattr(self.context, "media", None):
            QMessageBox.information(self, "提示", "请先在“复核”页打开一个项目")
            return
        from ..media import MediaEntry

        entries = []
        for record in self.context.media.values():
            kind = record.kind or record.media_type
            entries.append(MediaEntry(record.absolute_path, kind, record.relative_path))
        self.media = MediaSet(entries=entries, source=self.context.source_root)
        self._refresh_source_label()

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.output_edit.setText(path)

    # ----------------------------- 运行 ----------------------------- #
    def start(self):
        if self.progress.state in (RUNNING, CANCEL_REQUESTED, PRECHECK):
            return
        if not self.media:
            QMessageBox.information(self, "提示", "请先选择来源")
            return
        output_dir = self.output_edit.text().strip()
        if not output_dir:
            output_dir = QFileDialog.getExistingDirectory(self, "选择输出目录")
            if not output_dir:
                return
            self.output_edit.setText(output_dir)

        workflow_config = self._active_workflow()
        models = {m.name: m for m in load_model_library()}
        precheck = self._precheck(workflow_config, models)
        if precheck:
            self.progress.state = PRECHECK
            self.state_label.setText(PRECHECK)
            detail = "\n".join(precheck)
            answer = QMessageBox.question(
                self, "运行前检查未通过", f"发现 {len(precheck)} 个问题：\n\n{detail}\n\n仍然运行吗？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.progress.state = QUEUED
                self.state_label.setText(QUEUED)
                return

        self._cancel = False
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.table.setRowCount(0)
        self.progress_bar.setValue(0)
        snapshot_id = self._record_snapshot(workflow_config, models)
        self.progress = Progress(
            state=RUNNING,
            media_total=len(self.media),
            frames_total=0,
            nodes=len(workflow_config.nodes),
            started_at=time.monotonic(),
        )
        self.progress.snapshot_id = snapshot_id
        self.state_label.setText(RUNNING)
        self._timer.start()

        self.progress.media_total = len(self.media)
        self._run_token = uuid.uuid4().hex
        run_token = self._run_token
        worker = FunctionWorker(
            _run_batch,
            [entry.path for entry in self.media.entries],
            output_dir,
            str(self.template_combo.currentData()),
            self.fps_spin.value(),
            workflow_config,
            models,
            self.progress,
            lambda: self._cancel,
        )
        worker.signals.finished.connect(lambda result: self._finished(result, run_token))
        worker.signals.failed.connect(lambda detail: self._failed(detail, run_token))
        # 必须持有引用：QRunnable 被回收会让完成信号永远送不到 UI
        self._worker = worker
        self.thread_pool.start(worker)

    def _active_workflow(self):
        index = int(self.workflow_combo.currentData() or 0)
        return self._workflows[index] if 0 <= index < len(self._workflows) else starter_workflow_config()

    def _precheck(self, workflow_config, models) -> list[str]:
        """运行前检查：节点参数、模型存在性和上下游数据依赖。"""
        from ..workflow.validation import validate_workflow

        return [
            (f"第 {issue.node_index} 个节点：" if issue.node_index else "") + issue.message
            for issue in validate_workflow(workflow_config, models)
            if issue.severity == "error"
        ]

    def _record_snapshot(self, workflow_config, models) -> str:
        """把不可变的执行计划落库，便于事后追溯“当时用的是哪份配置”。"""
        if self.context is None or getattr(self.context, "run_service", None) is None:
            return ""
        try:
            snapshot = self.context.run_service.snapshot(workflow_config, list(models.values()))
            return snapshot.snapshot_id
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"运行快照未落库：{exc}")
            return ""

    def request_cancel(self):
        if self.progress.state not in (RUNNING, PRECHECK):
            return
        self._cancel = True
        self.progress.state = CANCEL_REQUESTED
        self.state_label.setText(CANCEL_REQUESTED)
        self.status.setText("正在取消……已有结果会保留")
        self.cancel_button.setEnabled(False)

    # ----------------------------- 进度轮询 ----------------------------- #
    def _poll(self):
        progress = self.progress
        self._render_metrics(progress)
        if progress.state in TERMINAL_STATES:
            self._timer.stop()
            self._render_rows(progress.rows)

    def _render_metrics(self, progress: Progress):
        total = progress.frames_total or progress.frames_done
        if total:
            self.progress_bar.setValue(int(progress.frames_done / total * 100))
        eta = progress.eta
        elapsed = progress.elapsed
        self.metrics_label.setText(
            f"媒体 {progress.media_done}/{progress.media_total} · "
            f"帧 {progress.frames_done}/{total or '?'} · "
            f"节点 {progress.nodes} · "
            f"耗时 {'—' if elapsed <= 0 else f'{elapsed:.1f}s'} · "
            f"速度 {'—' if progress.speed <= 0 else f'{progress.speed:.1f} 帧/秒'} · "
            f"预计剩余 {'—' if eta <= 0 else f'{eta:.0f}s'} · "
            f"成功 {progress.saved} / 失败 {progress.failed} / 跳过 {progress.skipped}"
        )
        if progress.state in (RUNNING, CANCEL_REQUESTED):
            self.state_label.setText(progress.state)

    def _render_rows(self, rows):
        self.table.setRowCount(0)
        for row_data in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, key in enumerate(("media", "frame", "crops", "status", "failure")):
                item = QTableWidgetItem(str(row_data.get(key, "")))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

    # ----------------------------- 结束 ----------------------------- #
    def _finished(self, result, run_token: str | None = None):
        # run_token=None 表示调用方直接驱动（测试/内部调用）；传了 token 才做迟到校验
        if run_token is not None and run_token != self._run_token:
            return
        self._run_token = ""
        self._timer.stop()
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._timer.stop()
        cancelled = bool(result.get("cancelled"))
        failed = int(result.get("failed", 0))
        saved = int(result.get("saved", 0))
        if cancelled:
            state = CANCELLED
        elif failed and saved:
            state = PARTIAL_FAIL
        elif failed:
            state = FAILED
        else:
            state = SUCCESS
        self.progress.state = state
        # 把最终统计折回进度对象，否则指标栏会停留在运行中的旧值
        self.progress.saved = int(result.get("saved", self.progress.saved))
        self.progress.failed = int(result.get("failed", self.progress.failed))
        self.progress.skipped = int(result.get("skipped", self.progress.skipped))
        self.progress.media_done = int(result.get("media_done", self.progress.media_done))
        self.progress.frames_done = int(result.get("frames_done", self.progress.frames_done))
        self.state_label.setText(state)
        self._render_rows(result.get("rows", []))
        self._render_metrics(self.progress)
        summary = [
            f"{state}：处理 {result.get('media_done', 0)} 个媒体、"
            f"{result.get('frames_done', 0)} 帧。",
            f"成功 {saved}，失败 {failed}，跳过 {result.get('skipped', 0)}。",
        ]
        if result.get("manifest"):
            summary.append(f"清单：{result['manifest']}")
        if failed:
            summary.append("失败明细见表格“失败原因”列。")
        self.status.setText("\n".join(summary))
        if failed:
            QMessageBox.warning(self, f"批处理{state}", "\n".join(summary))
        else:
            QMessageBox.information(self, f"批处理{state}", "\n".join(summary))

    def _failed(self, detail: str, run_token: str | None = None):
        if run_token is not None and run_token != self._run_token:
            return
        self._run_token = ""
        self._timer.stop()
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.state = FAILED
        self.state_label.setText(FAILED)
        self.status.setText(detail)
        QMessageBox.critical(self, "批处理失败", detail)


def _run_batch(
    paths: list[str],
    output_dir: str,
    template: str,
    fps: int,
    workflow_config,
    models: dict,
    progress: Progress,
    cancel_flag,
) -> dict:
    """后台线程：按不可变快照运行，结果流式写入清单，不堆积在内存。"""
    import csv

    from ..inference.datatypes import Frame
    from ..inference.pipeline import VideoSource
    from ..media import MediaSet, read_image

    media = MediaSet.from_paths(paths)
    exporter = ExportService(output_dir, template)
    workflow = Workflow.from_config(workflow_config)
    batch_id = uuid.uuid4().hex[:12]
    manifest_dir = Path(output_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"run_manifest_{batch_id}.csv"
    progress.rows = []

    # 预扫帧数，便于显示进度与预计剩余时间
    frame_plan: list[tuple] = []
    for entry in media.entries:
        if entry.kind == "video":
            source = VideoSource(entry.path)
            indices = source.sample_indices(fps)
            source.close()
            frame_plan.append((entry, indices))
            progress.frames_total += len(indices)
        else:
            frame_plan.append((entry, [0]))
            progress.frames_total += 1

    with manifest_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["media", "frame", "crops", "saved", "failed_count", "failure"],
        )
        writer.writeheader()
        for entry, indices in frame_plan:
            if cancel_flag():
                break
            source = VideoSource(entry.path) if entry.kind == "video" else None
            try:
                for index in indices:
                    if cancel_flag():
                        break
                    if source is not None:
                        frame = source.read(index)
                    else:
                        frame = Frame(index=0, timestamp=0.0, image=read_image(entry.path))
                    if frame is None:
                        progress.frames_done += 1
                        continue
                    packet = workflow.run(
                        Packet(
                            frame=frame,
                            media_identity=entry.path,
                            run_id=batch_id,
                            meta={"models": models},
                        )
                    )
                    export = exporter.export_frame(
                        frame_tag=_frame_tag(entry.path, frame.index, batch_id),
                        frame_image=frame.image,
                        rendered=packet.meta.get("rendered", frame.image),
                        crops=[
                            {
                                "image": slot.crop_image,
                                "meta": {
                                    "result_layers": sorted(slot.results),
                                    "offset_x": slot.offset_x,
                                    "offset_y": slot.offset_y,
                                },
                            }
                            for slot in packet.crops
                        ],
                    )
                    progress.saved += export.succeeded
                    progress.failed += export.failed
                    progress.skipped += export.skipped
                    progress.frames_done += 1
                    failure = "；".join(error for _path, error in export.failures)
                    row = {
                        "media": entry.display,
                        "frame": frame.index,
                        "crops": len(packet.crops),
                        "status": f"成功{export.succeeded}/失败{export.failed}/跳过{export.skipped}",
                        "failure": failure,
                    }
                    progress.rows.append(row)
                    writer.writerow({
                        "media": entry.display,
                        "frame": frame.index,
                        "crops": len(packet.crops),
                        "saved": export.succeeded,
                        "failed_count": export.failed,
                        "failure": failure,
                    })
                    handle.flush()
            finally:
                if source is not None:
                    source.close()
            progress.media_done += 1

    cancelled = cancel_flag()
    progress.state = CANCELLED if cancelled else (
        PARTIAL_FAIL if progress.failed and progress.saved else FAILED if progress.failed else SUCCESS
    )
    return {
        "cancelled": cancelled,
        "rows": progress.rows,
        "saved": progress.saved,
        "failed": progress.failed,
        "skipped": progress.skipped,
        "media_done": progress.media_done,
        "frames_done": progress.frames_done,
        "manifest": str(manifest_path),
        "batch_id": batch_id,
        "summary": json.dumps({
            "batch_id": batch_id,
            "saved": progress.saved,
            "failed": progress.failed,
            "skipped": progress.skipped,
        }, ensure_ascii=False),
    }


def _frame_tag(media_identity: str, frame_index: int, batch_id: str) -> str:
    from ..export.service import frame_tag

    return frame_tag(media_identity, frame_index, run_id=batch_id)
