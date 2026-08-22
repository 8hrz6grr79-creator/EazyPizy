# =========================================
# STYLES
# =========================================

APP_STYLE = """
QWidget { font-family: 'Segoe UI', sans-serif; }
QToolTip {
    background: #111113;
    color: rgba(255,255,255,0.85);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 12px;
}
"""

BAR_STYLE = """
QFrame#bar {
    background: #1e1e22;
    border-radius: 20px;
    border: 1px solid rgba(255,255,255,0.08);
}
"""

MODE_BTN_STYLE = """
QPushButton {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    color: rgba(255,255,255,0.65);
    font-size: 13px;
    padding: 0px 14px;
}
QPushButton:hover { background: rgba(255,255,255,0.10); color: white; }
QPushButton:pressed { background: rgba(255,255,255,0.14); }
QPushButton:checked {
    background: rgba(88,101,242,0.20);
    border-color: rgba(88,101,242,0.50);
    color: #a5b4fc;
}
"""

ICON_BTN_STYLE = """
QPushButton {
    background: transparent;
    border: none;
    border-radius: 10px;
}
QPushButton:hover { background: rgba(255,255,255,0.08); }
QPushButton:pressed { background: rgba(255,255,255,0.14); }
"""

CLOSE_BTN_STYLE = """
QPushButton {
    background: rgba(255, 82, 82, 0.12);
    border: 1px solid rgba(255, 82, 82, 0.20);
    border-radius: 13px;
    color: rgba(255, 100, 100, 0.75);
    font-size: 15px;
    font-weight: bold;
}
QPushButton:hover {
    background: rgba(255, 59, 59, 0.85);
    border-color: rgba(255, 80, 80, 0.60);
    color: white;
}
QPushButton:pressed { background: rgba(200, 30, 30, 0.90); }
"""

MINIMIZE_BTN_STYLE = """
QPushButton {
    background: rgba(255, 196, 0, 0.10);
    border: 1px solid rgba(255, 196, 0, 0.18);
    border-radius: 13px;
    color: rgba(255, 196, 0, 0.65);
    font-size: 16px;
    font-weight: bold;
}
QPushButton:hover {
    background: rgba(255, 196, 0, 0.80);
    border-color: rgba(255, 196, 0, 0.50);
    color: #1a1a1a;
}
QPushButton:pressed { background: rgba(200, 150, 0, 0.90); }
"""

KB_PILL_STYLE = """
QFrame#kbPill {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
}
"""

KB_INPUT_STYLE = """
QLineEdit {
    background: transparent;
    border: none;
    color: white;
    font-size: 13px;
    font-family: 'Segoe UI', sans-serif;
}
"""

ACTION_BTN_STYLE = """
QPushButton {
    background: #5865F2;
    border: none;
    border-radius: 12px;
    color: white;
    font-size: 13px;
    font-weight: bold;
    padding: 0px 18px;
}
QPushButton:hover { background: #4752C4; }
QPushButton:pressed { background: #3c46b0; }
QPushButton:disabled { background: rgba(88,101,242,0.4); color: rgba(255,255,255,0.4); }
"""

SECONDARY_BTN_STYLE = """
QPushButton {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    color: rgba(255,255,255,0.70);
    font-size: 13px;
    font-weight: 500;
    padding: 0px 14px;
}
QPushButton:hover { background: rgba(255,255,255,0.10); color: white; }
QPushButton:pressed { background: rgba(255,255,255,0.14); }
"""

HINT_STYLE = "QLabel { color: rgba(255,255,255,0.20); font-size: 12px; }"

LIST_FRAME_STYLE = """
QFrame#listFrame {
    background: #1e1e22;
    border-radius: 20px;
    border: 1px solid rgba(255,255,255,0.07);
}
"""

LIST_WIDGET_STYLE = """
QListWidget {
    background: transparent;
    border: none;
    color: rgba(255,255,255,0.85);
    font-size: 13px;
    padding: 4px;
}
QListWidget::item {
    padding: 9px 14px;
    border-radius: 9px;
    margin-bottom: 2px;
}
QListWidget::item:hover { background: rgba(255,255,255,0.04); }
QListWidget::item:selected { background: rgba(255,255,255,0.07); }
"""

PROGRESS_STYLE = """
QProgressBar {
    background: rgba(255,255,255,0.07);
    border: none;
    border-radius: 3px;
    height: 5px;
}
QProgressBar::chunk { background: #5865F2; border-radius: 3px; }
"""

MENU_STYLE = """
QMenu {
    background: #2b2b2f;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 18px;
    padding: 8px;
    color: rgba(255,255,255,0.75);
    font-size: 13px;
    font-family: 'Segoe UI', sans-serif;
}
QMenu::item { padding: 10px 40px 10px 14px; border-radius: 12px; }
QMenu::item:selected { background: rgba(255,255,255,0.08); color: white; }
QMenu::item:disabled { color: rgba(255,255,255,0.25); }
QMenu::separator { height: 1px; background: rgba(255,255,255,0.08); margin: 4px 0; }
"""

SPINBOX_STYLE = """
QSpinBox {
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    color: white;
    font-size: 13px;
    padding: 5px 8px;
    min-width: 68px;
}
QSpinBox::up-button, QSpinBox::down-button { width: 0px; }
"""

COMBO_STYLE = """
QComboBox {
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    color: white;
    font-size: 13px;
    padding: 5px 10px;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #2b2b2f;
    border: 1px solid rgba(255,255,255,0.12);
    color: white;
    selection-background-color: rgba(255,255,255,0.10);
}
"""

PANEL_STYLE = """
QFrame#panel {
    background: #1e1e22;
    border-radius: 20px;
    border: 1px solid rgba(255,255,255,0.07);
}
"""

PDF_COMPACT_PANEL_STYLE = """
QFrame#pdfCompactPanel {
    background: #1e1e22;
    border-radius: 18px;
    border: 1px solid rgba(255,255,255,0.08);
}
QFrame#pdfToolHeader {
    background: transparent;
    border: none;
}
"""

PDF_HEADER_STYLE = """
QLabel {
    color: rgba(255,255,255,0.88);
    font-size: 14px;
    font-weight: 700;
    border: none;
}
"""

PDF_SUBTITLE_STYLE = """
QLabel {
    color: rgba(255,255,255,0.42);
    font-size: 12px;
    border: none;
}
"""

SIDEBAR_STYLE = """
    background: #26262b;
    border-left: 1px solid rgba(255,255,255,0.06);
    border-top-right-radius: 20px;
    border-bottom-right-radius: 20px;
"""

PDF_TOOL_BTN_STYLE = """
QPushButton {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    color: rgba(255,255,255,0.75);
    font-size: 12px;
    font-weight: 600;
    padding: 0px 12px;
}
QPushButton:hover {
    background: rgba(255,255,255,0.10);
    color: white;
}
QPushButton:checked {
    background: rgba(88,101,242,0.22);
    border: 1px solid rgba(88,101,242,0.50);
    color: #a5b4fc;
}
QPushButton:pressed { background: rgba(88,101,242,0.30); }
"""

PDF_STATUS_OK  = "color: #4ade80; font-size: 12px;"
PDF_STATUS_ERR = "color: #f87171; font-size: 12px;"
PDF_LABEL_STYLE = "color: rgba(255,255,255,0.50); font-size: 12px;"

PRESET_BTN_STYLE = """
QPushButton {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    color: rgba(255,255,255,0.65);
    font-size: 11px;
    padding: 6px 10px;
    text-align: left;
}
QPushButton:hover {
    background: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.15);
    color: rgba(255,255,255,0.90);
}
QPushButton:checked {
    background: rgba(88,101,242,0.18);
    border: 1px solid rgba(88,101,242,0.55);
    color: #a5b4fc;
}
QPushButton:pressed { background: rgba(88,101,242,0.25); }
"""

PRESET_CATEGORY_BTN_STYLE = """
QPushButton {
    background: transparent;
    border: none;
    color: rgba(255,255,255,0.40);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-align: left;
    padding: 4px 0px;
}
QPushButton:hover { color: rgba(255,255,255,0.65); }
"""

SCROLL_AREA_STYLE = """
QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: transparent;
    width: 5px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.12);
    border-radius: 2px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255,255,255,0.22);
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: transparent;
}
"""