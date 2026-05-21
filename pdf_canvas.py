import os
import io

from PIL import Image

from PyQt5.QtWidgets import QWidget, QSizePolicy
from PyQt5.QtCore import Qt, QRect, pyqtSignal
from PyQt5.QtGui import QColor, QPixmap, QPainter, QPen, QBrush

from helpers import checkerboard_paint


# =========================================
# PDF DROP CANVAS
# =========================================

class PdfDropCanvas(QWidget):
    files_changed = pyqtSignal(list)

    def __init__(self, accept_images=False, accept_pdfs=True, parent=None):
        super().__init__(parent)
        self._accept_images = accept_images
        self._accept_pdfs   = accept_pdfs
        self._files         = []
        self._previews      = []
        self._hovering      = False
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(200)

    def get_files(self):
        return list(self._files)

    def clear(self):
        self._files    = []
        self._previews = []
        self.files_changed.emit([])
        self.update()

    def add_files(self, paths):
        for p in paths:
            if p not in self._files:
                self._files.append(p)
                if self._accept_images:
                    try:
                        pil = Image.open(p)
                        pil.thumbnail((120, 120), Image.LANCZOS)
                        buf = io.BytesIO()
                        pil.save(buf, "PNG")
                        buf.seek(0)
                        px = QPixmap()
                        px.loadFromData(buf.read())
                        self._previews.append(px)
                    except Exception:
                        self._previews.append(None)
        self.files_changed.emit(list(self._files))
        self.update()

    def _valid_exts(self):
        exts = []
        if self._accept_pdfs:   exts += ['.pdf']
        if self._accept_images: exts += ['.png', '.jpg', '.jpeg', '.webp']
        return exts

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in e.mimeData().urls()]
            valid = any(os.path.splitext(p)[1].lower() in self._valid_exts() for p in paths)
            if valid:
                self._hovering = True
                self.update()
                e.accept()
                return
        e.ignore()

    def dragLeaveEvent(self, e):
        self._hovering = False
        self.update()

    def dropEvent(self, e):
        self._hovering = False
        paths = [
            u.toLocalFile() for u in e.mimeData().urls()
            if os.path.splitext(u.toLocalFile())[1].lower() in self._valid_exts()
        ]
        self.add_files(paths)
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        checkerboard_paint(p, w, h)
        if self._hovering:
            p.setPen(QPen(QColor(88, 101, 242, 180), 2, Qt.DashLine))
            p.setBrush(QBrush(QColor(88, 101, 242, 30)))
            p.drawRoundedRect(4, 4, w - 8, h - 8, 16, 16)
        if not self._files:
            self._draw_empty(p, w, h)
        elif self._accept_images and self._previews:
            self._draw_image_grid(p, w, h)
        else:
            self._draw_file_list(p, w, h)

    def _draw_empty(self, p, w, h):
        p.setPen(QPen(QColor(255, 255, 255, 35), 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(16, 16, w - 32, h - 32, 16, 16)
        icon_y = h // 2 - 36
        p.setPen(QColor(255, 255, 255, 45))
        p.setFont(self.font())
        emoji = "🖼️" if self._accept_images else "📄"
        p.drawText(QRect(0, icon_y, w, 48), Qt.AlignCenter, emoji)
        p.setPen(QColor(255, 255, 255, 55))
        font = self.font()
        font.setPointSize(13)
        p.setFont(font)
        exts = "images" if self._accept_images and not self._accept_pdfs else \
               "PDFs" if not self._accept_images else "images or PDFs"
        p.drawText(QRect(0, icon_y + 52, w, 28), Qt.AlignCenter, f"Drop {exts} here")
        p.setPen(QColor(255, 255, 255, 28))
        font2 = self.font()
        font2.setPointSize(11)
        p.setFont(font2)
        p.drawText(QRect(0, icon_y + 82, w, 22), Qt.AlignCenter, "or use the Add button →")

    def _draw_image_grid(self, p, w, h):
        THUMB = 110; GAP = 10
        COLS = max(1, (w - GAP) // (THUMB + GAP))
        total = len(self._previews)
        rows  = (total + COLS - 1) // COLS
        start_y = max(GAP, (h - rows * (THUMB + GAP) + GAP) // 2)
        for i, px in enumerate(self._previews):
            col = i % COLS
            row = i // COLS
            x = GAP + col * (THUMB + GAP)
            y = start_y + row * (THUMB + GAP)
            cell = QRect(x, y, THUMB, THUMB)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(40, 40, 44)))
            p.drawRoundedRect(cell, 8, 8)
            if px and not px.isNull():
                sw, sh = px.width(), px.height()
                scale = min(THUMB / sw, THUMB / sh)
                dw, dh = int(sw * scale), int(sh * scale)
                dx = x + (THUMB - dw) // 2
                dy = y + (THUMB - dh) // 2
                p.drawPixmap(QRect(dx, dy, dw, dh), px, px.rect())
            fname = os.path.basename(self._files[i])
            if len(fname) > 14:
                fname = fname[:11] + "…"
            p.setPen(QColor(255, 255, 255, 140))
            font = self.font()
            font.setPointSize(9)
            p.setFont(font)
            p.drawText(QRect(x, y + THUMB - 18, THUMB, 18), Qt.AlignCenter, fname)

    def _draw_file_list(self, p, w, h):
        LINE_H = 36; pad = 20
        total_h = len(self._files) * LINE_H
        start_y = max(pad, (h - total_h) // 2)
        font = self.font()
        font.setPointSize(12)
        p.setFont(font)
        for i, path in enumerate(self._files):
            y = start_y + i * LINE_H
            if i % 2 == 0:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(255, 255, 255, 8)))
                p.drawRoundedRect(pad, y + 2, w - pad * 2, LINE_H - 4, 8, 8)
            icon = "📄"
            size_kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
            label = f"{icon}  {os.path.basename(path)}   ·   {size_kb:.1f} KB"
            p.setPen(QColor(255, 255, 255, 180))
            p.drawText(QRect(pad + 8, y, w - pad * 2, LINE_H), Qt.AlignVCenter, label)
