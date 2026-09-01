import os

from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QVBoxLayout, QSpinBox
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QColor, QIcon, QCursor, QPixmap, QPainter

from ui.styles import ICON_BTN_STYLE, SPINBOX_STYLE

# =========================================
# CONSTANTS
# =========================================

HANDLE  = 9
PANEL_H = 500
WIN_W   = 960

# =========================================
# ASPECT RATIOS & CROP PRESETS
# =========================================

# Each entry: (label, ratio_value, preset_info)
#   ratio_value : None (free), "custom", "sep" (separator), or (w, h) tuple
#   preset_info : None (plain ratio), or (target_w_px, target_h_px, dpi) for
#                 automatic resize-on-save
CROP_PRESETS = [
    ("Free",                      None,           None),
    ("sep",                       "sep",          None),
    ("Instagram Post",            (1, 1),         (1080, 1080, 72)),
    ("Instagram Portrait",        (1080, 1350),   (1080, 1350, 72)),
    ("Instagram Story",           (1080, 1920),   (1080, 1920, 72)),
    ("Facebook Post",             (1200, 630),    (1200, 630, 72)),
    ("Facebook Cover",            (1640, 624),    (1640, 624, 72)),
    ("X / Twitter Post",          (1600, 900),    (1600, 900, 72)),
    ("LinkedIn Post",             (1200, 627),    (1200, 627, 72)),
    ("YouTube Thumbnail",         (1280, 720),    (1280, 720, 72)),
    ("YouTube Banner",            (2560, 1440),   (2560, 1440, 72)),
    ("WhatsApp Status",           (1080, 1920),   (1080, 1920, 72)),
    ("WhatsApp DP",               (1, 1),         (500, 500, 72)),
    ("TikTok",                    (1080, 1920),   (1080, 1920, 72)),
    ("Pinterest Pin",             (1000, 1500),   (1000, 1500, 72)),
    ("sep",                       "sep",          None),
    ("Passport (India) 35×45mm",  (35, 45),       (413, 531, 300)),
    ("Passport (US) 2×2in",       (1, 1),         (600, 600, 300)),
    ("Visa Photo 35×45mm",        (35, 45),       (413, 531, 300)),
    ("ID Card Photo 1:1",         (1, 1),         None),
    ("Aadhaar Photo 35×45mm",     (35, 45),       (413, 531, 300)),
]

# Backward-compat alias (main_window still imports ASPECT_RATIOS in some paths)
ASPECT_RATIOS = CROP_PRESETS

# =========================================
# WIDGET HELPERS
# =========================================

def make_divider():
    d = QFrame()
    d.setFixedSize(1, 30)
    d.setStyleSheet("background: rgba(255,255,255,0.10);")
    return d


def grey_icon_path(icon_path):
    """Given 'assets/icons/bgremove.png', return 'assets/icons/bgremove_grey.png'.

    Used to swap a mode button's icon to its greyed-out counterpart when
    the tool is disabled (e.g. BG Remove while offline), instead of just
    disabling the button and leaving the full-color icon showing.
    """
    base, ext = os.path.splitext(icon_path)
    return f"{base}_grey{ext}"


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

# Cache of pre-rendered 2x2 checkerboard tile pixmaps, keyed by tile size.
# Built once per size on first use, then reused for the app's whole
# lifetime — turns thousands of per-frame fillRect() calls into a single
# drawTiledPixmap() call.
_checkerboard_tile_cache = {}


def _get_checkerboard_tile(tile):
    pm = _checkerboard_tile_cache.get(tile)
    if pm is None:
        pm = QPixmap(tile * 2, tile * 2)
        c0 = QColor(50, 50, 54)
        c1 = QColor(40, 40, 44)
        p = QPainter(pm)
        p.fillRect(0,    0,    tile, tile, c0)
        p.fillRect(tile, 0,    tile, tile, c1)
        p.fillRect(0,    tile, tile, tile, c1)
        p.fillRect(tile, tile, tile, tile, c0)
        p.end()
        _checkerboard_tile_cache[tile] = pm
    return pm


def checkerboard_paint(painter, width, height, tile=12):
    # Was: nested Python loop calling painter.fillRect() once per tile
    # (~3,150 calls for a typical 900x500 canvas, every single repaint —
    # including every frame while dragging a crop handle). Now: one
    # cached 2x2 tile pixmap, drawn in a single native call that tiles
    # to fill the requested area. Same visual output, near-zero cost.
    painter.drawTiledPixmap(0, 0, width, height, _get_checkerboard_tile(tile))