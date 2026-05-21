import os

from PIL import Image

from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QSpinBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCursor

from styles import (
    PANEL_STYLE, SIDEBAR_STYLE, ACTION_BTN_STYLE, SECONDARY_BTN_STYLE,
    SPINBOX_STYLE, PDF_LABEL_STYLE, PDF_STATUS_OK, PDF_STATUS_ERR
)
from helpers import PANEL_H, section_label, sep_widget
from pdf_canvas import PdfDropCanvas
from workers import PdfWorker

from PyQt5.QtCore import QThread


# =========================================
# PDF TOOL PANEL
# =========================================

class PdfToolPanel(QFrame):
    def __init__(self, tool_id, parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self.setObjectName("panel")
        self.setStyleSheet(PANEL_STYLE)
        self.setFixedHeight(PANEL_H)
        self._thread = None
        self._worker = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        canvas_cfg = {
            'img2pdf':  dict(accept_images=True,  accept_pdfs=False),
            'pdf2img':  dict(accept_images=False,  accept_pdfs=True),
            'merge':    dict(accept_images=False,  accept_pdfs=True),
            'split':    dict(accept_images=False,  accept_pdfs=True),
            'compress': dict(accept_images=False,  accept_pdfs=True),
        }
        cfg = canvas_cfg.get(tool_id, dict(accept_images=False, accept_pdfs=True))
        self.canvas = PdfDropCanvas(**cfg)
        layout.addWidget(self.canvas, 1)

        sb = QFrame()
        sb.setFixedWidth(200)
        sb.setStyleSheet(SIDEBAR_STYLE)
        sbl = QVBoxLayout(sb)
        sbl.setContentsMargins(14, 18, 14, 18)
        sbl.setSpacing(10)

        self._build_sidebar(sbl, tool_id)
        sbl.addStretch()
        layout.addWidget(sb)

    def _build_sidebar(self, layout, tool_id):
        builders = {
            'img2pdf':  self._sb_img2pdf,
            'pdf2img':  self._sb_pdf2img,
            'merge':    self._sb_merge,
            'split':    self._sb_split,
            'compress': self._sb_compress,
        }
        builders.get(tool_id, lambda l: None)(layout)

    def _lbl(self, text):
        l = QLabel(text)
        l.setStyleSheet(PDF_LABEL_STYLE)
        l.setWordWrap(True)
        return l

    def _action_btn(self, text):
        btn = QPushButton(text)
        btn.setStyleSheet(ACTION_BTN_STYLE)
        btn.setFixedHeight(38)
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        return btn

    def _secondary_btn(self, text, height=34):
        btn = QPushButton(text)
        btn.setStyleSheet(SECONDARY_BTN_STYLE)
        btn.setFixedHeight(height)
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        return btn

    # ------------------------------------------------------------------
    # IMG → PDF
    # ------------------------------------------------------------------
    def _sb_img2pdf(self, layout):
        layout.addWidget(section_label("Image → PDF"))
        layout.addWidget(sep_widget())
        layout.addWidget(self._lbl("Drop PNG / JPG / WEBP images into the canvas."))
        layout.addWidget(self._lbl("Images will be combined in drop order."))
        add_btn = self._secondary_btn("➕  Add Images")
        add_btn.clicked.connect(self._add_images)
        clr_btn = self._secondary_btn("🗑  Clear")
        clr_btn.clicked.connect(self.canvas.clear)
        layout.addWidget(add_btn)
        layout.addWidget(clr_btn)
        layout.addWidget(sep_widget())
        go_btn = self._action_btn("✅  Convert to PDF")
        go_btn.clicked.connect(self._run_img2pdf)
        self._status = QLabel("")
        self._status.setStyleSheet(PDF_LABEL_STYLE)
        self._status.setWordWrap(True)
        layout.addWidget(go_btn)
        layout.addWidget(self._status)
        self._go_btn = go_btn

    def _add_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Images", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if files:
            self.canvas.add_files(files)

    def _run_img2pdf(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("⚠️  Drop at least one image first", err=True)
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
        return f"✅  Saved → {os.path.basename(out)}"

    # ------------------------------------------------------------------
    # PDF → IMG
    # ------------------------------------------------------------------
    def _sb_pdf2img(self, layout):
        layout.addWidget(section_label("PDF → Images"))
        layout.addWidget(sep_widget())
        layout.addWidget(self._lbl("Drop a PDF into the canvas."))
        add_btn = self._secondary_btn("📂  Add PDF")
        add_btn.clicked.connect(self._add_pdf_single)
        layout.addWidget(add_btn)
        layout.addWidget(sep_widget())
        layout.addWidget(section_label("DPI"))
        self._dpi_spin = QSpinBox()
        self._dpi_spin.setRange(72, 600)
        self._dpi_spin.setValue(150)
        self._dpi_spin.setButtonSymbols(QSpinBox.NoButtons)
        self._dpi_spin.setStyleSheet(SPINBOX_STYLE)
        layout.addWidget(self._dpi_spin)
        layout.addWidget(sep_widget())
        go_btn = self._action_btn("✅  Export Images")
        go_btn.clicked.connect(self._run_pdf2img)
        self._status = QLabel("")
        self._status.setStyleSheet(PDF_LABEL_STYLE)
        self._status.setWordWrap(True)
        layout.addWidget(go_btn)
        layout.addWidget(self._status)
        self._go_btn = go_btn

    def _add_pdf_single(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF (*.pdf)")
        if f:
            self.canvas.clear()
            self.canvas.add_files([f])

    def _run_pdf2img(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("⚠️  Drop a PDF first", err=True)
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
                    from PIL import Image
                    import io
                    pages.append(Image.open(io.BytesIO(pix.tobytes("png"))))
            except Exception as e2:
                raise RuntimeError(
                    f"pdf2image (needs Poppler) or PyMuPDF not available.\n"
                    f"Install: pip install pdf2image  or  pip install pymupdf\n{e2}")
        name = os.path.splitext(os.path.basename(pdf_path))[0]
        for i, page in enumerate(pages):
            page.save(os.path.join(folder, f"{name}_page{i+1}.jpg"), "JPEG")
        return f"✅  {len(pages)} page(s) saved"

    # ------------------------------------------------------------------
    # MERGE
    # ------------------------------------------------------------------
    def _sb_merge(self, layout):
        layout.addWidget(section_label("Merge PDFs"))
        layout.addWidget(sep_widget())
        layout.addWidget(self._lbl("Drop PDFs into the canvas.\nThey'll be merged in drop order."))
        add_btn = self._secondary_btn("➕  Add PDFs")
        add_btn.clicked.connect(self._add_pdfs_multi)
        clr_btn = self._secondary_btn("🗑  Clear")
        clr_btn.clicked.connect(self.canvas.clear)
        layout.addWidget(add_btn)
        layout.addWidget(clr_btn)
        layout.addWidget(sep_widget())
        go_btn = self._action_btn("✅  Merge & Save")
        go_btn.clicked.connect(self._run_merge)
        self._status = QLabel("")
        self._status.setStyleSheet(PDF_LABEL_STYLE)
        self._status.setWordWrap(True)
        layout.addWidget(go_btn)
        layout.addWidget(self._status)
        self._go_btn = go_btn

    def _add_pdfs_multi(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDFs", "", "PDF (*.pdf)")
        if files:
            self.canvas.add_files(files)

    def _run_merge(self):
        files = self.canvas.get_files()
        if len(files) < 2:
            self._set_status("⚠️  Add at least 2 PDFs", err=True)
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
        return f"✅  Merged {len(files)} files → {os.path.basename(out)}"

    # ------------------------------------------------------------------
    # SPLIT
    # ------------------------------------------------------------------
    def _sb_split(self, layout):
        layout.addWidget(section_label("Split PDF"))
        layout.addWidget(sep_widget())
        layout.addWidget(self._lbl("Drop a PDF.\nEach page saved as a separate PDF."))
        add_btn = self._secondary_btn("📂  Add PDF")
        add_btn.clicked.connect(self._add_pdf_single)
        layout.addWidget(add_btn)
        layout.addWidget(sep_widget())
        go_btn = self._action_btn("✅  Split & Save")
        go_btn.clicked.connect(self._run_split)
        self._status = QLabel("")
        self._status.setStyleSheet(PDF_LABEL_STYLE)
        self._status.setWordWrap(True)
        layout.addWidget(go_btn)
        layout.addWidget(self._status)
        self._go_btn = go_btn

    def _run_split(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("⚠️  Drop a PDF first", err=True)
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
        return f"✅  Split into {n} page(s)"

    # ------------------------------------------------------------------
    # COMPRESS PDF
    # ------------------------------------------------------------------
    def _sb_compress(self, layout):
        layout.addWidget(section_label("Compress PDF"))
        layout.addWidget(sep_widget())
        layout.addWidget(self._lbl("Drop a PDF.\nCompresses content streams — best on text-heavy PDFs."))
        add_btn = self._secondary_btn("📂  Add PDF")
        add_btn.clicked.connect(self._add_pdf_single)
        layout.addWidget(add_btn)
        layout.addWidget(sep_widget())
        go_btn = self._action_btn("✅  Compress & Save")
        go_btn.clicked.connect(self._run_compress)
        self._status = QLabel("")
        self._status.setStyleSheet(PDF_LABEL_STYLE)
        self._status.setWordWrap(True)
        layout.addWidget(go_btn)
        layout.addWidget(self._status)
        self._go_btn = go_btn

    def _run_compress(self):
        files = self.canvas.get_files()
        if not files:
            self._set_status("⚠️  Drop a PDF first", err=True)
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
        return f"✅  {orig_kb:.0f} KB → {new_kb:.0f} KB  (−{pct}%)"

    # ------------------------------------------------------------------
    # SHARED WORKER RUNNER
    # ------------------------------------------------------------------
    def _run_worker(self, fn, *args):
        self._set_status("⏳  Working…", err=False)
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
        self._go_btn.setEnabled(True)

    def _on_err(self, err):
        self._set_status(f"❌  {err}", err=True)
        self._go_btn.setEnabled(True)

    def _set_status(self, text, err=False):
        self._status.setStyleSheet(PDF_STATUS_ERR if err else PDF_STATUS_OK)
        self._status.setText(text)
