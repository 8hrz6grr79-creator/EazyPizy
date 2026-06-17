import sys
import os
import threading
import time
import io

from PIL import Image
import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import keyboard
    HAS_KEYBOARD = True
except Exception:
    HAS_KEYBOARD = False

from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton, QFileDialog,
    QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
    QListWidget, QFrame, QMessageBox,
    QMenu, QAction, QProgressBar,
    QSizePolicy, QSpinBox, QComboBox,
    QDialog, QDialogButtonBox, QSlider,
    QScrollArea
)
from PyQt5.QtCore import (
    Qt, QPoint, QSize, QPointF, QRectF,
    QPropertyAnimation, QEasingCurve,
    pyqtSignal, pyqtSlot, QThread, QMetaObject, Q_ARG,
    QTimer
)
from PyQt5.QtGui import QColor, QIcon, QPixmap, QCursor, QPainter, QPen, QBrush, QLinearGradient, QPainterPath

from scan_server import ScanServer

from styles import (
    APP_STYLE, BAR_STYLE, MODE_BTN_STYLE, ICON_BTN_STYLE,
    CLOSE_BTN_STYLE, MINIMIZE_BTN_STYLE, KB_PILL_STYLE, KB_INPUT_STYLE,
    ACTION_BTN_STYLE, SECONDARY_BTN_STYLE, HINT_STYLE,
    LIST_FRAME_STYLE, LIST_WIDGET_STYLE, PROGRESS_STYLE,
    MENU_STYLE, SPINBOX_STYLE, COMBO_STYLE, PANEL_STYLE,
    SIDEBAR_STYLE, PDF_TOOL_BTN_STYLE,
    PDF_COMPACT_PANEL_STYLE, PDF_HEADER_STYLE, PDF_SUBTITLE_STYLE
)
from helpers import (
    WIN_W, PANEL_H, CROP_PRESETS,
    make_divider, make_icon_btn, make_wm_btn,
    sep_widget, section_label, spin_col
)
from canvases import CropCanvas, BgCanvas
from pdf_panel import PdfToolPanel
from workers import CompressWorker, BgRemoveWorker, warmup_remover


# =========================================
# COLOR PICKER SUB-WIDGETS
# =========================================

class _SvSquare(QWidget):
    """Saturation-Value 2-D picker square."""
    sv_changed = pyqtSignal(int, int)   # sat 0-255, val 0-255

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0
        self._sat = 255
        self._val = 255
        self._dragging = False
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CrossCursor))

    def set_hue(self, hue):
        self._hue = hue
        self.update()

    def set_sv(self, sat, val):
        self._sat = sat
        self._val = val
        self.update()

    def _sv_to_pos(self):
        w, h = self.width(), self.height()
        x = int(self._sat / 255 * w)
        y = int((1 - self._val / 255) * h)
        return max(0, min(x, w)), max(0, min(y, h))

    def _pos_to_sv(self, x, y):
        w, h = self.width(), self.height()
        s = int(max(0, min(x / w, 1)) * 255)
        v = int(max(0, min(1 - y / h, 1)) * 255)
        return s, v

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        rect = QRectF(0, 0, w, h)

        # Base hue fill (left to right: white → pure hue)
        hue_color = QColor.fromHsv(self._hue, 255, 255)
        sat_grad = QLinearGradient(0, 0, w, 0)
        sat_grad.setColorAt(0.0, QColor(255, 255, 255))
        sat_grad.setColorAt(1.0, hue_color)
        p.fillRect(rect, QBrush(sat_grad))

        # Overlay top→bottom: transparent → black
        val_grad = QLinearGradient(0, 0, 0, h)
        val_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        val_grad.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(rect, QBrush(val_grad))

        # Rounded corners clip
        p.setRenderHint(QPainter.Antialiasing)
        pen_color = QColor(255, 255, 255, 200)

        # Crosshair / circle handle
        cx, cy = self._sv_to_pos()
        # Outer white ring
        p.setPen(QPen(QColor(255, 255, 255, 240), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 7, 7)
        # Inner dark ring for visibility on bright areas
        p.setPen(QPen(QColor(0, 0, 0, 100), 1))
        p.drawEllipse(QPointF(cx, cy), 9, 9)

    def _update_from_pos(self, x, y):
        s, v = self._pos_to_sv(x, y)
        if s != self._sat or v != self._val:
            self._sat, self._val = s, v
            self.update()
            self.sv_changed.emit(s, v)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._update_from_pos(e.x(), e.y())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._update_from_pos(e.x(), e.y())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = False


class _HueBar(QWidget):
    """Horizontal hue rainbow bar."""
    hue_changed = pyqtSignal(int)   # 0-359

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0
        self._dragging = False
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def set_hue(self, hue):
        self._hue = hue
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if w == 0 or h == 0:
            return
        r = 6  # corner radius

        # Rainbow gradient — clamp stop hues to 0-359
        grad = QLinearGradient(0, 0, w, 0)
        for i in range(7):
            hue = min(int(i * 60), 359)   # i=6 → 360 is invalid; clamp to 359
            grad.setColorAt(i / 6, QColor.fromHsv(hue, 255, 255))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, w, h, r, r)

        # Handle circle
        safe_hue = max(0, min(self._hue, 359))
        cx = int(safe_hue / 359 * w)
        cx = max(h // 2, min(cx, w - h // 2))   # keep handle inside bar
        p.setPen(QPen(QColor(255, 255, 255, 240), 2))
        p.setBrush(QColor.fromHsv(safe_hue, 255, 255))
        p.drawEllipse(QPointF(cx, h / 2), h / 2 - 1, h / 2 - 1)
        p.setPen(QPen(QColor(0, 0, 0, 80), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, h / 2), h / 2 + 1, h / 2 + 1)

    def _update_from_x(self, x):
        hue = int(max(0, min(x / max(self.width(), 1), 1)) * 359)
        hue = min(hue, 359)   # hard clamp — QColor.fromHsv rejects 360
        if hue != self._hue:
            self._hue = hue
            self.update()
            self.hue_changed.emit(hue)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._update_from_x(e.x())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._update_from_x(e.x())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = False


class _AlphaBar(QWidget):
    """Horizontal alpha/opacity bar with checkerboard bg."""
    alpha_changed = pyqtSignal(int)   # 0-255

    def __init__(self, parent=None):
        super().__init__(parent)
        self._alpha = 255
        self._color = QColor(255, 255, 255)
        self._dragging = False
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def set_alpha(self, alpha):
        self._alpha = alpha
        self.update()

    def set_color(self, qcolor):
        self._color = qcolor
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = 6

        # Checkerboard bg
        tile = h // 2
        for row in range((h // tile) + 1):
            for col in range((w // tile) + 1):
                c = QColor(180, 180, 180) if (row + col) % 2 == 0 else QColor(255, 255, 255)
                p.fillRect(col * tile, row * tile, tile, tile, c)

        # Color gradient transparent → opaque
        opaque = QColor(self._color.red(), self._color.green(), self._color.blue(), 255)
        transp = QColor(self._color.red(), self._color.green(), self._color.blue(), 0)
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, transp)
        grad.setColorAt(1.0, opaque)
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, w, h, r, r)

        # Handle
        cx = int(self._alpha / 255 * w)
        p.setPen(QPen(QColor(255, 255, 255, 240), 2))
        p.setBrush(opaque)
        p.drawEllipse(QPointF(cx, h / 2), h / 2 - 1, h / 2 - 1)
        p.setPen(QPen(QColor(0, 0, 0, 80), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, h / 2), h / 2 + 1, h / 2 + 1)

    def _update_from_x(self, x):
        alpha = int(max(0, min(x / self.width(), 1)) * 255)
        if alpha != self._alpha:
            self._alpha = alpha
            self.update()
            self.alpha_changed.emit(alpha)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._update_from_x(e.x())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._update_from_x(e.x())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = False


# =========================================
# ROTATION RULER  — scrolling tick-mark scrubber
# =========================================

class RotationRuler(QWidget):
    """
    A horizontal scrubbing ruler used as the crop-tool rotation control.
    Styled like a small measuring-tape ruler: thin tick marks of varying
    length scroll past a fixed brand-coloured centre indicator as the user
    drags left / right, with degree labels at major intervals.
    Intentionally API-compatible with QSlider so that existing handlers
    (_on_rotate_slider, _flip_crop_h, …) work unchanged.
    """
    valueChanged = pyqtSignal(int)   # emitted with int degrees, -180 … 180

    _PPD  = 5.0    # pixels per degree (compact)
    _FADE = 36     # px fade margin at each edge
    _SNAP_POINTS = (-180, -135, -90, -45, 0, 45, 90, 135, 180)
    _SNAP_DEG    = 3.0   # magnet pull radius, in degrees

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value      = 0.0   # float for sub-degree smoothness
        self._range_min  = -180
        self._range_max  =  180
        self._drag_x     = None
        self._drag_val   = 0.0
        self.setFixedHeight(32)
        self.setCursor(QCursor(Qt.SizeHorCursor))
        self.setMouseTracking(True)

    # ── QSlider-compatible API ──────────────────────────────────────────

    def value(self):
        return int(round(self._value))

    def setValue(self, v):
        v = float(max(self._range_min, min(self._range_max, v)))
        if v == self._value:
            return
        old_int    = int(round(self._value))
        self._value = v
        self.update()
        if not self.signalsBlocked() and int(round(v)) != old_int:
            self.valueChanged.emit(int(round(v)))

    def setRange(self, lo, hi):
        self._range_min = lo
        self._range_max = hi

    # ── Paint ───────────────────────────────────────────────────────────

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h   = self.width(), self.height()
        ppd    = self._PPD
        cx     = w / 2.0
        fade   = self._FADE
        mid_y  = h / 2.0

        # ── Horizontal centre track line ──────────────────────────────────
        track_grad = QLinearGradient(0, 0, w, 0)
        track_grad.setColorAt(0.00, QColor(255, 255, 255,  0))
        track_grad.setColorAt(0.15, QColor(255, 255, 255, 22))
        track_grad.setColorAt(0.50, QColor(255, 255, 255, 40))
        track_grad.setColorAt(0.85, QColor(255, 255, 255, 22))
        track_grad.setColorAt(1.00, QColor(255, 255, 255,  0))
        p.setPen(QPen(QBrush(track_grad), 1.0))
        p.drawLine(QPointF(0, mid_y), QPointF(w, mid_y))

        # ── Ticks ─────────────────────────────────────────────────────────
        half = cx / ppd + 3
        lo   = int(self._value - half)
        hi   = int(self._value + half) + 1

        is_snapped = self._value in self._SNAP_POINTS

        for deg in range(lo, hi + 1):
            x = cx + (deg - self._value) * ppd

            dist_edge = min(x, w - x)
            opacity   = min(1.0, dist_edge / fade)
            if opacity <= 0:
                continue

            is_snap_tick = (deg % 45 == 0)
            is_major     = (deg % 15 == 0)
            is_minor5    = (deg % 5  == 0)

            if is_snap_tick:
                tick_len = 18
                color    = QColor(139, 148, 255, int(230 * opacity))  # indigo accent
                width    = 2.5
            elif is_major:
                tick_len = 11
                color    = QColor(255, 255, 255, int(160 * opacity))
                width    = 1.5
            elif is_minor5:
                tick_len = 7
                color    = QColor(255, 255, 255, int(90 * opacity))
                width    = 1.2
            else:
                tick_len = 4
                color    = QColor(255, 255, 255, int(50 * opacity))
                width    = 1.0

            pen = QPen(color, width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x, mid_y - tick_len / 2.0),
                       QPointF(x, mid_y + tick_len / 2.0))

        # ── Edge vignette (left & right fade overlay) ─────────────────────
        for side in (0, 1):
            vg = QLinearGradient(0 if side == 0 else w, 0,
                                 fade * 1.2 if side == 0 else w - fade * 1.2, 0)
            vg.setColorAt(0.0, QColor(30, 30, 34, 200))
            vg.setColorAt(1.0, QColor(30, 30, 34,   0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(vg))
            p.drawRect(0 if side == 0 else int(w - fade * 1.2),
                       0, int(fade * 1.2), h)

        # ── Centre indicator — glowing accent pill ─────────────────────────
        glow_color = QColor(130, 140, 255, 60) if not is_snapped else QColor(100, 220, 150, 70)
        accent     = QColor(180, 190, 255, 255) if not is_snapped else QColor(110, 240, 170, 255)

        # Outer glow
        for gw, ga in ((7, 18), (5, 35), (3, 60)):
            pen = QPen(QColor(glow_color.red(), glow_color.green(),
                              glow_color.blue(), ga), gw)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(cx, mid_y - 12), QPointF(cx, mid_y + 12))

        # Inner bright line
        pen = QPen(accent, 2.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx, mid_y - 13), QPointF(cx, mid_y + 13))


    # ── Mouse interaction ────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_x   = e.x()
            self._drag_val = self._value

    def mouseMoveEvent(self, e):
        if self._drag_x is None:
            return
        dx       = e.x() - self._drag_x
        new_val  = self._drag_val - dx / self._PPD
        new_val  = max(self._range_min, min(self._range_max, new_val))

        # Magnet effect — snap toward 0° / ±90° / ±180° when close
        for snap in self._SNAP_POINTS:
            if abs(new_val - snap) <= self._SNAP_DEG:
                new_val = float(snap)
                break

        # Only repaint + signal when position changed by at least 0.5 px
        if abs(new_val - self._value) < 0.5 / self._PPD:
            return
        old_int     = int(round(self._value))
        self._value = new_val
        self.update()
        new_int = int(round(new_val))
        if new_int != old_int and not self.signalsBlocked():
            self.valueChanged.emit(new_int)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_x = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setValue(0)


# =========================================
# MAIN WINDOW
# =========================================

class ImageCompressor(QWidget):

    toggle_signal  = pyqtSignal()
    MODE_COMPRESS = "compress"
    MODE_CROP     = "crop"
    MODE_BGREMOVE = "bgremove"
    MODE_PDF      = "pdf"

    PDF_TOOLS = [
        ("img2pdf",  "IMG → PDF",  "assets/icons/img2pdf.png"),
        ("pdf2img",  "PDF → IMG",  "assets/icons/pdf2img.png"),
        ("merge",    "Merge",      "assets/icons/merge.png"),
        ("split",    "Split",      "assets/icons/split.png"),
        ("compress", "Compress",   "assets/icons/compresspdf.png"),
        ("organize", "Organize",   "assets/icons/organizepdf.png"),
        ("protect",  "Protect",    "assets/icons/protect.png"),
    ]

    def __init__(self):
        super().__init__()
        self.files         = []
        self.mode          = self.MODE_COMPRESS
        self.crop_path     = None
        self.bgremove_path = None
        self.oldPos        = QPoint()
        self._anim         = None
        self._thread       = None
        self._worker       = None
        self._bg_thread    = None
        self._bg_worker    = None
        self._bgremove_result = None   # cached (pil_rgba, src_rgb) when result arrives off-mode
        self._syncing      = False
        self._active_pdf_tool = None
        self._scan_server     = None
        self._scan_dlg        = None
        self._scan_temps      = set()   # paths of scanned files not yet saved
        self._tray            = []   # [{"path": str, "type": "image"|"pdf"}]
        self._bg_sidebar_rel  = None   # QPoint: sidebar pos relative to main window top-left
        self._drag_reset_sidebar = False  # True once sidebar has been reset for current drag
        self.setup_ui()
        self.hide()
        self.toggle_signal.connect(self.toggle_visibility)
        self.bg_remover = None
        # Delay warmup by 5s so it does not compete with app launch or first Insert show
        QTimer.singleShot(5000, lambda: threading.Thread(target=warmup_remover, daemon=True).start())

        if HAS_KEYBOARD:
            def _listen():
                try:
                    def _on_key(e):
                        # Only trigger on real Insert key, not Numpad 0
                        # e.name == "insert" and is_keypad=False distinguishes them
                        if (e.event_type == "down"
                                and e.name == "insert"
                                and not getattr(e, "is_keypad", False)):
                            self.toggle_signal.emit()
                    keyboard.hook(_on_key)
                    keyboard.wait()
                except Exception:
                    pass
            threading.Thread(target=_listen, daemon=True).start()

        # Auto-start scan server immediately on launch
        self._scan_server = ScanServer(
            on_file_received=self._scan_callback,
            save_dir=self._scan_tmp_dir(),
        )
        self._scan_server.start()
        self._update_scan_btn_tooltip()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
            if hasattr(self, '_bg_sidebar'):
                self._bg_sidebar.hide()
        else:
            self.show()
            self.activateWindow()
            self.raise_()
            if self.bgremove_panel.isVisible():
                QTimer.singleShot(50, self._show_bg_sidebar)

    # ------------------------------------------------------------------
    # UI SETUP
    # ------------------------------------------------------------------
    def setup_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        scr = QApplication.primaryScreen().geometry()
        self.setGeometry((scr.width() - WIN_W) // 2, 4, WIN_W, 90)
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_STYLE)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(10)

        self._build_bar()
        self._build_tray()
        self._build_hint()
        self._build_progress()
        self._build_compress_list()
        self._build_crop_panel()
        self._build_pdf_tool_panels()

        QApplication.processEvents()
        self.resize(WIN_W, self.bar.sizeHint().height() or 80)

    def _build_bar(self):
        self.bar = QFrame()
        self.bar.setObjectName("bar")
        self.bar.setFixedHeight(80)
        self.bar.setStyleSheet(BAR_STYLE)

        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(12, 0, 12, 0)
        bl.setSpacing(6)

        # Logo
        self.logo = QLabel()
        self.logo.setFixedSize(50, 50)
        px = QPixmap("assets/icons/logo.png")
        if not px.isNull():
            self.logo.setPixmap(px.scaled(50, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.logo.setStyleSheet("border-radius:14px;")
        else:
            self.logo.setText("✦")
            self.logo.setAlignment(Qt.AlignCenter)
            self.logo.setStyleSheet("background:#5865F2; border-radius:12px; color:white; font-size:18px;")
        bl.addWidget(self.logo)
        bl.addWidget(make_divider())

        # Mode pill buttons
        self._mode_btns = {}
        modes = [
            (self.MODE_COMPRESS, "Compress",  "assets/icons/compress.png"),
            (self.MODE_CROP,     "Crop",      "assets/icons/crop.png"),
            (self.MODE_BGREMOVE, "BG Remove", "assets/icons/bgremove.png"),
            (self.MODE_PDF,      "PDF",       "assets/icons/pdf.png"),
        ]
        for mode_id, label, icon_path in modes:
            btn = QPushButton()
            btn.setToolTip(label)
            btn.setFixedSize(48, 48)
            btn.setCheckable(True)
            btn.setStyleSheet(MODE_BTN_STYLE)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            if os.path.exists(icon_path):
                btn.setIcon(QIcon(icon_path))
                btn.setIconSize(QSize(28, 28))
            else:
                btn.setText(label[0])
            btn.clicked.connect(lambda checked, m=mode_id: self._switch_mode(m))
            bl.addWidget(btn)
            self._mode_btns[mode_id] = btn
        self._mode_btns[self.MODE_COMPRESS].setChecked(True)

        bl.addWidget(make_divider())

        # Compress controls
        self.compress_controls = QFrame()
        self.compress_controls.setStyleSheet("background:transparent; border:none;")
        ccl = QHBoxLayout(self.compress_controls)
        ccl.setContentsMargins(0, 0, 0, 0)
        ccl.setSpacing(5)
        kb_pill = QFrame()
        kb_pill.setObjectName("kbPill")
        kb_pill.setFixedHeight(48)
        kb_pill.setStyleSheet(KB_PILL_STYLE)
        kbl = QHBoxLayout(kb_pill)
        kbl.setContentsMargins(10, 0, 12, 0)
        kbl.setSpacing(5)
        ki = QLabel("⌖")
        ki.setStyleSheet("color:rgba(255,255,255,0.30); font-size:14px;")
        kbl.addWidget(ki)
        self.kb_input = QLineEdit()
        self.kb_input.setPlaceholderText("Target KB")
        self.kb_input.setFixedWidth(65)
        self.kb_input.setAlignment(Qt.AlignCenter)
        self.kb_input.setStyleSheet(KB_INPUT_STYLE)
        kbl.addWidget(self.kb_input)
        kl = QLabel("KB")
        kl.setStyleSheet("color:rgba(255,255,255,0.30); font-size:12px;")
        kbl.addWidget(kl)
        ccl.addWidget(kb_pill)
        ccl.addWidget(make_divider())
        self.compress_btn = QPushButton("⚡  Compress")
        self.compress_btn.setFixedHeight(48)
        self.compress_btn.setMinimumWidth(120)
        self.compress_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.compress_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.compress_btn.clicked.connect(self.compress_all)
        ccl.addWidget(self.compress_btn)
        bl.addWidget(self.compress_controls)

        # Hidden aspect ratio combo — not shown in bar but drives _on_bar_ratio logic
        self.bar_ratio_combo = QComboBox()
        self.bar_ratio_combo.hide()
        for label, ratio, preset in CROP_PRESETS:
            if ratio == "sep":
                self.bar_ratio_combo.insertSeparator(self.bar_ratio_combo.count())
            else:
                self.bar_ratio_combo.addItem(label)
                idx = self.bar_ratio_combo.count() - 1
                self.bar_ratio_combo.setItemData(idx, (ratio, preset))
        self.bar_ratio_combo.currentIndexChanged.connect(self._on_bar_ratio)

        # Crop bar controls
        self.crop_bar_controls = QFrame()
        self.crop_bar_controls.setStyleSheet("background:transparent; border:none;")
        cbl = QHBoxLayout(self.crop_bar_controls)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.setSpacing(5)
        self.crop_save_btn = QPushButton("Crop")
        self.crop_save_btn.setFixedHeight(48)
        self.crop_save_btn.setMinimumWidth(130)
        self.crop_save_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.crop_save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.crop_save_btn.clicked.connect(self._do_crop_save)
        cbl.addWidget(self.crop_save_btn)
        self.crop_bar_controls.hide()
        bl.addWidget(self.crop_bar_controls)

        # BG Remove bar controls
        self.bgremove_bar_controls = QFrame()
        self.bgremove_bar_controls.setStyleSheet("background:transparent; border:none;")
        bgl = QHBoxLayout(self.bgremove_bar_controls)
        bgl.setContentsMargins(0, 0, 0, 0)
        bgl.setSpacing(5)
        self.bgremove_save_btn = QPushButton("💾  Save Result")
        self.bgremove_save_btn.setFixedHeight(48)
        self.bgremove_save_btn.setMinimumWidth(130)
        self.bgremove_save_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.bgremove_save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.bgremove_save_btn.clicked.connect(self._bgremove_save)
        bgl.addWidget(self.bgremove_save_btn)
        self.bgremove_bar_controls.hide()
        bl.addWidget(self.bgremove_bar_controls)

        # PDF tool buttons
        self.pdf_bar_controls = QFrame()
        self.pdf_bar_controls.setStyleSheet("background:transparent; border:none;")
        pbl = QHBoxLayout(self.pdf_bar_controls)
        pbl.setContentsMargins(0, 0, 0, 0)
        pbl.setSpacing(4)

        self._pdf_tool_btns = {}
        for tool_id, label, icon_path in self.PDF_TOOLS:
            btn = QPushButton()
            btn.setToolTip(label)
            btn.setFixedSize(48, 48)
            btn.setCheckable(True)
            btn.setStyleSheet(PDF_TOOL_BTN_STYLE)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            if os.path.exists(icon_path):
                btn.setIcon(QIcon(icon_path))
                btn.setIconSize(QSize(28, 28))
            else:
                btn.setText(label[0])
            btn.clicked.connect(lambda checked, tid=tool_id: self._on_pdf_tool_btn(tid))
            pbl.addWidget(btn)
            self._pdf_tool_btns[tool_id] = btn

        self.pdf_bar_controls.hide()
        bl.addWidget(self.pdf_bar_controls)

        bl.addSpacing(8)

        # Right-side icon buttons
        bl.addWidget(make_divider())
        self.scan_btn = make_icon_btn("assets/icons/scan.png", "Scan from Phone", "📷", size=48)
        # Hovering shows QR flyout; clicking does nothing extra
        self.scan_btn.installEventFilter(self)
        bl.addWidget(self.scan_btn)

        # Files button — hidden until files are loaded, shows dropdown
        self.files_btn = QPushButton("Files  0")
        self.files_btn.setFixedHeight(48)
        self.files_btn.setMinimumWidth(80)
        self.files_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
                color: rgba(255,255,255,0.70);
                font-size: 13px;
                font-weight: 600;
                padding: 0px 14px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.10); color: white; }
            QPushButton:pressed { background: rgba(255,255,255,0.14); }
        """)
        self.files_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.files_btn.clicked.connect(self._show_files_menu)
        self.files_btn.hide()
        bl.addWidget(self.files_btn)

        self.add_btn = make_icon_btn("assets/icons/add.png", "Add image", "＋", size=48)
        self.add_btn.clicked.connect(self.add_files)
        bl.addWidget(self.add_btn)
        self.folder_btn = make_icon_btn("assets/icons/folder.png", "Open output folder", "📂", size=48)
        self.folder_btn.clicked.connect(self.open_output_folder)
        bl.addWidget(self.folder_btn)
        self.clear_btn = make_icon_btn("assets/icons/trash.png", "Clear", "🗑", size=48)
        self.clear_btn.clicked.connect(self.clear_files)
        bl.addWidget(self.clear_btn)

        self.bar.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, 0)
        bar_row.setSpacing(0)
        bar_row.addStretch(1)
        bar_row.addWidget(self.bar)
        bar_row.addStretch(1)
        self._outer.addLayout(bar_row)

    def _build_hint(self):
        self.hint = QLabel("Drop images anywhere  ·  0 files loaded")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet(HINT_STYLE)
        self._outer.addWidget(self.hint)

    def _build_tray(self):
        pass  # tray is now the files_btn dropdown in the toolbar

    def _refresh_tray(self):
        """Update the Files button label/visibility."""
        n = len(self._tray)
        if n:
            self.files_btn.setText(f"🗂  Files  {n}")
            self.files_btn.show()
        else:
            self.files_btn.hide()

    def _show_files_menu(self):
        """Show a dropdown menu listing all tray files with send/remove actions."""
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)

        if not self._tray:
            empty = QAction("No files loaded", self)
            empty.setEnabled(False)
            menu.addAction(empty)
        else:
            for i, entry in enumerate(self._tray):
                path = entry["path"]
                ftype = entry["type"]
                name = os.path.basename(path)
                short = name if len(name) <= 30 else name[:27] + "…"
                kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
                icon = "🖼" if ftype == "image" else "📄"

                # File header action (not clickable, just label)
                file_action = QAction(f"{icon}  {short}   ·   {kb:.0f} KB", self)
                file_action.triggered.connect(
                    lambda _, p=path, t=ftype: self._tray_send(p, t))
                menu.addAction(file_action)

                remove_action = QAction(f"    ✕  Remove", self)
                remove_action.triggered.connect(lambda _, idx=i: self._tray_remove(idx))
                menu.addAction(remove_action)

                if i < len(self._tray) - 1:
                    menu.addSeparator()

            menu.addSeparator()
            clear_action = QAction("🗑  Clear all files", self)
            clear_action.triggered.connect(self._tray_clear_all)
            menu.addAction(clear_action)

        # Show below the button, aligned to its left edge
        btn_pos = self.files_btn.mapToGlobal(
            self.files_btn.rect().bottomLeft())
        menu.exec_(btn_pos)

    def _tray_add(self, path: str):
        """Add a file to the tray if not already present, then route it."""
        ext = os.path.splitext(path)[1].lower()
        ftype = "pdf" if ext == ".pdf" else "image"
        if not any(e["path"] == path for e in self._tray):
            self._tray.append({"path": path, "type": ftype})
        self._refresh_tray()
        self._tray_send(path, ftype)

    def _tray_remove(self, idx: int):
        if 0 <= idx < len(self._tray):
            removed = self._tray.pop(idx)
            path = removed["path"]
            if path in self.files:
                row = self.files.index(path)
                self.files.remove(path)
                self.file_list.takeItem(row)
                self.update_hint()
        self._refresh_tray()
        if not self._tray:
            self._tray_clear_all()

    def _tray_clear_all(self):
        self._tray.clear()
        self._refresh_tray()
        # reset everything without touching tray again
        self.files = []
        self.crop_path = None
        self.bgremove_path = None
        self._bgremove_result = None
        self.file_list.clear()
        self.crop_panel.hide()
        self.bgremove_panel.hide()
        self._bg_sidebar.hide()
        self.list_frame.hide()
        for p in self._pdf_panels.values():
            p.hide()
        self.hint.show()
        self.update_hint()
        self._animate_size(self.bar.height() or 80)

    def _tray_send(self, path: str, ftype: str):
        """Route a tray file to whatever tool is currently active."""
        if self.mode == self.MODE_COMPRESS and ftype == "image":
            if path not in self.files:
                self.files.append(path)
                kb = os.path.getsize(path) / 1024
                self.file_list.addItem(f"○  {os.path.basename(path)}   ·   {kb:.0f} KB")
                self.update_hint()
            self._show_compress_list()
        elif self.mode == self.MODE_CROP and ftype == "image":
            self._load_crop_image(path)
        elif self.mode == self.MODE_BGREMOVE and ftype == "image":
            # Ignore if a removal is already in progress
            if self._bg_thread_is_running():
                return
            self._bgremove_result = None   # discard any cached result for the old image
            self._load_bgremove(path)
        elif self.mode == self.MODE_PDF and self._active_pdf_tool:
            self._pdf_panels[self._active_pdf_tool].add_files([path])

    def _build_progress(self):
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(PROGRESS_STYLE)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self._outer.addWidget(self.progress_bar)

    def _build_compress_list(self):
        self.list_frame = QFrame()
        self.list_frame.setObjectName("listFrame")
        self.list_frame.setStyleSheet(LIST_FRAME_STYLE)
        self.list_frame.hide()
        lfl = QVBoxLayout(self.list_frame)
        lfl.setContentsMargins(0, 0, 0, 0)
        lfl.setSpacing(0)
        header = QFrame()
        header.setStyleSheet("border-bottom:1px solid rgba(255,255,255,0.06);")
        header.setFixedHeight(32)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 20, 0)
        for text, align, fw in [
            ("File", Qt.AlignLeft, None), ("Original", Qt.AlignRight, 80),
            ("Result", Qt.AlignRight, 90), ("Saved", Qt.AlignRight, 60)
        ]:
            l = QLabel(text)
            l.setStyleSheet("color:rgba(255,255,255,0.25); font-size:11px;")
            l.setAlignment(align)
            if fw:
                l.setFixedWidth(fw)
                hl.addWidget(l, 0)
            else:
                hl.addWidget(l, 1)
        lfl.addWidget(header)
        self.file_list = QListWidget()
        self.file_list.setStyleSheet(LIST_WIDGET_STYLE)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._compress_item_menu)
        lfl.addWidget(self.file_list)
        self._outer.addWidget(self.list_frame)

    def _build_crop_panel(self):
        self.crop_panel = QFrame()
        self.crop_panel.setObjectName("panel")
        self.crop_panel.setStyleSheet(PANEL_STYLE)
        self.crop_panel.setFixedHeight(PANEL_H)
        self.crop_panel.setFixedWidth(WIN_W)
        self.crop_panel.hide()

        # Outer: canvas on top, bottom toolbar below
        layout = QVBoxLayout(self.crop_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Active preset tracking
        self._active_preset = None

        # ── Canvas (centred, fixed width to match bg-remover aesthetics) ──
        canvas_row = QHBoxLayout()
        canvas_row.setContentsMargins(0, 0, 0, 0)
        canvas_row.setSpacing(0)
        self.crop_canvas = CropCanvas()
        self.crop_canvas.setFixedWidth(WIN_W)
        self.crop_canvas.crop_changed.connect(self._on_crop_changed)
        self.crop_canvas.setToolTip(
            "Drag the image or use the arrow keys to move image in crop region"
        )
        canvas_row.addStretch(1)
        canvas_row.addWidget(self.crop_canvas)
        canvas_row.addStretch(1)
        layout.addLayout(canvas_row, 1)

        # ── Bottom toolbar ──────────────────────────────────────────────
        bottom = QFrame()
        bottom.setFixedHeight(122)
        bottom.setStyleSheet(
            "background: transparent;"
            "border-top: 1px solid rgba(255,255,255,0.06);"
        )
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(6, 8, 6, 10)
        bl.setSpacing(4)

        # Angle label centred above the ruler
        self.rotate_angle_lbl = QLabel("0 °")
        self.rotate_angle_lbl.setAlignment(Qt.AlignCenter)
        self.rotate_angle_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.80); font-size: 13px;"
            "font-weight: 600; border: none;"
        )
        bl.addWidget(self.rotate_angle_lbl)

        # Rotation ruler
        self.rotate_slider = RotationRuler()
        self.rotate_slider.setRange(-180, 180)
        self.rotate_slider.valueChanged.connect(self._on_rotate_slider)
        bl.addWidget(self.rotate_slider)

        # ── Action-button row ───────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        # Left: Rotate CCW / CW
        _icon_btn_style = """
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
            }
            QPushButton:hover  { background: rgba(255,255,255,0.12); }
            QPushButton:pressed{ background: rgba(255,255,255,0.18); }
        """
        _lbl_style = "color: rgba(255,255,255,0.38); font-size: 9px; border: none;"

        def _btn_col(symbol, label_text, size=20):
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            col.setAlignment(Qt.AlignHCenter)
            btn = QPushButton(symbol)
            btn.setFixedSize(40, 40)
            btn.setStyleSheet(_icon_btn_style + f"QPushButton {{ font-size: {size}px; color: rgba(255,255,255,0.80); }}")
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            col.addWidget(btn)
            return col, btn

        ccw_col, self.rot90_ccw_btn = _btn_col("↺", "CCW")
        self.rot90_ccw_btn.setToolTip("Rotate 90° counter-clockwise")
        self.rot90_ccw_btn.clicked.connect(self._rotate_90_ccw)
        btn_row.addLayout(ccw_col)

        cw_col, self.rot90_cw_btn = _btn_col("↻", "CW")
        self.rot90_cw_btn.setToolTip("Rotate 90° clockwise")
        self.rot90_cw_btn.clicked.connect(self._rotate_90_cw)
        btn_row.addLayout(cw_col)

        btn_row.addStretch(1)

        # Centre: ratio selector button + auto-crop button (side by side)
        _centre_row = QHBoxLayout()
        _centre_row.setContentsMargins(0, 0, 0, 0)
        _centre_row.setSpacing(6)

        self._ratio_btn = QPushButton()
        self._ratio_btn.setFixedHeight(40)
        self._ratio_btn.setMinimumWidth(110)
        _ratio_icon = os.path.join("assets", "icons", "crop.png")
        if os.path.exists(_ratio_icon):
            self._ratio_btn.setIcon(QIcon(_ratio_icon))
            self._ratio_btn.setIconSize(QSize(18, 18))
        self._ratio_btn.setText("  Free")
        self._ratio_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                color: rgba(255,255,255,0.75);
                font-size: 13px;
                font-weight: 500;
                padding: 0px 14px;
            }
            QPushButton:hover  { background: rgba(255,255,255,0.10); color: white; }
            QPushButton:pressed{ background: rgba(255,255,255,0.14); }
        """)
        self._ratio_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._ratio_btn.clicked.connect(self._show_ratio_menu)
        _centre_row.addWidget(self._ratio_btn)

        self.auto_crop_btn = QPushButton()
        self.auto_crop_btn.setFixedHeight(40)
        self.auto_crop_btn.setMinimumWidth(100)
        self.auto_crop_btn.setToolTip("Auto-detect subject and position crop box")
        _auto_crop_icon = os.path.join("assets", "icons", "auto_crop.png")
        if os.path.exists(_auto_crop_icon):
            self.auto_crop_btn.setIcon(QIcon(_auto_crop_icon))
            self.auto_crop_btn.setIconSize(QSize(18, 18))
        self.auto_crop_btn.setText("  Auto Crop")
        self.auto_crop_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                color: rgba(255,255,255,0.75);
                font-size: 13px;
                font-weight: 500;
                padding: 0px 14px;
            }
            QPushButton:hover  { background: rgba(255,255,255,0.10); color: white; }
            QPushButton:pressed{ background: rgba(255,255,255,0.14); }
        """)
        self.auto_crop_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.auto_crop_btn.clicked.connect(self._auto_crop)
        _centre_row.addWidget(self.auto_crop_btn)

        btn_row.addLayout(_centre_row)

        btn_row.addStretch(1)

        # Right: Flip H / V
        fliph_col, self.flip_h_btn = _btn_col("⇆", "Mirror", 18)
        self.flip_h_btn.setToolTip("Flip horizontally (mirror)")
        self.flip_h_btn.clicked.connect(self._flip_crop_h)
        btn_row.addLayout(fliph_col)

        flipv_col, self.flip_v_btn = _btn_col("⇅", "Invert", 18)
        self.flip_v_btn.setToolTip("Flip vertically")
        self.flip_v_btn.clicked.connect(self._flip_crop_v)
        btn_row.addLayout(flipv_col)

        bl.addLayout(btn_row)
        layout.addWidget(bottom)

        # ── Hidden/compat widgets (handlers still reference these) ──────
        # crop_img_info is updated in several handlers — keep it alive but invisible
        self.crop_img_info = QLabel("")
        self.crop_img_info.hide()
        # rotate_reset_btn — double-click the ruler resets; keep a hidden stub
        self.rotate_reset_btn = QPushButton()
        self.rotate_reset_btn.hide()
        self.rotate_reset_btn.clicked.connect(self._reset_rotation)

        # ── Custom ratio spinboxes (shown as popup via _show_ratio_menu) ─
        self.custom_ratio_frame = QFrame()
        self.custom_ratio_frame.setStyleSheet("background:transparent; border:none;")
        cfl = QHBoxLayout(self.custom_ratio_frame)
        cfl.setContentsMargins(0, 0, 0, 0)
        cfl.setSpacing(4)
        self.ratio_w = QSpinBox()
        self.ratio_w.setRange(1, 99)
        self.ratio_w.setValue(16)
        self.ratio_h = QSpinBox()
        self.ratio_h.setRange(1, 99)
        self.ratio_h.setValue(9)
        self.ratio_w.setStyleSheet(SPINBOX_STYLE)
        self.ratio_h.setStyleSheet(SPINBOX_STYLE)
        colon = QLabel("×")
        colon.setStyleSheet("border:none; color:rgba(255,255,255,0.4); font-size:13px;")
        cfl.addWidget(self.ratio_w)
        cfl.addWidget(colon)
        cfl.addWidget(self.ratio_h)
        self.ratio_w.valueChanged.connect(self._apply_custom_ratio)
        self.ratio_h.valueChanged.connect(self._apply_custom_ratio)
        self.custom_ratio_frame.hide()

        # Auto-crop button is now visible in btn_row beside _ratio_btn

        crop_row = QHBoxLayout()
        crop_row.setContentsMargins(0, 0, 0, 0)
        crop_row.setSpacing(0)
        crop_row.addStretch(1)
        crop_row.addWidget(self.crop_panel)
        crop_row.addStretch(1)
        self._outer.addLayout(crop_row)


        self.bgremove_panel = QFrame()
        self.bgremove_panel.setObjectName("panel")
        self.bgremove_panel.setStyleSheet(PANEL_STYLE)
        self.bgremove_panel.setFixedHeight(PANEL_H)
        self.bgremove_panel.hide()
        layout = QHBoxLayout(self.bgremove_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.bg_canvas = BgCanvas()
        layout.addWidget(self.bg_canvas, 1)   # canvas now fills full width

        # ================================================================
        # SIDEBAR — child widget overlay (no separate OS window)
        # ================================================================
        from PyQt5.QtGui import QPainter as _QP, QBrush as _QB, QColor as _QC, QPen as _QPen, QPainterPath as _QPPath

        class _Sidebar(QWidget):
            def __init__(self, on_moved=None):
                # True top-level OS window — frameless, always-on-top, no taskbar entry
                super().__init__(None)
                self.setWindowFlags(
                    Qt.FramelessWindowHint |
                    Qt.WindowStaysOnTopHint |
                    Qt.Tool
                )
                self.setAttribute(Qt.WA_TranslucentBackground)
                self._drag_pos = None
                self._on_moved = on_moved   # called with new QPoint when user finishes drag

            def paintEvent(self, ev):
                painter = _QP(self)
                painter.setRenderHint(_QP.Antialiasing)
                path = _QPPath()
                path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 14, 14)
                painter.fillPath(path, _QB(_QC(0x26, 0x26, 0x2b)))
                pen = _QPen(_QC(255, 255, 255, 22))
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawPath(path)

            def mousePressEvent(self, e):
                if e.button() == Qt.LeftButton:
                    # Store offset of click relative to window global top-left
                    self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
                e.accept()

            def mouseMoveEvent(self, e):
                if self._drag_pos is not None and e.buttons() == Qt.LeftButton:
                    self.move(e.globalPos() - self._drag_pos)
                e.accept()

            def mouseReleaseEvent(self, e):
                self._drag_pos = None
                # Notify main window of new position so moveEvent can keep tracking
                if self._on_moved:
                    self._on_moved(self.pos())
                e.accept()

        def _sidebar_moved(new_global_pos):
            """Store sidebar pos relative to main window so moveEvent can track it."""
            self._bg_sidebar_rel = QPoint(
                new_global_pos.x() - self.x(),
                new_global_pos.y() - self.y(),
            )

        sb = _Sidebar(on_moved=_sidebar_moved)

        # Lock sidebar size permanently
        sb.setFixedSize(280, PANEL_H - 16)

        # Prevent Qt from auto-resizing when tab content changes
        sb.setMinimumSize(280, PANEL_H - 16)
        sb.setMaximumSize(280, PANEL_H - 16)
        
        sb.setStyleSheet("""
            QWidget   { background: transparent; color: white; font-family: 'Segoe UI', sans-serif; }
            QLabel    { background: transparent; }
            QFrame    { background: transparent; }
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: transparent; width: 4px; margin: 0; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.20); border-radius: 2px; min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        sb.hide()
        self._bg_sidebar = sb

        sbl = QVBoxLayout(sb)
        sbl.setContentsMargins(12, 10, 12, 10)
        sbl.setSpacing(4)

        # ---- 4-tab row ----
        TAB_STYLE = """
            QPushButton {
                background: transparent; border: none;
                border-bottom: 2px solid transparent;
                color: rgba(255,255,255,0.40);
                font-size: 11px; font-weight: 600; padding: 0px 6px;
            }
            QPushButton:checked { color: white; border-bottom: 2px solid #5865F2; }
            QPushButton:hover   { color: rgba(255,255,255,0.75); }
        """
        tab_row = QHBoxLayout()
        tab_row.setSpacing(0)
        self._bg_tab_swatches = QPushButton("Swatches")
        self._bg_tab_custom   = QPushButton("Custom")
        for tb in (self._bg_tab_swatches, self._bg_tab_custom):
            tb.setFixedHeight(30)
            tb.setCheckable(True)
            tb.setStyleSheet(TAB_STYLE)
            tb.setCursor(QCursor(Qt.PointingHandCursor))
        self._bg_tab_swatches.setChecked(True)
        self._bg_tab_swatches.clicked.connect(lambda: self._switch_bg_tab("swatches"))
        self._bg_tab_custom.clicked.connect(lambda:   self._switch_bg_tab("custom"))
        for tb in (self._bg_tab_swatches, self._bg_tab_custom):
            tab_row.addWidget(tb)
        tab_row.addStretch()
        sbl.addLayout(tab_row)
        sbl.addWidget(sep_widget())

        # =======================================================
        # SWATCHES PAGE — scrollable, equal-height solid + gradient boxes
        # =======================================================
        from PyQt5.QtWidgets import QScrollArea

        self._bg_swatches_page = QFrame()
        self._bg_swatches_page.setStyleSheet("background:transparent; border:none;")
        swp_outer = QVBoxLayout(self._bg_swatches_page)
        swp_outer.setContentsMargins(0, 0, 0, 0)
        swp_outer.setSpacing(0)

        # Scroll area so content is never squished or stretched.
        # Subclassed to ignore wheel events that Qt spuriously delivers to the
        # floating sidebar while the *main* window is being dragged — without
        # this the scroll position drifts downward during every drag gesture.
        class _NoSpuriousScroll(QScrollArea):
            def wheelEvent(self, e):
                # Only scroll when the mouse pointer is genuinely inside this
                # widget (i.e. the user is intentionally scrolling the sidebar).
                # During a main-window drag the cursor is nowhere near the
                # sidebar, so we simply discard those phantom events.
                if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
                    super().wheelEvent(e)
                else:
                    e.ignore()

        scroll = _NoSpuriousScroll()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent; width: 4px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.18); border-radius: 2px; min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        scroll_content = QFrame()
        scroll_content.setStyleSheet("background:transparent; border:none;")
        swp_layout = QVBoxLayout(scroll_content)
        swp_layout.setContentsMargins(0, 0, 6, 4)
        swp_layout.setSpacing(3)
        swp_layout.setSizeConstraint(QVBoxLayout.SetMinimumSize)

        # ---- SOLID section ----
        solid_lbl = QLabel("SOLID")
        solid_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.28); font-size:10px; letter-spacing:1px; border:none;")
        swp_layout.addWidget(solid_lbl)

        self._swatch_btns = []
        COLS = 4; BOX = 58; GAP = 5
        SOLID_VISIBLE_ROWS = 3          # rows shown before "Show more"
        SOLID_VISIBLE = COLS * SOLID_VISIBLE_ROWS   # 12 slots; 11 swatches + show-more btn

        SHOW_MORE_BTN_STYLE = """
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 9px;
                color: rgba(255,255,255,0.55);
                font-size: 10px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.12); color: white; }
            QPushButton:pressed { background: rgba(255,255,255,0.18); }
        """

        def _make_row_widget():
            """Return a QFrame whose layout is a tight QHBoxLayout row."""
            rw = QFrame()
            rw.setStyleSheet("background:transparent; border:none;")
            rl = QHBoxLayout(rw)
            rl.setSpacing(GAP)
            rl.setContentsMargins(0, 0, 0, 0)
            return rw, rl

        def _add_solid_swatch(name, color, target_layout):
            btn = QPushButton()
            btn.setFixedSize(BOX, BOX)
            btn.setToolTip(name)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setCheckable(True)
            if color is None:
                btn.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                            stop:0 #3a3a3f,stop:0.49 #3a3a3f,
                            stop:0.5 #2e2e33,stop:1 #2e2e33);
                        border: 2px solid rgba(255,255,255,0.12); border-radius: 9px;
                    }
                    QPushButton:checked { border: 3px solid #5865F2; }
                    QPushButton:hover   { border: 2px solid rgba(255,255,255,0.40); }
                """)
            else:
                r2, g2, b2 = color.red(), color.green(), color.blue()
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgb({r2},{g2},{b2});
                        border: 2px solid rgba(255,255,255,0.08); border-radius: 9px;
                    }}
                    QPushButton:checked {{ border: 3px solid #5865F2; }}
                    QPushButton:hover   {{ border: 2px solid rgba(255,255,255,0.50); }}
                """)
            btn.clicked.connect(lambda checked, c=color, b=btn: self._on_swatch(c, b))
            target_layout.addWidget(btn)
            self._swatch_btns.append(btn)

        solid_frame = QFrame()
        solid_frame.setStyleSheet("background:transparent; border:none;")
        solid_grid = QVBoxLayout(solid_frame)
        solid_grid.setSpacing(GAP)
        solid_grid.setContentsMargins(0, 0, 0, 0)

        self._solid_extra_rows = []
        self._solid_expanded   = False

        all_solid   = list(BgCanvas.SWATCHES)
        total_solid = len(all_solid)
        INITIAL_SOLID = COLS * SOLID_VISIBLE_ROWS - 1   # last slot reserved for toggle btn

        # --- Visible rows (all as QFrame so spacing is identical to hidden rows) ---
        cur_rw = cur_rl = None
        for i in range(INITIAL_SOLID):
            if i % COLS == 0:
                cur_rw, cur_rl = _make_row_widget()
                solid_grid.addWidget(cur_rw)
            name, color = all_solid[i]
            _add_solid_swatch(name, color, cur_rl)

        # Toggle button goes in the last slot of the last visible row
        self._solid_toggle_btn = QPushButton(f"+{total_solid - INITIAL_SOLID}")
        self._solid_toggle_btn.setFixedSize(BOX, BOX)
        self._solid_toggle_btn.setToolTip("Show all solid colours")
        self._solid_toggle_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._solid_toggle_btn.setStyleSheet(SHOW_MORE_BTN_STYLE)
        cur_rl.addWidget(self._solid_toggle_btn)

        # --- Extra rows (hidden by default) ---
        for offset, i in enumerate(range(INITIAL_SOLID, total_solid)):
            if offset % COLS == 0:
                cur_rw, cur_rl = _make_row_widget()
                cur_rw.hide()
                solid_grid.addWidget(cur_rw)
                self._solid_extra_rows.append(cur_rw)
            name, color = all_solid[i]
            _add_solid_swatch(name, color, cur_rl)
        # Pad last extra row so swatches stay left-aligned
        if cur_rl is not None:
            remainder_extra = (total_solid - INITIAL_SOLID) % COLS
            if remainder_extra:
                for _ in range(COLS - remainder_extra):
                    cur_rl.addStretch()

        def _toggle_solid():
            self._solid_expanded = not self._solid_expanded
            for w in self._solid_extra_rows:
                w.setVisible(self._solid_expanded)
            remaining = total_solid - INITIAL_SOLID
            if self._solid_expanded:
                self._solid_toggle_btn.setText("▲")
                self._solid_toggle_btn.setToolTip("Show fewer")
            else:
                self._solid_toggle_btn.setText(f"+{remaining}")
                self._solid_toggle_btn.setToolTip("Show all solid colours")

        self._solid_toggle_btn.clicked.connect(_toggle_solid)
        swp_layout.addWidget(solid_frame)

        # ---- Divider ----
        swp_layout.addSpacing(4)
        swp_layout.addWidget(sep_widget())
        swp_layout.addSpacing(2)

        # ---- GRADIENTS section ----
        grad_lbl = QLabel("GRADIENTS")
        grad_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.28); font-size:10px; letter-spacing:1px; border:none;")
        swp_layout.addWidget(grad_lbl)

        self._gradient_btns = []
        GCOLS = 4; GRAD_W = 58; GRAD_H = 58
        GRAD_VISIBLE_ROWS = 3
        INITIAL_GRAD = GCOLS * GRAD_VISIBLE_ROWS - 1   # 11 gradient swatches initially

        self._grad_extra_rows = []
        self._grad_expanded   = False

        all_grads   = list(BgCanvas.GRADIENTS)
        total_grads = len(all_grads)

        def _add_grad_swatch(grad_spec, target_layout):
            gname, stops, angle_deg = grad_spec
            gbtn = QPushButton()
            gbtn.setFixedSize(GRAD_W, GRAD_H)
            gbtn.setToolTip(gname)
            gbtn.setCursor(QCursor(Qt.PointingHandCursor))
            gbtn.setCheckable(True)
            stop_css = ", ".join(
                f"stop:{pos:.2f} rgba({c.red()},{c.green()},{c.blue()},255)"
                for pos, c in stops
            )
            gbtn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, {stop_css});
                    border: 2px solid rgba(255,255,255,0.08); border-radius: 9px;
                }}
                QPushButton:checked {{ border: 3px solid #5865F2; }}
                QPushButton:hover   {{ border: 2px solid rgba(255,255,255,0.50); }}
            """)
            gbtn.clicked.connect(
                lambda checked, gs=grad_spec, b=gbtn: self._on_gradient_swatch(gs, b))
            target_layout.addWidget(gbtn)
            self._gradient_btns.append(gbtn)

        grad_frame = QFrame()
        grad_frame.setStyleSheet("background:transparent; border:none;")
        grad_grid = QVBoxLayout(grad_frame)
        grad_grid.setSpacing(GAP)
        grad_grid.setContentsMargins(0, 0, 0, 0)

        # --- Visible rows ---
        g_cur_rw = g_cur_rl = None
        for i in range(INITIAL_GRAD):
            if i % GCOLS == 0:
                g_cur_rw, g_cur_rl = _make_row_widget()
                grad_grid.addWidget(g_cur_rw)
            _add_grad_swatch(all_grads[i], g_cur_rl)

        # Toggle button — last slot of last visible row
        self._grad_toggle_btn = QPushButton(f"+{total_grads - INITIAL_GRAD}")
        self._grad_toggle_btn.setFixedSize(GRAD_W, GRAD_H)
        self._grad_toggle_btn.setToolTip("Show all gradients")
        self._grad_toggle_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._grad_toggle_btn.setStyleSheet(SHOW_MORE_BTN_STYLE)
        g_cur_rl.addWidget(self._grad_toggle_btn)

        # --- Extra rows (hidden) ---
        for offset, i in enumerate(range(INITIAL_GRAD, total_grads)):
            if offset % GCOLS == 0:
                g_cur_rw, g_cur_rl = _make_row_widget()
                g_cur_rw.hide()
                grad_grid.addWidget(g_cur_rw)
                self._grad_extra_rows.append(g_cur_rw)
            _add_grad_swatch(all_grads[i], g_cur_rl)
        # Pad last extra row
        if g_cur_rl is not None:
            remainder_eg = (total_grads - INITIAL_GRAD) % GCOLS
            if remainder_eg:
                for _ in range(GCOLS - remainder_eg):
                    g_cur_rl.addStretch()

        def _toggle_grad():
            self._grad_expanded = not self._grad_expanded
            for w in self._grad_extra_rows:
                w.setVisible(self._grad_expanded)
            remaining_g = total_grads - INITIAL_GRAD
            if self._grad_expanded:
                self._grad_toggle_btn.setText("▲")
                self._grad_toggle_btn.setToolTip("Show fewer gradients")
            else:
                self._grad_toggle_btn.setText(f"+{remaining_g}")
                self._grad_toggle_btn.setToolTip("Show all gradients")

        self._grad_toggle_btn.clicked.connect(_toggle_grad)
        swp_layout.addWidget(grad_frame)

        scroll.setWidget(scroll_content)
        swp_outer.addWidget(scroll)
        self._bg_swatches_scroll = scroll   # kept so _show_bg_sidebar can reset position

        # =======================================================
        # CUSTOM COLOR PAGE
        # =======================================================
        self._bg_custom_page = QFrame()
        self._bg_custom_page.setStyleSheet("background:transparent; border:none;")
        self._bg_custom_page.hide()
        cust_layout = QVBoxLayout(self._bg_custom_page)
        cust_layout.setContentsMargins(0, 4, 0, 0)
        cust_layout.setSpacing(6)

        # ---- SV square (full width, fills remaining space) ----
        self._sv_square = _SvSquare()
        self._sv_square.setMinimumHeight(150)
        self._sv_square.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._sv_square.sv_changed.connect(self._on_sv_changed)
        cust_layout.addWidget(self._sv_square, 1)  # stretch factor 1 fills leftover space

        # ---- Hue bar ----
        self._hue_bar = _HueBar()
        self._hue_bar.setFixedHeight(18)
        self._hue_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._hue_bar.hue_changed.connect(self._on_hue_changed)
        cust_layout.addWidget(self._hue_bar)

        # ---- Alpha bar ----
        self._alpha_bar = _AlphaBar()
        self._alpha_bar.setFixedHeight(18)
        self._alpha_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._alpha_bar.alpha_changed.connect(self._on_alpha_changed)
        cust_layout.addWidget(self._alpha_bar)

        # ---- Preview swatch + Hex + R G B fields ----
        fields_row = QHBoxLayout()
        fields_row.setSpacing(5)
        fields_row.setContentsMargins(0, 2, 0, 0)

        self._custom_preview = QFrame()
        self._custom_preview.setFixedSize(40, 40)
        self._custom_preview.setStyleSheet(
            "background:white; border-radius:8px; border:2px solid rgba(255,255,255,0.20);")
        fields_row.addWidget(self._custom_preview)

        FIELD_STYLE = """
            QLineEdit {
                background: rgba(255,255,255,0.09);
                border: 1px solid rgba(255,255,255,0.16);
                border-radius: 7px; color: white;
                font-size: 12px; padding: 2px 6px;
            }
            QLineEdit:focus { border-color: rgba(88,101,242,0.70); }
        """
        FIELD_LBL_STYLE = "color:rgba(255,255,255,0.35); font-size:10px; border:none;"

        def _make_field(label, w):
            col = QVBoxLayout(); col.setSpacing(2)
            lbl = QLabel(label); lbl.setStyleSheet(FIELD_LBL_STYLE)
            inp = QLineEdit(); inp.setFixedSize(w, 28); inp.setStyleSheet(FIELD_STYLE)
            col.addWidget(lbl); col.addWidget(inp)
            return col, inp

        hex_col, self._hex_input = _make_field("Hex", 80)
        self._hex_input.setPlaceholderText("#FF60DF")
        self._hex_input.setMaxLength(7)
        fields_row.addLayout(hex_col)

        r_col, self._rgb_r = _make_field("R", 38)
        g_col, self._rgb_g = _make_field("G", 38)
        b_col, self._rgb_b = _make_field("B", 38)
        for sp in (self._rgb_r, self._rgb_g, self._rgb_b):
            sp.setMaxLength(3)
        fields_row.addLayout(r_col)
        fields_row.addLayout(g_col)
        fields_row.addLayout(b_col)
        fields_row.addStretch()

        cust_layout.addLayout(fields_row)

        # Wire hex + RGB inputs
        self._hex_input.editingFinished.connect(self._update_custom_from_hex)
        self._rgb_r.editingFinished.connect(self._update_custom_from_rgb)
        self._rgb_g.editingFinished.connect(self._update_custom_from_rgb)
        self._rgb_b.editingFinished.connect(self._update_custom_from_rgb)

        # Compatibility stubs — image-bg slot UI removed but methods kept intact
        self._bg_img_entries   = []
        self._bg_img_btns      = []
        self._IMG_BOX          = 52
        self._IMG_MAX          = 4
        self._pending_bg_pil   = None
        self._bg_img_thumb_pix = None
        self._BOX_EMPTY_STYLE  = ""
        self._BOX_FILLED_STYLE = ""

        # ---- Init colour state ----
        self._custom_hue = 300; self._custom_sat = 255
        self._custom_val = 255; self._custom_alpha = 255
        self._sv_square.set_hue(self._custom_hue)
        self._sv_square.set_sv(self._custom_sat, self._custom_val)
        self._hue_bar.set_hue(self._custom_hue)
        self._alpha_bar.set_color(QColor.fromHsv(300, 255, 255))
        self._alpha_bar.set_alpha(255)
        self._sync_custom_ui()

        # Stubs kept so old references don't crash
        self._CSWATCH_MAX       = 4
        self._CSWATCH_SIZE      = 52
        self._CSWATCH_BTN_STYLE = ""
        self._cust_swatch_btns  = []
        self._custom_saved      = []

        # Add pages to sbl
        self._bg_blur_page = QFrame()   # kept as stub so _switch_bg_tab doesn't crash
        sbl.addWidget(self._bg_swatches_page)
        sbl.addWidget(self._bg_custom_page)

        # sb is a floating window — NOT added to layout
        self._outer.addWidget(self.bgremove_panel)

    def _position_bg_sidebar(self):
        """Position sidebar as a floating window to the right of the main window."""

        sb    = self._bg_sidebar
        panel = self.bgremove_panel

        if self._bg_sidebar_rel is None:
            # Default: just to the right of the main window
            panel_global_y = panel.mapToGlobal(QPoint(0, 0)).y()

            x = self.x() + self.width() + 10
            y = panel_global_y + (panel.height() - sb.height()) // 2

            # Clamp to screen
            screen = QApplication.primaryScreen().geometry()
            x = max(screen.left(), min(x, screen.right() - sb.width() - 4))
            y = max(screen.top(),  min(y, screen.bottom() - sb.height() - 4))

            sb.move(x, y)

            self._bg_sidebar_rel = QPoint(
                x - self.x(),
                y - self.y()
            )

        else:
            sb.move(
                self.x() + self._bg_sidebar_rel.x(),
                self.y() + self._bg_sidebar_rel.y(),
            )

        sb.raise_()

    def _build_pdf_tool_panels(self):
        self._pdf_panels = {}
        for tool_id, _, _icon in self.PDF_TOOLS:
            panel = PdfToolPanel(tool_id)
            panel.hide()
            panel.height_hint_changed.connect(self._on_pdf_panel_height_changed)
            self._pdf_panels[tool_id] = panel
            self._outer.addWidget(panel, 0, Qt.AlignHCenter)

    # ------------------------------------------------------------------
    # MODE SWITCHING
    # ------------------------------------------------------------------
    def _switch_mode(self, mode):
        self.mode = mode
        self.files = []
        self.list_frame.hide()
        self.crop_panel.hide()
        self.bgremove_panel.hide()
        self._bg_sidebar.hide()
        for p in self._pdf_panels.values():
            p.hide()
        self._active_pdf_tool = None
        self.file_list.clear()
        self.compress_controls.hide()
        self.crop_bar_controls.hide()
        self.bgremove_bar_controls.hide()
        self.pdf_bar_controls.hide()
        self.hint.show()
        self._animate_size(self.bar.height() or 80)

        for m, btn in self._mode_btns.items():
            btn.setChecked(m == mode)

        if mode == self.MODE_COMPRESS:
            self.compress_controls.show()
            self.update_hint()
            # re-populate compress list from tray
            for entry in self._tray:
                if entry["type"] == "image":
                    p = entry["path"]
                    self.files.append(p)
                    kb = os.path.getsize(p) / 1024
                    self.file_list.addItem(f"○  {os.path.basename(p)}   ·   {kb:.0f} KB")
            if self.files:
                self.update_hint()
                self._show_compress_list()
        elif mode == self.MODE_CROP:
            self.crop_bar_controls.show()
            imgs = [e["path"] for e in self._tray if e["type"] == "image"]
            if self.crop_path is not None and self.crop_canvas._pil is not None:
                # Revisit — canvas already loaded, just restore the panel instantly
                self.hint.hide()
                self.crop_panel.show()
                self._animate_size(self.bar.height() or 80 + 10 + PANEL_H)
            elif imgs:
                self._load_crop_image(imgs[-1])
            else:
                self.crop_path = None
                self.hint.setText("Drop an image anywhere  ·  or click  ＋")
        elif mode == self.MODE_BGREMOVE:
            self.bgremove_bar_controls.show()
            # If a result is cached (finished while we were on another tab), apply it now
            if self._bgremove_result is not None:
                pil_rgba, src_rgb = self._bgremove_result
                self._bgremove_result = None
                self._apply_bgremove_result(pil_rgba, src_rgb)
            # If the worker is still running, just show the in-progress hint
            elif self._bg_thread is not None and self._bg_thread_is_running():
                self.hint.setText("⏳  Removing background…")
            # Canvas already has a result — restore the panel instantly, no re-run
            elif self.bg_canvas._base_pix is not None and self.bgremove_path is not None:
                self.hint.hide()
                self.bgremove_panel.show()
                self._animate_size(self.bar.height() or 80 + 10 + PANEL_H)
                QTimer.singleShot(50, self._show_bg_sidebar)
            else:
                # Nothing loaded yet — load from tray if available
                self.bgremove_path = None
                self.hint.setText("Drop an image  ·  background will be removed automatically")
                imgs = [e["path"] for e in self._tray if e["type"] == "image"]
                if imgs:
                    self._load_bgremove(imgs[-1])
        elif mode == self.MODE_PDF:
            self.pdf_bar_controls.show()
            for btn in self._pdf_tool_btns.values():
                btn.setChecked(False)
            self.hint.setText("Choose a PDF tool above  ·  then drop files below")

    def _on_pdf_tool_btn(self, tool_id):
        for tid, btn in self._pdf_tool_btns.items():
            btn.setChecked(tid == tool_id)
        for p in self._pdf_panels.values():
            p.hide()
        panel = self._pdf_panels[tool_id]
        self._active_pdf_tool = tool_id
        self.hint.hide()
        panel.show()
        self._animate_size((self.bar.height() or 80) + 10 + panel.height())

    def _on_pdf_panel_height_changed(self, height):
        if self.mode == self.MODE_PDF and self._active_pdf_tool:
            self._animate_size((self.bar.height() or 80) + 10 + height)

    # ------------------------------------------------------------------
    # DRAG & DROP
    # ------------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.accept()

    def dropEvent(self, e):
        paths = [url.toLocalFile() for url in e.mimeData().urls()]
        valid_img = ('.png', '.jpg', '.jpeg', '.webp')
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in valid_img or ext == '.pdf':
                self._tray_add(path)
                # BG Remove only processes one image — stop after the first valid image
                if self.mode == self.MODE_BGREMOVE and ext in valid_img:
                    break

    # ------------------------------------------------------------------
    # ADD FILES
    # ------------------------------------------------------------------
    def add_files(self):
        if self.mode == self.MODE_BGREMOVE:
            # BG Remove only handles one image — use single-file dialog
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Image", "",
                "Images (*.png *.jpg *.jpeg *.webp)"
            )
            if path:
                self._tray_add(path)
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Files", "",
            "Images & PDFs (*.png *.jpg *.jpeg *.webp *.pdf)"
        )
        for p in files:
            self._tray_add(p)

    # ------------------------------------------------------------------
    # CROP
    # ------------------------------------------------------------------
    def _load_crop_image(self, path):
        try:
            pil = Image.open(path)
            self.crop_path = path
            self.crop_canvas.load_image(pil)
            self.crop_img_info.setText(f"{pil.width} × {pil.height} px\n{os.path.basename(path)}")
            self.bar_ratio_combo.blockSignals(True)
            self.bar_ratio_combo.setCurrentIndex(0)
            self.bar_ratio_combo.blockSignals(False)
            self._ratio_btn.setText("  Free")
            # Reset rotation ruler without triggering the handler
            self.rotate_slider.blockSignals(True)
            self.rotate_slider.setValue(0)
            self.rotate_angle_lbl.setText("0 °")
            self.rotate_slider.blockSignals(False)
            self.custom_ratio_frame.hide()
            self._clear_preset()
            self.hint.hide()
            self.bgremove_panel.hide()
            self.list_frame.hide()
            for p in self._pdf_panels.values():
                p.hide()
            self.crop_panel.show()
            self._animate_size(self.bar.height() or 80 + 10 + PANEL_H)
        except Exception as ex:
            QMessageBox.critical(self, "Load error", str(ex))

    def _on_crop_changed(self, x, y, w, h):
        pass  # resolution display removed

    def _on_bar_ratio(self, idx):
        data = self.bar_ratio_combo.itemData(idx)
        if data is None:
            return  # separator row
        ratio, preset_info = data
        self.custom_ratio_frame.hide()

        # Sync ratio button label
        label_text = self.bar_ratio_combo.currentText()
        self._ratio_btn.setText(f"  {label_text}" if label_text else "  Free")

        if ratio == "custom":
            self.custom_ratio_frame.show()
            self._apply_custom_ratio()
            self._active_preset = None
        elif ratio is None:
            # Free
            self.crop_canvas.set_aspect(None)
            self._active_preset = None
        else:
            self.crop_canvas.set_aspect(ratio)
            if preset_info:
                pw, ph, dpi = preset_info
                cat = "official" if dpi == 300 else "social"
                self._active_preset = (
                    self.bar_ratio_combo.currentText(), pw, ph, cat
                )
                # Auto-crop if image loaded
                if self.crop_path:
                    if cat == "official":
                        self._auto_crop_for_official()
                    else:
                        self._auto_crop()
            else:
                self._active_preset = None

    def _show_ratio_menu(self):
        """Pop up a preset ratio menu anchored below the ratio button."""
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)

        for label, ratio, preset in CROP_PRESETS:
            if ratio == "sep":
                menu.addSeparator()
                continue
            action = QAction(label, self)
            action.setData((label, ratio, preset))
            menu.addAction(action)

        chosen = menu.exec_(
            self._ratio_btn.mapToGlobal(self._ratio_btn.rect().bottomLeft())
        )
        if chosen is None:
            return

        label, ratio, preset_info = chosen.data()

        # Sync the bar combo so all downstream logic (_on_bar_ratio) fires
        for i in range(self.bar_ratio_combo.count()):
            if self.bar_ratio_combo.itemText(i) == label:
                self.bar_ratio_combo.setCurrentIndex(i)
                break

    def _apply_custom_ratio(self):
        self.crop_canvas.set_aspect((self.ratio_w.value(), self.ratio_h.value()))

    def _reset_crop(self):
        self.bar_ratio_combo.blockSignals(True)
        self.bar_ratio_combo.setCurrentIndex(0)
        self.bar_ratio_combo.blockSignals(False)
        self._ratio_btn.setText("  Free")
        self.crop_canvas.set_aspect(None)
        self.crop_canvas.reset_zoom()
        self.custom_ratio_frame.hide()
        self._clear_preset()

    def _on_rotate_slider(self, value):
        """Called whenever the rotation ruler moves."""
        if not self.crop_path:
            return
        self.rotate_angle_lbl.setText(f"{value} °")
        self.crop_canvas.rotate_image(value)

    def _reset_rotation(self):
        """Snap the rotation slider back to 0 and restore the unrotated image."""
        self.rotate_slider.setValue(0)  # triggers _on_rotate_slider(0)

    def _flip_crop_h(self):
        """Flip image horizontally, commit as new base, and reset slider to 0."""
        if not self.crop_path:
            return
        angle = self.rotate_slider.value()
        if angle != 0:
            self.crop_canvas.rotate_image(angle)
            self.crop_canvas._pil_original = self.crop_canvas._pil.copy()
        self.crop_canvas.flip_image('horizontal')
        self.rotate_slider.blockSignals(True)
        self.rotate_slider.setValue(0)
        self.rotate_angle_lbl.setText("0 °")
        self.rotate_slider.blockSignals(False)
        pil = self.crop_canvas._pil
        if pil:
            self.crop_img_info.setText(
                f"{pil.width} \u00d7 {pil.height} px\n{os.path.basename(self.crop_path)}"
            )

    def _flip_crop_v(self):
        """Flip image vertically, commit as new base, and reset slider to 0."""
        if not self.crop_path:
            return
        angle = self.rotate_slider.value()
        if angle != 0:
            self.crop_canvas.rotate_image(angle)
            self.crop_canvas._pil_original = self.crop_canvas._pil.copy()
        self.crop_canvas.flip_image('vertical')
        self.rotate_slider.blockSignals(True)
        self.rotate_slider.setValue(0)
        self.rotate_angle_lbl.setText("0 °")
        self.rotate_slider.blockSignals(False)
        pil = self.crop_canvas._pil
        if pil:
            self.crop_img_info.setText(
                f"{pil.width} \u00d7 {pil.height} px\n{os.path.basename(self.crop_path)}"
            )

    def _rotate_90_cw(self):
        """Rotate image 90° clockwise by moving the slider. Keeps slider in [-180, 180]."""
        if not self.crop_path:
            return
        current = self.rotate_slider.value()
        new_angle = ((current - 90 + 180) % 360) - 180
        self.rotate_slider.setValue(new_angle)

    def _rotate_90_ccw(self):
        """Rotate image 90° counter-clockwise by moving the slider. Keeps slider in [-180, 180]."""
        if not self.crop_path:
            return
        current = self.rotate_slider.value()
        new_angle = ((current + 90 + 180) % 360) - 180
        self.rotate_slider.setValue(new_angle)

    # ── Auto Crop ────────────────────────────────────────────────────────

    @staticmethod
    def _prepare_gray(pil, max_dim=900):
        """Return (gray_small, scale_factor, gray_full) for detection."""
        import numpy as np
        iw, ih = pil.width, pil.height
        img_rgb = np.array(pil.convert("RGB"))
        img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        scale = 1.0
        if max(iw, ih) > max_dim:
            scale = max_dim / max(iw, ih)
            small = cv2.resize(gray, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA)
        else:
            small = gray
        return small, scale, gray

    @staticmethod
    def _detect_faces(small_gray, scale, iw, ih):
        """
        Run frontal + profile cascades over multiple scaleFactor passes.
        Returns a list of (x, y, w, h) in full-image coordinates, or [].
        """
        cascades = [
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml",
            cv2.data.haarcascades + "haarcascade_profileface.xml",
        ]
        scale_factors = [1.05, 1.1, 1.15]
        all_faces = []
        for cascade_path in cascades:
            try:
                det = cv2.CascadeClassifier(cascade_path)
                if det.empty():
                    continue
                for sf in scale_factors:
                    hits = det.detectMultiScale(
                        small_gray, scaleFactor=sf,
                        minNeighbors=4, minSize=(24, 24)
                    )
                    if len(hits) > 0:
                        for (fx, fy, fw, fh) in hits:
                            all_faces.append((
                                int(fx / scale), int(fy / scale),
                                int(fw / scale), int(fh / scale),
                            ))
                        break   # found at this cascade — no need for coarser SF
            except Exception:
                pass

        if not all_faces:
            return []

        # Merge overlapping detections with a simple union-of-bbox approach
        # Sort by area descending, keep only those that don't overlap too much
        all_faces.sort(key=lambda f: f[2] * f[3], reverse=True)
        merged = []
        for face in all_faces:
            fx, fy, fw, fh = face
            duplicate = False
            for mx, my, mw, mh in merged:
                # Intersection-over-min-area overlap check
                ix = max(0, min(fx + fw, mx + mw) - max(fx, mx))
                iy = max(0, min(fy + fh, my + mh) - max(fy, my))
                inter = ix * iy
                min_area = min(fw * fh, mw * mh)
                if min_area > 0 and inter / min_area > 0.4:
                    duplicate = True
                    break
            if not duplicate:
                merged.append(face)
        return merged

    @staticmethod
    def _fit_roi_to_aspect(rx, ry, rw, rh, iw, ih, aspect):
        """
        Expand/shrink the roi box so it matches the target aspect ratio (w, h tuple),
        keeping the subject centred.  Returns (rx, ry, rw, rh) as floats.
        """
        if aspect is None:
            return float(rx), float(ry), float(rw), float(rh)
        aw, ah = float(aspect[0]), float(aspect[1])
        target = aw / ah
        cx = rx + rw / 2.0
        cy = ry + rh / 2.0
        cur = rw / max(1.0, rh)
        if cur > target:
            # ROI is wider than target → expand height
            new_h = rw / target
            new_w = float(rw)
        else:
            # ROI is taller than target → expand width
            new_w = rh * target
            new_h = float(rh)
        # Clamp to image bounds while keeping the ratio
        if new_w > iw:
            new_w = float(iw)
            new_h = new_w / target
        if new_h > ih:
            new_h = float(ih)
            new_w = new_h * target
        rx = max(0.0, min(cx - new_w / 2, iw - new_w))
        ry = max(0.0, min(cy - new_h / 2, ih - new_h))
        return rx, ry, new_w, new_h

    @staticmethod
    def _rule_of_thirds_nudge(rx, ry, rw, rh, iw, ih):
        """
        Nudge the crop box toward the nearest rule-of-thirds intersection.
        Only applied when the crop is meaningfully smaller than the image.
        """
        if rw >= iw * 0.75 or rh >= ih * 0.75:
            return rx, ry, rw, rh
        cx = rx + rw / 2.0
        cy = ry + rh / 2.0
        # Nearest third on each axis
        tx = min([iw / 3.0, 2 * iw / 3.0], key=lambda t: abs(t - cx))
        ty = min([ih / 3.0, 2 * ih / 3.0], key=lambda t: abs(t - cy))
        max_shift = min(iw, ih) * 0.12   # cap the nudge at 12% of the shorter side
        dx = max(-max_shift, min(max_shift, tx - cx))
        dy = max(-max_shift, min(max_shift, ty - cy))
        new_rx = rx + dx
        new_ry = ry + dy
        # Keep within image
        if new_rx >= 0 and new_rx + rw <= iw:
            rx = new_rx
        if new_ry >= 0 and new_ry + rh <= ih:
            ry = new_ry
        return rx, ry, rw, rh

    @staticmethod
    def _saliency_roi(gray, iw, ih):
        """
        Centre-weighted saliency map combining edge energy and a Gaussian
        centre-bias, so subjects near the middle of busy images win.
        """
        # Edge energy
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        energy = np.sqrt(gx ** 2 + gy ** 2)
        energy = energy / (energy.max() + 1e-8)

        # Centre-bias Gaussian (sigma = 40% of image size)
        h, w = gray.shape
        Y, X = np.mgrid[0:h, 0:w]
        sigma_x = w * 0.40
        sigma_y = h * 0.40
        gauss = np.exp(
            -((X - w / 2) ** 2) / (2 * sigma_x ** 2)
            - ((Y - h / 2) ** 2) / (2 * sigma_y ** 2)
        )

        saliency = (energy * 0.6 + gauss * 0.4)
        saliency = (saliency / (saliency.max() + 1e-8) * 255).astype(np.uint8)

        blur_size = max(3, min(iw, ih) // 6) | 1
        blurred = cv2.GaussianBlur(saliency, (blur_size, blur_size), 0)

        thresh_val = np.percentile(blurred, 72)
        _, mask = cv2.threshold(blurred, int(thresh_val), 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        # Weight contours by area × distance-from-centre score
        cx_img, cy_img = w / 2.0, h / 2.0
        def _score(c):
            area = cv2.contourArea(c)
            m = cv2.moments(c)
            if m["m00"] == 0:
                return area
            ccx = m["m10"] / m["m00"]
            ccy = m["m01"] / m["m00"]
            dist = ((ccx - cx_img) ** 2 + (ccy - cy_img) ** 2) ** 0.5
            max_dist = (cx_img ** 2 + cy_img ** 2) ** 0.5 + 1
            centre_w = 1.0 - 0.5 * dist / max_dist
            return area * centre_w
        top = sorted(contours, key=_score, reverse=True)[:6]
        all_pts = np.vstack(top)
        bx, by, bw, bh = cv2.boundingRect(all_pts)
        pad = int(min(bw, bh) * 0.12)
        return (
            max(0, bx - pad),
            max(0, by - pad),
            min(iw, bx + bw + pad) - max(0, bx - pad),
            min(ih, by + bh + pad) - max(0, by - pad),
        )

    def _auto_crop(self):
        """
        Smart auto-crop: tries face detection → upper-body → centre-weighted
        saliency → whole-image fallback, then fits to the active aspect ratio
        and nudges toward a rule-of-thirds intersection.
        """
        if not self.crop_path:
            return
        if not HAS_CV2:
            QMessageBox.warning(self, "Auto Crop",
                                "OpenCV is not installed.\n"
                                "Run:  pip install opencv-python-headless")
            return

        # Use _pil — the same image whose pixel coords set_crop_coords operates on.
        # get_pil_image() returns the full-res original which can be much larger,
        # causing every computed ROI to be clamped away as out-of-bounds.
        pil = self.crop_canvas._pil
        if pil is None:
            return

        iw, ih = pil.width, pil.height
        small_gray, scale, gray = self._prepare_gray(pil)
        roi = None

        # ── Strategy 1: Face detection (frontal + profile, multi-pass) ──
        faces = self._detect_faces(small_gray, scale, iw, ih)
        if faces:
            # Union bounding box of all detected faces
            fx1 = min(f[0] for f in faces)
            fy1 = min(f[1] for f in faces)
            fx2 = max(f[0] + f[2] for f in faces)
            fy2 = max(f[1] + f[3] for f in faces)
            fw, fh = fx2 - fx1, fy2 - fy1
            # Generous padding: more headroom above, body space below
            pad_x     = int(fw * 0.65)
            pad_top   = int(fh * 0.60)
            pad_bot   = int(fh * 1.30)
            x1 = max(0, fx1 - pad_x)
            y1 = max(0, fy1 - pad_top)
            x2 = min(iw, fx2 + pad_x)
            y2 = min(ih, fy2 + pad_bot)
            roi = (x1, y1, x2 - x1, y2 - y1)

        # ── Strategy 2: Upper-body detector ─────────────────────────────
        if roi is None:
            try:
                det = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_upperbody.xml"
                )
                if not det.empty():
                    for sf in [1.05, 1.1, 1.15]:
                        bodies = det.detectMultiScale(
                            small_gray, scaleFactor=sf,
                            minNeighbors=3, minSize=(40, 40)
                        )
                        if len(bodies) > 0:
                            bx1 = int(min(b[0] for b in bodies) / scale)
                            by1 = int(min(b[1] for b in bodies) / scale)
                            bx2 = int(max(b[0] + b[2] for b in bodies) / scale)
                            by2 = int(max(b[1] + b[3] for b in bodies) / scale)
                            pad = int((bx2 - bx1) * 0.25)
                            roi = (
                                max(0, bx1 - pad), max(0, by1 - pad),
                                min(iw, bx2 + pad) - max(0, bx1 - pad),
                                min(ih, by2 + pad) - max(0, by1 - pad),
                            )
                            break
            except Exception:
                pass

        # ── Strategy 3: Centre-weighted saliency map ─────────────────────
        if roi is None:
            try:
                roi = self._saliency_roi(gray, iw, ih)
            except Exception:
                pass

        # ── Strategy 4: Full-image fallback ──────────────────────────────
        if roi is None:
            roi = (0, 0, iw, ih)

        rx, ry, rw, rh = roi

        # Fit to active aspect ratio (keeps subject centred)
        aspect = self.crop_canvas._aspect
        if aspect:
            rx, ry, rw, rh = self._fit_roi_to_aspect(rx, ry, rw, rh, iw, ih, aspect)
        else:
            # Add a small breathing margin when no ratio is locked
            margin = int(min(rw, rh) * 0.06)
            rx = max(0, rx - margin)
            ry = max(0, ry - margin)
            rw = min(iw - rx, rw + 2 * margin)
            rh = min(ih - ry, rh + 2 * margin)

        # Rule-of-thirds nudge
        rx, ry, rw, rh = self._rule_of_thirds_nudge(rx, ry, rw, rh, iw, ih)

        self.crop_canvas.set_crop_coords(
            max(0, int(rx)), max(0, int(ry)),
            max(1, min(int(rw), iw - max(0, int(rx)))),
            max(1, min(int(rh), ih - max(0, int(ry)))),
        )
        self.crop_canvas._emit()

    # ── Preset helpers ───────────────────────────────────────────────────
    def _clear_preset(self):
        """Clear active preset tracking."""
        self._active_preset = None

    def _auto_crop_for_official(self):
        """
        Face-centred auto-crop for passport / visa / ID photos.
        Targets ICAO guidelines: face occupies ~70-80% of frame height,
        eyes positioned in the upper-centre third of the frame.
        Falls back to general _auto_crop if no face is found.
        """
        if not self.crop_path or not HAS_CV2:
            self._auto_crop()
            return

        pil = self.crop_canvas._pil
        if pil is None:
            return

        iw, ih = pil.width, pil.height
        small_gray, scale, _ = self._prepare_gray(pil)

        faces = self._detect_faces(small_gray, scale, iw, ih)
        if not faces:
            self._auto_crop()
            return

        # Use the largest detected face
        faces.sort(key=lambda f: f[2] * f[3], reverse=True)
        fx, fy, fw, fh = faces[0]

        # ICAO: face height ~70% of frame → frame height = fh / 0.70
        # Eyes are roughly at fy + 0.35*fh; position them at ~40% from top of frame
        frame_h = fh / 0.70
        frame_w = frame_h  # will be adjusted to aspect ratio below
        eye_y   = fy + fh * 0.35          # estimated eye line in image coords
        top     = eye_y - frame_h * 0.40   # frame top so eyes sit at 40% height

        cx = fx + fw / 2.0
        rx = cx - frame_w / 2.0
        ry = top

        # Clamp within image
        rx = max(0.0, min(rx, iw - frame_w))
        ry = max(0.0, min(ry, ih - frame_h))
        rw, rh = frame_w, frame_h

        # Fit to active aspect ratio
        aspect = self.crop_canvas._aspect
        if aspect:
            rx, ry, rw, rh = self._fit_roi_to_aspect(rx, ry, rw, rh, iw, ih, aspect)

        self.crop_canvas.set_crop_coords(
            max(0, int(rx)), max(0, int(ry)),
            max(1, min(int(rw), iw - max(0, int(rx)))),
            max(1, min(int(rh), ih - max(0, int(ry)))),
        )
        self.crop_canvas._emit()

    def _do_crop_save(self):
        if not self.crop_path:
            QMessageBox.warning(self, "No image", "Drop or add an image first.")
            return
        x, y, w, h = self.crop_canvas.get_crop_coords()
        if w < 1 or h < 1:
            QMessageBox.warning(self, "Invalid crop", "Crop area is too small.")
            return
        pil = self.crop_canvas._pil
        cropped = pil.crop((x, y, x + w, y + h))

        # Resize to exact preset dimensions if a preset is active
        save_dpi = None
        if self._active_preset:
            p_name, p_w, p_h, p_cat = self._active_preset
            if p_cat == "official" and p_w > 1 and p_h > 1:
                cropped = cropped.resize((p_w, p_h), Image.LANCZOS)
                save_dpi = (300, 300)
            elif p_cat == "social":
                cropped = cropped.resize((p_w, p_h), Image.LANCZOS)
                save_dpi = (72, 72)

        folder = "cropped_images"
        os.makedirs(folder, exist_ok=True)
        name, ext = os.path.splitext(os.path.basename(self.crop_path))
        ext = ext or ".png"
        out = os.path.join(folder, f"{name}_cropped{ext}")
        c = 1
        while os.path.exists(out):
            out = os.path.join(folder, f"{name}_cropped_{c}{ext}")
            c += 1
        try:
            save_kwargs = {}
            if save_dpi:
                save_kwargs["dpi"] = save_dpi
            cropped.save(out, **save_kwargs)
            self.crop_save_btn.setText("Saved!")
            QApplication.processEvents()
            time.sleep(0.8)
            self.crop_save_btn.setText("Crop")
        except Exception as ex:
            QMessageBox.critical(self, "Save failed", str(ex))



    # ------------------------------------------------------------------
    # BG REMOVE
    # ------------------------------------------------------------------

    def _load_bgremove(self, path):
        self.bgremove_path = path
        self.hint.show()
        self.hint.setText("⏳  Removing background…")
        self.bgremove_panel.hide()
        self._bg_sidebar.hide()
        self.crop_panel.hide()
        for p in self._pdf_panels.values():
            p.hide()
        self._animate_size(self.bar.height() or 80)

        self._bg_thread = QThread()
        self._bg_worker = BgRemoveWorker(
            path,
            remover=self.bg_remover
        )
        self._bg_worker.moveToThread(self._bg_thread)
        self._bg_thread.started.connect(self._bg_worker.run)
        self._bg_worker.finished.connect(self._on_bgremove_done)
        self._bg_worker.error.connect(self._on_bgremove_error)
        self._bg_worker.finished.connect(self._bg_thread.quit)
        self._bg_thread.finished.connect(self._bg_thread.deleteLater)
        self._bg_thread.finished.connect(self._on_bg_thread_done)
        self._bg_thread.start()

    def _on_bg_thread_done(self):
        """Null out the thread ref after Qt deletes the C++ object."""
        self._bg_thread = None
        self._bg_worker = None

    def _bg_thread_is_running(self):
        """Safe isRunning() — returns False if the C++ object has been deleted."""
        try:
            return self._bg_thread is not None and self._bg_thread.isRunning()
        except RuntimeError:
            self._bg_thread = None
            self._bg_worker = None
            return False

    def _on_bgremove_done(self, pil_rgba, remover):
        self.bg_remover = remover
        try:
            src_rgb = Image.open(self.bgremove_path).convert("RGB")
        except Exception:
            src_rgb = None
        # If user has switched to another mode, cache the result silently —
        # it will be applied when they return to BG Remove.
        if self.mode != self.MODE_BGREMOVE:
            self._bgremove_result = (pil_rgba, src_rgb)
            return
        self._apply_bgremove_result(pil_rgba, src_rgb)

    def _apply_bgremove_result(self, pil_rgba, src_rgb):
        self.bg_canvas.set_image(pil_rgba, src_rgb)
        for btn in self._swatch_btns:
            btn.setChecked(False)
        for btn in self._gradient_btns:
            btn.setChecked(False)
        self._swatch_btns[0].setChecked(True)
        self.bg_canvas.set_bg_color(None)
        # Reset image-bg state
        self._pending_bg_pil   = None
        self._bg_img_thumb_pix = None
        self.hint.hide()
        self.bgremove_panel.show()
        self._animate_size(self.bar.height() or 80 + 10 + PANEL_H)
        # Preserve sidebar if already shown, just reposition
        if self._bg_sidebar.isVisible():
            self._position_bg_sidebar()
        else:
            QTimer.singleShot(50, self._show_bg_sidebar)

    def _show_bg_sidebar(self):
        self._bg_sidebar.show()
        self._bg_sidebar.raise_()
        self._position_bg_sidebar()
        # Reset swatches scroll to top — guards against any phantom wheel events
        # that may have shifted it during a previous drag session.
        if hasattr(self, '_bg_swatches_scroll'):
            self._bg_swatches_scroll.verticalScrollBar().setValue(0)

    def _on_bgremove_error(self, msg):
        self.hint.show()
        self.hint.setText("Drop an image  ·  background will be removed automatically")
        QMessageBox.critical(self, "Error",
            f"Background removal failed:\n{msg}\n\nMake sure rembg is installed:\npip install rembg")

    # ------------------------------------------------------------------
    # CUSTOM COLOR — SV square / hue bar / alpha bar handlers
    # ------------------------------------------------------------------

    def _on_sv_changed(self, sat, val):
        self._custom_sat = sat
        self._custom_val = val
        self._sync_custom_ui()

    def _on_hue_changed(self, hue):
        self._custom_hue = max(0, min(hue, 359))
        self._sv_square.set_hue(self._custom_hue)
        self._alpha_bar.set_color(
            QColor.fromHsv(self._custom_hue, self._custom_sat, self._custom_val))
        self._sync_custom_ui()

    def _on_alpha_changed(self, alpha_255):
        self._custom_alpha = max(0, min(alpha_255, 255))
        self._sync_custom_ui()

    def _sync_custom_ui(self):
        """Refresh hex/RGB inputs, preview swatch, and canvas live preview."""
        h = max(0, min(self._custom_hue, 359))
        s = max(0, min(self._custom_sat, 255))
        v = max(0, min(self._custom_val, 255))
        a = max(0, min(self._custom_alpha, 255))
        color = QColor.fromHsv(h, s, v)
        color.setAlpha(a)
        self._alpha_bar.set_color(color)

        # Hex field
        self._hex_input.blockSignals(True)
        self._hex_input.setText(f"#{color.red():02X}{color.green():02X}{color.blue():02X}")
        self._hex_input.blockSignals(False)

        # RGB fields
        for field, val in ((self._rgb_r, color.red()),
                           (self._rgb_g, color.green()),
                           (self._rgb_b, color.blue())):
            field.blockSignals(True)
            field.setText(str(val))
            field.blockSignals(False)

        # Preview swatch
        self._custom_preview.setStyleSheet(
            f"background: rgba({color.red()},{color.green()},{color.blue()},{a});"
            "border-radius:7px; border:2px solid rgba(255,255,255,0.20);"
        )
        self._current_custom_color = color

        # Live canvas preview — only when Custom tab is visible and canvas has an image
        if (hasattr(self, '_bg_custom_page') and self._bg_custom_page.isVisible()
                and hasattr(self, 'bg_canvas') and self.bg_canvas._base_pix is not None):
            self.bg_canvas.set_bg_color(color)

    def _update_custom_from_hex(self):
        text = self._hex_input.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        color = QColor(text)
        if not color.isValid():
            return
        h, s, v, _ = color.getHsv()
        if h < 0: h = 0
        self._custom_hue = h
        self._custom_sat = s
        self._custom_val = v
        self._sv_square.set_hue(h)
        self._sv_square.set_sv(s, v)
        self._hue_bar.set_hue(h)
        self._alpha_bar.set_color(color)
        self._sync_custom_ui()

    def _update_custom_from_rgb(self):
        try:
            r = max(0, min(int(self._rgb_r.text() or 0), 255))
            g = max(0, min(int(self._rgb_g.text() or 0), 255))
            b = max(0, min(int(self._rgb_b.text() or 0), 255))
        except ValueError:
            return
        color = QColor(r, g, b)
        h, s, v, _ = color.getHsv()
        if h < 0: h = 0
        self._custom_hue = h
        self._custom_sat = s
        self._custom_val = v
        self._sv_square.set_hue(h)
        self._sv_square.set_sv(s, v)
        self._hue_bar.set_hue(h)
        self._alpha_bar.set_color(color)
        self._sync_custom_ui()

    # Keep these stubs so old signal refs don't crash (they're no longer wired but harmless)
    def _update_custom_from_sliders(self): pass

    def _on_swatch(self, color, clicked_btn):
        for btn in self._swatch_btns:
            btn.setChecked(btn is clicked_btn)
        for btn in self._gradient_btns:
            btn.setChecked(False)
        self.bg_canvas.set_bg_color(color)

    def _on_gradient_swatch(self, grad_spec, clicked_btn):
        for btn in self._swatch_btns:
            btn.setChecked(False)
        for btn in self._gradient_btns:
            btn.setChecked(btn is clicked_btn)
        self.bg_canvas.set_bg_gradient(grad_spec)

    def _switch_bg_tab(self, tab):
        pages = {
            "swatches": self._bg_swatches_page,
            "custom":   self._bg_custom_page,
        }
        tabs = {
            "swatches": self._bg_tab_swatches,
            "custom":   self._bg_tab_custom,
        }
        for key, page in pages.items():
            page.setVisible(key == tab)
        for key, btn in tabs.items():
            btn.setChecked(key == tab)

    # ---- Image background ----
    def _img_drag_enter(self, e):
        if e.mimeData().hasUrls():
            urls = [u.toLocalFile() for u in e.mimeData().urls()]
            if any(u.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp'))
                   for u in urls):
                e.accept(); return
        e.ignore()

    def _img_drop(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith(
                     ('.png', '.jpg', '.jpeg', '.webp', '.bmp'))]
        if paths:
            # Find the first empty slot, else use slot 0
            idx = next((i for i, en in enumerate(self._bg_img_entries) if en is None), 0)
            self._load_bg_image_into_slot(paths[0], idx)

    def _browse_bg_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Background Image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._load_bg_image_path(path)

    def _load_bg_image_path(self, path):
        idx = next((i for i, en in enumerate(self._bg_img_entries) if en is None), 0)
        self._load_bg_image_into_slot(path, idx)

    def _load_bg_image_into_slot(self, path, slot_idx):
        """Load image into a specific slot. Preview version applied instantly,
        full-res stored for save."""
        try:
            pil_full = Image.open(path).convert("RGB")
        except Exception:
            return

        # ---- Preview version: downscale so canvas update is instant ----
        PREVIEW_MAX = 1000   # px on longest side — fast to composite
        pil_preview = pil_full.copy()
        if max(pil_preview.size) > PREVIEW_MAX:
            pil_preview.thumbnail((PREVIEW_MAX, PREVIEW_MAX), Image.LANCZOS)

        # ---- Thumbnail for button icon (cover-crop to slot size) ----
        SIZE = self._IMG_BOX
        src  = pil_full.copy()
        sw, sh = src.size
        scale = SIZE / min(sw, sh)
        nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
        src  = src.resize((nw, nh), Image.LANCZOS)
        left = (nw - SIZE) // 2
        top  = (nh - SIZE) // 2
        src  = src.crop((left, top, left + SIZE, top + SIZE))
        buf  = io.BytesIO()
        src.save(buf, "PNG")
        buf.seek(0)
        px = QPixmap(); px.loadFromData(buf.read())

        entry = {"pil_full": pil_full, "pil_preview": pil_preview, "pixmap": px}
        self._bg_img_entries[slot_idx] = entry

        # Update the slot button
        btn = self._bg_img_btns[slot_idx]
        btn.setIcon(QIcon(px))
        btn.setIconSize(QSize(SIZE, SIZE))
        btn.setStyleSheet(self._BOX_FILLED_STYLE)
        btn.setText("")

        # Mark this slot as active
        for ob in self._bg_img_btns:
            ob.setChecked(ob is btn)
        for ob in self._swatch_btns:
            ob.setChecked(False)
        for ob in self._gradient_btns:
            ob.setChecked(False)
        btn.setChecked(True)

        # Instantly apply preview version
        self._pending_bg_pil = pil_full   # full-res for save
        self.bg_canvas.set_bg_image(pil_preview)

    def _apply_custom_color(self):
        color = getattr(self, "_current_custom_color", QColor(255, 255, 255))
        for btn in self._swatch_btns:
            btn.setChecked(False)
        for btn in self._gradient_btns:
            btn.setChecked(False)
        self.bg_canvas.set_bg_color(color)

    def _apply_image_bg(self):
        """Legacy stub — slot system handles this now."""
        pass

    def _add_bg_img_box(self, pil):
        """Legacy stub — slot system handles this now."""
        pass

    def _save_custom_swatch(self, entry):
        """No-op — colour swatches row removed."""
        pass

    def _bgremove_save(self):
        # If a preview bg image is active, swap in full-res before getting result
        active_slot = next(
            (i for i, b in enumerate(self._bg_img_btns) if b.isChecked()
             and i < len(self._bg_img_entries) and self._bg_img_entries[i] is not None),
            None
        )
        if active_slot is not None:
            entry = self._bg_img_entries[active_slot]
            # Re-apply full-res silently so get_result() uses it
            self.bg_canvas.set_bg_image(entry["pil_full"])

        result = self.bg_canvas.get_result()
        if result is None:
            QMessageBox.warning(self, "Nothing to save", "Remove a background first.")
            return
        folder = "removed_bg"
        os.makedirs(folder, exist_ok=True)
        name, _ = os.path.splitext(os.path.basename(self.bgremove_path))
        ext = ".png" if self.bg_canvas._bg_mode == "none" else ".jpg"
        out = os.path.join(folder, f"{name}_nobg{ext}")
        c = 1
        while os.path.exists(out):
            out = os.path.join(folder, f"{name}_nobg_{c}{ext}")
            c += 1
        try:
            result.save(out)
            self._scan_temps.discard(self.bgremove_path)
            self.bgremove_save_btn.setText("✓  Saved!")
            QApplication.processEvents()
            time.sleep(0.8)
            self.bgremove_save_btn.setText("💾  Save Result")
        except Exception as ex:
            QMessageBox.critical(self, "Save failed", str(ex))

    # ------------------------------------------------------------------
    # COMPRESS
    # ------------------------------------------------------------------
    def _show_compress_list(self):
        self.list_frame.show()
        rows = min(len(self.files), 6)
        self._animate_size(self.bar.height() or 80 + 10 + 20 + 34 + rows * 44 + 20)

    def _compress_item_menu(self, pos):
        item = self.file_list.itemAt(pos)
        if not item:
            return
        row = self.file_list.row(item)
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        rem = QAction("✕  Remove this file", self)
        menu.addAction(rem)
        chosen = menu.exec_(self.file_list.mapToGlobal(pos))
        if chosen == rem:
            self.files.pop(row)
            self.file_list.takeItem(row)
            self.update_hint()
            if not self.files:
                a = QPropertyAnimation(self, b"size")
                a.setDuration(220)
                a.setStartValue(self.size())
                a.setEndValue(QSize(WIN_W, 80))
                a.setEasingCurve(QEasingCurve.OutCubic)
                a.finished.connect(self.list_frame.hide)
                a.start()
                self._anim = a

    def compress_all(self):
        if not self.files:
            QMessageBox.warning(self, "No images", "Add some images first.")
            return
        try:
            target_kb = int(self.kb_input.text())
            if target_kb <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Invalid size", "Enter a valid target KB value.")
            return

        self.compress_btn.setEnabled(False)
        self.compress_btn.setText("⏳  Compressing…")
        self.add_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.progress_bar.setMaximum(len(self.files))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.file_list.clear()
        for p in self.files:
            kb = os.path.getsize(p) / 1024
            self.file_list.addItem(f"○  {os.path.basename(p)}   ·   {kb:.0f} KB")

        self._thread = QThread()
        self._worker = CompressWorker(list(self.files), target_kb, "compressed_images")
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_done)
        self._worker.error.connect(self._on_err)
        self._worker.finished.connect(self._on_all_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_done(self, i, text):
        item = self.file_list.item(i)
        if item:
            item.setText("●  " + text)
            item.setForeground(QColor(74, 222, 128))
        self.progress_bar.setValue(i + 1)

    def _on_err(self, i, msg):
        item = self.file_list.item(i)
        fn = os.path.basename(self.files[i]) if i < len(self.files) else "?"
        if item:
            item.setText(f"●  {fn}  —  {msg}")
            item.setForeground(QColor(248, 113, 113))
        self.progress_bar.setValue(self.progress_bar.value() + 1)

    def _on_all_done(self):
        # Any scanned files that were compressed are now saved — remove from temps
        for p in list(self.files):
            self._scan_temps.discard(p)
        self.compress_btn.setEnabled(True)
        self.compress_btn.setText("⚡  Compress")
        self.add_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.progress_bar.hide()
        rows = min(len(self.files), 6)
        self._animate_size(self.bar.height() or 80 + 10 + 20 + 34 + rows * 44 + 20)

    # ------------------------------------------------------------------
    # SHARED
    # ------------------------------------------------------------------
    def clear_files(self):
        if self._thread and self._thread.isRunning():
            return
        self._tray.clear()
        self._refresh_tray()
        self.files = []
        self.crop_path = None
        self.bgremove_path = None
        self._bgremove_result = None
        self.file_list.clear()
        self.crop_panel.hide()
        self.bgremove_panel.hide()
        self._bg_sidebar.hide()
        self.list_frame.hide()
        for p in self._pdf_panels.values():
            p.hide()
        if self.mode == self.MODE_PDF:
            if self._active_pdf_tool:
                panel = self._pdf_panels[self._active_pdf_tool]
                if hasattr(panel, "canvas") and panel.canvas is not None:
                    panel.canvas.clear()
                elif hasattr(panel, "_protect_clear"):
                    panel._protect_clear()
            self._active_pdf_tool = None
            for btn in self._pdf_tool_btns.values():
                btn.setChecked(False)
            self.hint.show()
            self.hint.setText("Choose a PDF tool above  ·  then drop files below")
        self.hint.show()
        self.update_hint()
        self._animate_size(self.bar.height() or 80)

    def open_output_folder(self):
        if self.mode == self.MODE_CROP:         folder = "cropped_images"
        elif self.mode == self.MODE_BGREMOVE:   folder = "removed_bg"
        elif self.mode == self.MODE_PDF:        folder = "pdf_output"
        else:                                   folder = "compressed_images"
        folder = os.path.abspath(folder)
        os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":    os.startfile(folder)
        elif sys.platform == "darwin": os.system(f'open "{folder}"')
        else:                          os.system(f'xdg-open "{folder}"')

    def update_hint(self):
        n = len(self.files)
        if n == 0:    self.hint.setText("Drop images anywhere  ·  0 files loaded")
        elif n == 1:  self.hint.setText("1 file loaded  ·  ready to compress")
        else:         self.hint.setText(f"{n} files loaded  ·  ready to compress")

    def _animate_size(self, target_h):
        target = QSize(WIN_W, target_h)
        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"size")
        self._anim.setDuration(240)
        self._anim.setStartValue(self.size())
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.oldPos = e.globalPos()
            self._drag_reset_sidebar = False   # track whether we've reset yet this drag

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton:
            # On the first drag pixel, clear the stored sidebar offset so that
            # _position_bg_sidebar will recalculate the default right-aligned
            # position instead of keeping wherever the user last moved it.
            if not self._drag_reset_sidebar:
                self._drag_reset_sidebar = True
                self._bg_sidebar_rel = None
            d = e.globalPos() - self.oldPos
            self.move(self.x() + d.x(), self.y() + d.y())
            self.oldPos = e.globalPos()

    def moveEvent(self, e):
        """Keep floating sidebar aligned with the main window as it is dragged."""
        super().moveEvent(e)
        if hasattr(self, '_bg_sidebar') and self._bg_sidebar.isVisible():
            # When _bg_sidebar_rel is None (start of a new drag gesture),
            # _position_bg_sidebar recalculates and stores the default offset.
            # On subsequent moveEvents it just applies the stored offset.
            self._position_bg_sidebar()

    # ------------------------------------------------------------------
    # PHONE SCAN
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_tmp_dir():
        """Dedicated temp folder for incoming scans — wiped on close."""
        import tempfile, os
        d = os.path.join(tempfile.gettempdir(), "imgcomp_scans")
        os.makedirs(d, exist_ok=True)
        return d

    def _update_scan_btn_tooltip(self):
        if self._scan_server and self._scan_server.running:
            self.scan_btn.setToolTip(
                f"📡  Active — {self._scan_server.url}")
        else:
            self.scan_btn.setToolTip("Scan from Phone (offline)")

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self.scan_btn:
            if event.type() == QEvent.Enter:
                self._show_qr_flyout()
            elif event.type() == QEvent.Leave:
                # Close flyout only if mouse didn't move into it
                if self._scan_dlg and self._scan_dlg.isVisible():
                    # Let the flyout's own leave-event close itself
                    pass
        return super().eventFilter(obj, event)

    def _show_qr_flyout(self):
        """Show a tiny QR-only popup anchored below the scan button."""
        if not self._scan_server or not self._scan_server.running:
            return
        # Reuse if already open
        if self._scan_dlg and self._scan_dlg.isVisible():
            return
        self._scan_dlg = QrFlyout(self._scan_server, self.scan_btn, parent=None)
        dlg = self._scan_dlg
        dlg.adjustSize()
        dlg.show()          # show first so geometry is known
        # Position below the scan button
        btn_global = self.scan_btn.mapToGlobal(self.scan_btn.rect().bottomLeft())
        screen = QApplication.primaryScreen().geometry()
        x = btn_global.x() - dlg.width() // 2 + self.scan_btn.width() // 2
        y = btn_global.y() + 8
        x = max(0, min(x, screen.width()  - dlg.width()))
        y = max(0, min(y, screen.height() - dlg.height()))
        dlg.move(x, y)

    def _scan_callback(self, path: str):
        """Called from the HTTP server background thread."""
        QMetaObject.invokeMethod(
            self, "_on_scan_received",
            Qt.QueuedConnection,
            Q_ARG(str, path),
        )

    @pyqtSlot(str)
    def _on_scan_received(self, path: str):
        """Runs on the Qt main thread — register as temp, add to tray."""
        # Mark as a temporary scan file — deleted unless saved by a tool
        self._scan_temps.add(path)
        self.show()
        self.activateWindow()
        self.raise_()
        self._tray_add(path)

    def _delete_scan_temps(self):
        """Delete any scanned files that were never saved by an editing tool."""
        import shutil
        tmp_dir = self._scan_tmp_dir()
        # Delete individual tracked temps
        for p in list(self._scan_temps):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        self._scan_temps.clear()
        # Also wipe the whole temp folder
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    def closeEvent(self, e):
        if self._scan_server:
            self._scan_server.stop()
        self._delete_scan_temps()
        if self._worker:
            self._worker.cancel()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        e.accept()

# =========================================
# QR FLYOUT  — hover-only, QR code only
# =========================================

class QrFlyout(QDialog):
    """
    Tiny frameless popup shown while hovering the scan button.
    A QTimer polls cursor position every 100 ms and closes the flyout
    as soon as the cursor leaves both the button and the flyout.
    """

    def __init__(self, server, btn, parent=None):
        super().__init__(parent)
        self._server = server
        self._btn    = btn
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("qrCard")
        card.setStyleSheet("""
            QFrame#qrCard {
                background: #1e1e22;
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.10);
            }
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(8)

        # QR image
        if server.qr_pil:
            buf = io.BytesIO()
            server.qr_pil.save(buf, "PNG")
            buf.seek(0)
            qr_pix = QPixmap()
            qr_pix.loadFromData(buf.read())
            qr_lbl = QLabel()
            qr_lbl.setPixmap(
                qr_pix.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            qr_lbl.setAlignment(Qt.AlignCenter)
            qr_lbl.setStyleSheet(
                "border:none; background:white; border-radius:10px; padding:5px;")
            cl.addWidget(qr_lbl)
        else:
            no_qr = QLabel("pip install qrcode[pil]")
            no_qr.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:11px; border:none;")
            no_qr.setAlignment(Qt.AlignCenter)
            cl.addWidget(no_qr)

        outer.addWidget(card)

        # Poll every 100 ms — close when cursor leaves both btn and flyout
        from PyQt5.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._check_cursor)
        self._timer.start()

    def _check_cursor(self):
        cursor = QCursor.pos()
        over_btn    = self._btn.rect().contains(self._btn.mapFromGlobal(cursor))
        over_flyout = self.rect().contains(self.mapFromGlobal(cursor))
        if not over_btn and not over_flyout:
            self._timer.stop()
            self.close()
