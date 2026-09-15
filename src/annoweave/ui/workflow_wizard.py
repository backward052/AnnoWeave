"""创建工作流向导：先选场景，再填模型。

设计原则（新手友好）：
1. **场景优先**：第一页列的是"我要做什么"，不是"节点类型"。每个模板都有
   一句话说明、一段适用场景和一条中文链路。
2. **职责填坑**：第二页只问"这一步用哪个模型"，并按检测/分类/姿态做软过滤；
   不匹配的模型排在后面但依然可选，不会把人卡住。
3. **可见即所得**：第三页把最终链路和每个模型的来源列出来，创建后进节点编辑器还能改。
4. 创建出来的是**草稿**，点「保存工作流」才写入工作流目录。

兼容旧接口：`build_guided_workflow` / `build_association_workflow` 保留，
内部改走模板系统，历史调用方与测试不受影响。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..workflow.templates import (
    TEMPLATES,
    WorkflowTemplate,
    build_from_template,
    get_template,
    template_categories,
)
from ..workflow.workflow import WorkflowConfig

#: 模型的类型 → 粗任务分类，用于向导里的软过滤
_POSE_TYPES = ("yolov8_pose", "yolov5_pose", "dwpose", "rtmpose", "rtmwpose")


def task_of(model_type: str) -> str:
    value = (model_type or "").lower()
    if value.endswith("_cls"):
        return "cls"
    if value in _POSE_TYPES:
        return "pose"
    return "det"


# --------------------------------------------------------------------------- #
# 兼容旧接口
# --------------------------------------------------------------------------- #
def build_guided_workflow(name, selected_models, roles=None):
    """Build a generic multi-model workflow from selected model names."""
    models = list(selected_models or [])
    if roles:
        models.extend(value for value in roles.values() if value)
    return build_from_template("multi_model_full", name, extra_models=list(dict.fromkeys(models)))


def build_association_workflow(name, left_model, right_model, left_classes, right_classes,
                               downstream_model, metric='ioa_right', threshold=.4,
                               expand_ratio=.1, min_area=0):
    """兼容旧调用：等价于「双模型空间关联 → 裁剪 → 下游」模板。"""
    if not all((str(name).strip(), left_model, right_model, downstream_model)):
        raise ValueError('请完整选择两个全图模型和一个下游模型')
    if not left_classes or not right_classes:
        raise ValueError('请至少选择一个主体类别和一个候选类别')
    template = get_template("dual_model_associate")
    assert template is not None
    return build_from_template(
        "dual_model_associate",
        name,
        role_models={
            "subject_det": left_model,
            "candidate_det": right_model,
            "downstream": downstream_model,
        },
        option_values={
            "subject_classes": ",".join(left_classes),
            "candidate_classes": ",".join(right_classes),
            "metric": metric,
            "threshold": float(threshold),
            "crop_expand": float(expand_ratio),
            "crop_min_area": int(min_area),
            "assoc_expand": 0.0,
            "min_subject_area": 0,
        },
    )


# --------------------------------------------------------------------------- #
# 向导
# --------------------------------------------------------------------------- #
class WorkflowWizard(QWizard):
    def __init__(self, models, parent=None):
        super().__init__(parent)
        self.setWindowTitle('创建工作流')
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(760, 620)
        self.setButtonText(QWizard.WizardButton.NextButton, '下一步')
        self.setButtonText(QWizard.WizardButton.BackButton, '上一步')
        self.setButtonText(QWizard.WizardButton.FinishButton, '创建草稿')
        self.setButtonText(QWizard.WizardButton.CancelButton, '取消')
        self.models = list(models or [])
        self._role_widgets: dict[str, QComboBox] = {}
        self._option_widgets: dict[str, object] = {}
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#E05252;")

        self._build_page_scenario()
        self._build_page_models()
        self._build_page_summary()

    # --------------------------- 第一页：场景 --------------------------- #
    def _build_page_scenario(self):
        page = QWizardPage()
        page.setTitle('第 1 步 · 你要做什么？')
        page.setSubTitle('选一个场景模板。系统会把模块和顺序都接好；之后仍可在节点编辑器里改。')
        form = QFormLayout(page)
        self.name_edit = QLineEdit('我的工作流')
        #: 用户是否亲手改过名字；没改过就跟着所选模板走
        self._name_touched = False
        self.name_edit.textEdited.connect(lambda _text: setattr(self, '_name_touched', True))
        form.addRow('工作流名称', self.name_edit)

        self.category_combo = QComboBox()
        self.category_combo.addItem('全部场景', '')
        for name in template_categories():
            self.category_combo.addItem(name, name)
        self.category_combo.currentIndexChanged.connect(self._fill_templates)
        form.addRow('场景分组', self.category_combo)

        self.template_list = QListWidget()
        self.template_list.setMinimumHeight(230)
        self.template_list.setSpacing(3)
        self.template_list.currentItemChanged.connect(self._on_template_changed)
        form.addRow('场景模板', self.template_list)

        self.scenario_label = QLabel()
        self.scenario_label.setWordWrap(True)
        self.scenario_label.setProperty('role', 'muted')
        form.addRow('说明', self.scenario_label)

        self.pipeline_label = QLabel()
        self.pipeline_label.setWordWrap(True)
        form.addRow('处理链路', self.pipeline_label)

        self._fill_templates()
        self.addPage(page)

    def _fill_templates(self, *_args):
        category = str(self.category_combo.currentData() or '')
        self.template_list.clear()
        for template in TEMPLATES:
            if category and template.category != category:
                continue
            item = QListWidgetItem(f"{template.name}\n{template.summary}")
            item.setData(Qt.ItemDataRole.UserRole, template.template_id)
            item.setToolTip(f"{template.summary}\n\n适用：{template.scenario}")
            self.template_list.addItem(item)
        if self.template_list.count():
            self.template_list.setCurrentRow(self._preferred_row())

    def _preferred_row(self) -> int:
        for row in range(self.template_list.count()):
            if self.template_list.item(row).data(Qt.ItemDataRole.UserRole) == 'multi_model_full':
                return row
        return 0

    def current_template(self) -> Optional[WorkflowTemplate]:
        item = self.template_list.currentItem()
        if item is None:
            return None
        return get_template(str(item.data(Qt.ItemDataRole.UserRole) or ''))

    def _on_template_changed(self, *_args):
        template = self.current_template()
        if template is None:
            self.scenario_label.clear()
            self.pipeline_label.clear()
            return
        self.scenario_label.setText(template.scenario)
        self.pipeline_label.setText(' → '.join(template.pipeline))
        if not getattr(self, '_name_touched', False):
            self.name_edit.setText(template.name)
        self._rebuild_model_page()

    # --------------------------- 第二页：模型 --------------------------- #
    def _build_page_models(self):
        self.page_models = QWizardPage()
        self.page_models.setTitle('第 2 步 · 每一步用哪个模型？')
        self.page_models.setSubTitle('本地权重由你自己导入；这里只是把工作流里的"职责"指向模型库里的条目。')
        self.models_layout = QVBoxLayout(self.page_models)
        self.models_host_layout = QVBoxLayout()
        self.models_layout.addLayout(self.models_host_layout)
        self.models_layout.addWidget(self.error_label)
        self.models_layout.addStretch()
        self._models_body: Optional[QWidget] = None
        self.addPage(self.page_models)

    def _role_combo(self, template: WorkflowTemplate, role) -> QComboBox:
        """一个职责对应一个模型下拉。

        默认**不预选**任何模型：宁可让用户显式点一次，也不要悄悄选错。
        只有模板 defaults 里明确写了模型名、而且模型库里确实有，才自动选中。
        """
        combo = QComboBox()
        combo.addItem('（请选择模型）', '')
        wanted = [m for m in self.models if role.task == 'any' or task_of(m.model_type) == role.task]
        others = [m for m in self.models if m not in wanted]
        for model in wanted:
            combo.addItem(f"{model.name}（{task_of(model.model_type)}）", model.name)
        if others:
            combo.addItem('— 其他类型模型 —', '')
            combo.model().item(combo.count() - 1).setEnabled(False)
            for model in others:
                combo.addItem(f"{model.name}（{task_of(model.model_type)}）", model.name)
        if not role.required:
            combo.insertItem(0, '（不用）', '')
        default_name = str((template.defaults or {}).get(role.key, '') or '')
        if default_name:
            index = combo.findData(default_name)
            if index >= 0:
                combo.setCurrentIndex(index)
        return combo

    def _rebuild_model_page(self):
        if not hasattr(self, 'models_host_layout'):
            return  # 第二页还没建好（第一页填充时就会触发一次）
        # 每次重建都换一个全新的 body widget：直接销毁旧控件，避免延迟删除
        # 造成新旧表单在界面上互相叠压。
        if self._models_body is not None:
            self.models_host_layout.removeWidget(self._models_body)
            self._models_body.setParent(None)
            self._models_body.deleteLater()
            self._models_body = None
        self._role_widgets.clear()
        self._option_widgets.clear()
        template = self.current_template()
        if template is None:
            return
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self._models_body = body
        self.models_host_layout.addWidget(body)

        if template.shape == 'full_multi':
            self.model_check = QListWidget()
            self.model_check.setMinimumHeight(180)
            for model in self.models:
                entry = QListWidgetItem(f"{model.name}  ·  {model.model_type}")
                entry.setData(Qt.ItemDataRole.UserRole, model.name)
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                entry.setCheckState(Qt.CheckState.Checked)
                self.model_check.addItem(entry)
            body_layout.addWidget(QLabel('勾选本次要一起跑的整图模型：'))
            body_layout.addWidget(self.model_check)
            if not self.models:
                warn = QLabel('模型库还是空的。先到「模型」页导入本地 ONNX 权重，再回来创建工作流。')
                warn.setWordWrap(True)
                warn.setStyleSheet('color:#F5A623;')
                body_layout.addWidget(warn)
            return

        form = QFormLayout()
        for role in template.roles:
            combo = self._role_combo(template, role)
            self._role_widgets[role.key] = combo
            form.addRow(role.label, combo)
            if role.hint:
                hint = QLabel(role.hint)
                hint.setWordWrap(True)
                hint.setProperty('role', 'muted')
                form.addRow('', hint)
        body_layout.addLayout(form)
        options_form = self._build_option_form(template)
        if options_form is not None:
            heading = QLabel('规则与几何参数（可先用默认值，创建后在节点里再调）：')
            heading.setWordWrap(True)
            heading.setProperty('role', 'muted')
            body_layout.addWidget(heading)
            body_layout.addLayout(options_form)

    def _build_option_form(self, template: WorkflowTemplate) -> Optional[QFormLayout]:
        defaults = dict(template.defaults or {})
        if not defaults:
            return None
        # 枚举型参数一律用下拉：新手不该去猜 "ioa_right" 要怎么写。
        choices = {
            'metric': (
                '关联方式',
                [('候选框落在主体内的比例（IoA，推荐）', 'ioa_right'),
                 ('交并比 IoU', 'iou'),
                 ('主体落在候选内的比例（IoA，反向）', 'ioa_left'),
                 ('候选中心点在主体框内', 'center_inside')],
            ),
            'anchor': (
                '判定点',
                [('框中心', 'center'), ('框底边中点（人脚位置）', 'bottom_center'),
                 ('框底部 20% 处', 'bottom20')],
            ),
            'op': (
                '比较符',
                [('大于等于 >=', '>='), ('大于 >', '>'), ('等于 ==', '=='),
                 ('小于 <', '<'), ('小于等于 <=', '<=')],
            ),
        }
        labels = {
            'points': '区域顶点（0~1 归一化，如 0.4,0 1,0 1,1 0.4,1）',
            'count_threshold': '人数阈值',
            'count_label': '结论名称',
            'count_classes': '只统计类别（可空）',
            'threshold': '关联阈值',
            'crop_expand': '裁剪扩边比例',
            'crop_min_area': '裁剪最小面积(px²)',
            'expand_ratio': '裁剪扩边比例',
            'min_area': '裁剪最小面积(px²)',
            'pose_classes': '姿态模型里「人」的类别名',
            'carry_classes': '携带物品类别（逗号分隔）',
        }
        int_fields = {'count_threshold', 'crop_min_area', 'min_area'}
        float_fields = {'threshold', 'crop_expand', 'expand_ratio'}

        form = QFormLayout()
        for key, default in defaults.items():
            if key in choices:
                label, options = choices[key]
                combo = QComboBox()
                for text, value in options:
                    combo.addItem(text, value)
                index = combo.findData(str(default))
                if index >= 0:
                    combo.setCurrentIndex(index)
                self._option_widgets[key] = combo
                form.addRow(label, combo)
            elif key in int_fields:
                spin = QSpinBox()
                spin.setRange(0, 10 ** 7)
                spin.setValue(int(default))
                self._option_widgets[key] = spin
                form.addRow(labels.get(key, key), spin)
            elif key in float_fields:
                spin = QDoubleSpinBox()
                spin.setRange(0.0, 10.0)
                spin.setSingleStep(0.05)
                spin.setDecimals(3)
                spin.setValue(float(default))
                self._option_widgets[key] = spin
                form.addRow(labels.get(key, key), spin)
            else:
                edit = QLineEdit(str(default))
                self._option_widgets[key] = edit
                form.addRow(labels.get(key, key), edit)
        return form

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                WorkflowWizard._clear_layout(item.layout())

    # --------------------------- 第三页：检查 --------------------------- #
    def _build_page_summary(self):
        page = QWizardPage()
        page.setTitle('第 3 步 · 检查并创建')
        page.setSubTitle('创建后进入可编辑草稿；点「保存工作流」才会写入工作流目录。')
        layout = QVBoxLayout(page)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch()
        self.addPage(page)

    # --------------------------- 取值 --------------------------- #
    def _extra_models(self) -> list[str]:
        widget = getattr(self, 'model_check', None)
        if widget is None:
            return []
        return [
            str(widget.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(widget.count())
            if widget.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _role_models(self) -> dict[str, str]:
        return {key: str(combo.currentData() or '') for key, combo in self._role_widgets.items()}

    def _option_values(self) -> dict:
        values: dict = {}
        for key, widget in self._option_widgets.items():
            if isinstance(widget, QComboBox):
                values[key] = widget.currentData()
            elif isinstance(widget, QLineEdit):
                values[key] = widget.text()
            else:
                values[key] = widget.value()
        return values

    def values(self) -> WorkflowConfig:
        template = self.current_template()
        if template is None:
            raise ValueError('请选择一个场景模板')
        return build_from_template(
            template.template_id,
            self.name_edit.text().strip(),
            role_models=self._role_models(),
            extra_models=self._extra_models(),
            option_values=self._option_values(),
        )

    # --------------------------- 校验 --------------------------- #
    def validateCurrentPage(self) -> bool:  # noqa: N802 (Qt 命名)
        page_id = self.currentId()
        # currentId() 为 -1 表示向导还没 start（例如被单测直接构造）；按第一页处理。
        if page_id <= 0:
            if not self.name_edit.text().strip():
                self.error_label.setText('请填写工作流名称')
                return False
            if self.current_template() is None:
                self.error_label.setText('请选择一个场景模板')
                return False
            self._rebuild_model_page()
            self.error_label.clear()
            return True
        if page_id == 1:
            try:
                config = self.values()
            except ValueError as exc:
                self.error_label.setText(str(exc))
                return False
            self.error_label.clear()
            self.summary.setText(self._summary_text(config))
        return True

    def _summary_text(self, config: WorkflowConfig) -> str:
        lines = [f"工作流：{config.name}", "", "处理链路："]
        for index, node in enumerate(config.nodes, start=1):
            model = node.params.get('model') if isinstance(node.params, dict) else None
            name = model.get('name') if isinstance(model, dict) else ''
            suffix = f"  ← {name}" if name else ""
            lines.append(f"  {index}. {node.name}{suffix}")
        verdict = [node.name for node in config.nodes if node.type in ('count_rule', 'roi_filter')]
        if verdict:
            lines += ["", "规则节点：" + "、".join(verdict)]
        lines += ["", "提示：模型只在本机运行，也不会自动下载权重。"]
        return "\n".join(lines)
