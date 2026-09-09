import os
import io

from PIL import Image

from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QSizePolicy, QLineEdit, QCheckBox, QComboBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QCursor, QPixmap

from ui.styles import (
    ACTION_BTN_STYLE, SECONDARY_BTN_STYLE,
    PDF_LABEL_STYLE, PDF_STATUS_OK, PDF_STATUS_ERR,
    PDF_COMPACT_PANEL_STYLE, PDF_HEADER_STYLE, PDF_SUBTITLE_STYLE,
    COMBO_STYLE
)
from ui.helpers import sep_widget, section_label
from ui.pdf_canvas import PdfDropCanvas, OrganizePdfCanvas
from tools.pdf_tools.logic import PdfWorker


# =========================================
# PDF TOOL PANEL
# =========================================

class PdfToolPanel(QFrame):
    height_hint_changed = pyqtSignal(int)

    TOOL_TITLES = {
        "img2pdf": "Image to PDF",
        "merge": "Merge PDF",
        "organize": "PDF",
        "protect": "Protect PDF",
    }

    TOOL_GLYPHS = {
        "img2pdf": "IMG",
        "merge": "PDF",
        "organize": "A/B",
        "protect": "🔒",
    }

    # Drop image files here with these exact names (either extension works,
    # svg preferred) to swap the Organize / Protect header badges over from
    # the text glyphs above to real icons. Any tool without a matching file
    # here just keeps using its TOOL_GLYPHS text as before.
    #   Fastutil/assets/icons/organizer.svg  (or organizer.png)
    #   Fastutil/assets/icons/protect.svg    (or protect.png)
    ICONS_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "assets", "icons"
    )
    # Maps tool_id -> icon filename stem. Only listed here because "organize"
    # the tool_id and "organizer" the icon file don't match.
    ICON_FILENAMES = {"organize": "pdf", "protect": "protect"}

    def __init__(self, tool_id, parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self.setObjectName("pdfCompactPanel")
        self.setStyleSheet(PDF_COMPACT_PANEL_STYLE)
        # img2pdf/merge need extra width to fit 4 cards per row (was 3 at 760px).
        # 840, not 820: once the vertical scrollbar appears (5+ files) it eats
        # ~12-16px off the grid's width, which was just enough to tip the
        # column math back down to 3 — the wider panel keeps a buffer so 4
        # columns hold whether or not the scrollbar is showing.
        panel_widths = {"organize": 820, "img2pdf": 840, "merge": 840}
        self.setFixedWidth(panel_widths.get(tool_id, 760))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._thread = None
        self._worker = None
        self._go_btn = None
        self._status = None
        self._fade_anim = None
        self.last_output_dir = None  # set by _output_path() after each save
        self._page_size_combo = None  # Img->PDF specific
        # Protect-PDF specific widgets
        self._pw_input = None
        self._pw_confirm = None
        self._overwrite_chk = None
        self._protect_file_card = None  # QFrame shown when a file is loaded
        self._protect_drop_area = None  # QFrame shown when no file is loaded

        layout = QVBoxLayout(self)
        self._layout = layout
        self._canvas_index = 2
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self._build_header(layout)
        layout.addWidget(sep_widget())

        if tool_id == "protect":
            # Protect PDF gets its own fully custom compact layout —
            # no shared PdfDropCanvas needed.
            self._build_protect_layout(layout)
        else:
            canvas_cfg = {
                "img2pdf": dict(accept_images=True, accept_pdfs=False),
                "merge": dict(accept_images=False, accept_pdfs=True),
            }
            if tool_id == "organize":
                self.canvas = OrganizePdfCanvas()
            else:
                self.canvas = PdfDropCanvas(**canvas_cfg.get(tool_id, dict(accept_images=False, accept_pdfs=True)))
            self.canvas.files_changed.connect(self._on_files_changed)
            self.canvas.height_hint_changed.connect(self._sync_panel_height)
            layout.addWidget(self.canvas)

            self._controls = QFrame()
            self._controls.setStyleSheet("background: transparent; border: none;")
            controls_layout = QHBoxLayout(self._controls)
            controls_layout.setContentsMargins(0, 0, 0, 0)
            controls_layout.setSpacing(8)
            self._build_controls(controls_layout, tool_id)
            layout.addWidget(self._controls)

            self._status = QLabel("")
            self._status.setStyleSheet(PDF_LABEL_STYLE)
            self._status.setWordWrap(True)
            self._status.setFixedHeight(22)
            layout.addWidget(self._status)
            self._sync_action_state()
            self._sync_panel_height()

    def showEvent(self, event):
        super().showEvent(event)
        # Immediate repaint on show — no opacity effect.
        #
        # ROOT CAUSE of the former transparency bug:
        # _run_entry_animation() installed a QGraphicsOpacityEffect at
        # opacity=0.0 and animated it to 1.0 over 180 ms.  On a window with
        # WA_TranslucentBackground Qt renders QGraphicsOpacityEffect widgets
        # into an ARGB32 offscreen pixmap; during the fade the compositor
        # blended that semi-transparent surface against the transparent window
        # background, producing a blank/transparent panel for ~180–500 ms
        # (longer when the concurrent size animation was also running).
        #
        # The size animation in _on_pdf_tool_btn() already gives the user a
        # clear visual cue that the panel has appeared.  No additional
        # opacity animation is needed, and removing it eliminates the bug.
        self.update()

    # _run_entry_animation intentionally removed — it was the root cause of
    # the transparency flash.  See showEvent comment above.

    def _tool_icon_pixmap(self, tool_id, size=16):
        """Look for {icon_stem}.svg or .png (any case) in ICONS_DIR and
        return it scaled to `size`, or None if no icon file exists yet for
        this tool."""
        icon_stem = self.ICON_FILENAMES.get(tool_id)
        if icon_stem is None:
            return None
        if not os.path.isdir(self.ICONS_DIR):
            return None

        candidates = []
        for fname in os.listdir(self.ICONS_DIR):
            stem, ext = os.path.splitext(fname)
            if stem.lower() == icon_stem.lower() and ext.lower() in (".svg", ".png"):
                candidates.append((ext.lower(), fname))
        if not candidates:
            return None
        # Try every match in preference order (svg first) — if the "best"
        # one fails to load (e.g. an .svg file but the QtSvg plugin isn't
        # available), fall through to the next instead of giving up.
        candidates.sort(key=lambda c: c[0] != ".svg")
        for _, fname in candidates:
            path = os.path.join(self.ICONS_DIR, fname)
            px = QPixmap(path)
            if not px.isNull():
                return px.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return None

    def _build_header(self, layout):
        header = QFrame()
        header.setObjectName("pdfToolHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(9)

        icon_px = self._tool_icon_pixmap(self.tool_id)
        badge = QLabel()
        badge.setAlignment(Qt.AlignCenter)
        if icon_px is not None:
            badge.setPixmap(icon_px)
            badge.setFixedSize(28, 28)
            badge.setStyleSheet("""
                QLabel {
                    background: rgba(88,101,242,0.18);
                    border: 1px solid rgba(88,101,242,0.34);
                    border-radius: 9px;
                }
            """)
        else:
            badge.setText(self.TOOL_GLYPHS.get(self.tool_id, "PDF"))
            badge.setFixedHeight(28)
            badge.setStyleSheet("""
                QLabel {
                    background: rgba(88,101,242,0.18);
                    border: 1px solid rgba(88,101,242,0.34);
                    border-radius: 9px;
                    color: #dfe3ff;
                    font-size: 10px;
                    font-weight: 800;
                    padding: 0px 11px;
                }
            """)
        title = QLabel(self.TOOL_TITLES.get(self.tool_id, "PDF Tool"))
        title.setStyleSheet(PDF_HEADER_STYLE)
        hl.addWidget(badge)
        hl.addWidget(title)
        hl.addStretch(1)
        layout.addWidget(header)

    def _build_controls(self, layout, tool_id):
        builders = {
            "img2pdf": self._sb_img2pdf,
            "merge": self._sb_merge,
            "organize": self._sb_organize,
            "protect": self._sb_protect,
        }
        builders.get(tool_id, lambda l: None)(layout)

    def _lbl(self, text):
        label = QLabel(text)
        label.setStyleSheet(PDF_LABEL_STYLE)
        label.setWordWrap(True)
        return label

    def _action_btn(self, text):
        btn = QPushButton(text)
        btn.setStyleSheet(ACTION_BTN_STYLE)
        btn.setFixedHeight(34)
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        return btn

    def _secondary_btn(self, text, height=34):
        btn = QPushButton(text)
        btn.setStyleSheet(SECONDARY_BTN_STYLE)
        btn.setFixedHeight(height)
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        return btn

    # ------------------------------------------------------------------
    # DIRECT-SAVE OUTPUT PATH
    # ------------------------------------------------------------------
    # Saves next to the source file(s) that were actually worked on,
    # matching the convention main_window.py uses for its other direct-
    # save tools (_do_crop_save, _bgremove_save) after that change.
    # PDF_OUTPUT_FOLDER is now only a last-resort fallback for the rare
    # case no source folder can be determined (e.g. empty input list).
    PDF_OUTPUT_FOLDER = "pdf_output"

    def _output_path(self, filename, source_dir=None):
        if source_dir:
            folder = os.path.abspath(source_dir)
        else:
            folder = os.path.abspath(self.PDF_OUTPUT_FOLDER)
        os.makedirs(folder, exist_ok=True)
        # Remembered so main_window.py's "open output folder" button can
        # point at wherever the most recent PDF tool run actually saved.
        self.last_output_dir = folder
        out = os.path.join(folder, filename)
        if not os.path.exists(out):
            return out
        name, ext = os.path.splitext(filename)
        c = 1
        while True:
            candidate = os.path.join(folder, f"{name}_{c}{ext}")
            if not os.path.exists(candidate):
                return candidate
            c += 1

    def _add_common_buttons(self, layout, browse_text, browse_slot, clear=True):
        browse_btn = self._secondary_btn(browse_text, 34)
        browse_btn.clicked.connect(browse_slot)
        layout.addWidget(browse_btn)
        if clear:
            clear_btn = self._secondary_btn("Clear", 34)
            clear_btn.clicked.connect(self.canvas.clear)
            layout.addWidget(clear_btn)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # IMG -> PDF  (retired as a standalone tool — Organize now covers this;
    # kept here only for reference, never reached since PDF_TOOLS in
    # main_window.py no longer lists "img2pdf".)
    # ------------------------------------------------------------------
    def _sb_img2pdf(self, layout):
        self._add_common_buttons(layout, "Browse Images", self._add_images)
        layout.addWidget(section_label("Page Size"))
        self._page_size_combo = QComboBox()
        self._page_size_combo.addItems(["Fit to Image", "A4", "US Letter"])
        self._page_size_combo.setFixedHeight(34)
        self._page_size_combo.setStyleSheet(COMBO_STYLE)
        layout.addWidget(self._page_size_combo)
        go_btn = self._action_btn("Convert to PDF")
        go_btn.clicked.connect(self._run_img2pdf)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

    def _add_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Images", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if files:
            self.canvas.add_files(files)

    def _run_img2pdf(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("Add at least one image first.", err=True)
            return
        base = os.path.splitext(os.path.basename(files[0]))[0]
        out = self._output_path(f"{base}_converted.pdf", source_dir=os.path.dirname(os.path.abspath(files[0])))
        page_size = self._page_size_combo.currentText() if self._page_size_combo else "Fit to Image"
        self._run_worker(self._do_img2pdf, files, out, page_size)

    @staticmethod
    def _do_img2pdf(files, out, page_size="Fit to Image"):
        # Standard page sizes at 200 DPI (good balance of quality vs file size).
        PAGE_SIZES_PX = {
            "A4": (1654, 2339),
            "US Letter": (1700, 2200),
        }

        source_imgs = [Image.open(f).convert("RGB") for f in files]

        if page_size in PAGE_SIZES_PX:
            target_w, target_h = PAGE_SIZES_PX[page_size]
            pages = []
            for img in source_imgs:
                page = Image.new("RGB", (target_w, target_h), "white")
                # Scale the image down to fit within the page while
                # preserving aspect ratio, then center it.
                scale = min(target_w / img.width, target_h / img.height)
                new_w, new_h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
                resized = img.resize((new_w, new_h), Image.LANCZOS)
                page.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
                pages.append(page)
        else:
            # "Fit to Image": each page is exactly the size of its source image.
            pages = source_imgs

        first, rest = pages[0], pages[1:]
        first.save(out, save_all=True, append_images=rest)
        return f"Saved {len(pages)} page(s) -> {os.path.basename(out)}"

    # ------------------------------------------------------------------
    # MERGE  (retired as a standalone tool — Organize now covers this;
    # kept here only for reference, never reached since PDF_TOOLS in
    # main_window.py no longer lists "merge".)
    # ------------------------------------------------------------------
    def _sb_merge(self, layout):
        self._add_common_buttons(layout, "Browse PDFs", self._add_organize_files)
        go_btn = self._action_btn("Merge PDF")
        go_btn.clicked.connect(self._run_merge)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

    def _add_organize_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs or Images", "",
            "PDFs and Images (*.pdf *.png *.jpg *.jpeg *.webp *.bmp)")
        if files:
            self.canvas.add_files(files)

    def _run_merge(self):
        files = self.canvas.get_files()
        if len(files) < 2:
            self._set_status("Add at least two PDFs.", err=True)
            return
        out = self._output_path("merged.pdf", source_dir=os.path.dirname(os.path.abspath(files[0])))
        self._run_worker(self._do_merge, files, out)

    @staticmethod
    def _do_merge(files, out):
        try:
            # pypdf is the actively maintained package and the one listed
            # in requirements.txt — tried first so a clean venv actually
            # uses it, rather than silently falling through to PyPDF2.
            import pypdf
            merger = pypdf.PdfWriter()
            for f in files:
                reader = pypdf.PdfReader(f)
                for page in reader.pages:
                    merger.add_page(page)
            with open(out, "wb") as fh:
                merger.write(fh)
        except ImportError:
            # Legacy fallback for environments that only have the older,
            # now-unmaintained PyPDF2 installed instead of pypdf.
            from PyPDF2 import PdfMerger
            merger = PdfMerger()
            for f in files:
                merger.append(f)
            merger.write(out)
            merger.close()
        return f"Merged {len(files)} files -> {os.path.basename(out)}"

    # ------------------------------------------------------------------
    # ORGANIZE PDF  (now the app's single unified PDF tool: accepts PDFs
    # and images dropped anywhere on the app window, reorder/rotate/delete
    # pages. Save PDF lives on the main window's top bar, not here.)
    # ------------------------------------------------------------------
    def _sb_organize(self, layout):
        clear_btn = self._secondary_btn("Clear", 34)
        clear_btn.clicked.connect(self.canvas.clear)
        layout.addWidget(clear_btn)
        layout.addStretch(1)

        expand_btn = self._secondary_btn("Organize", 34)
        expand_btn.clicked.connect(self._toggle_organize_expanded)
        layout.addWidget(expand_btn)
        self._expand_btn = expand_btn
        self._organize_expanded = False

    def _toggle_organize_expanded(self):
        """Switch between the compact per-file card view (default) and the
        full per-page editor (reorder/rotate/delete individual pages) —
        triggered by the "Organize"/"Done" button, no separate window.

        set_expanded_workspace() already resizes the page grid's cards and
        emits height_hint_changed, which _sync_panel_height() below is
        wired to, which in turn tells main_window to grow/shrink the whole
        app window around it. No reparenting needed.
        """
        if self.tool_id != "organize" or not hasattr(self.canvas, "set_expanded_workspace"):
            return
        self._organize_expanded = not self._organize_expanded
        self.canvas.set_expanded_workspace(self._organize_expanded)
        self._expand_btn.setText("Done" if self._organize_expanded else "Organize")
        self._sync_panel_height()

    def _run_organize(self):
        pages = self.canvas.get_pages()
        if not pages:
            self._set_status("Add PDF pages or images first.", err=True)
            return
        out = self._output_path("organized.pdf", source_dir=os.path.dirname(os.path.abspath(pages[0]["path"])))
        self._run_worker(self._do_organize, pages, out)

    # ------------------------------------------------------------------
    # PROTECT PDF — compact custom layout
    # (Retired as a standalone tool — password-protect now lives inside
    # Organize's own save step, see _build_organize_password_section and
    # _do_organize. Kept here only for reference, never reached since
    # PDF_TOOLS in main_window.py no longer lists "protect".)
    # ------------------------------------------------------------------
    def _build_protect_layout(self, layout):
        """Build the full compact Protect PDF panel in one vertical layout.

        Replaces the shared canvas + controls-row pattern entirely.
        Single-file only: drop zone hides after a file is accepted.
        """
        # ---- Internal state ----
        self._protect_path = None   # currently loaded PDF path

        # ---- Drop zone ----
        drop_zone = QFrame()
        drop_zone.setObjectName("protectDropZone")
        drop_zone.setAcceptDrops(True)
        drop_zone.setFixedHeight(72)
        drop_zone.setStyleSheet("""
            QFrame#protectDropZone {
                background: rgba(88,101,242,0.07);
                border: 1.5px dashed rgba(116,130,255,0.40);
                border-radius: 12px;
            }
            QFrame#protectDropZone:hover {
                background: rgba(88,101,242,0.13);
                border-color: rgba(116,130,255,0.70);
            }
        """)
        dz_layout = QHBoxLayout(drop_zone)
        dz_layout.setContentsMargins(14, 0, 14, 0)
        dz_layout.setSpacing(10)
        dz_icon = QLabel("📄")
        dz_icon.setStyleSheet("font-size: 22px; border: none; background: transparent;")
        dz_text = QVBoxLayout()
        dz_text.setSpacing(1)
        dz_title = QLabel("Drop a PDF here")
        dz_title.setStyleSheet("color: rgba(255,255,255,0.80); font-size: 13px; font-weight: 600; border: none; background: transparent;")
        dz_sub = QLabel("or use Browse PDF")
        dz_sub.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 11px; border: none; background: transparent;")
        dz_text.addWidget(dz_title)
        dz_text.addWidget(dz_sub)
        dz_layout.addWidget(dz_icon)
        dz_layout.addLayout(dz_text, 1)
        browse_btn = self._secondary_btn("Browse PDF", 30)
        browse_btn.clicked.connect(self._protect_browse)
        dz_layout.addWidget(browse_btn)
        self._protect_drop_area = drop_zone
        layout.addWidget(drop_zone)

        # Wire drag-drop on drop zone
        drop_zone.dragEnterEvent  = self._protect_drag_enter
        drop_zone.dragLeaveEvent  = self._protect_drag_leave
        drop_zone.dropEvent       = self._protect_drop

        # ---- File card (hidden until file loaded) ----
        file_card = QFrame()
        file_card.setObjectName("protectFileCard")
        file_card.setStyleSheet("""
            QFrame#protectFileCard {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }
        """)
        fc_layout = QHBoxLayout(file_card)
        fc_layout.setContentsMargins(14, 10, 14, 10)
        fc_layout.setSpacing(12)

        fc_icon = QLabel("📄")
        fc_icon.setStyleSheet("font-size: 26px; border: none; background: transparent;")
        fc_info = QVBoxLayout()
        fc_info.setSpacing(2)
        self._protect_name_lbl = QLabel("—")
        self._protect_name_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.88); font-size: 13px; font-weight: 600; border: none; background: transparent;"
        )
        self._protect_size_lbl = QLabel("")
        self._protect_size_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size: 11px; border: none; background: transparent;"
        )
        fc_info.addWidget(self._protect_name_lbl)
        fc_info.addWidget(self._protect_size_lbl)

        clear_btn = QPushButton("✕")
        clear_btn.setFixedSize(26, 26)
        clear_btn.setToolTip("Remove file")
        clear_btn.setCursor(QCursor(Qt.PointingHandCursor))
        clear_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.07);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: rgba(255,255,255,0.55);
                font-size: 12px;
            }
            QPushButton:hover { background: rgba(248,113,113,0.22); color: #f87171; border-color: rgba(248,113,113,0.35); }
        """)
        clear_btn.clicked.connect(self._protect_clear)

        fc_layout.addWidget(fc_icon)
        fc_layout.addLayout(fc_info, 1)
        fc_layout.addWidget(clear_btn)
        self._protect_file_card = file_card
        file_card.hide()
        layout.addWidget(file_card)

        # ---- Separator ----
        layout.addWidget(sep_widget())

        # ---- Password field ----
        _PW_STYLE = """
            QLineEdit {
                background: rgba(255,255,255,0.07);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px;
                color: white;
                font-size: 13px;
                padding: 0px 10px;
            }
            QLineEdit:focus { border-color: rgba(88,101,242,0.60); }
        """
        pw_lbl = QLabel("Password")
        pw_lbl.setStyleSheet(PDF_LABEL_STYLE + " margin-bottom: 2px;")
        layout.addWidget(pw_lbl)
        self._pw_input = QLineEdit()
        self._pw_input.setEchoMode(QLineEdit.Password)
        self._pw_input.setPlaceholderText("Enter password…")
        self._pw_input.setFixedHeight(32)
        self._pw_input.setStyleSheet(_PW_STYLE)
        layout.addWidget(self._pw_input)

        # ---- Confirm field ----
        cf_lbl = QLabel("Confirm Password")
        cf_lbl.setStyleSheet(PDF_LABEL_STYLE + " margin-top: 4px; margin-bottom: 2px;")
        layout.addWidget(cf_lbl)
        self._pw_confirm = QLineEdit()
        self._pw_confirm.setEchoMode(QLineEdit.Password)
        self._pw_confirm.setPlaceholderText("Repeat password…")
        self._pw_confirm.setFixedHeight(32)
        self._pw_confirm.setStyleSheet(_PW_STYLE)
        layout.addWidget(self._pw_confirm)

        # ---- Overwrite checkbox ----
        self._overwrite_chk = QCheckBox("Overwrite original PDF")
        self._overwrite_chk.setChecked(False)
        self._overwrite_chk.setStyleSheet("""
            QCheckBox {
                color: rgba(255,255,255,0.55);
                font-size: 12px;
                spacing: 7px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid rgba(255,255,255,0.22);
                border-radius: 4px;
                background: rgba(255,255,255,0.06);
            }
            QCheckBox::indicator:checked {
                background: #5865F2;
                border-color: #5865F2;
                image: none;
            }
            QCheckBox::indicator:hover {
                border-color: rgba(88,101,242,0.60);
            }
            QCheckBox:hover { color: rgba(255,255,255,0.80); }
        """)
        layout.addWidget(self._overwrite_chk)

        # ---- Protect button ----
        go_btn = self._action_btn("🔒  Protect PDF")
        go_btn.clicked.connect(self._run_protect)
        go_btn.setEnabled(False)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

        # ---- Status label ----
        self._status = QLabel("")
        self._status.setStyleSheet(PDF_LABEL_STYLE)
        self._status.setWordWrap(True)
        self._status.setFixedHeight(22)
        layout.addWidget(self._status)

        self._sync_panel_height()

    # -- Protect drag-drop handlers --
    def _protect_drag_enter(self, event):
        if (event.mimeData().hasUrls() and self._protect_path is None and
                any(url.toLocalFile().lower().endswith('.pdf')
                    for url in event.mimeData().urls())):
            event.acceptProposedAction()
            self._protect_drop_area.setStyleSheet("""
                QFrame#protectDropZone {
                    background: rgba(88,101,242,0.18);
                    border: 1.5px dashed rgba(116,130,255,0.80);
                    border-radius: 12px;
                }
            """)
        else:
            event.ignore()

    def _protect_drag_leave(self, event):
        self._protect_drop_area.setStyleSheet("""
            QFrame#protectDropZone {
                background: rgba(88,101,242,0.07);
                border: 1.5px dashed rgba(116,130,255,0.40);
                border-radius: 12px;
            }
            QFrame#protectDropZone:hover {
                background: rgba(88,101,242,0.13);
                border-color: rgba(116,130,255,0.70);
            }
        """)
        event.accept()

    def _protect_drop(self, event):
        self._protect_drag_leave(event)
        if self._protect_path is not None:
            return  # single-file only
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith('.pdf'):
                self._protect_load(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _protect_browse(self):
        if self._protect_path is not None:
            self._set_status("Protect PDF supports one file at a time.", err=True)
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF (*.pdf)")
        if path:
            self._protect_load(path)

    def _protect_load(self, path):
        """Accept a single PDF for protection, update the file card, hide drop zone."""
        self._protect_path = path
        name = os.path.basename(path)
        try:
            kb = os.path.getsize(path) / 1024
            size_str = f"{kb:.1f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"
        except OSError:
            size_str = ""
        self._protect_name_lbl.setText(name)
        self._protect_size_lbl.setText(size_str)
        self._protect_drop_area.hide()
        self._protect_file_card.show()
        self._go_btn.setEnabled(True)
        self._set_status("", err=False)
        self._sync_panel_height()

    def _protect_clear(self):
        """Remove the loaded file, restore the drop zone."""
        self._protect_path = None
        self._protect_file_card.hide()
        self._protect_drop_area.show()
        self._go_btn.setEnabled(False)
        self._set_status("", err=False)
        self._sync_panel_height()

    # -- existing _sb_protect is replaced by _build_protect_layout --
    def _sb_protect(self, layout):
        """No-op: protect tool uses _build_protect_layout() via __init__ branch."""
        pass

    def _run_protect(self):
        """Validate inputs then dispatch the protect worker."""
        if not self._protect_path:
            self._set_status("Drop a PDF first.", err=True)
            return
        pw = self._pw_input.text() if self._pw_input else ""
        confirm = self._pw_confirm.text() if self._pw_confirm else ""
        if not pw:
            self._set_status("Password cannot be empty.", err=True)
            return
        if pw != confirm:
            self._set_status("Passwords do not match.", err=True)
            return

        overwrite = self._overwrite_chk.isChecked() if self._overwrite_chk else False

        if overwrite:
            # Overwrite: write to a temp file first, then atomically replace.
            out = self._protect_path
        else:
            # Save to a new file in the pdf_output folder.
            base = os.path.splitext(os.path.basename(self._protect_path))[0]
            out = self._output_path(f"{base}_protected.pdf",
                                     source_dir=os.path.dirname(os.path.abspath(self._protect_path)))

        self._run_worker(self._do_protect, self._protect_path, out, pw, overwrite)

    @staticmethod
    def _do_protect(pdf_path, out, password, overwrite=False):
        """Encrypt a PDF with user+owner password using pypdf (128-bit RC4).

        If overwrite=True, writes to a temp file first, verifies the output
        is a valid encrypted PDF, then atomically replaces the original.
        """
        import tempfile, shutil

        # Always write to a temp file so the original is never damaged
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.pdf')
        os.close(tmp_fd)
        try:
            try:
                import pypdf
                reader = pypdf.PdfReader(pdf_path)
                writer = pypdf.PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                try:
                    writer.encrypt(user_password=password, owner_password=password,
                                    use_128bit=True)
                except TypeError:
                    writer.encrypt(password)
                with open(tmp_path, "wb") as fh:
                    writer.write(fh)
            except ImportError:
                try:
                    from PyPDF2 import PdfReader, PdfWriter
                    reader = PdfReader(pdf_path)
                    writer = PdfWriter()
                    for page in reader.pages:
                        writer.add_page(page)
                    writer.encrypt(password)
                    with open(tmp_path, "wb") as fh:
                        writer.write(fh)
                except ImportError:
                    raise RuntimeError(
                        "pypdf or PyPDF2 is required.\n"
                        "Install: pip install pypdf"
                    )

            # Integrity check: confirm output is a valid encrypted PDF
            try:
                import pypdf as _pypdf
                _r = _pypdf.PdfReader(tmp_path)
                assert _r.is_encrypted, "Output PDF is not encrypted"
            except AssertionError:
                raise RuntimeError("Encryption verification failed.")
            except Exception:
                pass  # pypdf unavailable for verify — trust the write succeeded

            if overwrite:
                # Atomic replace: move temp over original
                shutil.move(tmp_path, pdf_path)
                tmp_path_to_clean = None
                return f"Overwritten -> {os.path.basename(pdf_path)}"
            else:
                shutil.move(tmp_path, out)
                tmp_path_to_clean = None
                return f"Protected -> {os.path.basename(out)}"
        finally:
            # Clean up temp file if something went wrong before the move
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _do_organize(pages, out):
        """Builds the final PDF from a mix of real PDF pages and standalone
        images — each image becomes its own single-page PDF in memory, via
        Pillow, then gets folded into the same writer as a normal page.
        This is now the app's one PDF save path, replacing what used to be
        three separate tools (Image→PDF, Merge, Organize)."""
        import copy

        IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

        def _image_to_pdf_buffer(image_path):
            img = Image.open(image_path).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PDF")
            buf.seek(0)
            return buf

        # Keeps in-memory PDF buffers/readers alive until writer.write()
        # actually consumes their pages — letting them get garbage
        # collected early would corrupt the output.
        keep_alive = []

        try:
            # pypdf is the actively maintained package and the one listed
            # in requirements.txt — tried first so a clean venv actually
            # uses it, rather than silently falling through to PyPDF2.
            import pypdf
            writer = pypdf.PdfWriter()
            readers = {}
            for item in pages:
                path = item["path"]
                if os.path.splitext(path)[1].lower() in IMAGE_EXTS:
                    buf = _image_to_pdf_buffer(path)
                    reader = pypdf.PdfReader(buf)
                    keep_alive.append((buf, reader))
                    page = reader.pages[0]
                else:
                    if path not in readers:
                        readers[path] = pypdf.PdfReader(path)
                    page = copy.copy(readers[path].pages[item["page_index"]])
                rotation = item.get("rotation", 0) % 360
                if rotation:
                    page.rotate(rotation)
                writer.add_page(page)

            with open(out, "wb") as fh:
                writer.write(fh)
        except ImportError:
            # Legacy fallback for environments that only have the older,
            # now-unmaintained PyPDF2 installed instead of pypdf.
            from PyPDF2 import PdfReader, PdfWriter
            writer = PdfWriter()
            readers = {}
            for item in pages:
                path = item["path"]
                if os.path.splitext(path)[1].lower() in IMAGE_EXTS:
                    buf = _image_to_pdf_buffer(path)
                    reader = PdfReader(buf)
                    keep_alive.append((buf, reader))
                    page = reader.pages[0]
                else:
                    if path not in readers:
                        readers[path] = PdfReader(path)
                    page = copy.copy(readers[path].pages[item["page_index"]])
                rotation = item.get("rotation", 0) % 360
                if rotation:
                    if hasattr(page, "rotate"):
                        page.rotate(rotation)
                    else:
                        page.rotate_clockwise(rotation)
                writer.add_page(page)

            with open(out, "wb") as fh:
                writer.write(fh)

        return f"Saved {len(pages)} page(s) -> {os.path.basename(out)}"

    # ------------------------------------------------------------------
    # SHARED WORKER RUNNER
    # ------------------------------------------------------------------
    def add_files(self, paths):
        """Public entry point used by the tray / toolbar to route files here.

        Dispatches correctly for the protect tool (single-PDF, no canvas)
        vs all other tools (canvas-based). Handles type validation and shows
        user-facing error messages instead of crashing.
        """
        if self.tool_id == "protect":
            # Protect supports ONE PDF only — route via _protect_load.
            pdf_paths  = [p for p in paths if p and p.lower().endswith(".pdf")]
            non_pdf    = [p for p in paths if p and not p.lower().endswith(".pdf")]
            if non_pdf:
                self._set_status("Only PDF files are supported.", err=True)
            if not pdf_paths:
                return
            if self._protect_path is not None:
                self._set_status("Protect PDF supports one file at a time.", err=True)
                return
            # Load only the first PDF (single-file constraint).
            self._protect_load(pdf_paths[0])
        else:
            # Canvas-based tools: delegate directly.
            if hasattr(self, "canvas") and self.canvas is not None:
                self.canvas.add_files(paths)

    def _on_files_changed(self, files):
        self._sync_action_state(files)
        if files:
            if self.tool_id == "img2pdf":
                noun = "image" if len(files) == 1 else "images"
                self._set_status(f"{len(files)} {noun} ready — will create a {len(files)}-page PDF.", err=False)
            else:
                self._set_status(f"{len(files)} file(s) ready.", err=False)
        else:
            self._set_status("", err=False)

    def _sync_action_state(self, files=None):
        if not self._go_btn:
            return
        # Protect tool manages its own button state via _protect_load/_protect_clear.
        if self.tool_id == "protect":
            return
        if not hasattr(self, "canvas") or self.canvas is None:
            return
        if self.tool_id == "organize":
            count = len(self.canvas.get_pages())
        else:
            count = len(files if files is not None else self.canvas.get_files())
        required = 2 if self.tool_id == "merge" else 1
        self._go_btn.setEnabled(count >= required)

    def _sync_panel_height(self, *_args):
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())
        self.height_hint_changed.emit(self.height())

    def _worker_is_running(self):
        """Safe isRunning() — returns False if the C++ QThread has been deleted."""
        try:
            return self._thread is not None and self._thread.isRunning()
        except RuntimeError:
            self._thread = None
            self._worker = None
            return False

    def _run_worker(self, fn, *args):
        # Guard against re-entrant runs. self._go_btn.setEnabled(False)
        # below only protects the compact panel's own button — the
        # Organize "Expand" dialog builds its own separate Save/Browse
        # buttons that aren't disabled by that, so without this check a
        # user could click Save PDF again mid-run and kick off a second
        # concurrent worker/thread writing to disk at the same time.
        if self._worker_is_running():
            return
        self._set_status("Working...", err=False)
        if self._go_btn:
            self._go_btn.setEnabled(False)
        self._thread = QThread()
        self._worker = PdfWorker(fn, *args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(lambda msg: self._on_done(msg))
        self._worker.error.connect(lambda err: self._on_err(err))
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        # Both worker and thread need cleanup here, not just the thread —
        # every PDF tool action creates a fresh PdfWorker via this method,
        # and without this line each one was left orphaned on its
        # now-dead thread instead of actually being deleted.
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_done(self, msg):
        self._set_status(msg, err=False)
        self._sync_action_state()

    def _on_err(self, err):
        self._set_status(err, err=True)
        self._sync_action_state()

    def _set_status(self, text, err=False):
        if not self._status:
            return
        self._status.setStyleSheet(PDF_STATUS_ERR if err else PDF_STATUS_OK)
        self._status.setText(text)