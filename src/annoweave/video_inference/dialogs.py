from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..inference.config import (
    ROLE_FULL_FRAME,
    ROLES,
    SAVE_TEMPLATES,
    ModelConfig,
    PipelineConfig,
    SaveConfig,
)

KNOWN_TYPES = ("yolov8", "yolov5", "yolov8_pose", "dwpose", "yolov8_cls", "yolov5_cls")
ROLE_LABELS = {ROLE_FULL_FRAME: "全图模型", "downstream": "下游模型"}


def _task_of(type_name: str) -> str:
    value = (type_name or "").lower()
    if value.endswith("_cls"):
        return "分类"
    if value in ("yolov8_pose", "yolov5_pose", "dwpose", "rtmpose"):
        return "姿态"
    return "检测"


TYPE_TASK = {key: _task_of(key) for key in KNOWN_TYPES}


class ModelConfigDialog(QDialog):
    def __init__(self, parent=None, model: Optional[ModelConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑模型" if model else "新增模型")
        self._model = model
        self.setMinimumWidth(620)
        form = QFormLayout(self)
        hint = QLabel("选择权重 → 确认算法与输入尺寸 → 按训练时的顺序填写类别。\n保存后，在模型库点击“试跑选中模型”验证。")
        hint.setWordWrap(True)
        form.addRow(hint)

        self.name_edit = QLineEdit(model.name if model else "")
        self.role_combo = QComboBox()
        for role in ROLES:
            self.role_combo.addItem(ROLE_LABELS[role], role)
        self.type_combo = QComboBox()
        for type_name in KNOWN_TYPES:
            self.type_combo.addItem(f"{type_name}（{TYPE_TASK[type_name]}）", type_name)
        if model and model.model_type not in KNOWN_TYPES:
            self.type_combo.addItem(model.model_type, model.model_type)
        self.path_edit = QLineEdit(model.model_path if model else "")
        browse = QPushButton("浏览")
        browse.clicked.connect(self._browse_model)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse)
        self.device_combo = QComboBox()
        self.device_combo.addItem("CPU（兼容优先）", "cpu")
        self.device_combo.addItem("CUDA（需要 GPU 后端）", "cuda")
        if model and model.device not in ('cpu', 'cuda'):
            self.device_combo.addItem(model.device, model.device)
        self.width_spin = QSpinBox()
        self.height_spin = QSpinBox()
        self.width_spin.setRange(1, 4096)
        self.height_spin.setRange(1, 4096)
        self.width_spin.setValue(640)
        self.height_spin.setValue(640)
        self.letterbox_check = QCheckBox("保持长宽比并填充（letterbox）")
        self.letterbox_check.setChecked(True)
        self.score_spin = QDoubleSpinBox()
        self.score_spin.setRange(0, 1)
        self.score_spin.setSingleStep(0.05)
        self.score_spin.setValue(0.35)
        self.iou_spin = QDoubleSpinBox()
        self.iou_spin.setRange(0, 1)
        self.iou_spin.setSingleStep(0.05)
        self.iou_spin.setValue(0.45)
        self.max_det_spin = QSpinBox()
        self.max_det_spin.setRange(1, 10000)
        self.max_det_spin.setValue(300)
        self.classes_edit = QLineEdit()
        self.classes_edit.setPlaceholderText("逗号分隔，如 person, car")
        self.output_format_combo = QComboBox()
        # 姿态模型专用：关键点数量与排列
        self.keypoint_count_spin = QSpinBox()
        self.keypoint_count_spin.setRange(0, 256)
        self.keypoint_count_spin.setSpecialValueText("自动（按输出列数推断）")
        self.keypoint_count_spin.setValue(0)
        self.keypoint_format_combo = QComboBox()
        for label, value in (("自动", ""), ("COCO-17（人体）", "coco17"), ("OpenPose-18", "openpose18")):
            self.keypoint_format_combo.addItem(label, value)

        form.addRow("名称", self.name_edit)
        form.addRow("角色", self.role_combo)
        form.addRow("类型", self.type_combo)
        form.addRow("模型路径", path_row)
        form.addRow("类别表（类别 0 开始）", self.classes_edit)
        advanced_toggle = QPushButton("高级参数（展开核对输入尺寸、设备与阈值）")
        advanced_toggle.setCheckable(True)
        form.addRow(advanced_toggle)
        advanced_widget = QWidget()
        advanced = QFormLayout(advanced_widget)
        advanced_widget.setVisible(False)
        advanced_toggle.toggled.connect(advanced_widget.setVisible)
        form.addRow(advanced_widget)
        advanced.addRow("运行设备", self.device_combo)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("宽"))
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("高"))
        size_row.addWidget(self.height_spin)
        advanced.addRow("输入尺寸", size_row)
        advanced.addRow("", self.letterbox_check)
        advanced.addRow("置信度阈值", self.score_spin)
        advanced.addRow("NMS 阈值", self.iou_spin)
        advanced.addRow("最大目标数", self.max_det_spin)
        advanced.addRow("输出格式", self.output_format_combo)
        self.keypoint_count_row = self.keypoint_count_spin
        advanced.addRow("关键点数量", self.keypoint_count_spin)
        advanced.addRow("关键点排列", self.keypoint_format_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存模型")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.type_combo.currentIndexChanged.connect(self._sync_type_fields)
        if model:
            self.name_edit.setText(model.name)
            self._set_combo(self.role_combo, model.role)
            self._set_combo(self.type_combo, model.model_type)
            self.path_edit.setText(model.model_path)
            self._set_combo(self.device_combo, model.device)
            self.width_spin.setValue(model.input_size[0])
            self.height_spin.setValue(model.input_size[1])
            self.letterbox_check.setChecked(model.letterbox)
            self.score_spin.setValue(model.score_threshold)
            self.iou_spin.setValue(model.iou_threshold)
            self.max_det_spin.setValue(model.max_det)
            self.classes_edit.setText(", ".join(model.classes))
            self.keypoint_count_spin.setValue(int(getattr(model, "num_keypoints", 0) or 0))
            self._set_combo(self.keypoint_format_combo, getattr(model, "keypoint_format", "") or "")
        self._sync_type_fields()

    @staticmethod
    def _set_combo(combo: QComboBox, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _browse_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 ONNX 模型", "", "ONNX (*.onnx)")
        if path:
            self.path_edit.setText(path)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(path).stem)

    def _sync_type_fields(self):
        type_name = str(self.type_combo.currentData())
        is_detection = not type_name.endswith('_cls')
        is_pose = type_name in ("yolov8_pose", "yolov5_pose", "dwpose", "rtmpose")
        self.iou_spin.setEnabled(is_detection)
        self.max_det_spin.setEnabled(is_detection)
        if is_pose:
            # 姿态模型默认 COCO-17；dwpose 这类自顶向下模型输入的是"人框 + 图"。
            default_format = "coco17"
            if not self.keypoint_format_combo.currentData():
                index = self.keypoint_format_combo.findData(default_format)
                if index >= 0:
                    self.keypoint_format_combo.setCurrentIndex(index)
        self.output_format_combo.clear()
        if is_pose:
            self.output_format_combo.addItem("YOLO (cxcywh+conf+kpt)", "yolo")
            self.output_format_combo.addItem("xyxy+conf+kpt", "xyxy")
        elif is_detection:
            self.output_format_combo.addItem("YOLO (cxcywh+conf+cls)", "yolo")
            self.output_format_combo.addItem("xyxy+cls", "xyxy")
        else:
            self.output_format_combo.addItem("logits（softmax 后取最大）", "logits")
            self.output_format_combo.addItem("softmax/概率", "softmax")
        if self._model:
            self._set_combo(self.output_format_combo, self._model.output_format)

    def _validate(self):
        name = self.name_edit.text().strip()
        path = self.path_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "模型配置", "名称不能为空")
            return
        if not path:
            QMessageBox.warning(self, "模型配置", "模型路径不能为空")
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "模型配置", "权重文件不存在，请选择本机的 ONNX 文件。")
            return
        classes = [value.strip() for value in self.classes_edit.text().split(",") if value.strip()]
        if not classes:
            QMessageBox.warning(self, "模型配置", "类别表不能为空")
            return
        self.accept()

    def values(self) -> ModelConfig:
        return ModelConfig(
            name=self.name_edit.text().strip(),
            role=str(self.role_combo.currentData()),
            model_type=str(self.type_combo.currentData()),
            model_path=self.path_edit.text().strip(),
            device=str(self.device_combo.currentData()),
            input_size=(self.width_spin.value(), self.height_spin.value()),
            letterbox=self.letterbox_check.isChecked(),
            score_threshold=self.score_spin.value(),
            iou_threshold=self.iou_spin.value(),
            max_det=self.max_det_spin.value(),
            classes=[value.strip() for value in self.classes_edit.text().split(",") if value.strip()],
            output_format=str(self.output_format_combo.currentData()),
            num_keypoints=int(self.keypoint_count_spin.value()),
            keypoint_format=str(self.keypoint_format_combo.currentData() or ""),
            top_down=str(self.type_combo.currentData()) in ("dwpose", "rtmpose"),
        )


class PipelineConfigDialog(QDialog):
    def __init__(self, config: PipelineConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("推理流水线配置")
        self.resize(640, 520)
        self.config = config
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(0.01, 240.0)
        self.fps_spin.setValue(config.sampling_fps)
        form.addRow("采样帧率（FPS）", self.fps_spin)
        layout.addLayout(form)

        layout.addWidget(QLabel("模型库"))
        self.model_table = QTableWidget(0, 3)
        self.model_table.setHorizontalHeaderLabels(["名称", "类型", "角色"])
        self.model_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.model_table)
        model_buttons = QHBoxLayout()
        for text, handler in (
            ("新增", self._add_model),
            ("编辑", self._edit_model),
            ("删除", self._remove_model),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            model_buttons.addWidget(button)
        layout.addLayout(model_buttons)

        form2 = QFormLayout()
        self.expand_px_spin = QSpinBox()
        self.expand_px_spin.setRange(0, 1000)
        self.expand_px_spin.setValue(config.crop.expand_px)
        self.expand_ratio_spin = QDoubleSpinBox()
        self.expand_ratio_spin.setRange(0, 2.0)
        self.expand_ratio_spin.setSingleStep(0.05)
        self.expand_ratio_spin.setValue(config.crop.expand_ratio)
        self.min_size_spin = QSpinBox()
        self.min_size_spin.setRange(0, 10000)
        self.min_size_spin.setValue(config.crop.min_size)
        form2.addRow("扩边像素", self.expand_px_spin)
        form2.addRow("扩边比例", self.expand_ratio_spin)
        form2.addRow("最小尺寸", self.min_size_spin)

        self.crop_classes_box = QVBoxLayout()
        form2.addRow("裁剪类别", self.crop_classes_widget())
        layout.addLayout(form2)

        form3 = QFormLayout()
        self.save_template_combo = QComboBox()
        for key, label in SAVE_TEMPLATES.items():
            self.save_template_combo.addItem(label, key)
        index = self.save_template_combo.findData(config.save.template)
        if index >= 0:
            self.save_template_combo.setCurrentIndex(index)
        self.output_dir_edit = QLineEdit(config.save.output_dir)
        browse = QPushButton("浏览")
        browse.clicked.connect(self._browse_output)
        out_row = QHBoxLayout()
        out_row.addWidget(self.output_dir_edit)
        out_row.addWidget(browse)
        form3.addRow("保存模板", self.save_template_combo)
        form3.addRow("输出目录", out_row)
        layout.addLayout(form3)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._reload_models()
        self._refresh_crop_classes()

    def crop_classes_widget(self) -> QWidget:
        from PySide6.QtWidgets import QWidget

        widget = QWidget()
        widget.setLayout(self.crop_classes_box)
        return widget

    def _reload_models(self):
        self.model_table.setRowCount(0)
        for model in self.config.models:
            row = self.model_table.rowCount()
            self.model_table.insertRow(row)
            self.model_table.setItem(row, 0, QTableWidgetItem(model.name))
            self.model_table.setItem(row, 1, QTableWidgetItem(TYPE_TASK.get(model.model_type, model.model_type)))
            self.model_table.setItem(row, 2, QTableWidgetItem(ROLE_LABELS.get(model.role, model.role)))

    def _selected_model(self) -> Optional[ModelConfig]:
        row = self.model_table.currentRow()
        if row < 0:
            return None
        return self.config.models[row]

    def _add_model(self):
        dialog = ModelConfigDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config.models.append(dialog.values())
            self._reload_models()
            self._refresh_crop_classes()

    def _edit_model(self):
        model = self._selected_model()
        if not model:
            return
        dialog = ModelConfigDialog(self, model)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config.models[self.model_table.currentRow()] = dialog.values()
            self._reload_models()
            self._refresh_crop_classes()

    def _remove_model(self):
        model = self._selected_model()
        if not model:
            return
        self.config.models.remove(model)
        self._reload_models()
        self._refresh_crop_classes()

    def _refresh_crop_classes(self):
        # 清空旧的类别勾选框
        while self.crop_classes_box.count():
            item = self.crop_classes_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        full_frame = self.config.full_frame_models
        classes = sorted({name for model in full_frame for name in model.classes})
        for name in classes:
            check = QCheckBox(name)
            check.setChecked(name in self.config.crop.classes)
            check.stateChanged.connect(lambda _state, n=name: self._toggle_crop_class(n, check.isChecked()))
            self.crop_classes_box.addWidget(check)

    def _toggle_crop_class(self, name: str, checked: bool):
        classes = set(self.config.crop.classes)
        if checked:
            classes.add(name)
        else:
            classes.discard(name)
        self.config.crop.classes = sorted(classes)

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.output_dir_edit.setText(path)

    def _validate(self):
        if not self.config.full_frame_models:
            QMessageBox.warning(self, "流水线配置", "至少需要配置一个全图模型")
            return
        self.accept()

    def values(self) -> PipelineConfig:
        self.config.sampling_fps = self.fps_spin.value()
        self.config.crop.expand_px = self.expand_px_spin.value()
        self.config.crop.expand_ratio = self.expand_ratio_spin.value()
        self.config.crop.min_size = self.min_size_spin.value()
        self.config.save = SaveConfig(
            template=str(self.save_template_combo.currentData()),
            output_dir=self.output_dir_edit.text().strip(),
        )
        return self.config
