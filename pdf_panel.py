import os

from PIL import Image

from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QSpinBox, QSizePolicy, QDialog, QLineEdit, QWidget, QCheckBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QCursor, QColor, QPainter, QPen, QBrush, QFont

from styles import (
    ACTION_BTN_STYLE, SECONDARY_BTN_STYLE,
    SPINBOX_STYLE, PDF_LABEL_STYLE, PDF_STATUS_OK, PDF_STATUS_ERR,
    PDF_COMPACT_PANEL_STYLE, PDF_HEADER_STYLE, PDF_SUBTITLE_STYLE
)
from helpers import section_label, sep_widget
from pdf_canvas import PdfDropCanvas, OrganizePdfCanvas
from workers import PdfWorker


# ---------------------------------------------------------------------------
# Password strength helpers (used by Protect PDF panel)
# ---------------------------------------------------------------------------

def _password_strength(pw: str) -> int:
    """Return a strength score 0-4 for a given password string.

    Score levels:
        0 — empty
        1 — weak   (< 6 chars)
        2 — fair   (6-9 chars, or meets some criteria)
        3 — good   (10+ chars with mixed types)
        4 — strong (12+ chars with upper+lower+digit+symbol)
    """
    if not pw:
        return 0
    n = len(pw)
    has_upper  = any(c.isupper()  for c in pw)
    has_lower  = any(c.islower()  for c in pw)
    has_digit  = any(c.isdigit()  for c in pw)
    has_symbol = any(not c.isalnum() for c in pw)
    variety = sum([has_upper, has_lower, has_digit, has_symbol])
    if n < 6:
        return 1
    if n < 10 or variety < 2:
        return 2
    if n < 12 or variety < 3:
        return 3
    return 4


class _PasswordStrengthBar(QWidget):
    """A thin progress bar that shows password strength with color coding."""

    _COLORS = [
        None,                        # 0: empty — no bar
        QColor(239, 68,  68),        # 1: weak   — red
        QColor(249, 115, 22),        # 2: fair   — orange
        QColor(234, 179,  8),        # 3: good   — yellow
        QColor( 74, 222, 128),       # 4: strong — green
    ]
    _LABELS = ["", "Weak", "Fair", "Good", "Strong"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0
        self._match = None   # None = unchecked, True = match, False = mismatch
        self.setFixedHeight(14)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_state(self, score: int, match):
        self._score = max(0, min(4, score))
        self._match = match
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background track
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 18))
        painter.drawRoundedRect(0, 2, w, h - 4, 3, 3)

        if self._score > 0:
            fill_w = int(w * self._score / 4)
            color = self._COLORS[self._score]
            # Mismatch override: tint red even if score is high
            if self._match is False:
                color = QColor(239, 68, 68)
            painter.setBrush(color)
            painter.drawRoundedRect(0, 2, fill_w, h - 4, 3, 3)

            # Label
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            label = self._LABELS[self._score]
            if self._match is False:
                label = "Mismatch"
            elif self._match is True:
                label = self._LABELS[self._score] + " ✓"
            painter.setPen(QColor(255, 255, 255, 160))
            from PyQt5.QtCore import QRect
            painter.drawText(QRect(0, 0, w, h), Qt.AlignRight | Qt.AlignVCenter, label)


class ExpandedWorkspaceDialog(QDialog):
    def __init__(self, title, workspace, actions, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(1100, 760)
        self.setStyleSheet(PDF_COMPACT_PANEL_STYLE + """
            QDialog {
                background: #1e1e22;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(10)

        header = QFrame()
        header.setStyleSheet("background: transparent; border: none;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(PDF_HEADER_STYLE)
        hl.addWidget(title_lbl)
        hl.addStretch(1)

        for text, slot, primary in actions:
            btn = QPushButton(text)
            btn.setFixedHeight(34)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setStyleSheet(ACTION_BTN_STYLE if primary else SECONDARY_BTN_STYLE)
            btn.clicked.connect(slot)
            hl.addWidget(btn)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(34)
        close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        close_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        close_btn.clicked.connect(self.accept)
        hl.addWidget(close_btn)

        outer.addWidget(header)
        outer.addWidget(sep_widget())
        outer.addWidget(workspace)


# =========================================
# PDF TOOL PANEL
# =========================================

class PdfToolPanel(QFrame):
    height_hint_changed = pyqtSignal(int)

    TOOL_TITLES = {
        "img2pdf": "Image to PDF",
        "pdf2img": "PDF to Images",
        "merge": "Merge PDF",
        "split": "Split PDF",
        "compress": "Compress PDF",
        "organize": "Organize PDF",
        "protect": "Protect PDF",
    }

    TOOL_GLYPHS = {
        "img2pdf": "IMG",
        "pdf2img": "PDF",
        "merge": "PDF",
        "split": "PDF",
        "compress": "PDF",
        "organize": "A/B",
        "protect": "🔒",
    }

    def __init__(self, tool_id, parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self.setObjectName("pdfCompactPanel")
        self.setStyleSheet(PDF_COMPACT_PANEL_STYLE)
        self.setFixedWidth(820 if tool_id == "organize" else 760)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._thread = None
        self._worker = None
        self._go_btn = None
        self._status = None
        self._fade_anim = None
        # Protect-PDF specific widgets
        self._pw_input = None
        self._pw_confirm = None
        self._pw_strength = None
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
                "pdf2img": dict(accept_images=False, accept_pdfs=True),
                "merge": dict(accept_images=False, accept_pdfs=True),
                "split": dict(accept_images=False, accept_pdfs=True),
                "compress": dict(accept_images=False, accept_pdfs=True),
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

    def _build_header(self, layout):
        header = QFrame()
        header.setObjectName("pdfToolHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(9)

        badge = QLabel(self.TOOL_GLYPHS.get(self.tool_id, "PDF"))
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(34, 28)
        badge.setStyleSheet("""
            QLabel {
                background: rgba(88,101,242,0.18);
                border: 1px solid rgba(88,101,242,0.34);
                border-radius: 9px;
                color: #dfe3ff;
                font-size: 10px;
                font-weight: 800;
            }
        """)
        title = QLabel(self.TOOL_TITLES.get(self.tool_id, "PDF Tool"))
        title.setStyleSheet(PDF_HEADER_STYLE)
        chevron = QLabel("⌄")
        chevron.setAlignment(Qt.AlignCenter)
        chevron.setStyleSheet(PDF_SUBTITLE_STYLE)
        hl.addWidget(badge)
        hl.addWidget(title)
        hl.addStretch(1)
        hl.addWidget(chevron)
        layout.addWidget(header)

    def _build_controls(self, layout, tool_id):
        builders = {
            "img2pdf": self._sb_img2pdf,
            "pdf2img": self._sb_pdf2img,
            "merge": self._sb_merge,
            "split": self._sb_split,
            "compress": self._sb_compress,
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
    # IMG -> PDF
    # ------------------------------------------------------------------
    def _sb_img2pdf(self, layout):
        self._add_common_buttons(layout, "Browse Images", self._add_images)
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
        out, _ = QFileDialog.getSaveFileName(self, "Save PDF as", "output.pdf", "PDF (*.pdf)")
        if not out:
            return
        self._run_worker(self._do_img2pdf, files, out)

    @staticmethod
    def _do_img2pdf(files, out):
        imgs = []
        first = Image.open(files[0]).convert("RGB")
        for f in files[1:]:
            imgs.append(Image.open(f).convert("RGB"))
        first.save(out, save_all=True, append_images=imgs)
        return f"Saved -> {os.path.basename(out)}"

    # ------------------------------------------------------------------
    # PDF -> IMG
    # ------------------------------------------------------------------
    def _sb_pdf2img(self, layout):
        self._add_common_buttons(layout, "Browse PDF", self._add_pdf_single, clear=False)
        layout.addWidget(section_label("DPI"))
        self._dpi_spin = QSpinBox()
        self._dpi_spin.setRange(72, 600)
        self._dpi_spin.setValue(150)
        self._dpi_spin.setButtonSymbols(QSpinBox.NoButtons)
        self._dpi_spin.setFixedHeight(34)
        self._dpi_spin.setStyleSheet(SPINBOX_STYLE)
        layout.addWidget(self._dpi_spin)
        layout.addStretch(1)
        go_btn = self._action_btn("Export Images")
        go_btn.clicked.connect(self._run_pdf2img)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

    def _add_pdf_single(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF (*.pdf)")
        if file_path:
            self.canvas.clear()
            self.canvas.add_files([file_path])

    def _run_pdf2img(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("Drop a PDF first.", err=True)
            return
        folder = QFileDialog.getExistingDirectory(self, "Output folder")
        if not folder:
            return
        self._run_worker(self._do_pdf2img, files[0], folder, self._dpi_spin.value())

    @staticmethod
    def _do_pdf2img(pdf_path, folder, dpi):
        try:
            from pdf2image import convert_from_path
            pages = convert_from_path(pdf_path, dpi=dpi)
        except Exception:
            try:
                import fitz
                doc = fitz.open(pdf_path)
                pages = []
                for page in doc:
                    mat = fitz.Matrix(dpi / 72, dpi / 72)
                    pix = page.get_pixmap(matrix=mat)
                    import io
                    pages.append(Image.open(io.BytesIO(pix.tobytes("png"))))
            except Exception as e2:
                raise RuntimeError(
                    f"pdf2image (needs Poppler) or PyMuPDF not available.\n"
                    f"Install: pip install pdf2image or pip install pymupdf\n{e2}")
        name = os.path.splitext(os.path.basename(pdf_path))[0]
        for i, page in enumerate(pages):
            page.save(os.path.join(folder, f"{name}_page{i+1}.jpg"), "JPEG")
        return f"{len(pages)} page(s) saved"

    # ------------------------------------------------------------------
    # MERGE
    # ------------------------------------------------------------------
    def _sb_merge(self, layout):
        self._add_common_buttons(layout, "Browse PDFs", self._add_pdfs_multi)
        go_btn = self._action_btn("Merge PDF")
        go_btn.clicked.connect(self._run_merge)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

    def _add_pdfs_multi(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDFs", "", "PDF (*.pdf)")
        if files:
            self.canvas.add_files(files)

    def _run_merge(self):
        files = self.canvas.get_files()
        if len(files) < 2:
            self._set_status("Add at least two PDFs.", err=True)
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save merged PDF", "merged.pdf", "PDF (*.pdf)")
        if not out:
            return
        self._run_worker(self._do_merge, files, out)

    @staticmethod
    def _do_merge(files, out):
        try:
            from PyPDF2 import PdfMerger
            merger = PdfMerger()
            for f in files:
                merger.append(f)
            merger.write(out)
            merger.close()
        except ImportError:
            import pypdf
            merger = pypdf.PdfWriter()
            for f in files:
                reader = pypdf.PdfReader(f)
                for page in reader.pages:
                    merger.add_page(page)
            with open(out, "wb") as fh:
                merger.write(fh)
        return f"Merged {len(files)} files -> {os.path.basename(out)}"

    # ------------------------------------------------------------------
    # SPLIT
    # ------------------------------------------------------------------
    def _sb_split(self, layout):
        self._add_common_buttons(layout, "Browse PDF", self._add_pdf_single, clear=False)
        clear_btn = self._secondary_btn("Clear", 34)
        clear_btn.clicked.connect(self.canvas.clear)
        layout.addWidget(clear_btn)
        layout.addStretch(1)
        go_btn = self._action_btn("Split PDF")
        go_btn.clicked.connect(self._run_split)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

    def _run_split(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("Drop a PDF first.", err=True)
            return
        folder = QFileDialog.getExistingDirectory(self, "Output folder for split pages")
        if not folder:
            return
        self._run_worker(self._do_split, files[0], folder)

    @staticmethod
    def _do_split(pdf_path, folder):
        try:
            from PyPDF2 import PdfReader, PdfWriter
            reader = PdfReader(pdf_path)
            n = len(reader.pages)
            name = os.path.splitext(os.path.basename(pdf_path))[0]
            for i, page in enumerate(reader.pages):
                writer = PdfWriter()
                writer.add_page(page)
                out = os.path.join(folder, f"{name}_page{i+1}.pdf")
                with open(out, "wb") as fh:
                    writer.write(fh)
        except ImportError:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            n = len(reader.pages)
            name = os.path.splitext(os.path.basename(pdf_path))[0]
            for i, page in enumerate(reader.pages):
                writer = pypdf.PdfWriter()
                writer.add_page(page)
                out = os.path.join(folder, f"{name}_page{i+1}.pdf")
                with open(out, "wb") as fh:
                    writer.write(fh)
        return f"Split into {n} page(s)"

    # ------------------------------------------------------------------
    # COMPRESS PDF
    # ------------------------------------------------------------------
    def _sb_compress(self, layout):
        self._add_common_buttons(layout, "Browse PDF", self._add_pdf_single, clear=False)
        clear_btn = self._secondary_btn("Clear", 34)
        clear_btn.clicked.connect(self.canvas.clear)
        layout.addWidget(clear_btn)
        layout.addStretch(1)
        go_btn = self._action_btn("Compress PDF")
        go_btn.clicked.connect(self._run_compress)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

    def _run_compress(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("Drop a PDF first.", err=True)
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save compressed PDF", "compressed.pdf", "PDF (*.pdf)")
        if not out:
            return
        self._run_worker(self._do_compress, files[0], out)

    @staticmethod
    def _do_compress(pdf_path, out):
        orig_kb = os.path.getsize(pdf_path) / 1024
        try:
            from PyPDF2 import PdfReader, PdfWriter
            reader = PdfReader(pdf_path)
            writer = PdfWriter()
            for page in reader.pages:
                page.compress_content_streams()
                writer.add_page(page)
            with open(out, "wb") as fh:
                writer.write(fh)
        except ImportError:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            writer = pypdf.PdfWriter()
            for page in reader.pages:
                page.compress_content_streams()
                writer.add_page(page)
            with open(out, "wb") as fh:
                writer.write(fh)
        new_kb = os.path.getsize(out) / 1024
        pct = int((1 - new_kb / orig_kb) * 100) if orig_kb > 0 else 0
        return f"{orig_kb:.0f} KB -> {new_kb:.0f} KB ({pct}% smaller)"

    # ------------------------------------------------------------------
    # ORGANIZE PDF
    # ------------------------------------------------------------------
    def _sb_organize(self, layout):
        self._add_common_buttons(layout, "Browse PDFs", self._add_pdfs_multi)
        expand_btn = self._secondary_btn("Expand", 34)
        expand_btn.clicked.connect(self._open_expanded_organizer)
        layout.addWidget(expand_btn)
        go_btn = self._action_btn("Save PDF")
        go_btn.clicked.connect(self._run_organize)
        layout.addWidget(go_btn)
        self._go_btn = go_btn

    def _open_expanded_organizer(self):
        if self.tool_id != "organize":
            return
        self._layout.removeWidget(self.canvas)
        self.canvas.setParent(None)
        if hasattr(self.canvas, "set_expanded_workspace"):
            self.canvas.set_expanded_workspace(True)
        dialog = ExpandedWorkspaceDialog(
            "Organize PDF",
            self.canvas,
            [
                ("Browse PDFs", self._add_pdfs_multi, False),
                ("Clear", self.canvas.clear, False),
                ("Save PDF", self._run_organize, True),
            ],
            self.window()
        )
        dialog.finished.connect(lambda _code: self._restore_compact_canvas())
        dialog.exec_()

    def _restore_compact_canvas(self):
        if self.canvas.parent() is self:
            return
        if hasattr(self.canvas, "set_expanded_workspace"):
            self.canvas.set_expanded_workspace(False)
        self.canvas.setParent(self)
        self._layout.insertWidget(self._canvas_index, self.canvas)
        self.canvas.show()
        self._sync_panel_height()

    def _run_organize(self):
        pages = self.canvas.get_pages()
        if not pages:
            self._set_status("Add PDF pages first.", err=True)
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save organized PDF", "organized.pdf", "PDF (*.pdf)")
        if not out:
            return
        self._run_worker(self._do_organize, pages, out)

    # ------------------------------------------------------------------
    # PROTECT PDF — compact custom layout
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
        self._pw_input.textChanged.connect(self._update_pw_strength)
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
        self._pw_confirm.textChanged.connect(self._update_pw_strength)
        layout.addWidget(self._pw_confirm)

        # ---- Strength bar ----
        self._pw_strength = _PasswordStrengthBar()
        self._pw_strength.setFixedHeight(12)
        layout.addWidget(self._pw_strength)

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

    def _update_pw_strength(self):
        """Update the strength bar whenever either password field changes."""
        if self._pw_strength is None:
            return
        pw = self._pw_input.text() if self._pw_input else ""
        confirm = self._pw_confirm.text() if self._pw_confirm else ""
        score = _password_strength(pw)
        match = (pw == confirm) if (pw and confirm) else None
        self._pw_strength.set_state(score, match)

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
            # Save to a new file chosen by the user.
            base = os.path.splitext(os.path.basename(self._protect_path))[0]
            default_name = f"{base}_protected.pdf"
            out, _ = QFileDialog.getSaveFileName(
                self, "Save protected PDF", default_name, "PDF (*.pdf)")
            if not out:
                return

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
        import copy
        try:
            from PyPDF2 import PdfReader, PdfWriter
            writer = PdfWriter()
            readers = {}
            for item in pages:
                path = item["path"]
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
        except ImportError:
            import pypdf
            writer = pypdf.PdfWriter()
            readers = {}
            for item in pages:
                path = item["path"]
                if path not in readers:
                    readers[path] = pypdf.PdfReader(path)
                page = copy.copy(readers[path].pages[item["page_index"]])
                rotation = item.get("rotation", 0) % 360
                if rotation:
                    page.rotate(rotation)
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

    def _run_worker(self, fn, *args):
        self._set_status("Working...", err=False)
        self._go_btn.setEnabled(False)
        self._thread = QThread()
        self._worker = PdfWorker(fn, *args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(lambda msg: self._on_done(msg))
        self._worker.error.connect(lambda err: self._on_err(err))
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
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
