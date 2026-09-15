"""Shared schema-to-Qt form helper used by the node builder."""
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox


def build_param_form(layout, schema: dict, params: dict, model_names: list[str]):
    widgets = {}
    for key, spec in schema.items():
        label = spec.get("label", key)
        value_type = spec.get("type", "str")
        if value_type == "model":
            widget = QComboBox()
            widget.addItem("（选择模型）", "")
            for name in model_names:
                widget.addItem(name, name)
            current = params.get(key)
            current = current.get("name") if isinstance(current, dict) else current
            index = widget.findData(current)
            if index >= 0:
                widget.setCurrentIndex(index)
        elif value_type == "float":
            widget = QDoubleSpinBox()
            widget.setRange(-1e9, 1e9)
            widget.setDecimals(4)
            widget.setValue(float(params.get(key, spec.get("default", 0.0))))
        elif value_type == "int":
            widget = QSpinBox()
            widget.setRange(-(10**9), 10**9)
            widget.setValue(int(params.get(key, spec.get("default", 0))))
        elif value_type == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(params.get(key, spec.get("default", False))))
        else:
            widget = QLineEdit(str(params.get(key, spec.get("default", ""))))
        layout.addRow(label, widget)
        widgets[key] = widget
    return widgets
