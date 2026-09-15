"""AnnoWeave 视觉系统（M5）：统一色彩/间距/字体语义 token + QSS。

对齐评审第 6 节：深蓝 #172B45、青绿 #19B99A；深色工作面 #111820、面板 #18222D；
正文 #E7EDF5、辅助 #A8B4C3；状态琥珀/绿/红；微软雅黑 13–14px；间距 4/8/12/16/24；
按钮 32–36 高、圆角 4–6。
"""

from __future__ import annotations

TOKENS = {
    "brand": "#172B45",
    "brand_accent": "#19B99A",
    "surface": "#111820",
    "panel": "#18222D",
    "text": "#E7EDF5",
    "text_muted": "#A8B4C3",
    "border": "#2A3644",
    "state_pending": "#F5A623",
    "state_confirmed": "#2FBF71",
    "state_rejected": "#E05252",
    "space_1": 4,
    "space_2": 8,
    "space_3": 12,
    "space_4": 16,
    "space_5": 24,
    "font_size": 13,
    "font_title": 15,
    "radius": 5,
    "button_height": 34,
}

FONT_FAMILY = "Microsoft YaHei, 微软雅黑, Segoe UI, sans-serif"


def stylesheet() -> str:
    t = TOKENS
    return f"""
    QWidget {{ background: {t['surface']}; color: {t['text']};
               font-family: {FONT_FAMILY}; font-size: {t['font_size']}px; }}
    QMainWindow, QDialog {{ background: {t['surface']}; }}
    QLabel {{ color: {t['text']}; }}
    QLabel[role="muted"] {{ color: {t['text_muted']}; }}
    QLabel[role="title"] {{ font-size: 16px; font-weight: 600; }}
    QLabel#BrandWordmark {{ font-size: 18px; font-weight: 700; letter-spacing: 0.4px; }}
    QComboBox#LanguageSelector {{ min-width: 92px; }}
    QPushButton {{ background: {t['panel']}; color: {t['text']};
                   border: 1px solid {t['border']}; border-radius: {t['radius']}px;
                   padding: 4px 10px; min-height: {t['button_height'] - 8}px; }}
    QPushButton:hover {{ border-color: {t['brand_accent']}; }}
    QPushButton:checked {{ background: {t['brand']}; border-color: {t['brand_accent']}; }}
    QPushButton:focus, QComboBox:focus, QLineEdit:focus {{ border-color: {t['brand_accent']}; }}
    QMenu {{ background: {t['panel']}; border: 1px solid {t['border']}; padding: 6px; }}
    QMenu::item {{ padding: 7px 22px; }}
    QMenu::item:selected {{ background: {t['brand']}; }}
    QTabWidget::pane {{ border: 1px solid {t['border']}; }}
    QTabBar::tab {{ background: {t['panel']}; padding: 8px 16px; color: {t['text_muted']}; }}
    QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['brand_accent']}; }}
    QScrollBar:vertical {{ background: {t['surface']}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #3A4A5A; min-height: 28px; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: #637B90; }}
    QScrollBar::handle:vertical:pressed {{ background: {t['brand_accent']}; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    /* 横向滚动条：文件列表里查看长文件名时用得到。
       默认不写样式会回退到 Windows 原生外观（深色界面上又细又亮、抓不住），
       这里与纵向保持同一套尺寸/圆角/悬停反馈，并加长最小滑块长度。 */
    QScrollBar:horizontal {{ background: {t['surface']}; height: 12px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: #3A4A5A; min-width: 40px; border-radius: 5px;
                                    margin: 2px 1px; }}
    QScrollBar::handle:horizontal:hover {{ background: #637B90; }}
    QScrollBar::handle:horizontal:pressed {{ background: {t['brand_accent']}; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QPushButton:disabled {{ color: {t['text_muted']}; border-color: {t['border']}; }}
    QPushButton[accent="true"] {{ background: {t['brand_accent']}; color: #06131F; border: none; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {t['panel']}; color: {t['text']}; border: 1px solid {t['border']};
        border-radius: {t['radius']}px; padding: 2px 6px; min-height: 26px; }}
    QListWidget, QTableWidget, QTreeWidget, QTextEdit {{
        background: {t['panel']}; color: {t['text']}; border: 1px solid {t['border']};
        border-radius: {t['radius']}px; }}
    QListWidget::item:selected, QTableWidget::item:selected {{ background: {t['brand']}; }}
    /* 文件分类页左侧列表：行高更宽松、悬停与选中用不同颜色区分
       （悬停=浅一档，选中=品牌蓝），否则用户分不清"鼠标在哪一行"和"当前打开哪一行"。 */
    QListWidget#MediaList {{ padding: 2px; }}
    QListWidget#MediaList::item {{ padding: 4px 6px; border-radius: 4px; }}
    QListWidget#MediaList::item:hover:!selected {{ background: #22303F; }}
    QListWidget#MediaList::item:selected {{ background: {t['brand']}; }}
    QHeaderView::section {{ background: {t['panel']}; color: {t['text_muted']};
                            border: none; padding: 4px; }}
    QSplitter::handle {{ background: {t['border']}; }}
    QSplitter::handle:hover {{ background: #4C687D; }}
    QStatusBar {{ background: {t['panel']}; color: {t['text_muted']}; }}
    QToolTip {{ background: {t['panel']}; color: {t['text']}; border: 1px solid {t['border']}; }}
    """


def apply_theme(app) -> None:
    app.setStyleSheet(stylesheet())
