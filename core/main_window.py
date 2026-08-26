import sys
import os
import threading
import time
import io
import socket

from PIL import Image

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
    QSizePolicy, QComboBox,
    QDialog, QDialogButtonBox, QSlider,
    QScrollArea
)
from PyQt5.QtCore import (
    Qt, QPoint, QSize, QPointF, QRect, QRectF,
    QPropertyAnimation, QEasingCurve,
    pyqtSignal, pyqtSlot, QThread, QMetaObject, Q_ARG,
    QTimer, QObject
)
from PyQt5.QtGui import QColor, QIcon, QPixmap, QCursor, QPainter, QPen, QBrush, QLinearGradient, QFont, QFontMetrics

from tools.lan_share.server import ScanServer

from ui.styles import (
    APP_STYLE, BAR_STYLE, MODE_BTN_STYLE, ICON_BTN_STYLE,
    CLOSE_BTN_STYLE, MINIMIZE_BTN_STYLE, KB_PILL_STYLE, KB_INPUT_STYLE,
    ACTION_BTN_STYLE, SECONDARY_BTN_STYLE, HINT_STYLE,
    LIST_FRAME_STYLE, LIST_WIDGET_STYLE, PROGRESS_STYLE,
    MENU_STYLE, COMBO_STYLE, PANEL_STYLE,
    SIDEBAR_STYLE, PDF_TOOL_BTN_STYLE,
    PDF_COMPACT_PANEL_STYLE, PDF_HEADER_STYLE, PDF_SUBTITLE_STYLE
)
from ui.helpers import (
    WIN_W, PANEL_H, CROP_PRESETS,
    make_divider, make_icon_btn, make_wm_btn,
    sep_widget, section_label, spin_col, grey_icon_path
)
from ui.canvases import CropCanvas, BgCanvas
from ui.pdf_canvas import PdfDropCanvas  # used by pdf_panel
from ui.pdf_panel import PdfToolPanel
from tools.compress.logic import CompressWorker
from tools.background_remove.logic import BgRemoveWorker


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
# NETWORK MONITOR
# =========================================
# BG Remove now depends on the remove.bg API, which needs an internet
# connection. This polls connectivity on a background thread (a quick raw
# socket connect, not a full HTTP request) and reports changes via a Qt
# signal so the UI thread can enable/disable the feature accordingly.

class NetworkMonitor(QObject):
    status_changed = pyqtSignal(bool)   # True = online, False = offline

    CHECK_HOST = "8.8.8.8"   # Google DNS — fast, reliable, no HTTP overhead
    CHECK_PORT = 53
    CHECK_TIMEOUT = 2.0      # seconds
    POLL_INTERVAL = 5.0      # seconds between checks

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_flag = threading.Event()
        self._last_status = None
        self._thread = None

    @staticmethod
    def _check_once():
        try:
            socket.setdefaulttimeout(NetworkMonitor.CHECK_TIMEOUT)
            with socket.create_connection(
                (NetworkMonitor.CHECK_HOST, NetworkMonitor.CHECK_PORT),
                timeout=NetworkMonitor.CHECK_TIMEOUT
            ):
                return True
        except OSError:
            return False

    def _run(self):
        while not self._stop_flag.is_set():
            online = self._check_once()
            if online != self._last_status:
                self._last_status = online
                self.status_changed.emit(online)
            self._stop_flag.wait(self.POLL_INTERVAL)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_flag.set()


# =========================================
# MAIN WINDOW
# =========================================

class _NullHintLabel:
    """No-op stand-in for the old 'Drop images anywhere...' hint label.

    The hint text below the top bar has been removed. Rather than hunting
    down and editing the ~28 scattered self.hint.show()/.hide()/.setText()
    calls throughout this file, this object just swallows them silently —
    nothing is ever actually shown.
    """
    def show(self): pass
    def hide(self): pass
    def setText(self, *a, **k): pass
    def setAlignment(self, *a, **k): pass
    def setStyleSheet(self, *a, **k): pass
    def isVisible(self): return False


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
        self._bgremove_done = False   # True only after an actual removal has run
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

        # "Idealized state": if the window stays hidden (via the Insert
        # hotkey) for 5s straight, release the memory-heavy canvases
        # (Crop / Bg Remove) rather than holding a full-res image in RAM
        # indefinitely while the app isn't even visible. A quick re-press
        # of Insert within the 5s window cancels this — see
        # toggle_visibility() / _enter_idealized_state() / _exit_idealized_state().
        self._idealize_timer = QTimer(self)
        self._idealize_timer.setSingleShot(True)
        self._idealize_timer.timeout.connect(self._enter_idealized_state)
        self._is_idealized = False

        self.hide()          # hide before setup_ui so processEvents() never shows it
        self.setup_ui()
        self.toggle_signal.connect(self.toggle_visibility)
        self.bg_remover = None

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

        # BG Remove needs internet (remove.bg API) — monitor connectivity
        # and enable/disable that tool accordingly.
        self._is_online = True   # optimistic until first check completes
        self._net_monitor = NetworkMonitor(self)
        self._net_monitor.status_changed.connect(self._on_network_status_changed)
        self._net_monitor.start()

    def _on_network_status_changed(self, online):
        self._is_online = online

        mode_btn = self._mode_btns.get(self.MODE_BGREMOVE)
        if mode_btn is not None:
            mode_btn.setEnabled(online)
            mode_btn.setToolTip(
                "BG Remove" if online else "BG Remove — requires an internet connection"
            )
            icon_path = self._mode_btn_icons.get(self.MODE_BGREMOVE, "")
            if not online:
                path = grey_icon_path(icon_path)
                if not os.path.exists(path):
                    path = icon_path   # fall back to the normal icon if grey asset is missing
            else:
                path = icon_path
            if os.path.exists(path):
                mode_btn.setIcon(QIcon(path))
                mode_btn.setIconSize(QSize(28, 28))

        if hasattr(self, 'bgremove_remove_btn'):
            # Only re-enable Remove if we're not mid-removal already
            running = self._bg_thread is not None and self._bg_thread_is_running()
            self.bgremove_remove_btn.setEnabled(online and not running and bool(self.bgremove_path) and not self._bgremove_done)
            if not online:
                self.bgremove_remove_btn.setToolTip("No internet connection")
            else:
                self.bgremove_remove_btn.setToolTip("")

        # If we're offline and currently sitting in BG Remove mode with
        # nothing loaded, bounce back to Compress rather than leaving the
        # user stuck on a dead tool.
        if not online and self.mode == self.MODE_BGREMOVE and self.bgremove_path is None:
            self._switch_mode(self.MODE_COMPRESS)

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
            if hasattr(self, '_bg_sidebar'):
                self._bg_sidebar.hide()
            # Start the 5s debounce — only actually release memory if the
            # window is still hidden when the timer fires.
            self._idealize_timer.start(5000)
        else:
            # Re-shown before 5s elapsed: nothing was released, just cancel.
            self._idealize_timer.stop()
            if self._is_idealized:
                # Already idealized (was hidden 5s+) — reload whatever the
                # active tool needs before the window becomes visible again.
                self._exit_idealized_state()
            self.show()
            self.activateWindow()
            self.raise_()
            if self.bgremove_panel.isVisible():
                QTimer.singleShot(50, self._show_bg_sidebar)

    # ------------------------------------------------------------------
    # IDEALIZED STATE — release memory after 5s hidden via Insert
    # ------------------------------------------------------------------
    def _enter_idealized_state(self):
        """Fired 5s after the window was hidden, if it's still hidden.

        Releases the memory-heavy Crop and Bg Remove canvases. Crop is
        always safe to release — reloading it just re-decodes the local
        file (any unsaved crop position/rotation/zoom will reset).
        Bg Remove is only released if nothing has been removed yet: an
        already-removed result cost a remove.bg API call, so we don't
        want the user to silently pay for that call again just because
        the window sat hidden for a few seconds.
        """
        if self.isVisible():
            return  # re-shown right as the timer fired — nothing to do
        self._is_idealized = True

        if not self._bg_thread_is_running():
            self.crop_canvas.unload()
            if not self._bgremove_done:
                self.bg_canvas.clear()

    def _exit_idealized_state(self):
        """Reload whatever the currently active tool needs, right before
        the window becomes visible again after having been idealized."""
        self._is_idealized = False
        if self.mode == self.MODE_CROP and self.crop_path:
            self._load_crop_image(self.crop_path)
        elif self.mode == self.MODE_BGREMOVE and self.bgremove_path and not self._bgremove_done:
            self._preview_bgremove_source(self.bgremove_path)

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
        self._build_compress_canvas()
        self._build_crop_panel()
        self._build_pdf_tool_panels()

        QApplication.processEvents()

        # Initial mode is compress — show hint only; drop zone appears when files are added
        self.compress_canvas.hide()
        self.compress_controls.show()
        self.hint.show()
        self.update_hint()
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
        self._mode_btn_icons = {}   # mode_id -> normal icon path (for grey-swap on disable)
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
            self._mode_btn_icons[mode_id] = icon_path
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
        # ki = QLabel("")
        # ki.setStyleSheet("color:rgba(255,255,255,0.30); font-size:14px;")
        # kbl.addWidget(ki)
        self.kb_input = QLineEdit()
        self.kb_input.setPlaceholderText("TARGET")
        self.kb_input.setToolTip("Default target KB for all files")
        self.kb_input.setFixedWidth(75)
        self.kb_input.setAlignment(Qt.AlignCenter)
        self.kb_input.setStyleSheet(KB_INPUT_STYLE)
        kbl.addWidget(self.kb_input)
        kl = QLabel("KB")
        kl.setStyleSheet("color:rgba(255,255,255,0.30); font-size:12px;")
        kbl.addWidget(kl)
        ccl.addWidget(kb_pill)
        ccl.addWidget(make_divider())
        self.compress_btn = QPushButton("COMPRESS")
        self.compress_btn.setFixedHeight(48)
        self.compress_btn.setMinimumWidth(120)
        self.compress_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.compress_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.compress_btn.clicked.connect(self.compress_all)
        ccl.addWidget(self.compress_btn)
        bl.addWidget(self.compress_controls)

        # Crop bar controls
        self.crop_bar_controls = QFrame()
        self.crop_bar_controls.setStyleSheet("background:transparent; border:none;")
        cbl = QHBoxLayout(self.crop_bar_controls)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.setSpacing(5)
        self.bar_ratio_combo = QComboBox()
        self.bar_ratio_combo.setFixedHeight(48)
        self.bar_ratio_combo.setMinimumWidth(180)
        self.bar_ratio_combo.setStyleSheet(COMBO_STYLE)
        for label, ratio, preset in CROP_PRESETS:
            if ratio == "sep":
                self.bar_ratio_combo.insertSeparator(self.bar_ratio_combo.count())
            else:
                self.bar_ratio_combo.addItem(label)
                idx = self.bar_ratio_combo.count() - 1
                self.bar_ratio_combo.setItemData(idx, (ratio, preset))
        self.bar_ratio_combo.currentIndexChanged.connect(self._on_bar_ratio)
        cbl.addWidget(self.bar_ratio_combo)
        cbl.addWidget(make_divider())
        self.crop_save_btn = QPushButton("CROP")
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
        self.bgremove_remove_btn = QPushButton("REMOVE")
        self.bgremove_remove_btn.setFixedHeight(48)
        self.bgremove_remove_btn.setMinimumWidth(110)
        self.bgremove_remove_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.bgremove_remove_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.bgremove_remove_btn.clicked.connect(self._trigger_bgremove)
        bgl.addWidget(self.bgremove_remove_btn)
        self.bgremove_save_btn = QPushButton("SAVE")
        self.bgremove_save_btn.setFixedHeight(48)
        self.bgremove_save_btn.setMinimumWidth(130)
        self.bgremove_save_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.bgremove_save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.bgremove_save_btn.setEnabled(False)   # nothing removed yet
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
        self.crop_dim_lbl = QLabel("")
        self.crop_dim_lbl.setStyleSheet("color:rgba(255,255,255,0.28); font-size:12px;")
        self.crop_dim_lbl.hide()
        bl.addWidget(self.crop_dim_lbl)

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
        # The "Drop images anywhere..." hint label has been removed.
        # self.hint is kept as a no-op so the many existing
        # .show()/.hide()/.setText() calls elsewhere stay valid.
        self.hint = _NullHintLabel()

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
        self._bgremove_done = False
        self._bgremove_result = None
        self.file_list.clear()
        # Release any full-resolution image data held by the crop/bg-remove
        # canvases — otherwise it stays resident in memory even though
        # nothing on screen references it anymore.
        if not self._bg_thread_is_running():
            self.crop_canvas.unload()
            self.bg_canvas.clear()
        self.crop_panel.hide()
        self.bgremove_panel.hide()
        self._bg_sidebar.hide()
        self.list_frame.hide()
        for p in self._pdf_panels.values():
            p.hide()
        self.crop_dim_lbl.hide()
        self.hint.show()
        self.update_hint()
        self._animate_size(self.bar.height() or 80)

    def _tray_send(self, path: str, ftype: str):
        """Route a tray file to whatever tool is currently active."""
        if self.mode == self.MODE_COMPRESS and ftype in ("image", "pdf"):
            if path not in self.files:
                self.files.append(path)
                self.update_hint()
                # Sync canvas
                self.compress_canvas.add_files([path])
            # Reveal the drop zone now that we have at least one file
            self.hint.hide()
            self.compress_canvas.show()
            self._animate_size((self.bar.height() or 80) + 10 + self.compress_canvas.preferred_height())
        elif self.mode == self.MODE_CROP and ftype == "image":
            self._load_crop_image(path)
        elif self.mode == self.MODE_BGREMOVE and ftype == "image":
            # Ignore if a removal is already in progress
            if self._bg_thread_is_running():
                return
            self._bgremove_result = None   # discard any cached result for the old image
            self._preview_bgremove_source(path)
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
        lfl.addWidget(self.file_list)
        self._outer.addWidget(self.list_frame)

    def _build_compress_canvas(self):
        self.compress_canvas = _CompressDropZone(parent=self)
        self.compress_canvas.browse_clicked.connect(self.add_files)
        self.compress_canvas.file_removed.connect(self._on_compress_canvas_remove)
        self.compress_canvas.hide()
        self._outer.addWidget(self.compress_canvas)

    def _build_crop_panel(self):
        self.crop_panel = QFrame()
        self.crop_panel.setObjectName("panel")
        self.crop_panel.setStyleSheet(PANEL_STYLE)
        self.crop_panel.setFixedHeight(PANEL_H)
        self.crop_panel.hide()
        layout = QHBoxLayout(self.crop_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.crop_canvas = CropCanvas()
        self.crop_canvas.crop_changed.connect(self._on_crop_changed)
        layout.addWidget(self.crop_canvas, 1)

        # --- Active preset tracking ---
        self._active_preset = None   # (name, w, h, dpi) or None

        # --- Sidebar ---
        sb = QFrame()
        sb.setFixedWidth(190)
        sb.setStyleSheet(SIDEBAR_STYLE)
        sbl = QVBoxLayout(sb)
        sbl.setContentsMargins(14, 18, 14, 18)
        sbl.setSpacing(12)

        # --- Image info ---
        self.crop_img_info = QLabel("—")
        self.crop_img_info.setStyleSheet("color:rgba(255,255,255,0.28); font-size:11px; border:none;")
        sbl.addWidget(self.crop_img_info)
        sbl.addWidget(sep_widget())
        sbl.addWidget(section_label("Transform"))

        # --- Rotation slider ---
        rot_header = QHBoxLayout()
        rot_lbl = QLabel("Rotate")
        rot_lbl.setStyleSheet("color:rgba(255,255,255,0.45); font-size:11px; border:none;")
        rot_header.addWidget(rot_lbl)
        rot_header.addStretch()
        self.rotate_angle_lbl = QLabel("0°")
        self.rotate_angle_lbl.setStyleSheet("color:rgba(255,255,255,0.70); font-size:11px; border:none; font-weight:600;")
        rot_header.addWidget(self.rotate_angle_lbl)
        self.rotate_reset_btn = QPushButton("")
        self.rotate_reset_btn.setIcon(QIcon(os.path.join("assets", "icons", "reset_rotate.png")))
        self.rotate_reset_btn.setIconSize(QSize(16, 16))
        self.rotate_reset_btn.setFixedSize(22, 22)
        self.rotate_reset_btn.setToolTip("Reset rotation")
        self.rotate_reset_btn.setStyleSheet("""
            QPushButton { background:rgba(255,255,255,0.07); border:1px solid rgba(255,255,255,0.10);
                          border-radius:6px; color:rgba(255,255,255,0.55); font-size:12px; }
            QPushButton:hover { background:rgba(255,255,255,0.14); color:white; }
        """)
        self.rotate_reset_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.rotate_reset_btn.clicked.connect(self._reset_rotation)
        rot_header.addWidget(self.rotate_reset_btn)
        sbl.addLayout(rot_header)

        self.rotate_slider = QSlider(Qt.Horizontal)
        self.rotate_slider.setRange(-180, 180)
        self.rotate_slider.setValue(0)
        self.rotate_slider.setTickInterval(45)
        self.rotate_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px;
                background: rgba(255,255,255,0.10);
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #5865F2;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px;
                margin: -5px 0;
                background: white;
                border-radius: 7px;
            }
        """)
        self.rotate_slider.valueChanged.connect(self._on_rotate_slider)
        sbl.addWidget(self.rotate_slider)

        sbl.addWidget(sep_widget())

        # --- Rotate 90° buttons ---
        rot90_row = QHBoxLayout()
        rot90_row.setSpacing(6)
        self.rot90_ccw_btn = QPushButton("")
        self.rot90_ccw_btn.setIcon(QIcon(os.path.join("assets", "icons", "rotate_ccw.png")))
        self.rot90_ccw_btn.setIconSize(QSize(28, 28))
        self.rot90_ccw_btn.setToolTip("Rotate 90° counter-clockwise")
        self.rot90_ccw_btn.setFixedSize(78, 44)
        self.rot90_ccw_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.rot90_ccw_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.rot90_ccw_btn.clicked.connect(self._rotate_90_ccw)
        rot90_row.addWidget(self.rot90_ccw_btn)
        self.rot90_cw_btn = QPushButton("")
        self.rot90_cw_btn.setIcon(QIcon(os.path.join("assets", "icons", "rotate_cw.png")))
        self.rot90_cw_btn.setIconSize(QSize(28, 28))
        self.rot90_cw_btn.setToolTip("Rotate 90° clockwise")
        self.rot90_cw_btn.setFixedSize(78, 44)
        self.rot90_cw_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.rot90_cw_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.rot90_cw_btn.clicked.connect(self._rotate_90_cw)
        rot90_row.addWidget(self.rot90_cw_btn)
        sbl.addLayout(rot90_row)

        # --- Flip buttons ---
        flip_row = QHBoxLayout()
        flip_row.setSpacing(6)
        self.flip_h_btn = QPushButton("")
        self.flip_h_btn.setIcon(QIcon(os.path.join("assets", "icons", "flip_h.png")))
        self.flip_h_btn.setIconSize(QSize(28, 28))
        self.flip_h_btn.setToolTip("Flip horizontally (mirror)")
        self.flip_h_btn.setFixedSize(78, 44)
        self.flip_h_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.flip_h_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.flip_h_btn.clicked.connect(self._flip_crop_h)
        flip_row.addWidget(self.flip_h_btn)
        self.flip_v_btn = QPushButton("")
        self.flip_v_btn.setIcon(QIcon(os.path.join("assets", "icons", "flip_v.png")))
        self.flip_v_btn.setIconSize(QSize(28, 28))
        self.flip_v_btn.setToolTip("Flip vertically")
        self.flip_v_btn.setFixedSize(78, 44)
        self.flip_v_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.flip_v_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.flip_v_btn.clicked.connect(self._flip_crop_v)
        flip_row.addWidget(self.flip_v_btn)
        sbl.addLayout(flip_row)

        # --- Quick preset buttons ---
        sbl.addWidget(sep_widget())
        sbl.addWidget(section_label("Quick Presets"))
        quick_row1 = QHBoxLayout()
        quick_row1.setSpacing(6)
        self.preset_passport_btn = QPushButton("")
        self.preset_passport_btn.setIcon(QIcon(os.path.join("assets", "icons", "preset_passport.png")))
        self.preset_passport_btn.setIconSize(QSize(28, 28))
        self.preset_passport_btn.setToolTip("Passport (India) 35×45mm")
        self.preset_passport_btn.setFixedSize(78, 44)
        self.preset_passport_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.preset_passport_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.preset_passport_btn.clicked.connect(
            lambda: self._select_preset_by_label("Passport (India) 35×45mm")
        )
        quick_row1.addWidget(self.preset_passport_btn)
        self.preset_youtube_btn = QPushButton("")
        self.preset_youtube_btn.setIcon(QIcon(os.path.join("assets", "icons", "preset_youtube.png")))
        self.preset_youtube_btn.setIconSize(QSize(28, 28))
        self.preset_youtube_btn.setToolTip("YouTube Thumbnail")
        self.preset_youtube_btn.setFixedSize(78, 44)
        self.preset_youtube_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.preset_youtube_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.preset_youtube_btn.clicked.connect(
            lambda: self._select_preset_by_label("YouTube Thumbnail")
        )
        quick_row1.addWidget(self.preset_youtube_btn)
        sbl.addLayout(quick_row1)

        quick_row2 = QHBoxLayout()
        quick_row2.setSpacing(6)
        self.preset_ig_story_btn = QPushButton("")
        self.preset_ig_story_btn.setIcon(QIcon(os.path.join("assets", "icons", "preset_ig_story.png")))
        self.preset_ig_story_btn.setIconSize(QSize(28, 28))
        self.preset_ig_story_btn.setToolTip("Instagram Story")
        self.preset_ig_story_btn.setFixedSize(78, 44)
        self.preset_ig_story_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.preset_ig_story_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.preset_ig_story_btn.clicked.connect(
            lambda: self._select_preset_by_label("Instagram Story")
        )
        quick_row2.addWidget(self.preset_ig_story_btn)
        self.preset_x_btn = QPushButton("")
        self.preset_x_btn.setIcon(QIcon(os.path.join("assets", "icons", "preset_x_post.png")))
        self.preset_x_btn.setIconSize(QSize(28, 28))
        self.preset_x_btn.setToolTip("X / Twitter Post")
        self.preset_x_btn.setFixedSize(78, 44)
        self.preset_x_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.preset_x_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.preset_x_btn.clicked.connect(
            lambda: self._select_preset_by_label("X / Twitter Post")
        )
        quick_row2.addWidget(self.preset_x_btn)
        sbl.addLayout(quick_row2)

        hint = QLabel("Scroll to zoom\nDouble-click to reset view")
        hint.setStyleSheet("color:rgba(255,255,255,0.18); font-size:10px; border:none;")
        sbl.addWidget(hint)
        sbl.addStretch()
        layout.addWidget(sb)
        self._outer.addWidget(self.crop_panel)


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

    def _reveal_bg_sidebar(self):
        """Show/reposition the bg-color sidebar. Call this once the window's
        resize animation has actually finished, not before — positioning
        depends on the panel's final geometry."""
        if self._bg_sidebar.isVisible():
            self._position_bg_sidebar()
        else:
            self._show_bg_sidebar()

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
        self.compress_canvas.hide()
        self.compress_canvas.clear()
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
        self.crop_dim_lbl.hide()
        self.hint.show()
        self._animate_size(self.bar.height() or 80)

        for m, btn in self._mode_btns.items():
            btn.setChecked(m == mode)

        if mode == self.MODE_COMPRESS:
            self.compress_controls.show()
            # re-populate compress list from tray (images + pdfs)
            tray_paths = []
            for entry in self._tray:
                if entry["type"] in ("image", "pdf"):
                    p = entry["path"]
                    self.files.append(p)
                    tray_paths.append(p)
            if tray_paths:
                self.compress_canvas.add_files(tray_paths)
                self.hint.hide()
                self.compress_canvas.show()
                self._animate_size((self.bar.height() or 80) + 10 + self.compress_canvas.preferred_height())
            else:
                self.hint.show()
                self.update_hint()
                self._animate_size(self.bar.height() or 80)
        elif mode == self.MODE_CROP:
            self.crop_bar_controls.show()
            imgs = [e["path"] for e in self._tray if e["type"] == "image"]
            if self.crop_path is not None and self.crop_canvas._pil is not None:
                # Revisit — canvas already loaded, just restore the panel instantly
                self.hint.hide()
                self.crop_dim_lbl.show()
                self.crop_panel.show()
                self._animate_size((self.bar.height() or 80) + 10 + PANEL_H)
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
                self.hint.hide()
                self._apply_bgremove_result(pil_rgba, src_rgb)
            # If the worker is still running, restore the panel + in-progress state
            elif self._bg_thread is not None and self._bg_thread_is_running():
                self.hint.hide()
                self.bgremove_panel.show()
                self._animate_size((self.bar.height() or 80) + 10 + PANEL_H)
                self._anim.finished.connect(self._reveal_bg_sidebar)
                self.bgremove_remove_btn.setEnabled(False)
                self.bgremove_remove_btn.setText("REMOVING\u2026")
                self.bgremove_save_btn.setEnabled(False)
            # Already removed — restore instantly, sidebar included, no re-run
            elif self._bgremove_done and self.bg_canvas._base_pix is not None and self.bgremove_path is not None:
                self.hint.hide()
                self.bgremove_panel.show()
                self._animate_size((self.bar.height() or 80) + 10 + PANEL_H)
                self._anim.finished.connect(self._reveal_bg_sidebar)
                self.bgremove_remove_btn.setEnabled(False)
                self.bgremove_remove_btn.setText("REMOVE")
                self.bgremove_save_btn.setEnabled(True)
            # Preview-only (loaded but not yet removed) — restore instantly, sidebar included
            elif self.bgremove_path is not None and self.bg_canvas._base_pix is not None:
                self.hint.hide()
                self.bgremove_panel.show()
                self._animate_size((self.bar.height() or 80) + 10 + PANEL_H)
                self._anim.finished.connect(self._reveal_bg_sidebar)
                self.bgremove_remove_btn.setEnabled(True)
                self.bgremove_remove_btn.setText("REMOVE")
                self.bgremove_save_btn.setEnabled(False)
            else:
                # Nothing loaded yet — load from tray if available, as a preview only
                self.bgremove_path = None
                self.hint.setText("Drop an image  ·  then click Remove")
                imgs = [e["path"] for e in self._tray if e["type"] == "image"]
                if imgs:
                    self._preview_bgremove_source(imgs[-1])
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
            # Reset rotation slider without triggering the handler
            self.rotate_slider.blockSignals(True)
            self.rotate_slider.setValue(0)
            self.rotate_angle_lbl.setText("0°")
            self.rotate_slider.blockSignals(False)
            self._clear_preset()
            self.hint.hide()
            self.crop_dim_lbl.show()
            self.bgremove_panel.hide()
            self.list_frame.hide()
            for p in self._pdf_panels.values():
                p.hide()
            self.crop_panel.show()
            self._animate_size((self.bar.height() or 80) + 10 + PANEL_H)
        except Exception as ex:
            QMessageBox.critical(self, "Load error", str(ex))

    def _on_crop_changed(self, x, y, w, h):
        self.crop_dim_lbl.setText(f"{max(1, w)} × {max(1, h)} px")

    def _on_bar_ratio(self, idx):
        data = self.bar_ratio_combo.itemData(idx)
        if data is None:
            return  # separator row
        ratio, preset_info = data

        if ratio is None:
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
            else:
                self._active_preset = None

    def _select_preset_by_label(self, label):
        """Select a crop preset in the ratio combo by its label text."""
        idx = self.bar_ratio_combo.findText(label)
        if idx >= 0:
            self.bar_ratio_combo.setCurrentIndex(idx)

    def _reset_crop(self):
        self.bar_ratio_combo.blockSignals(True)
        self.bar_ratio_combo.setCurrentIndex(0)
        self.bar_ratio_combo.blockSignals(False)
        self.crop_canvas.set_aspect(None)
        self.crop_canvas.reset_zoom()
        self._clear_preset()

    def _on_rotate_slider(self, value):
        """Called whenever the rotation slider moves."""
        if not self.crop_path:
            return
        self.rotate_angle_lbl.setText(f"{value}°")
        self.crop_canvas.rotate_image(value)
        pil = self.crop_canvas._pil
        if pil:
            self.crop_img_info.setText(
                f"{pil.width} \u00d7 {pil.height} px\n{os.path.basename(self.crop_path)}"
            )

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
        self.rotate_angle_lbl.setText("0°")
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
        self.rotate_angle_lbl.setText("0°")
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

    # ── Preset helpers ───────────────────────────────────────────────────
    def _clear_preset(self):
        """Clear active preset tracking."""
        self._active_preset = None

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
            self.crop_save_btn.setText("✓  Saved!")
            QApplication.processEvents()
            time.sleep(0.8)
            self.crop_save_btn.setText("✂  Crop & Save")
        except Exception as ex:
            QMessageBox.critical(self, "Save failed", str(ex))



    # ------------------------------------------------------------------
    # BG REMOVE
    # ------------------------------------------------------------------

    def _preview_bgremove_source(self, path):
        """Show the original image as-is — no removal runs until the user
        clicks the Remove button."""
        if self._bg_thread_is_running():
            return
        try:
            orig = Image.open(path).convert("RGB")
        except Exception:
            return
        self.bgremove_path = path
        self._bgremove_done = False
        self._bgremove_result = None

        self.hint.hide()
        self.crop_panel.hide()
        for p in self._pdf_panels.values():
            p.hide()

        # Show as a fully-opaque preview — nothing has been removed yet
        self.bg_canvas.set_image(orig.convert("RGBA"), orig)
        self.bg_canvas.set_bg_color(None)
        self.bgremove_panel.show()
        self._animate_size((self.bar.height() or 80) + 10 + PANEL_H)
        self._anim.finished.connect(self._reveal_bg_sidebar)

        self.bgremove_remove_btn.setEnabled(True)
        self.bgremove_remove_btn.setText("REMOVE")
        self.bgremove_save_btn.setEnabled(False)

    def _trigger_bgremove(self):
        """User clicked Remove — actually run background removal now."""
        if not self.bgremove_path or self._bg_thread_is_running():
            return
        if not getattr(self, '_is_online', True):
            QMessageBox.warning(self, "No internet connection",
                "Background removal needs an internet connection to reach the remove.bg API.")
            return
        self._load_bgremove(self.bgremove_path)

    def _load_bgremove(self, path):
        self.bgremove_path = path
        self.bgremove_remove_btn.setEnabled(False)
        self.bgremove_remove_btn.setText("REMOVING\u2026")
        self.bgremove_save_btn.setEnabled(False)

        self._bg_thread = QThread(self)
        self._bg_worker = BgRemoveWorker(
            path,
            remover=self.bg_remover
        )
        self._bg_worker.moveToThread(self._bg_thread)
        self._bg_thread.started.connect(self._bg_worker.run)
        self._bg_worker.finished.connect(self._on_bgremove_done)
        self._bg_worker.error.connect(self._on_bgremove_error)
        self._bg_worker.finished.connect(self._bg_thread.quit)
        self._bg_worker.error.connect(self._bg_thread.quit)
        self._bg_thread.finished.connect(self._bg_worker.deleteLater)
        self._bg_thread.finished.connect(self._bg_thread.deleteLater)
        self._bg_thread.start()

    def _on_bg_thread_done(self):
        pass

    def _bg_thread_is_running(self):
        """Safe isRunning() — returns False if the C++ object has been deleted."""
        try:
            return self._bg_thread is not None and self._bg_thread.isRunning()
        except RuntimeError:
            self._bg_thread = None
            self._bg_worker = None
            return False

    def _compress_thread_is_running(self):
        """Safe isRunning() for self._thread — handles deleted C++ QThread."""
        try:
            return self._thread is not None and self._thread.isRunning()
        except RuntimeError:
            self._thread = None
            self._worker = None
            return False

    def _on_bgremove_done(self, pil_rgba, remover):
        self.bg_remover = remover
        self._bgremove_done = True
        self.bgremove_remove_btn.setEnabled(False)
        self.bgremove_remove_btn.setText("REMOVE")
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
        self.bgremove_save_btn.setEnabled(True)
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
        self._animate_size((self.bar.height() or 80) + 10 + PANEL_H)
        self._anim.finished.connect(self._reveal_bg_sidebar)

    def _show_bg_sidebar(self):
        self._bg_sidebar.show()
        self._bg_sidebar.raise_()
        self._position_bg_sidebar()
        # Reset swatches scroll to top — guards against any phantom wheel events
        # that may have shifted it during a previous drag session.
        if hasattr(self, '_bg_swatches_scroll'):
            self._bg_swatches_scroll.verticalScrollBar().setValue(0)

    def _on_bgremove_error(self, msg):
        self.bgremove_remove_btn.setEnabled(True)
        self.bgremove_remove_btn.setText("REMOVE")
        QMessageBox.critical(self, "Error",
            f"Background removal failed:\n{msg}\n\n"
            "Make sure REMOVEBG_API_KEY is set and you have an internet "
            "connection to reach the remove.bg API.")

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
            self.bgremove_save_btn.setText("SAVE")
        except Exception as ex:
            QMessageBox.critical(self, "Save failed", str(ex))

    # ------------------------------------------------------------------
    # COMPRESS
    # ------------------------------------------------------------------
    def _on_compress_canvas_remove(self, path):
        """Handle a file removed from the compress drop zone."""
        if path in self.files:
            self.files.remove(path)
        # Also remove from tray
        self._tray = [e for e in self._tray if e["path"] != path]
        self._refresh_tray()
        self.update_hint()
        if self.files:
            self._animate_size((self.bar.height() or 80) + 10 + self.compress_canvas.preferred_height())
        else:
            # Last file removed — hide canvas, show hint like crop mode
            self.compress_canvas.hide()
            self.hint.show()
            self._animate_size(self.bar.height() or 80)

    def compress_all(self, force_compress=False):
        if not self.files:
            QMessageBox.warning(self, "No files", "Add some images or PDFs first.")
            return

        try:
            global_target = int(self.kb_input.text())
            if global_target <= 0:
                global_target = None
        except ValueError:
            global_target = None

        # Collect per-file targets from cards
        file_targets = self.compress_canvas.get_file_targets()

        # Validate: every file must have a valid target
        all_valid = True
        files_with_targets = []
        for path, target_kb in file_targets:
            card = self.compress_canvas.get_card_by_path(path)
            
            final_target = target_kb if target_kb is not None else global_target

            if final_target is None:
                all_valid = False
                if card:
                    card.mark_invalid_target(True)
            else:
                if card:
                    card.mark_invalid_target(False)
                files_with_targets.append((path, final_target))

        if not all_valid:
            QMessageBox.warning(self, "Missing targets",
                                "Please enter a target size in the main input box or for each file.")
            return

        self._quality_limited_files = []   # track files that hit quality limits
        self._compress_file_targets = files_with_targets  # keep reference

        # Mark all cards as compressing
        for path, _kb in files_with_targets:
            card = self.compress_canvas.get_card_by_path(path)
            if card:
                card.set_compressing()

        self.compress_btn.setEnabled(False)
        self.compress_btn.setText("COMPRESSING\u2026")
        self.add_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.progress_bar.setMaximum(len(files_with_targets))
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        self._thread = QThread(self)
        self._worker = CompressWorker(files_with_targets, "compressed_images",
                                      force_compress=force_compress)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_done)
        self._worker.error.connect(self._on_err)
        self._worker.quality_limit.connect(self._on_quality_limit)
        self._worker.finished.connect(self._on_all_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_quality_limit(self, i, filename, achieved_kb, target_kb, output_path):
        """Handle a file that couldn't reach target size without quality loss.
        Shows an inline warning inside the file card — no popup dialog."""
        ext = os.path.splitext(filename)[1].lower()
        file_type = "PDF" if ext == '.pdf' else "Image"
        source_path = self._compress_file_targets[i][0] if hasattr(self, '_compress_file_targets') and i < len(self._compress_file_targets) else None

        info = {
            'index': i,
            'filename': filename,
            'achieved_kb': achieved_kb,
            'target_kb': target_kb,
            'output_path': output_path,
            'source_path': source_path,
            'type': file_type,
        }
        self._quality_limited_files.append(info)
        self.progress_bar.setValue(i + 1)

        if source_path:
            card = self.compress_canvas.get_card_by_path(source_path)
            if card:
                # Show the inline warning with keep/force buttons
                card.set_status("warning",
                    f"Compressed to {achieved_kb:.0f} KB · quality limit reached")

                def on_keep(_=False, info=info, card=card):
                    def _do_keep():
                        card.hide_quality_warning()
                        card.set_status("warning",
                            f"Kept at {info['achieved_kb']:.0f} KB (target: {info['target_kb']:.0f} KB)")
                        if info in self._quality_limited_files:
                            self._quality_limited_files.remove(info)
                        self.compress_canvas._sync_state()
                        self._animate_size((self.bar.height() or 80) + 10 + self.compress_canvas.preferred_height())
                    QTimer.singleShot(0, _do_keep)

                def on_force(_=False, info=info, card=card):
                    def _do_force():
                        card.hide_quality_warning()
                        self.compress_canvas._sync_state()
                        self._quality_limited_files = [info]
                        self._force_recompress_files()
                    QTimer.singleShot(0, _do_force)

                card.show_quality_warning(achieved_kb, target_kb, on_keep, on_force)
                # Re-size window to fit the expanded card
                QTimer.singleShot(50, lambda: self._animate_size(
                    (self.bar.height() or 80) + 10 + self.compress_canvas.preferred_height()))

    def _on_done(self, i, text):
        """Handle successful compression of a file — update its card."""
        if hasattr(self, '_compress_file_targets') and i < len(self._compress_file_targets):
            path = self._compress_file_targets[i][0]
            card = self.compress_canvas.get_card_by_path(path)
            if card:
                card.set_status("success", text)
        self.progress_bar.setValue(i + 1)

    def _on_err(self, i, msg):
        """Handle compression error — update the card."""
        if hasattr(self, '_compress_file_targets') and i < len(self._compress_file_targets):
            path = self._compress_file_targets[i][0]
            card = self.compress_canvas.get_card_by_path(path)
            if card:
                card.set_status("error", f"Error: {msg}")
        self.progress_bar.setValue(self.progress_bar.value() + 1)

    def _on_all_done(self):
        # Any scanned files that were compressed are now saved — remove from temps
        for p in list(self.files):
            self._scan_temps.discard(p)
        self.compress_btn.setEnabled(True)
        self.compress_btn.setText("COMPRESS")
        self.add_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.progress_bar.hide()
        self.compress_canvas.set_all_enabled(True)
        # Inline quality warnings are already shown per-card in _on_quality_limit.
        # Re-enable the target inputs on non-limited cards.
        if hasattr(self, '_compress_file_targets'):
            limited_paths = {f['source_path'] for f in self._quality_limited_files}
            for path, _kb in self._compress_file_targets:
                if path not in limited_paths:
                    card = self.compress_canvas.get_card_by_path(path)
                    if card:
                        card.target_input.setEnabled(True)

    def _show_quality_limit_dialog(self):
        """Show a styled per-file quality warning dialog matching the spec."""
        limited = self._quality_limited_files
        if not limited:
            return

        # We show one dialog per limited file so the user can decide for each
        force_list = []
        for f in limited:
            do_force = self._show_single_quality_dialog(f)
            if do_force:
                force_list.append(f)

        if force_list:
            self._quality_limited_files = force_list
            self._force_recompress_files()
        else:
            self._quality_limited_files = []

    def _show_single_quality_dialog(self, f):
        """Show quality warning for a single file. Returns True if user chose 'Compress Further'."""
        filename = f['filename']
        achieved_kb = f['achieved_kb']
        target_kb = f['target_kb']

        dlg = QDialog(self)
        dlg.setWindowTitle("Quality Warning")
        dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        dlg.setAttribute(Qt.WA_TranslucentBackground)
        dlg.setFixedWidth(480)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setObjectName("qlDialog")
        frame.setStyleSheet("""
            QFrame#qlDialog {
                background: #1a1a1f;
                border-radius: 20px;
                border: 1px solid rgba(255, 196, 0, 0.20);
            }
        """)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(28, 26, 28, 26)
        fl.setSpacing(14)

        # ── Title row ──────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        warn_icon = QLabel("\u26a0\ufe0f")
        warn_icon.setStyleSheet("font-size: 20px; border: none;")
        title_row.addWidget(warn_icon)
        title_lbl = QLabel("Quality Warning")
        title_lbl.setStyleSheet("""
            color: #ffc400;
            font-size: 16px;
            font-weight: 700;
            border: none;
        """)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        fl.addLayout(title_row)

        # ── Main description ───────────────────────────────────────────
        desc_lbl = QLabel(
            f"The requested target size for <b>{filename}</b> is too small and may cause "
            f"noticeable quality loss, distortion, blurred text, pixelation, or reduced readability."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("""
            color: rgba(255,255,255,0.80);
            font-size: 13px;
            border: none;
            line-height: 1.5;
        """)
        fl.addWidget(desc_lbl)

        # ── Info box ──────────────────────────────────────────────────
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 196, 0, 0.06);
                border: 1px solid rgba(255, 196, 0, 0.15);
                border-radius: 12px;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 14, 16, 14)
        info_layout.setSpacing(8)

        achievable_row = QHBoxLayout()
        achievable_lbl = QLabel("Current achievable size without significant quality loss:")
        achievable_lbl.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 12px; border: none;")
        achievable_row.addWidget(achievable_lbl)
        achievable_row.addStretch()
        achievable_val = QLabel(f"<b>{achieved_kb:.0f} KB</b>")
        achievable_val.setStyleSheet("color: rgba(74,222,128,0.90); font-size: 13px; font-weight: 700; border: none;")
        achievable_row.addWidget(achievable_val)
        info_layout.addLayout(achievable_row)

        requested_row = QHBoxLayout()
        requested_lbl = QLabel("Requested size:")
        requested_lbl.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 12px; border: none;")
        requested_row.addWidget(requested_lbl)
        requested_row.addStretch()
        requested_val = QLabel(f"<b>{target_kb:.0f} KB</b>")
        requested_val.setStyleSheet("color: rgba(248,113,113,0.90); font-size: 13px; font-weight: 700; border: none;")
        requested_row.addWidget(requested_val)
        info_layout.addLayout(requested_row)

        fl.addWidget(info_frame)

        # ── Question ──────────────────────────────────────────────────
        q_lbl = QLabel("Do you want to continue compressing this file further?")
        q_lbl.setWordWrap(True)
        q_lbl.setStyleSheet("""
            color: rgba(255,255,255,0.70);
            font-size: 13px;
            font-weight: 600;
            border: none;
        """)
        fl.addWidget(q_lbl)

        # ── Buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        keep_btn = QPushButton("Keep Best Quality")
        keep_btn.setFixedHeight(44)
        keep_btn.setStyleSheet("""
            QPushButton {
                background: rgba(74, 222, 128, 0.10);
                border: 1px solid rgba(74, 222, 128, 0.25);
                border-radius: 12px;
                color: rgba(74, 222, 128, 0.90);
                font-size: 13px;
                font-weight: 600;
                padding: 0px 18px;
            }
            QPushButton:hover {
                background: rgba(74, 222, 128, 0.18);
                border-color: rgba(74, 222, 128, 0.45);
                color: rgb(74, 222, 128);
            }
            QPushButton:pressed { background: rgba(74, 222, 128, 0.25); }
        """)
        keep_btn.setCursor(QCursor(Qt.PointingHandCursor))
        keep_btn.clicked.connect(dlg.accept)  # accept = keep quality
        btn_row.addWidget(keep_btn)

        force_btn = QPushButton("Compress Further")
        force_btn.setFixedHeight(44)
        force_btn.setToolTip("The file may become distorted and some content quality may be permanently reduced.")
        force_btn.setStyleSheet("""
            QPushButton {
                background: rgba(248, 113, 113, 0.12);
                border: 1px solid rgba(248, 113, 113, 0.30);
                border-radius: 12px;
                color: rgba(248, 113, 113, 0.90);
                font-size: 13px;
                font-weight: 700;
                padding: 0px 18px;
            }
            QPushButton:hover {
                background: rgba(248, 113, 113, 0.22);
                border-color: rgba(248, 113, 113, 0.55);
                color: rgb(248, 113, 113);
            }
            QPushButton:pressed { background: rgba(248, 113, 113, 0.30); }
        """)
        force_btn.setCursor(QCursor(Qt.PointingHandCursor))
        force_btn.clicked.connect(dlg.reject)  # reject = force compress
        btn_row.addWidget(force_btn)

        fl.addLayout(btn_row)

        # ── Footer warning ─────────────────────────────────────────────
        footer_lbl = QLabel(
            "If you choose Compress Further, the file may become distorted and "
            "some content quality may be permanently reduced."
        )
        footer_lbl.setWordWrap(True)
        footer_lbl.setStyleSheet("""
            color: rgba(255,255,255,0.30);
            font-size: 11px;
            border: none;
        """)
        fl.addWidget(footer_lbl)

        outer.addWidget(frame)

        result = dlg.exec_()
        return result == QDialog.Rejected  # True = user chose "Compress Further"

    def _force_recompress_files(self):
        """Re-run compression on quality-limited files with force=True."""
        if not hasattr(self, '_quality_limited_files') or not self._quality_limited_files:
            return
        # Guard: don't start if a thread is already running
        if self._compress_thread_is_running():
            return

        limited = self._quality_limited_files

        # Always use the original requested target_kb for force compress
        # (do NOT re-read the card input — use what the user originally asked for)
        files_with_targets = [
            (f['source_path'], int(f['target_kb']))
            for f in limited
            if f.get('source_path')
        ]

        if not files_with_targets:
            return

        # UI: mark buttons/progress
        self.compress_btn.setEnabled(False)
        self.compress_btn.setText("FORCE COMPRESSING\u2026")
        self.add_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.progress_bar.setMaximum(len(files_with_targets))
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        # Mark the limited cards as in-progress
        for info in limited:
            card = self.compress_canvas.get_card_by_path(info.get('source_path', ''))
            if card:
                card.set_compressing()

        self._force_limited_info = list(limited)   # copy, indexed by i
        self._quality_limited_files = []           # reset

        self._thread = QThread(self)
        self._worker = CompressWorker(files_with_targets, "compressed_images",
                                      force_compress=True)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_force_done)
        self._worker.error.connect(self._on_force_err)
        self._worker.finished.connect(self._on_force_all_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_force_done(self, i, text):
        """Force compression succeeded for file i — update its card to amber success."""
        if hasattr(self, '_force_limited_info') and i < len(self._force_limited_info):
            path = self._force_limited_info[i].get('source_path', '')
            card = self.compress_canvas.get_card_by_path(path)
            if card:
                # Show as warning-tinted success (force-compressed)
                card.set_status("warning", text)
        self.progress_bar.setValue(i + 1)

    def _on_force_err(self, i, msg):
        """Force compression error for file i — update its card."""
        if hasattr(self, '_force_limited_info') and i < len(self._force_limited_info):
            path = self._force_limited_info[i].get('source_path', '')
            fn = self._force_limited_info[i]['filename']
            card = self.compress_canvas.get_card_by_path(path)
            if card:
                card.set_status("error", f"{fn} \u2014 {msg}")
        self.progress_bar.setValue(self.progress_bar.value() + 1)

    def _on_force_all_done(self):
        """All force compressions finished."""
        self.compress_btn.setEnabled(True)
        self.compress_btn.setText("COMPRESS")
        self.add_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.progress_bar.hide()
        # Re-enable all card inputs and remove buttons
        self.compress_canvas.set_all_enabled(True)
        # Resize window to fit any card height changes
        QTimer.singleShot(50, lambda: self._animate_size(
            (self.bar.height() or 80) + 10 + self.compress_canvas.preferred_height()))

    # ------------------------------------------------------------------
    # SHARED
    # ------------------------------------------------------------------
    def clear_files(self):
        if self._compress_thread_is_running():
            return
        self._tray.clear()
        self._refresh_tray()
        self.files = []
        self.crop_path = None
        self.bgremove_path = None
        self._bgremove_done = False
        self._bgremove_result = None
        self.file_list.clear()
        self.compress_canvas.clear()
        # Release any full-resolution image data held by the crop/bg-remove
        # canvases — otherwise it stays resident in memory even though
        # nothing on screen references it anymore.
        if not self._bg_thread_is_running():
            self.crop_canvas.unload()
            self.bg_canvas.clear()
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
        self.crop_dim_lbl.hide()
        if self.mode == self.MODE_COMPRESS:
            self.compress_canvas.hide()
            self.hint.show()
            self.update_hint()
            self._animate_size(self.bar.height() or 80)
        else:
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
        if n == 0:    self.hint.setText("Drop images or PDFs anywhere  ·  0 files loaded")
        elif n == 1:  self.hint.setText("1 file loaded  ·  ready to compress")
        else:         self.hint.setText(f"{n} files loaded  ·  ready to compress")

    def _animate_size(self, target_h):
        if self.mode == self.MODE_COMPRESS and hasattr(self, 'compress_canvas'):
            self.compress_canvas._sync_state()
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
        if getattr(self, '_net_monitor', None):
            self._net_monitor.stop()
        if self._scan_server:
            self._scan_server.stop()
        self._delete_scan_temps()
        if self._worker:
            self._worker.cancel()
        if self._compress_thread_is_running():
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
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(14)

        # QR image
        if server.qr_pil:
            buf = io.BytesIO()
            server.qr_pil.save(buf, "PNG")
            buf.seek(0)
            qr_pix = QPixmap()
            qr_pix.loadFromData(buf.read())
            qr_lbl = QLabel()
            qr_lbl.setPixmap(
                qr_pix.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
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

        # Steps beside QR, same height
        steps_col = QVBoxLayout()
        steps_col.setSpacing(0)
        steps_col.setContentsMargins(0, 0, 0, 0)
        steps_col.addStretch()
        for i, (icon, title, sub) in enumerate([
            ("📶", "Same Wi-Fi", "Phone & PC on\nsame network"),
            ("📷", "Scan QR",    "Point camera\nat the code"),
            ("🖼", "Send Photo", "Pick photo —\narrives instantly"),
        ]):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setContentsMargins(0, 0, 0, 0)
            icon_lbl = QLabel(icon)
            icon_lbl.setFixedSize(28, 28)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet(
                "background:rgba(88,101,242,0.18); border-radius:8px;"
                "font-size:13px; border:none;")
            row.addWidget(icon_lbl)
            text_col = QVBoxLayout()
            text_col.setSpacing(1)
            text_col.setContentsMargins(0, 0, 0, 0)
            t = QLabel(title)
            t.setStyleSheet(
                "color:rgba(255,255,255,0.88); font-size:11px;"
                "font-weight:700; border:none;")
            s = QLabel(sub)
            s.setStyleSheet(
                "color:rgba(255,255,255,0.38); font-size:10px; border:none;")
            text_col.addWidget(t)
            text_col.addWidget(s)
            row.addLayout(text_col)
            row.addStretch()
            steps_col.addLayout(row)
            if i < 2:
                steps_col.addSpacing(10)
        steps_col.addStretch()
        cl.addLayout(steps_col)

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


# =========================================
# COMPRESS DROP ZONE (display-only, no own drops)
# =========================================

def _file_size_str(path):
    kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
    return f"{kb:.1f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"


# ── Individual file card ────────────────────────────────────────────

class _CompressFileCard(QFrame):
    """A single file card with thumbnail, info, target KB input, and inline quality warning."""
    removed = pyqtSignal(str)   # emits path

    CARD_STYLE = """
    QFrame#fileCard {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
    }
    QFrame#fileCard[status="success"] {
        border-color: rgba(74,222,128,0.35);
        background: rgba(74,222,128,0.06);
    }
    QFrame#fileCard[status="warning"] {
        border-color: rgba(255,196,0,0.35);
        background: rgba(255,196,0,0.04);
    }
    QFrame#fileCard[status="error"] {
        border-color: rgba(248,113,113,0.35);
        background: rgba(248,113,113,0.06);
    }
    """

    def __init__(self, path, preview_pixmap=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.setObjectName("fileCard")
        self.setStyleSheet(self.CARD_STYLE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        ext = os.path.splitext(path)[1].lower()
        self._is_pdf = ext == '.pdf'
        self._file_type = "PDF" if self._is_pdf else "Image"

        # ── Outer: VBox so the warning section can grow below ──────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(0)

        # ── Top row: thumb + info + target + remove ────────────────────
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(12)

        # Thumbnail
        self._thumb = QLabel()
        self._thumb.setFixedSize(52, 60)
        self._thumb.setAlignment(Qt.AlignCenter)
        if preview_pixmap and not preview_pixmap.isNull():
            scaled = preview_pixmap.scaled(
                52, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._thumb.setPixmap(scaled)
            self._thumb.setStyleSheet(
                "background: #16161a; border-radius: 8px; border: none;")
        else:
            badge_text = ext.replace('.', '').upper()[:3] or 'FILE'
            self._thumb.setText(badge_text)
            self._thumb.setStyleSheet("""
                background: rgba(88,101,242,0.18);
                border-radius: 8px;
                color: #e4e7ff;
                font-size: 11px;
                font-weight: bold;
                border: none;
            """)
        top_row.addWidget(self._thumb)

        # Info column
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(3)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name = os.path.basename(path)
        short = name if len(name) <= 28 else name[:25] + "…"
        self._name_lbl = QLabel(short)
        self._name_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.88); font-size: 13px; font-weight: 700; border: none;")
        name_row.addWidget(self._name_lbl)

        type_badge = QLabel(self._file_type)
        type_badge.setFixedHeight(20)
        type_badge.setStyleSheet("""
            background: rgba(88,101,242,0.16);
            color: #a5b4fc;
            font-size: 10px;
            font-weight: 600;
            border-radius: 6px;
            padding: 0px 8px;
            border: none;
        """)
        name_row.addWidget(type_badge)
        name_row.addStretch()
        info_col.addLayout(name_row)

        orig_size = _file_size_str(path)
        self._size_lbl = QLabel(f"Original: {orig_size}")
        self._size_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.42); font-size: 11px; border: none;")
        info_col.addWidget(self._size_lbl)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color: rgba(74,222,128,0.85); font-size: 11px; font-weight: 600; border: none;")
        self._status_lbl.hide()
        info_col.addWidget(self._status_lbl)

        top_row.addLayout(info_col, 1)

        # Target KB input column
        target_col = QVBoxLayout()
        target_col.setContentsMargins(0, 0, 0, 0)
        target_col.setSpacing(3)

        target_lbl = QLabel("Target")
        target_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.30); font-size: 10px; border: none;")
        target_lbl.setAlignment(Qt.AlignCenter)
        target_col.addWidget(target_lbl)

        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("KB")
        self.target_input.setFixedSize(72, 32)
        self.target_input.setAlignment(Qt.AlignCenter)
        self.target_input.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.07);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-family: 'Segoe UI', sans-serif;
            }
            QLineEdit:focus {
                border-color: rgba(88,101,242,0.50);
                background: rgba(88,101,242,0.08);
            }
        """)
        target_col.addWidget(self.target_input)
        top_row.addLayout(target_col)

        # Remove button
        self._remove_btn = QPushButton("✕")
        self._remove_btn.setFixedSize(24, 24)
        self._remove_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._remove_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: none;
                border-radius: 12px;
                color: rgba(255,255,255,0.40);
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(255,82,82,0.20);
                color: rgba(255,100,100,0.90);
            }
        """)
        self._remove_btn.clicked.connect(lambda: self.removed.emit(self.path))
        top_row.addWidget(self._remove_btn, 0, Qt.AlignTop)

        outer.addLayout(top_row)

        # ── Inline quality warning section (hidden by default) ─────────
        self._warn_frame = QFrame()
        self._warn_frame.setObjectName("warnFrame")
        self._warn_frame.setStyleSheet("""
            QFrame#warnFrame {
                background: rgba(255, 196, 0, 0.07);
                border: 1px solid rgba(255, 196, 0, 0.28);
                border-radius: 12px;
            }
        """)
        self._warn_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        warn_layout = QVBoxLayout(self._warn_frame)
        warn_layout.setContentsMargins(14, 12, 14, 12)
        warn_layout.setSpacing(8)

        # Title row
        warn_title_row = QHBoxLayout()
        warn_title_row.setSpacing(6)
        warn_icon_lbl = QLabel("⚠️")
        warn_icon_lbl.setStyleSheet("font-size: 14px; border: none; background: transparent;")
        warn_title_row.addWidget(warn_icon_lbl)
        warn_title_lbl = QLabel("Quality Warning")
        warn_title_lbl.setStyleSheet(
            "color: #ffc400; font-size: 12px; font-weight: 700; border: none; background: transparent;")
        warn_title_row.addWidget(warn_title_lbl)
        warn_title_row.addStretch()
        warn_layout.addLayout(warn_title_row)

        # Description
        self._warn_desc = QLabel(
            "This file cannot be compressed to the requested size without noticeable quality loss.")
        self._warn_desc.setWordWrap(True)
        self._warn_desc.setStyleSheet(
            "color: rgba(255,255,255,0.65); font-size: 11px; border: none; background: transparent;")
        warn_layout.addWidget(self._warn_desc)

        # Sizes row
        self._warn_sizes = QLabel()
        self._warn_sizes.setWordWrap(True)
        self._warn_sizes.setStyleSheet(
            "color: rgba(255,255,255,0.55); font-size: 11px; border: none; background: transparent;")
        warn_layout.addWidget(self._warn_sizes)

        # Effects
        effects_lbl = QLabel(
            "Compressing further may cause:\n"
            "  •  Blurry images\n"
            "  •  Pixelation\n"
            "  •  Distorted graphics\n"
            "  •  Reduced PDF readability"
        )
        effects_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.42); font-size: 10px; border: none; background: transparent;")
        warn_layout.addWidget(effects_lbl)

        # Buttons
        warn_btn_row = QHBoxLayout()
        warn_btn_row.addStretch()
        warn_btn_row.setSpacing(8)

        self._warn_keep_btn = QPushButton("Keep Best Quality")
        self._warn_keep_btn.setFixedHeight(32)
        self._warn_keep_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._warn_keep_btn.setStyleSheet("""
            QPushButton {
                background: rgba(74,222,128,0.12);
                border: 1px solid rgba(74,222,128,0.30);
                border-radius: 8px;
                color: rgba(74,222,128,0.90);
                font-size: 11px;
                font-weight: 600;
                padding: 0px 14px;
            }
            QPushButton:hover {
                background: rgba(74,222,128,0.22);
                color: rgb(74,222,128);
            }
        """)
        warn_btn_row.addWidget(self._warn_keep_btn)

        self._warn_force_btn = QPushButton("Compress Anyway")
        self._warn_force_btn.setFixedHeight(32)
        self._warn_force_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._warn_force_btn.setStyleSheet("""
            QPushButton {
                background: rgba(248,113,113,0.10);
                border: 1px solid rgba(248,113,113,0.28);
                border-radius: 8px;
                color: rgba(248,113,113,0.90);
                font-size: 11px;
                font-weight: 700;
                padding: 0px 14px;
            }
            QPushButton:hover {
                background: rgba(248,113,113,0.20);
                color: rgb(248,113,113);
            }
        """)
        warn_btn_row.addWidget(self._warn_force_btn)
        warn_layout.addLayout(warn_btn_row)

        self._warn_frame.hide()
        self._warn_spacer = QWidget()
        self._warn_spacer.setFixedHeight(8)
        self._warn_spacer.setStyleSheet("background: transparent; border: none;")
        self._warn_spacer.hide()
        outer.addWidget(self._warn_spacer)
        outer.addWidget(self._warn_frame)

    # ── Public API ──────────────────────────────────────────────────
    def get_target_kb(self):
        """Return target KB as int, or None if invalid/empty."""
        try:
            v = int(self.target_input.text())
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    def set_target_kb(self, kb):
        self.target_input.setText(str(int(kb)))

    def show_quality_warning(self, achieved_kb, target_kb, on_keep, on_force):
        """Show the inline quality warning with action callbacks."""
        self._warn_sizes.setText(
            f"<b style='color:rgba(248,113,113,0.9)'>Requested Size:</b>  {target_kb:.0f} KB    "
            f"<b style='color:rgba(74,222,128,0.9)'>Best Quality Size:</b>  {achieved_kb:.0f} KB"
        )
        # Disconnect old connections safely
        try:
            self._warn_keep_btn.clicked.disconnect()
        except TypeError:
            pass
        try:
            self._warn_force_btn.clicked.disconnect()
        except TypeError:
            pass
        self._warn_keep_btn.clicked.connect(on_keep)
        self._warn_force_btn.clicked.connect(on_force)
        self._warn_spacer.show()
        self._warn_frame.show()
        self.adjustSize()
        # Notify parent container to re-layout
        if self.parent():
            self.parent().updateGeometry()

    def hide_quality_warning(self):
        """Hide the inline warning."""
        self._warn_frame.hide()
        self._warn_spacer.hide()
        self.adjustSize()
        if self.parent():
            self.parent().updateGeometry()

    def set_status(self, status, text):
        """Set card status: 'success', 'warning', 'error', or '' to clear."""
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)

        self._status_lbl.setText(text)
        if status == "success":
            self._status_lbl.setStyleSheet(
                "color: rgba(74,222,128,0.85); font-size: 11px; font-weight: 600; border: none;")
        elif status == "warning":
            self._status_lbl.setStyleSheet(
                "color: rgba(255,196,0,0.85); font-size: 11px; font-weight: 600; border: none;")
        elif status == "error":
            self._status_lbl.setStyleSheet(
                "color: rgba(248,113,113,0.85); font-size: 11px; font-weight: 600; border: none;")
        else:
            self._status_lbl.hide()
            return
        self._status_lbl.show()

    def mark_invalid_target(self, invalid=True):
        """Highlight the target input as invalid."""
        if invalid:
            self.target_input.setStyleSheet("""
                QLineEdit {
                    background: rgba(248,113,113,0.10);
                    border: 1px solid rgba(248,113,113,0.50);
                    border-radius: 8px;
                    color: #f87171;
                    font-size: 12px;
                }
            """)
        else:
            self.target_input.setStyleSheet("""
                QLineEdit {
                    background: rgba(255,255,255,0.07);
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 8px;
                    color: white;
                    font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                }
                QLineEdit:focus {
                    border-color: rgba(88,101,242,0.50);
                    background: rgba(88,101,242,0.08);
                }
            """)

    def set_compressing(self):
        """Show in-progress state."""
        self.hide_quality_warning()
        self.set_status("", "")
        self._status_lbl.setText("⏳ Compressing…")
        self._status_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 11px; font-weight: 600; border: none;")
        self._status_lbl.show()
        self.target_input.setEnabled(False)
        self._remove_btn.setEnabled(False)


# ── Drop zone container (manages cards) ─────────────────────────────

class _CompressDropZone(QWidget):
    """Card-based drop zone for the compress panel.

    Each file gets its own card with a per-file target KB input.
    Maintains the same public API as before for compatibility.
    """
    browse_clicked = pyqtSignal()
    file_removed = pyqtSignal(str)   # emits path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards = {}       # path -> _CompressFileCard
        self._card_order = []  # list of paths in insertion order

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(420)
        self.setFixedHeight(self.preferred_height())

        # Main layout
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # ── Container frame with rounded corners ──
        self._container = QFrame()
        self._container.setObjectName("dropContainer")
        self._container.setStyleSheet("""
            QFrame#dropContainer {
                background: #1e1e22;
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.07);
            }
        """)
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(14, 12, 14, 12)
        container_layout.setSpacing(8)

        # ── Empty state ──
        self._empty_widget = QWidget()
        self._empty_widget.setFixedHeight(126)
        self._empty_widget.setCursor(Qt.PointingHandCursor)
        self._empty_widget.setStyleSheet("background: transparent; border: none;")
        self._empty_widget.mousePressEvent = lambda e: self.browse_clicked.emit()
        empty_layout = QVBoxLayout(self._empty_widget)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(6)
        empty_layout.setAlignment(Qt.AlignCenter)

        # Icon
        icon_frame = QFrame()
        icon_frame.setFixedSize(46, 46)
        icon_frame.setStyleSheet("""
            background: rgba(88,101,242,0.18);
            border-radius: 12px;
            border: none;
        """)
        icon_lbl = QLabel("FILE")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet(
            "color: #e4e7ff; font-size: 10px; font-weight: bold; border: none;")
        icon_inner = QVBoxLayout(icon_frame)
        icon_inner.setContentsMargins(0, 0, 0, 0)
        icon_inner.addWidget(icon_lbl)

        title_lbl = QLabel("Drop images or PDFs here")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.85); font-size: 13px; font-weight: 700; border: none;")

        sub_lbl = QLabel("or click to Browse")
        sub_lbl.setAlignment(Qt.AlignCenter)
        sub_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size: 11px; border: none;")

        empty_layout.addWidget(icon_frame, 0, Qt.AlignCenter)
        empty_layout.addWidget(title_lbl)
        empty_layout.addWidget(sub_lbl)
        container_layout.addWidget(self._empty_widget)

        # ── Scroll area for cards ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent; width: 5px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.12);
                border-radius: 2px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.22);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent; border: none;")
        self._cards_layout = QVBoxLayout(self._scroll_content)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()

        self._scroll.setWidget(self._scroll_content)
        self._scroll.hide()
        container_layout.addWidget(self._scroll)

        self._main_layout.addWidget(self._container)

    # ── Public API (compatible with old interface) ──────────────────
    def preferred_height(self):
        if not self._cards:
            return 160
        n = len(self._card_order)
        # Use actual card heights (cards may expand when warning is shown)
        total_card_h = sum(
            self._cards[p].sizeHint().height() + 8
            for p in self._card_order
        )
        # Show up to 4 cards without scrolling
        visible = min(n, 4)
        if n <= 4:
            return 24 + total_card_h
        # If more than 4, cap to 4 card heights + scroll
        visible_h = sum(
            self._cards[p].sizeHint().height() + 8
            for p in self._card_order[:4]
        )
        return 24 + visible_h

    def add_files(self, paths):
        changed = False
        for p in paths:
            if p and p not in self._cards:
                self._add_card(p)
                changed = True
        if changed:
            self._sync_state()

    def clear(self):
        for path in list(self._card_order):
            self._remove_card_widget(path)
        self._cards.clear()
        self._card_order.clear()
        self._sync_state()

    def get_files(self):
        return list(self._card_order)

    def get_file_targets(self):
        """Return list of (path, target_kb) tuples. Returns None for target_kb if invalid."""
        result = []
        for p in self._card_order:
            card = self._cards[p]
            result.append((p, card.get_target_kb()))
        return result

    def set_all_targets(self, kb):
        """Fill all card target inputs with the given KB value."""
        for card in self._cards.values():
            card.set_target_kb(kb)

    def get_card(self, index):
        """Get card by index."""
        if 0 <= index < len(self._card_order):
            return self._cards[self._card_order[index]]
        return None

    def get_card_by_path(self, path):
        return self._cards.get(path)

    def set_all_enabled(self, enabled):
        """Enable/disable all card inputs and remove buttons."""
        for card in self._cards.values():
            card.target_input.setEnabled(enabled)
            card._remove_btn.setEnabled(enabled)

    # ── Internals ───────────────────────────────────────────────────
    def _add_card(self, path):
        preview = self._make_thumb(path)
        card = _CompressFileCard(path, preview, parent=self._scroll_content)
        card.removed.connect(self._on_card_removed)
        self._cards[path] = card
        self._card_order.append(path)
        # Insert before the stretch
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        card.adjustSize()
        self._cards_layout.activate()

    def _on_card_removed(self, path):
        self._remove_card_widget(path)
        if path in self._cards:
            del self._cards[path]
        if path in self._card_order:
            self._card_order.remove(path)
        self._sync_state()
        self.file_removed.emit(path)

    def _remove_card_widget(self, path):
        card = self._cards.get(path)
        if card:
            self._cards_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()

    def _sync_state(self):
        has_files = bool(self._cards)
        self._empty_widget.setVisible(not has_files)
        self._scroll.setVisible(has_files)
        # Only reserve gutter space for the scrollbar when it's actually shown
        needs_scroll = len(self._card_order) > 4
        self._cards_layout.setContentsMargins(0, 0, 8 if needs_scroll else 0, 0)
        self._cards_layout.activate()
        h = self.preferred_height()
        if self.height() != h:
            self.setFixedHeight(h)

    @staticmethod
    def _make_thumb(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff'):
            try:
                pil = Image.open(path)
                pil.thumbnail((80, 80), Image.LANCZOS)
                buf = io.BytesIO()
                pil.save(buf, 'PNG')
                px = QPixmap()
                px.loadFromData(buf.getvalue())
                return px
            except Exception:
                pass
        return None