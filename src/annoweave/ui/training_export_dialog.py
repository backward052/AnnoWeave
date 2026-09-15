from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class TrainingExportDialog(QDialog):
    def __init__(self, full_models, crop_models, parent=None, title='导出当前帧训练数据', intro=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(intro or '导出干净图片及当前有效标注；可视化叠加图不会作为训练图片。'))
        self.full_checks = self._group(layout, '全图模型（YOLO 检测数据）', full_models)
        self.crop_checks = self._group(layout, '裁剪模型（检测或分类数据）', crop_models)
        self.save_images = QCheckBox('保存干净图片')
        self.save_images.setChecked(True)
        self.save_labels = QCheckBox('保存标注和类别表')
        self.save_labels.setChecked(True)
        layout.addWidget(self.save_images)
        layout.addWidget(self.save_labels)
        row = QHBoxLayout()
        row.addWidget(QLabel('输出目录'))
        self.output = QLineEdit()
        row.addWidget(self.output, 1)
        from PySide6.QtWidgets import QPushButton
        browse = QPushButton('浏览')
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('导出')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _group(self, layout, title, models):
        group = QGroupBox(title)
        inner = QVBoxLayout(group)
        checks = {}
        for key, name in models:
            check = QCheckBox(f'{name}（{key}）')
            check.setChecked(True)
            check.setProperty('model_key', key)
            inner.addWidget(check)
            checks[key] = check
        if not checks:
            inner.addWidget(QLabel('当前帧没有此类模型结果'))
        layout.addWidget(group)
        return checks

    def _browse(self):
        value = QFileDialog.getExistingDirectory(self, '选择训练数据目录')
        if value:
            self.output.setText(value)

    def values(self):
        return {'output': self.output.text().strip(),
                'full': {key for key, item in self.full_checks.items() if item.isChecked()},
                'crop': {key for key, item in self.crop_checks.items() if item.isChecked()},
                'images': self.save_images.isChecked(), 'labels': self.save_labels.isChecked()}
