"""Small runtime i18n layer for AnnoWeave's Qt interface.

Chinese remains the canonical source text so existing pages can migrate gradually.
The translator records canonical text on each widget, allowing lossless switching in
both directions without rebuilding pages or discarding the current review state.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, QSettings, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTextEdit,
    QWidget,
)

LANG_ZH = "zh_CN"
LANG_EN = "en_US"

# Primary product surfaces. Dynamic result data, model names and user labels are
# deliberately left untouched.
EN = {
    "文件分类": "Classify",
    "视觉复核": "Review",
    "工作流": "Workflows",
    "模型": "Model",
    "任务": "Batch",
    "中文": "中文",
    "English": "English",
    "语言": "Language",
    "打开文件夹": "Open folder",
    "重新扫描": "Rescan",
    "定义标签": "Labels",
    "快捷键": "Shortcuts",
    "生成分类目录": "Export classified files",
    "遍历模式": "Browse mode",
    "所有图片和视频（逐文件）": "All images and videos",
    "仅图片（同名组共享标签和删除）": "Images only (group matching names)",
    "尚未打开目录": "No folder open",
    "视频无法播放": "Unable to play video",
    "播放/暂停": "Play / Pause",
    "从头播放": "Restart",
    "静音": "Mute",
    "上一项": "Previous",
    "下一项": "Next",
    "移动到 _delete": "Move to _delete",
    "撤销上次删除": "Undo delete",
    "当前项标签（可多选，勾选即保存）": "Current labels (multi-select, saved immediately)",
    "清除当前全部标签": "Clear labels",
    "未选择媒体": "No media selected",
    "请选择告警文件夹": "Choose a media folder",
    "未打开项目": "No project open",
    "打开素材": "Open media",
    "批量处理": "Batch processing",
    "采样与导出设置": "Sampling & export settings",
    "刷新工作流": "Refresh workflows",
    "打开并编辑选中裁剪": "Open and edit selected crop",
    "置信": "Confidence",
    "关键点": "Keypoints",
    "选择 / 编辑": "Select / Edit",
    "绘制矩形": "Draw rectangle",
    "平移画布": "Pan canvas",
    "修改类别": "Change class",
    "删除框": "Delete box",
    "撤销": "Undo",
    "重做": "Redo",
    "视图设置": "View settings",
    "同名组展开成员": "Expand matching-name group",
    "上一采样帧  PgUp": "Previous sample  PgUp",
    "下一采样帧  PgDn": "Next sample  PgDn",
    "时间轴": "Timeline",
    "◀ 上一已运行命中": "◀ Previous processed hit",
    "播放": "Play",
    "下一已运行命中 ▶": "Next processed hit ▶",
    "下一视频 / 图片 →": "Next video / image →",
    "活动工作流": "Active workflow",
    "采样率": "Sampling rate",
    "导出内容": "Export content",
    "对象与证据": "Objects & evidence",
    "当前标注": "Current annotations",
    "本帧没有对象。运行工作流后这里会列出人身与行为框。": "No objects in this frame. Run the workflow to see detections.",
    "打开素材并运行当前帧后，这里显示对象、裁剪和下游证据。": "Open media and run the current frame to see objects, crops and downstream evidence.",
    "裁剪图（0）": "Crops (0)",
    "标注图层": "Annotation layers",
    "新框类别": "New box class",
    "R 编框 · Ctrl+J 编辑 · Ctrl+F 适应 · 改框后自动重算裁剪": "R draw · Ctrl+J edit · Ctrl+F fit · edits recompute crops",
    "素材队列": "Media queue",
    "尚未打开素材": "No media open",
    "按运行选项中的采样率跳转；← / → 微调原始帧": "Jump by sampling rate; ← / → steps source frames",
    "逐原始帧": "Every source frame",
    "倍速": "Speed",
    "保存状态：暂无修改": "Save status: no changes",
    "运行状态：空闲": "Run status: idle",
    "帧身份：—": "Frame identity: —",
    "当前帧 → 带框证据图与裁剪图……": "Current frame → evidence image and crops…",
    "当前帧 → 训练原图与标签……": "Current frame → training image and labels…",
    "保存集 → 带框证据图与裁剪图……": "Save set → evidence images and crops…",
    "保存集 → 训练原图与标签……": "Save set → training images and labels…",
    "预推理全部采样帧，然后从头复核": "Precompute all samples, then review from start",
    "批量推理并直接导出文件……": "Run batch inference and export files…",
    "放大   Ctrl++": "Zoom in   Ctrl++",
    "缩小   Ctrl+-": "Zoom out   Ctrl+-",
    "适应窗口   Ctrl+F": "Fit window   Ctrl+F",
    "100% 原图   Ctrl+=": "100% image   Ctrl+=",
    "保存全图": "Save full frame",
    "保存某张裁剪图": "Save selected crop",
    "保存全图及其所有裁剪图": "Save full frame and all crops",
    "仅保存裁剪图": "Save crops only",
    "人工修订版": "Human revision",
    "模型预测版": "Model prediction",
    "全部素材": "All media",
    "模型分歧": "Model disagreement",
    "已修改": "Modified",
    "仅视频": "Videos only",
    "仅图片": "Images only",
    "打开图片 / 视频……": "Open images / videos…",
    "打开文件夹……": "Open folder…",
    "运行当前帧": "Run current frame",
    "导出": "Export",
    "运行选项": "Run options",
    "上一采样帧": "Previous sample",
    "下一采样帧": "Next sample",
    "上一个文件": "Previous file",
    "下一个文件": "Next file",
    "模型库": "Model Library",
    "＋ 添加模型": "+ Add model",
    "编辑选中模型": "Edit selected",
    "移除": "Remove",
    "入门教程": "Quick Start",
    "试跑选中模型": "Test selected model",
    "重新检测": "Refresh status",
    "执行后端：—": "Runtime backend: —",
    "类型": "Type",
    "状态": "Status",
    "设备": "Device",
    "输入": "Input",
    "类别数": "Classes",
    "插件与扩展（展开）": "Plugins & extensions (expand)",
    "重新扫描插件": "Rescan plugins",
    "插件": "Plugin",
    "版本": "Version",
    "能力 / 原因": "Capabilities / reason",
    "＋ 创建工作流": "+ Create workflow",
    "打开文件": "Open file",
    "保存文件": "Save file",
    "添加选中模块 →": "Add selected module →",
    "启用该节点": "Enable node",
    "上移": "Move up",
    "下移": "Move down",
    "复制": "Duplicate",
    "删除": "Delete",
    "模块库 · 双击添加": "Module library · double-click to add",
    "节点属性（选中后编辑）": "Node properties",
    "名称": "Name",
    "从上到下依次执行。用上移 / 下移调整模块顺序。": "Nodes run from top to bottom. Use Move up / Move down to reorder.",
    "模板": "Template",
    "重新载入模板": "Reload templates",
    "运行前检查": "Preflight check",
    "保存工作流": "Save workflow",
    "导入 / 导出": "Import / Export",
    "导入工作流 JSON": "Import workflow JSON",
    "导出草稿 JSON": "Export draft JSON",
    "选择试跑图片": "Choose test image",
    "运行整条流水线": "Run full workflow",
    "运行至此": "Run to selected node",
    "仅重算下游": "Recompute downstream",
    "尚未运行": "Not run yet",
    "节点": "Node",
    "耗时": "Duration",
    "输出预览": "Output preview",
    "展开节点调试": "Show node diagnostics",
    "收起节点调试": "Hide node diagnostics",
    "选择文件": "Choose files",
    "选择文件夹": "Choose folder",
    "使用当前项目素材": "Use current project media",
    "未选择来源": "No source selected",
    "采样FPS": "Sampling FPS",
    "保存模板": "Export preset",
    "输出目录": "Output folder",
    "浏览": "Browse",
    "开始批量推理并导出": "Run batch inference and export",
    "取消": "Cancel",
    "排队": "Queued",
    "媒体": "Media",
    "帧": "Frame",
    "裁剪": "Crops",
    "失败原因": "Failure reason",
    "批处理会按采样率对全部素材运行所选工作流，并把全图/裁剪产物和运行清单写入输出目录。": "Runs the selected workflow over all media at the sampling rate and writes frames, crops and a run manifest.",
    "媒体 0/0 · 帧 0/0 · 节点 0 · 耗时 0.0s · 速度 0.0 帧/秒 · 预计剩余 —": "Media 0/0 · Frames 0/0 · Nodes 0 · Elapsed 0.0s · Speed 0.0 fps · ETA —",
    "对象": "Objects",
    "证据": "Evidence",
    "加入保存集": "Add to save set",
    "已加入保存集 ✓": "In save set ✓",
    "选择一个模型查看路径、哈希、类别表与试跑结果。": "Select a model to inspect its path, hash, classes and test result.",
    "运行工作流前请先在这里确认目标模型为“就绪”；缺少权重或 onnxruntime 时，工作流页会直接报出失败节点。": "Confirm each required model is Ready before running a workflow. Missing weights or ONNX Runtime are reported at the failing node.",
    "选中一个节点查看它的输入/输出摘要与耗时。": "Select a node to inspect its input, output and duration.",
    "编辑的是草稿；点“保存工作流”才会写入工作流目录。": "You are editing a draft. Save the workflow to make it available.",
}


def tr(text: str, language: str) -> str:
    if language != LANG_EN:
        return text
    translated = EN.get(text)
    if translated is not None:
        return translated
    if text.startswith("执行后端："):
        return (text.replace("执行后端：", "Runtime backend: ", 1)
                .replace("，可用 provider：", "; available providers: ")
                .replace("。设备列显示可用后端预估，请通过试跑验证模型兼容性。",
                         ". The Device column is an estimate; run a model test to verify compatibility."))
    match = re.fullmatch(r"共 (\d+) 个模型，就绪 (\d+) 个；哈希用于标识权重版本，试跑用于验证依赖与权重是否真的可用。", text)
    if match:
        return f"{match.group(1)} models; {match.group(2)} ready. Hashes identify weight versions; model tests verify weights and dependencies."
    if text.startswith("插件目录："):
        return "Plugin folder: " + text.removeprefix("插件目录：")
    if text.startswith("未发现插件。把插件目录放到 "):
        path = text.removeprefix("未发现插件。把插件目录放到 ").split(" 后点", 1)[0]
        return f"No plugins found. Add a plugin folder under {path}, then select Rescan plugins."
    if text.startswith("项目：直接打开 "):
        return text.replace("项目：直接打开 ", "Project: directly opened ", 1).replace(" 个素材", " media items")
    if text.startswith("项目："):
        return "Project: " + text.removeprefix("项目：").replace(" 个素材", " media items")
    return text


class LanguageManager(QObject):
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("AnnoWeave", "AnnoWeave")
        value = str(self.settings.value("interface/language", LANG_ZH))
        self.language = value if value in (LANG_ZH, LANG_EN) else LANG_ZH

    def set_language(self, language: str) -> None:
        if language not in (LANG_ZH, LANG_EN) or language == self.language:
            return
        self.language = language
        self.settings.setValue("interface/language", language)
        self.changed.emit(language)

    def text(self, source: str) -> str:
        return tr(source, self.language)

    def eventFilter(self, watched: QObject, event) -> bool:
        # Dialogs and popup menus are often created after the language switch.
        # Translate them as they become visible so every page need not duplicate
        # language lifecycle code.
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            self.apply(watched)
        return False

    def apply(self, root: QWidget) -> None:
        objects = [root, *root.findChildren(QObject)]
        for obj in objects:
            self._translate_object(obj)

    def _value(self, obj: QObject, name: str, current: str) -> str:
        key = f"aw_i18n_{name}"
        last_key = f"aw_i18n_last_{name}"
        source = obj.property(key)
        last = obj.property(last_key)
        # Pages update status labels after loading a project or finishing a task.
        # When that happens, promote the new value to canonical source text instead
        # of restoring the placeholder captured when the widget was constructed.
        if source is None or (last is not None and current != str(last)):
            source = current
            obj.setProperty(key, source)
        translated = tr(str(source), self.language)
        obj.setProperty(last_key, translated)
        return translated

    def _translate_object(self, obj: QObject) -> None:
        if isinstance(obj, QWidget):
            title = obj.windowTitle()
            if title:
                obj.setWindowTitle(self._value(obj, "title", title))
            tip = obj.toolTip()
            if tip:
                obj.setToolTip(self._value(obj, "tip", tip))
        if isinstance(obj, (QLabel, QAbstractButton, QGroupBox)):
            obj.setText(self._value(obj, "text", obj.text())) if not isinstance(obj, QGroupBox) else obj.setTitle(self._value(obj, "group", obj.title()))
        if isinstance(obj, QAction):
            obj.setText(self._value(obj, "action", obj.text()))
            if obj.toolTip():
                obj.setToolTip(self._value(obj, "action_tip", obj.toolTip()))
        if isinstance(obj, (QLineEdit, QTextEdit)):
            placeholder = obj.placeholderText()
            if placeholder:
                obj.setPlaceholderText(self._value(obj, "placeholder", placeholder))
        if isinstance(obj, QComboBox):
            source = obj.property("aw_i18n_items")
            if source is None:
                source = [obj.itemText(i) for i in range(obj.count())]
                obj.setProperty("aw_i18n_items", source)
            for index, text in enumerate(source):
                if index < obj.count():
                    obj.setItemText(index, tr(str(text), self.language))
        if isinstance(obj, QTableWidget):
            source = obj.property("aw_i18n_headers")
            if source is None:
                source = [obj.horizontalHeaderItem(i).text() if obj.horizontalHeaderItem(i) else ""
                          for i in range(obj.columnCount())]
                obj.setProperty("aw_i18n_headers", source)
            for index, text in enumerate(source):
                item = obj.horizontalHeaderItem(index)
                if item is not None:
                    item.setText(tr(str(text), self.language))
