import os

from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QVBoxLayout, QSpinBox
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QColor, QIcon, QCursor

from styles import ICON_BTN_STYLE, SPINBOX_STYLE

# =========================================
# CONSTANTS
# =========================================

HANDLE  = 9
PANEL_H = 500
WIN_W   = 960

# =========================================
# ASPECT RATIOS
# =========================================

ASPECT_RATIOS = [
    ("Free",   None),
    ("1 : 1",  (1, 1)),
    ("4 : 3",  (4, 3)),
    ("3 : 4",  (3, 4)),
    ("16 : 9", (16, 9)),
    ("9 : 16", (9, 16)),
    ("3 : 2",  (3, 2)),
    ("2 : 3",  (2, 3)),
    ("5 : 4",  (5, 4)),
    ("Custom", "custom"),
]

# =========================================
# WIDGET HELPERS
# =========================================

def make_divider():
    d = QFrame()
    d.setFixedSize(1, 30)
    d.setStyleSheet("background: rgba(255,255,255,0.10);")
    return d


def make_icon_btn(icon_path, tooltip, fallback_text="", size=48):
    btn = QPushButton()
    btn.setToolTip(tooltip)
    btn.setFixedSize(size, size)
    btn.setCursor(QCursor(Qt.PointingHandCursor))
    if icon_path and os.path.exists(icon_path):
        btn.setIcon(QIcon(icon_path))
        btn.setIconSize(QSize(35, 35))
        btn.setStyleSheet(ICON_BTN_STYLE)
    else:
        btn.setText(fallback_text)
        btn.setStyleSheet(
            ICON_BTN_STYLE +
            "QPushButton { color: rgba(255,255,255,0.65); font-size: 20px; }"
        )
    return btn


def make_wm_btn(style, text, tooltip, size=30):
    btn = QPushButton(text)
    btn.setToolTip(tooltip)
    btn.setFixedSize(size, size)
    btn.setCursor(QCursor(Qt.PointingHandCursor))
    btn.setStyleSheet(style)
    return btn


def sep_widget():
    s = QFrame()
    s.setFixedHeight(1)
    s.setStyleSheet("background:rgba(255,255,255,0.07); border:none;")
    return s


def section_label(text):
    l = QLabel(text.upper())
    l.setStyleSheet("color:rgba(255,255,255,0.28); font-size:10px; letter-spacing:1px; border:none;")
    return l


def spin_col(label, min_val, max_val):
    col = QVBoxLayout()
    col.setSpacing(3)
    lbl = QLabel(label)
    lbl.setStyleSheet("color:rgba(255,255,255,0.35); font-size:10px; border:none;")
    spin = QSpinBox()
    spin.setRange(min_val, max_val)
    spin.setButtonSymbols(QSpinBox.NoButtons)
    spin.setStyleSheet(SPINBOX_STYLE)
    col.addWidget(lbl)
    col.addWidget(spin)
    return col, spin


# =========================================
# PAINT HELPERS
# =========================================

def checkerboard_paint(painter, width, height, tile=12):
    for row in range(height // tile + 1):
        for col in range(width // tile + 1):
            c = QColor(50, 50, 54) if (row + col) % 2 == 0 else QColor(40, 40, 44)
            painter.fillRect(col * tile, row * tile, tile, tile, c)
