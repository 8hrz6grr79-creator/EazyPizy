import sys
import os
import threading
import time
import io

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
    QSizePolicy, QSpinBox, QComboBox,
    QDialog, QDialogButtonBox
)
from PyQt5.QtCore import (
    Qt, QPoint, QSize,
    QPropertyAnimation, QEasingCurve,
    pyqtSignal, pyqtSlot, QThread, QMetaObject, Q_ARG
)
from PyQt5.QtGui import QColor, QIcon, QPixmap, QCursor

from scan_server import ScanServer

from styles import (
    APP_STYLE, BAR_STYLE, MODE_BTN_STYLE, ICON_BTN_STYLE,
    CLOSE_BTN_STYLE, MINIMIZE_BTN_STYLE, KB_PILL_STYLE, KB_INPUT_STYLE,
    ACTION_BTN_STYLE, SECONDARY_BTN_STYLE, HINT_STYLE,
    LIST_FRAME_STYLE, LIST_WIDGET_STYLE, PROGRESS_STYLE,
    MENU_STYLE, SPINBOX_STYLE, COMBO_STYLE, PANEL_STYLE,
    SIDEBAR_STYLE, PDF_TOOL_BTN_STYLE
)
from helpers import (
    WIN_W, PANEL_H, ASPECT_RATIOS,
    make_divider, make_icon_btn, make_wm_btn,
    sep_widget, section_label, spin_col
)
from canvases import CropCanvas, BgCanvas
from pdf_panel import PdfToolPanel
from workers import CompressWorker, BgRemoveWorker


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
        self._syncing      = False
        self._active_pdf_tool = None
        self._scan_server     = None
        self._scan_dlg        = None
        self._tray            = []   # [{"path": str, "type": "image"|"pdf"}]
        self.setup_ui()
        self.hide()
        self.toggle_signal.connect(self.toggle_visibility)

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

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self.raise_()

    # ------------------------------------------------------------------
    # UI SETUP
    # ------------------------------------------------------------------
    def setup_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        scr = QApplication.primaryScreen().geometry()
        self.setGeometry((scr.width() - WIN_W) // 2, 40, WIN_W, 90)
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
        self._build_bgremove_panel()
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

        # Crop bar controls
        self.crop_bar_controls = QFrame()
        self.crop_bar_controls.setStyleSheet("background:transparent; border:none;")
        cbl = QHBoxLayout(self.crop_bar_controls)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.setSpacing(5)
        self.bar_ratio_combo = QComboBox()
        self.bar_ratio_combo.setFixedHeight(48)
        self.bar_ratio_combo.setMinimumWidth(115)
        self.bar_ratio_combo.setStyleSheet(COMBO_STYLE)
        for name, _ in ASPECT_RATIOS:
            self.bar_ratio_combo.addItem(name)
        self.bar_ratio_combo.currentIndexChanged.connect(self._on_bar_ratio)
        cbl.addWidget(self.bar_ratio_combo)
        cbl.addWidget(make_divider())
        self.crop_save_btn = QPushButton("✂  Crop & Save")
        self.crop_save_btn.setFixedHeight(48)
        self.crop_save_btn.setMinimumWidth(130)
        self.crop_save_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.crop_save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.crop_save_btn.clicked.connect(self._do_crop_save)
        cbl.addWidget(self.crop_save_btn)
        self.crop_reset_btn = QPushButton("↺")
        self.crop_reset_btn.setFixedSize(48, 48)
        self.crop_reset_btn.setToolTip("Reset crop")
        self.crop_reset_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.crop_reset_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.crop_reset_btn.clicked.connect(self._reset_crop)
        cbl.addWidget(self.crop_reset_btn)
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

        # Crop dim label
        self.crop_dim_lbl = QLabel("")
        self.crop_dim_lbl.setStyleSheet("color:rgba(255,255,255,0.28); font-size:12px;")
        self.crop_dim_lbl.hide()
        bl.addWidget(self.crop_dim_lbl)

        # Right-side icon buttons
        bl.addWidget(make_divider())
        self.scan_btn = make_icon_btn("assets/icons/scan.png", "Scan from Phone", "📷", size=48)
        self.scan_btn.clicked.connect(self._toggle_scan_server)
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
        bl.addWidget(make_divider())

        # Window controls
        wm = QFrame()
        wm.setStyleSheet("background:transparent; border:none;")
        wml = QHBoxLayout(wm)
        wml.setContentsMargins(0, 0, 0, 0)
        wml.setSpacing(5)
        self.minimize_btn = make_wm_btn(MINIMIZE_BTN_STYLE, "–", "Minimize", size=26)
        self.minimize_btn.clicked.connect(self.showMinimized)
        wml.addWidget(self.minimize_btn)
        self.close_btn = make_wm_btn(CLOSE_BTN_STYLE, "×", "Close", size=26)
        self.close_btn.clicked.connect(self.close)
        wml.addWidget(self.close_btn)
        bl.addWidget(wm)

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
        self.file_list.clear()
        self.crop_panel.hide()
        self.bgremove_panel.hide()
        self.list_frame.hide()
        for p in self._pdf_panels.values():
            p.hide()
        self.crop_dim_lbl.hide()
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
            self._load_bgremove(path)
        elif self.mode == self.MODE_PDF and self._active_pdf_tool:
            self._pdf_panels[self._active_pdf_tool].canvas.add_files([path])

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
        self.crop_panel.hide()
        layout = QHBoxLayout(self.crop_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.crop_canvas = CropCanvas()
        self.crop_canvas.crop_changed.connect(self._on_crop_changed)
        layout.addWidget(self.crop_canvas, 1)

        sb = QFrame()
        sb.setFixedWidth(190)
        sb.setStyleSheet(SIDEBAR_STYLE)
        sbl = QVBoxLayout(sb)
        sbl.setContentsMargins(14, 18, 14, 18)
        sbl.setSpacing(12)
        self.crop_img_info = QLabel("—")
        self.crop_img_info.setStyleSheet("color:rgba(255,255,255,0.28); font-size:11px; border:none;")
        sbl.addWidget(self.crop_img_info)
        sbl.addWidget(sep_widget())
        sbl.addWidget(section_label("Position"))
        pos_row = QHBoxLayout()
        pos_row.setSpacing(6)
        self._sx = spin_col("X", 0, 99999)
        self._sy = spin_col("Y", 0, 99999)
        pos_row.addLayout(self._sx[0])
        pos_row.addLayout(self._sy[0])
        sbl.addLayout(pos_row)
        sbl.addWidget(section_label("Size"))
        sz_row = QHBoxLayout()
        sz_row.setSpacing(6)
        self._sw = spin_col("W", 1, 99999)
        self._sh = spin_col("H", 1, 99999)
        sz_row.addLayout(self._sw[0])
        sz_row.addLayout(self._sh[0])
        sbl.addLayout(sz_row)
        for s in (self._sx, self._sy, self._sw, self._sh):
            s[1].valueChanged.connect(self._on_spin)
        sbl.addWidget(sep_widget())
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
        sbl.addWidget(section_label("Custom Ratio"))
        sbl.addWidget(self.custom_ratio_frame)
        hint = QLabel("Scroll to zoom\nMiddle-drag to pan\nDouble-click to reset view")
        hint.setStyleSheet("color:rgba(255,255,255,0.18); font-size:10px; border:none;")
        sbl.addWidget(hint)
        sbl.addStretch()
        layout.addWidget(sb)
        self._outer.addWidget(self.crop_panel)

    def _build_bgremove_panel(self):
        self.bgremove_panel = QFrame()
        self.bgremove_panel.setObjectName("panel")
        self.bgremove_panel.setStyleSheet(PANEL_STYLE)
        self.bgremove_panel.setFixedHeight(PANEL_H)
        self.bgremove_panel.hide()
        layout = QHBoxLayout(self.bgremove_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.bg_canvas = BgCanvas()
        layout.addWidget(self.bg_canvas, 1)

        sb = QFrame()
        sb.setFixedWidth(200)
        sb.setStyleSheet(SIDEBAR_STYLE)
        sbl = QVBoxLayout(sb)
        sbl.setContentsMargins(14, 18, 14, 18)
        sbl.setSpacing(10)
        sbl.addWidget(section_label("Background"))

        self._swatch_btns = []
        grid_frame = QFrame()
        grid_frame.setStyleSheet("background:transparent; border:none;")
        grid = QVBoxLayout(grid_frame)
        grid.setSpacing(5)
        grid.setContentsMargins(0, 0, 0, 0)
        COLS = 4
        row_layout = None
        for i, (name, color) in enumerate(BgCanvas.SWATCHES):
            if i % COLS == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(5)
                grid.addLayout(row_layout)
            btn = QPushButton()
            btn.setFixedSize(36, 36)
            btn.setToolTip(name)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setCheckable(True)
            if color is None:
                btn.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                            stop:0 #3a3a3f,stop:0.5 #3a3a3f,stop:0.5 #2e2e33,stop:1 #2e2e33);
                        border: 2px solid rgba(255,255,255,0.12); border-radius: 8px;
                    }
                    QPushButton:checked { border: 2px solid #5865F2; }
                    QPushButton:hover   { border: 2px solid rgba(255,255,255,0.35); }
                """)
            else:
                r, g, b = color.red(), color.green(), color.blue()
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgb({r},{g},{b});
                        border: 2px solid rgba(255,255,255,0.08); border-radius: 8px;
                    }}
                    QPushButton:checked {{ border: 2px solid #5865F2; }}
                    QPushButton:hover   {{ border: 2px solid rgba(255,255,255,0.40); }}
                """)
            btn.clicked.connect(lambda checked, c=color, b=btn: self._on_swatch(c, b))
            row_layout.addWidget(btn)
            self._swatch_btns.append(btn)

        sbl.addWidget(grid_frame)
        sbl.addWidget(sep_widget())
        sbl.addStretch()
        layout.addWidget(sb)
        self._outer.addWidget(self.bgremove_panel)

    def _build_pdf_tool_panels(self):
        self._pdf_panels = {}
        for tool_id, _, _icon in self.PDF_TOOLS:
            panel = PdfToolPanel(tool_id)
            panel.hide()
            self._pdf_panels[tool_id] = panel
            self._outer.addWidget(panel)

    # ------------------------------------------------------------------
    # MODE SWITCHING
    # ------------------------------------------------------------------
    def _switch_mode(self, mode):
        self.mode = mode
        self.files = []
        self.crop_path = None
        self.bgremove_path = None
        self.list_frame.hide()
        self.crop_panel.hide()
        self.bgremove_panel.hide()
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
            self.hint.setText("Drop an image anywhere  ·  or click  ＋")
            # load last image in tray
            imgs = [e["path"] for e in self._tray if e["type"] == "image"]
            if imgs:
                self._load_crop_image(imgs[-1])
        elif mode == self.MODE_BGREMOVE:
            self.bgremove_bar_controls.show()
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
        self._animate_size(self.bar.height() or 80 + 10 + PANEL_H)

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

    # ------------------------------------------------------------------
    # ADD FILES
    # ------------------------------------------------------------------
    def add_files(self):
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
            self.custom_ratio_frame.hide()
            self.hint.hide()
            self.crop_dim_lbl.show()
            self.bgremove_panel.hide()
            self.list_frame.hide()
            for p in self._pdf_panels.values():
                p.hide()
            self.crop_panel.show()
            self._animate_size(self.bar.height() or 80 + 10 + PANEL_H)
        except Exception as ex:
            QMessageBox.critical(self, "Load error", str(ex))

    def _on_crop_changed(self, x, y, w, h):
        if self._syncing:
            return
        self._syncing = True
        self._sx[1].setValue(x)
        self._sy[1].setValue(y)
        self._sw[1].setValue(max(1, w))
        self._sh[1].setValue(max(1, h))
        self.crop_dim_lbl.setText(f"{w} × {h}")
        self._syncing = False

    def _on_spin(self):
        if self._syncing:
            return
        self._syncing = True
        x = self._sx[1].value()
        y = self._sy[1].value()
        w = self._sw[1].value()
        h = self._sh[1].value()
        self.crop_canvas.set_crop_coords(x, y, w, h)
        self.crop_dim_lbl.setText(f"{w} × {h}")
        self._syncing = False

    def _on_bar_ratio(self, idx):
        _, val = ASPECT_RATIOS[idx]
        if val == "custom":
            self.custom_ratio_frame.show()
            self._apply_custom_ratio()
        else:
            self.custom_ratio_frame.hide()
            self.crop_canvas.set_aspect(val)

    def _apply_custom_ratio(self):
        self.crop_canvas.set_aspect((self.ratio_w.value(), self.ratio_h.value()))

    def _reset_crop(self):
        self.bar_ratio_combo.blockSignals(True)
        self.bar_ratio_combo.setCurrentIndex(0)
        self.bar_ratio_combo.blockSignals(False)
        self.crop_canvas.set_aspect(None)
        self.crop_canvas.reset_zoom()
        self.custom_ratio_frame.hide()

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
            cropped.save(out)
            self.crop_save_btn.setText("✓  Saved!")
            QApplication.processEvents()
            time.sleep(0.8)
            self.crop_save_btn.setText("✂  Crop & Save")
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
        self.crop_panel.hide()
        for p in self._pdf_panels.values():
            p.hide()
        self._animate_size(self.bar.height() or 80)

        self._bg_thread = QThread()
        self._bg_worker = BgRemoveWorker(path)
        self._bg_worker.moveToThread(self._bg_thread)
        self._bg_thread.started.connect(self._bg_worker.run)
        self._bg_worker.finished.connect(self._on_bgremove_done)
        self._bg_worker.error.connect(self._on_bgremove_error)
        self._bg_worker.finished.connect(self._bg_thread.quit)
        self._bg_thread.finished.connect(self._bg_thread.deleteLater)
        self._bg_thread.start()

    def _on_bgremove_done(self, pil_rgba):
        self.bg_canvas.set_image(pil_rgba)
        for btn in self._swatch_btns:
            btn.setChecked(False)
        self._swatch_btns[0].setChecked(True)
        self.bg_canvas.set_bg_color(None)
        self.hint.hide()
        self.bgremove_panel.show()
        self._animate_size(self.bar.height() or 80 + 10 + PANEL_H)

    def _on_bgremove_error(self, msg):
        self.hint.show()
        self.hint.setText("Drop an image  ·  background will be removed automatically")
        QMessageBox.critical(self, "Error",
            f"Background removal failed:\n{msg}\n\nMake sure rembg is installed:\npip install rembg")

    def _on_swatch(self, color, clicked_btn):
        for btn in self._swatch_btns:
            btn.setChecked(btn is clicked_btn)
        self.bg_canvas.set_bg_color(color)

    def _bgremove_save(self):
        result = self.bg_canvas.get_result()
        if result is None:
            QMessageBox.warning(self, "Nothing to save", "Remove a background first.")
            return
        folder = "removed_bg"
        os.makedirs(folder, exist_ok=True)
        name, _ = os.path.splitext(os.path.basename(self.bgremove_path))
        ext = ".png" if self.bg_canvas._bg_color is None else ".jpg"
        out = os.path.join(folder, f"{name}_nobg{ext}")
        c = 1
        while os.path.exists(out):
            out = os.path.join(folder, f"{name}_nobg_{c}{ext}")
            c += 1
        try:
            result.save(out)
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
        self.file_list.clear()
        self.crop_panel.hide()
        self.bgremove_panel.hide()
        self.list_frame.hide()
        for p in self._pdf_panels.values():
            p.hide()
        if self.mode == self.MODE_PDF:
            if self._active_pdf_tool:
                self._pdf_panels[self._active_pdf_tool].canvas.clear()
            self._active_pdf_tool = None
            for btn in self._pdf_tool_btns.values():
                btn.setChecked(False)
            self.hint.show()
            self.hint.setText("Choose a PDF tool above  ·  then drop files below")
        self.crop_dim_lbl.hide()
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

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton:
            d = e.globalPos() - self.oldPos
            self.move(self.x() + d.x(), self.y() + d.y())
            self.oldPos = e.globalPos()

    # ------------------------------------------------------------------
    # PHONE SCAN
    # ------------------------------------------------------------------
    def _toggle_scan_server(self):
        # If server is already running, re-show the QR dialog instead of stopping
        if self._scan_server and self._scan_server.running:
            self._show_scan_dialog()
            return

        self._scan_server = ScanServer(
            on_file_received=self._scan_callback,
            save_dir="scanned",
        )
        self._scan_server.start()
        self._show_scan_dialog()

    def _scan_callback(self, path: str):
        """Called from the HTTP server background thread.
        Use invokeMethod so the slot runs on the Qt main thread."""
        QMetaObject.invokeMethod(
            self, "_on_scan_received",
            Qt.QueuedConnection,
            Q_ARG(str, path),
        )

    @pyqtSlot(str)
    def _on_scan_received(self, path: str):
        """Runs on the Qt main thread — add to tray and route to active tool."""
        self.show()
        self.activateWindow()
        self.raise_()
        self._tray_add(path)
        # notify open dialog if present
        if hasattr(self, '_scan_dlg') and self._scan_dlg and self._scan_dlg.isVisible():
            self._scan_dlg.notify_received(path)

    def _show_scan_dialog(self):
        self._scan_dlg = ScanDialog(self._scan_server, parent=self)
        dlg = self._scan_dlg
        dlg.adjustSize()
        app_geo = self.geometry()
        dx = app_geo.left() + (app_geo.width() - dlg.width()) // 2
        dy = app_geo.bottom() + 12
        screen = QApplication.primaryScreen().geometry()
        dx = max(0, min(dx, screen.width()  - dlg.width()))
        dy = max(0, min(dy, screen.height() - dlg.height()))
        dlg.move(dx, dy)
        dlg.show()   # non-blocking show
        if self._scan_server and self._scan_server.running:
            self.scan_btn.setToolTip(
                f"📡  Active — {self._scan_server.url}\n(click to view QR / stop)"
            )
        else:
            self._scan_server = None
            self._scan_dlg = None
            self.scan_btn.setToolTip("Scan from Phone")

    def closeEvent(self, e):
        if self._scan_server:
            self._scan_server.stop()
        if self._worker:
            self._worker.cancel()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        e.accept()

# =========================================
# SCAN DIALOG
# =========================================

class ScanDialog(QDialog):
    def __init__(self, server, parent=None):
        super().__init__(parent)
        self._server   = server
        self._parent   = parent
        self._received = 0
        self.setWindowTitle("Scan from Phone")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(False)   # non-modal so app stays usable

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._card = QFrame()
        self._card.setStyleSheet("""
            QFrame {
                background: #1e1e22;
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.10);
            }
        """)
        cl = QVBoxLayout(self._card)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(12)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("📱  Scan from Phone")
        title.setStyleSheet("color:white; font-size:15px; font-weight:700; border:none;")
        title_row.addWidget(title)
        title_row.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(
            "color:#4ade80; font-size:12px; font-weight:600; border:none;")
        title_row.addWidget(self._count_lbl)
        cl.addLayout(title_row)

        sub = QLabel("Same Wi-Fi · scan QR or open URL in phone browser")
        sub.setStyleSheet("color:rgba(255,255,255,0.40); font-size:11px; border:none;")
        cl.addWidget(sub)

        # QR code
        if server.qr_pil:
            buf = io.BytesIO()
            server.qr_pil.save(buf, "PNG")
            buf.seek(0)
            qr_pix = QPixmap()
            qr_pix.loadFromData(buf.read())
            qr_lbl = QLabel()
            qr_lbl.setPixmap(
                qr_pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            qr_lbl.setAlignment(Qt.AlignCenter)
            qr_lbl.setStyleSheet(
                "border:none; background:white; border-radius:12px; padding:8px;")
            cl.addWidget(qr_lbl)
        else:
            no_qr = QLabel("Install qrcode:  pip install qrcode[pil]")
            no_qr.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:11px; border:none;")
            no_qr.setAlignment(Qt.AlignCenter)
            cl.addWidget(no_qr)

        # URL pill
        url_frame = QFrame()
        url_frame.setStyleSheet("""
            QFrame {
                background: rgba(88,101,242,0.14);
                border: 1px solid rgba(88,101,242,0.35);
                border-radius: 10px;
            }
        """)
        ul = QHBoxLayout(url_frame)
        ul.setContentsMargins(10, 7, 10, 7)
        url_lbl = QLabel(server.url)
        url_lbl.setStyleSheet(
            "color:#a5b4fc; font-size:12px; font-weight:600; border:none;")
        url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_lbl.setCursor(QCursor(Qt.IBeamCursor))
        ul.addWidget(url_lbl, 1)
        cl.addWidget(url_frame)

        # Last received thumbnail (hidden until first photo)
        self._thumb_frame = QFrame()
        self._thumb_frame.setStyleSheet(
            "QFrame{background:rgba(255,255,255,0.04);border-radius:10px;border:none;}")
        self._thumb_frame.hide()
        tl = QHBoxLayout(self._thumb_frame)
        tl.setContentsMargins(8, 8, 8, 8)
        tl.setSpacing(10)
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(54, 54)
        self._thumb_lbl.setStyleSheet(
            "border-radius:8px; border:none; background:#333;")
        self._thumb_lbl.setScaledContents(True)
        tl.addWidget(self._thumb_lbl)
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self._thumb_name = QLabel("—")
        self._thumb_name.setStyleSheet(
            "color:rgba(255,255,255,0.80); font-size:11px; font-weight:600; border:none;")
        self._thumb_status = QLabel("Waiting for photos…")
        self._thumb_status.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:10px; border:none;")
        info_col.addWidget(self._thumb_name)
        info_col.addWidget(self._thumb_status)
        tl.addLayout(info_col, 1)
        cl.addWidget(self._thumb_frame)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        stop_btn = QPushButton("⏹  Stop")
        stop_btn.setStyleSheet("""
            QPushButton {
                background: rgba(248,113,113,0.12);
                border: 1px solid rgba(248,113,113,0.25);
                border-radius: 10px; color: #f87171;
                font-size: 12px; font-weight: 600; padding: 8px 14px;
            }
            QPushButton:hover { background: rgba(248,113,113,0.25); }
        """)
        stop_btn.setCursor(QCursor(Qt.PointingHandCursor))
        stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(stop_btn)

        keep_btn = QPushButton("Keep Running  ✓")
        keep_btn.setStyleSheet("""
            QPushButton {
                background: #5865F2; border: none; border-radius: 10px;
                color: white; font-size: 12px; font-weight: 600; padding: 8px 14px;
            }
            QPushButton:hover { background: #4752C4; }
        """)
        keep_btn.setCursor(QCursor(Qt.PointingHandCursor))
        keep_btn.clicked.connect(self.accept)
        btn_row.addWidget(keep_btn)
        cl.addLayout(btn_row)

        outer.addWidget(self._card)

    def notify_received(self, path: str):
        """Call this when a photo arrives to update the dialog."""
        self._received += 1
        self._count_lbl.setText(f"✓ {self._received} received")

        # update thumbnail
        pix = QPixmap(path)
        if not pix.isNull():
            self._thumb_lbl.setPixmap(
                pix.scaled(54, 54, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        name = os.path.basename(path)
        kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
        self._thumb_name.setText(name if len(name) <= 24 else name[:21] + "…")
        self._thumb_status.setText(f"{kb:.0f} KB  ·  just now")
        self._thumb_frame.show()

        # auto-close after first photo
        if self._received == 1:
            self.accept()

    def _stop(self):
        self._server.stop()
        self.accept()
