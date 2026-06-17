import os
import io

from PIL import Image

from PyQt5.QtWidgets import QWidget, QSizePolicy, QScrollArea, QVBoxLayout
from PyQt5.QtCore import (
    Qt, QRect, QRectF, QPoint, QSize, pyqtSignal, pyqtProperty,
    QPropertyAnimation, QEasingCurve, QTimer
)
from PyQt5.QtGui import (
    QColor, QPixmap, QPainter, QPen, QBrush,
    QFont, QFontMetrics, QLinearGradient, QPalette
)


def _file_size(path):
    size_kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
    return f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"


def _elide(text, font, width):
    return QFontMetrics(font).elidedText(text, Qt.ElideMiddle, max(24, width))


class PdfDropCanvas(QWidget):
    files_changed = pyqtSignal(list)
    height_hint_changed = pyqtSignal(int)

    def __init__(self, accept_images=False, accept_pdfs=True, parent=None):
        super().__init__(parent)
        self._accept_images = accept_images
        self._accept_pdfs = accept_pdfs
        self._files = []
        self._previews = []
        self._remove_hit_rects = []
        self._hovering = False
        self._drag_progress = 0.0
        self._drop_pulse = 0.0
        self._expanded_workspace = False

        self._drag_anim = QPropertyAnimation(self, b"dragProgress", self)
        self._drag_anim.setDuration(170)
        self._drag_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._drop_anim = QPropertyAnimation(self, b"dropPulse", self)
        self._drop_anim.setDuration(260)
        self._drop_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(420)
        self.setFixedHeight(self.preferred_height())

    @pyqtProperty(float)
    def dragProgress(self):
        return self._drag_progress

    @dragProgress.setter
    def dragProgress(self, value):
        self._drag_progress = max(0.0, min(1.0, value))
        self.update()

    @pyqtProperty(float)
    def dropPulse(self):
        return self._drop_pulse

    @dropPulse.setter
    def dropPulse(self, value):
        self._drop_pulse = max(0.0, min(1.0, value))
        self.update()

    def preferred_height(self):
        return 216 if self._files else 156

    def get_files(self):
        return list(self._files)

    def clear(self):
        self._files = []
        self._previews = []
        self._remove_hit_rects = []
        self._sync_height()
        self.files_changed.emit([])
        self.update()

    def remove_file(self, index):
        if 0 <= index < len(self._files):
            self._files.pop(index)
            if index < len(self._previews):
                self._previews.pop(index)
            self._sync_height()
            self.files_changed.emit(list(self._files))
            self.update()

    def add_files(self, paths):
        added = False
        for path in paths:
            if not path or path in self._files:
                continue
            if os.path.splitext(path)[1].lower() not in self._valid_exts():
                continue
            self._files.append(path)
            self._previews.append(self._make_preview(path))
            added = True
        if added:
            self._play_drop_pulse()
            self._sync_height()
            self.files_changed.emit(list(self._files))
            self.update()

    def _sync_height(self):
        h = self.preferred_height()
        if self.height() != h:
            self.setFixedHeight(h)
            self.height_hint_changed.emit(h)

    def _make_preview(self, path):
        if not self._accept_images:
            return None
        try:
            pil = Image.open(path)
            pil.thumbnail((92, 92), Image.LANCZOS)
            buf = io.BytesIO()
            pil.save(buf, "PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            return px
        except Exception:
            return None

    def _valid_exts(self):
        exts = []
        if self._accept_pdfs:
            exts.append(".pdf")
        if self._accept_images:
            exts += [".png", ".jpg", ".jpeg", ".webp"]
        return exts

    def _set_drag_active(self, active):
        if self._hovering == active:
            return
        self._hovering = active
        self._drag_anim.stop()
        self._drag_anim.setStartValue(self._drag_progress)
        self._drag_anim.setEndValue(1.0 if active else 0.0)
        self._drag_anim.start()

    def _play_drop_pulse(self):
        self._drop_anim.stop()
        self._drop_anim.setStartValue(1.0)
        self._drop_anim.setEndValue(0.0)
        self._drop_anim.start()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            if any(os.path.splitext(path)[1].lower() in self._valid_exts() for path in paths):
                self._set_drag_active(True)
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction() if event.mimeData().hasUrls() else event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event):
        self._set_drag_active(False)
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if os.path.splitext(url.toLocalFile())[1].lower() in self._valid_exts()
        ]
        self.add_files(paths)
        event.acceptProposedAction()

    def mouseMoveEvent(self, event):
        self.setCursor(
            Qt.PointingHandCursor if any(rect.contains(event.pos()) for rect in self._remove_hit_rects)
            else Qt.ArrowCursor
        )

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        for index, rect in enumerate(self._remove_hit_rects):
            if rect.contains(event.pos()):
                self.remove_file(index)
                return

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(30, 30, 34))
        self._remove_hit_rects = []
        if not self._files:
            self._draw_empty_state(painter)
            return
        self._draw_drop_strip(painter)
        self._draw_file_cards(painter)

    def _draw_empty_state(self, painter):
        rect = self.rect().adjusted(12, 10, -12, -10)
        grow = int(5 * self._drag_progress)
        rect = rect.adjusted(-grow, -grow, grow, grow)
        glow = self._drag_progress
        pulse = self._drop_pulse

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(88, 101, 242, int(16 + 58 * glow + 24 * pulse)))
        painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 17, 17)

        border = QColor(255, 255, 255, 46)
        if glow:
            border = QColor(141, 151, 255, int(130 + 70 * glow))
        painter.setPen(QPen(border, 1.3 + glow, Qt.DashLine))
        painter.setBrush(QColor(255, 255, 255, int(8 + 18 * glow)))
        painter.drawRoundedRect(rect, 15, 15)

        center = rect.center()
        icon_rect = QRect(center.x() - 23, rect.top() + 25 - int(4 * glow), 46, 46)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(88, 101, 242, int(40 + 44 * glow)))
        painter.drawRoundedRect(icon_rect, 12, 12)

        icon_font = QFont(self.font())
        icon_font.setBold(True)
        icon_font.setPointSize(11)
        painter.setFont(icon_font)
        painter.setPen(QColor(228, 231, 255, 232))
        icon_text = "IMG" if self._accept_images and not self._accept_pdfs else "PDF"
        painter.drawText(icon_rect, Qt.AlignCenter, icon_text)

        title_font = QFont(self.font())
        title_font.setPointSize(12)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(255, 255, 255, 220))
        target = "images" if self._accept_images and not self._accept_pdfs else "PDF files"
        painter.drawText(QRect(rect.left(), icon_rect.bottom() + 10, rect.width(), 22), Qt.AlignCenter, f"Drop {target} here")

        body_font = QFont(self.font())
        body_font.setPointSize(9)
        painter.setFont(body_font)
        painter.setPen(QColor(255, 255, 255, 104))
        painter.drawText(QRect(rect.left(), icon_rect.bottom() + 34, rect.width(), 18), Qt.AlignCenter, "or Browse")

    def _draw_drop_strip(self, painter):
        rect = QRect(12, 10, self.width() - 24, 38)
        glow = self._drag_progress
        painter.setPen(QPen(QColor(116, 130, 255, int(60 + 120 * glow)), 1.1 + glow))
        painter.setBrush(QColor(255, 255, 255, int(8 + 15 * glow)))
        painter.drawRoundedRect(rect, 12, 12)

        font = QFont(self.font())
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 160))
        painter.drawText(rect, Qt.AlignCenter, "Release to add files" if glow else "Drop more files here")

    def _draw_file_cards(self, painter):
        files = self._files[:4]
        count = len(files)
        gap = 9
        card_w = min(172, max(132, (self.width() - 24 - gap * (count - 1)) // max(1, count)))
        card_h = 116
        total_w = count * card_w + max(0, count - 1) * gap
        start_x = (self.width() - total_w) // 2
        y = 62
        for index, path in enumerate(files):
            self._draw_card(painter, index, path, QRect(start_x + index * (card_w + gap), y, card_w, card_h))

        remaining = len(self._files) - len(files)
        if remaining > 0:
            font = QFont(self.font())
            font.setPointSize(9)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255, 82))
            painter.drawText(QRect(0, y + card_h + 8, self.width(), 20), Qt.AlignCenter, f"+{remaining} more queued")

    def _draw_card(self, painter, index, path, rect):
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
        painter.setBrush(QColor(255, 255, 255, 12))
        painter.drawRoundedRect(rect, 11, 11)

        remove_rect = QRect(rect.right() - 28, rect.top() + 8, 20, 20)
        self._remove_hit_rects.append(remove_rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 19))
        painter.drawEllipse(remove_rect)
        painter.setPen(QColor(255, 255, 255, 135))
        painter.drawText(remove_rect, Qt.AlignCenter, "x")

        thumb = QRect(rect.left() + 12, rect.top() + 16, 40, 46)
        preview = self._previews[index] if index < len(self._previews) else None
        if preview and not preview.isNull():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(22, 22, 25))
            painter.drawRoundedRect(thumb, 8, 8)
            scaled = preview.scaled(thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(thumb.left() + (thumb.width() - scaled.width()) // 2, thumb.top() + (thumb.height() - scaled.height()) // 2, scaled)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(88, 101, 242, 44))
            painter.drawRoundedRect(thumb, 8, 8)
            painter.setPen(QColor(225, 228, 255, 220))
            doc_font = QFont(self.font())
            doc_font.setPointSize(9)
            doc_font.setBold(True)
            painter.setFont(doc_font)
            painter.drawText(thumb, Qt.AlignCenter, (os.path.splitext(path)[1].replace(".", "").upper()[:3] or "PDF"))

        name_font = QFont(self.font())
        name_font.setPointSize(9)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor(255, 255, 255, 190))
        name_rect = QRect(rect.left() + 60, rect.top() + 19, rect.width() - 92, 38)
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignTop, _elide(os.path.basename(path), name_font, name_rect.width()))

        size_font = QFont(self.font())
        size_font.setPointSize(8)
        painter.setFont(size_font)
        painter.setPen(QColor(255, 255, 255, 96))
        painter.drawText(QRect(rect.left() + 60, rect.top() + 52, rect.width() - 76, 18), Qt.AlignLeft, _file_size(path))

        footer = QRectF(rect.left() + 12, rect.bottom() - 32, rect.width() - 24, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(88, 101, 242, 24))
        painter.drawRoundedRect(footer, 8, 8)
        painter.setPen(QColor(165, 180, 252, 178))
        painter.drawText(footer.toRect(), Qt.AlignCenter, f"File {index + 1}")


class OrganizePageGrid(QWidget):
    pages_changed = pyqtSignal()

    CARD_W = 158
    CARD_H = 218
    GAP = 14
    MARGIN = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.CARD_W = self.__class__.CARD_W
        self.CARD_H = self.__class__.CARD_H
        self.GAP = self.__class__.GAP
        self.MARGIN = self.__class__.MARGIN
        self._pages = []
        self._hit_rects = []
        self._drag_index = None
        self._insert_index = None
        self._drag_pos = QPoint()
        self._press_pos = QPoint()
        self._drag_active = False
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMinimumHeight(220)

    def set_expanded(self, expanded):
        if expanded:
            self.CARD_W = 190
            self.CARD_H = 258
            self.GAP = 16
            self.MARGIN = 16
        else:
            self.CARD_W = self.__class__.CARD_W
            self.CARD_H = self.__class__.CARD_H
            self.GAP = self.__class__.GAP
            self.MARGIN = self.__class__.MARGIN
        self._update_geometry()
        self.update()

    def set_pages(self, pages):
        self._pages = pages
        self._drag_index = None
        self._insert_index = None
        self._drag_active = False
        self._update_geometry()
        self.update()

    def _cols(self):
        usable = max(1, self.width() - self.MARGIN * 2)
        return max(1, usable // (self.CARD_W + self.GAP))

    def _grid_height(self):
        if not self._pages:
            return 220
        rows = (len(self._pages) + self._cols() - 1) // self._cols()
        return self.MARGIN * 2 + rows * self.CARD_H + max(0, rows - 1) * self.GAP

    def _update_geometry(self):
        self.setMinimumHeight(self._grid_height())
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_geometry()

    def sizeHint(self):
        return QSize(720, self._grid_height())

    def _card_rect(self, index):
        cols = self._cols()
        row = index // cols
        col = index % cols
        total_w = cols * self.CARD_W + max(0, cols - 1) * self.GAP
        start_x = max(self.MARGIN, (self.width() - total_w) // 2)
        return QRect(
            start_x + col * (self.CARD_W + self.GAP),
            self.MARGIN + row * (self.CARD_H + self.GAP),
            self.CARD_W,
            self.CARD_H
        )

    def _action_rects(self, rect):
        y = rect.bottom() - 34
        return {
            "left": QRect(rect.left() + 12, y, 28, 24),
            "right": QRect(rect.left() + 46, y, 28, 24),
            "remove": QRect(rect.right() - 40, rect.top() + 10, 28, 24),
        }

    def _card_at(self, pos):
        for action, index, rect in self._hit_rects:
            if action == "card" and rect.contains(pos):
                return index
        return None

    def reorder_page(self, old, insert_index):
        if not (0 <= old < len(self._pages)):
            return
        target = max(0, min(len(self._pages), insert_index))
        page = self._pages.pop(old)
        if target > old:
            target -= 1
        target = max(0, min(len(self._pages), target))
        self._pages.insert(target, page)
        self._drag_index = None
        self._insert_index = None
        self._drag_active = False
        self._update_geometry()
        self.pages_changed.emit()
        self.update()

    def _insert_at(self, pos):
        if not self._pages:
            return 0
        cols = self._cols()
        first = self._card_rect(0)
        step_x = self.CARD_W + self.GAP
        step_y = self.CARD_H + self.GAP
        row = max(0, int((pos.y() - self.MARGIN + step_y // 2) // step_y))
        col = max(0, int((pos.x() - first.left()) // step_x))
        col = min(cols - 1, col)
        index = min(len(self._pages), row * cols + col)
        if index < len(self._pages):
            rect = self._card_rect(index)
            if pos.x() > rect.center().x():
                index += 1
        return max(0, min(len(self._pages), index))

    def rotate_page(self, index, delta):
        if 0 <= index < len(self._pages):
            self._pages[index]["rotation"] = (self._pages[index].get("rotation", 0) + delta) % 360
            self.pages_changed.emit()
            self.update()

    def remove_page(self, index):
        if 0 <= index < len(self._pages):
            self._pages.pop(index)
            self._drag_index = None
            self._insert_index = None
            self._drag_active = False
            self._update_geometry()
            self.pages_changed.emit()
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        controls = [hit for hit in self._hit_rects if hit[0] != "card"]
        cards = [hit for hit in self._hit_rects if hit[0] == "card"]
        for action, index, rect in controls + cards:
            if rect.contains(event.pos()):
                if action == "remove":
                    self.remove_page(index)
                elif action == "left":
                    self.rotate_page(index, -90)
                elif action == "right":
                    self.rotate_page(index, 90)
                elif action == "card":
                    self._drag_index = index
                    self._insert_index = index
                    self._drag_pos = event.pos()
                    self._press_pos = event.pos()
                    self._drag_active = False
                    self.setCursor(Qt.ClosedHandCursor)
                return

    def mouseMoveEvent(self, event):
        if self._drag_index is not None:
            self._drag_pos = event.pos()
            if not self._drag_active and (event.pos() - self._press_pos).manhattanLength() > 5:
                self._drag_active = True
            self._insert_index = self._insert_at(event.pos())
            self.update()
            return

        hovering = any(rect.contains(event.pos()) for _, _, rect in self._hit_rects)
        self.setCursor(Qt.PointingHandCursor if hovering else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._drag_index is None:
            return
        self.reorder_page(
            self._drag_index,
            self._insert_index if self._insert_index is not None else self._drag_index
        )
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event):
        if self._drag_index is None:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(30, 30, 34))
        self._hit_rects = []

        if not self._pages:
            self._draw_grid_empty(painter)
            return

        if self._drag_active and self._drag_index is not None:
            self._draw_drag_layout(painter)
        else:
            for index in range(len(self._pages)):
                self._draw_page_card(painter, index, self._card_rect(index), False)

        if self._drag_active and self._drag_index is not None:
            self._draw_insertion_marker(painter)
            floating = QRect(
                self._drag_pos.x() - self.CARD_W // 2,
                self._drag_pos.y() - self.CARD_H // 2,
                self.CARD_W,
                self.CARD_H
            )
            self._draw_page_card(painter, self._drag_index, floating.adjusted(-4, -4, 4, 4), True)

    def _draw_drag_layout(self, painter):
        visual = [i for i in range(len(self._pages)) if i != self._drag_index]
        insert = self._insert_index if self._insert_index is not None else self._drag_index
        if insert > self._drag_index:
            insert -= 1
        insert = max(0, min(len(visual), insert))
        visual.insert(insert, None)
        for slot, page_index in enumerate(visual):
            rect = self._card_rect(slot)
            if page_index is None:
                self._draw_placeholder_card(painter, rect)
            else:
                self._draw_page_card(painter, page_index, rect, False)

    def _draw_grid_empty(self, painter):
        rect = self.rect().adjusted(14, 14, -14, -14)
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1.2, Qt.DashLine))
        painter.setBrush(QColor(255, 255, 255, 8))
        painter.drawRoundedRect(rect, 14, 14)
        font = QFont(self.font())
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 165))
        painter.drawText(rect, Qt.AlignCenter, "Drop PDF files here or use Browse PDFs")

    def _draw_placeholder_card(self, painter, rect):
        painter.setPen(QPen(QColor(116, 130, 255, 80), 1.4, Qt.DashLine))
        painter.setBrush(QColor(88, 101, 242, 20))
        painter.drawRoundedRect(rect, 12, 12)

    def _draw_insertion_marker(self, painter):
        index = self._insert_index if self._insert_index is not None else len(self._pages)
        if index >= len(self._pages):
            rect = self._card_rect(len(self._pages) - 1)
            x = rect.right() + self.GAP // 2
            marker = QRect(x - 2, rect.top() + 10, 4, rect.height() - 20)
        else:
            rect = self._card_rect(index)
            x = rect.left() - self.GAP // 2
            marker = QRect(x - 2, rect.top() + 10, 4, rect.height() - 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(165, 180, 252, 230))
        painter.drawRoundedRect(marker, 2, 2)

    def _draw_page_card(self, painter, index, rect, floating):
        page = self._pages[index]

        if floating:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(88, 101, 242, 72))
            painter.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), 16, 16)

        painter.setPen(QPen(QColor(116, 130, 255, 90 if floating else 34), 1.2))
        painter.setBrush(QColor(255, 255, 255, 20 if floating else 13))
        painter.drawRoundedRect(rect, 12, 12)

        thumb_h = max(126, rect.height() - 92)
        thumb_rect = QRect(rect.left() + 12, rect.top() + 12, rect.width() - 24, thumb_h)
        gradient = QLinearGradient(thumb_rect.topLeft(), thumb_rect.bottomRight())
        gradient.setColorAt(0, QColor(255, 255, 255, 34))
        gradient.setColorAt(1, QColor(88, 101, 242, 18))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(thumb_rect, 8, 8)

        thumb = page.get("thumb")
        if thumb and not thumb.isNull():
            scaled = thumb.scaled(thumb_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.save()
            if page.get("rotation", 0):
                center = thumb_rect.center()
                painter.translate(center)
                painter.rotate(page.get("rotation", 0))
                painter.translate(-center)
            painter.drawPixmap(
                thumb_rect.left() + (thumb_rect.width() - scaled.width()) // 2,
                thumb_rect.top() + (thumb_rect.height() - scaled.height()) // 2,
                scaled
            )
            painter.restore()
        else:
            font = QFont(self.font())
            font.setPointSize(16)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(225, 228, 255, 210))
            painter.drawText(thumb_rect, Qt.AlignCenter, f"P{page['page_index'] + 1}")

        page_font = QFont(self.font())
        page_font.setPointSize(9)
        page_font.setBold(True)
        painter.setFont(page_font)
        painter.setPen(QColor(255, 255, 255, 208))
        painter.drawText(QRect(rect.left() + 12, thumb_rect.bottom() + 8, rect.width() - 24, 18), Qt.AlignLeft, f"Page {index + 1}")

        name_font = QFont(self.font())
        name_font.setPointSize(8)
        painter.setFont(name_font)
        painter.setPen(QColor(255, 255, 255, 112))
        name = _elide(os.path.basename(page["path"]), name_font, rect.width() - 24)
        painter.drawText(QRect(rect.left() + 12, thumb_rect.bottom() + 26, rect.width() - 24, 18), Qt.AlignLeft, name)

        actions = self._action_rects(rect)
        self._hit_rects.append(("left", index, actions["left"]))
        self._hit_rects.append(("right", index, actions["right"]))
        self._hit_rects.append(("remove", index, actions["remove"]))
        self._hit_rects.append(("card", index, rect))
        # Professional Unicode glyphs: ↺ counterclockwise, ↻ clockwise, ✕ remove
        for key, text in [("left", "↺"), ("right", "↻"), ("remove", "✕")]:
            action_rect = actions[key]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 22 if key != "remove" else 28))
            painter.drawRoundedRect(action_rect, 8, 8)
            icon_font = QFont(self.font())
            icon_font.setPointSize(12)
            painter.setFont(icon_font)
            painter.setPen(QColor(255, 255, 255, 170 if key != "remove" else 200))
            painter.drawText(action_rect, Qt.AlignCenter, text)


class OrganizePdfCanvas(QWidget):
    files_changed = pyqtSignal(list)
    height_hint_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files = []
        self._pages = []
        self._hovering = False
        self._drag_progress = 0.0
        self._drop_pulse = 0.0
        self._expanded_workspace = False

        self._drag_anim = QPropertyAnimation(self, b"dragProgress", self)
        self._drag_anim.setDuration(170)
        self._drag_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._drop_anim = QPropertyAnimation(self, b"dropPulse", self)
        self._drop_anim.setDuration(260)
        self._drop_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(640)
        self.setFixedHeight(self.preferred_height())
        self._first_show = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._drop_strip = _OrganizeDropStrip()
        # Hide strip until pages are loaded — the grid's empty state already
        # shows a drop invitation, so the strip is only needed as an "add more"
        # target when pages already exist.
        self._drop_strip.hide()
        layout.addWidget(self._drop_strip)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setStyleSheet("""
            QScrollArea { background: #1e1e22; border: none; }
            QScrollBar:vertical {
                background: rgba(255,255,255,0.04);
                width: 8px;
                border-radius: 4px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.18);
                border-radius: 4px;
                min-height: 42px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(165,180,252,0.42); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        self._grid = OrganizePageGrid()
        self._grid.pages_changed.connect(self._on_pages_changed)
        self._scroll.setWidget(self._grid)
        # Ensure scroll viewport has a solid background so it never appears
        # transparent on first show (deferred layout / paint pass issue on Qt5).
        vp = self._scroll.viewport()
        vp.setAutoFillBackground(True)
        pal = vp.palette()
        pal.setColor(QPalette.Window, QColor(30, 30, 34))
        vp.setPalette(pal)
        layout.addWidget(self._scroll)

    @pyqtProperty(float)
    def dragProgress(self):
        return self._drag_progress

    @dragProgress.setter
    def dragProgress(self, value):
        self._drag_progress = max(0.0, min(1.0, value))
        self._drop_strip.set_progress(self._drag_progress, self._drop_pulse)

    @pyqtProperty(float)
    def dropPulse(self):
        return self._drop_pulse

    @dropPulse.setter
    def dropPulse(self, value):
        self._drop_pulse = max(0.0, min(1.0, value))
        self._drop_strip.set_progress(self._drag_progress, self._drop_pulse)

    def preferred_height(self):
        if self._expanded_workspace:
            return 650
        return 520 if self._pages else 276

    def showEvent(self, event):
        """Force a full repaint on first show to eliminate transparent region.

        Root cause: QScrollArea viewport background isn't painted during the
        initial deferred layout pass when the widget is first made visible
        (particularly on Windows with WA_TranslucentBackground on the parent
        window). A zero-delay timer fires after the event loop processes the
        show event, ensuring the viewport paints correctly on first display.
        """
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(0, self._force_repaint)

    def _force_repaint(self):
        self._scroll.viewport().update()
        self._grid.update()
        self.update()

    def set_expanded_workspace(self, expanded):
        self._expanded_workspace = expanded
        self._grid.set_expanded(expanded)
        self._sync_height()

    def get_files(self):
        return list(self._files)

    def get_pages(self):
        return [
            {key: value for key, value in page.items() if key != "thumb"}
            for page in self._pages
        ]

    def clear(self):
        self._files = []
        self._pages = []
        self._grid.set_pages(self._pages)
        self._drop_strip.hide()  # no pages → hide the add-more strip
        self._sync_height()
        self.files_changed.emit([])

    def add_files(self, paths):
        added = False
        for path in paths:
            if not path or os.path.splitext(path)[1].lower() != ".pdf":
                continue
            if path not in self._files:
                self._files.append(path)
            page_count = self._page_count(path)
            thumbs = self._render_thumbnails(path, page_count)
            for page_index in range(page_count):
                self._pages.append({
                    "path": path,
                    "page_index": page_index,
                    "rotation": 0,
                    "thumb": thumbs[page_index] if page_index < len(thumbs) else None,
                })
            added = added or page_count > 0
        if added:
            self._play_drop_pulse()
            self._grid.set_pages(self._pages)
            # Reveal the add-more strip now that pages exist
            if self._drop_strip.isHidden():
                self._drop_strip.show()
            self._sync_height()
            self.files_changed.emit(list(self._files))

    def _on_pages_changed(self):
        """Called when the grid mutates pages (rotate, delete, reorder)."""
        # Rebuild the files list from surviving pages
        seen = []
        for page in self._pages:
            if page["path"] not in seen:
                seen.append(page["path"])
        self._files = seen
        # Sync strip visibility: hide when all pages have been deleted
        if self._pages and self._drop_strip.isHidden():
            self._drop_strip.show()
        elif not self._pages and not self._drop_strip.isHidden():
            self._drop_strip.hide()
        self._sync_height()
        self.files_changed.emit(list(self._files))

    def _page_count(self, path):
        try:
            import fitz
            doc = fitz.open(path)
            count = doc.page_count
            doc.close()
            return count
        except Exception:
            try:
                from PyPDF2 import PdfReader
                return len(PdfReader(path).pages)
            except Exception:
                import pypdf
                return len(pypdf.PdfReader(path).pages)

    def _render_thumbnails(self, path, count):
        thumbs = []
        try:
            import fitz
            doc = fitz.open(path)
            for page_index in range(count):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(matrix=fitz.Matrix(0.48, 0.48), alpha=False)
                px = QPixmap()
                px.loadFromData(pix.tobytes("png"))
                thumbs.append(px)
            doc.close()
        except Exception:
            thumbs = [None for _ in range(count)]
        return thumbs

    def _sync_height(self):
        h = self.preferred_height()
        if self.height() != h:
            self.setFixedHeight(h)
            self.height_hint_changed.emit(h)

    def _set_drag_active(self, active):
        if self._hovering == active:
            return
        self._hovering = active
        self._drag_anim.stop()
        self._drag_anim.setStartValue(self._drag_progress)
        self._drag_anim.setEndValue(1.0 if active else 0.0)
        self._drag_anim.start()

    def _play_drop_pulse(self):
        self._drop_anim.stop()
        self._drop_anim.setStartValue(1.0)
        self._drop_anim.setEndValue(0.0)
        self._drop_anim.start()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            if any(os.path.splitext(path)[1].lower() == ".pdf" for path in paths):
                self._set_drag_active(True)
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction() if event.mimeData().hasUrls() else event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event):
        self._set_drag_active(False)
        self.add_files([
            url.toLocalFile() for url in event.mimeData().urls()
            if os.path.splitext(url.toLocalFile())[1].lower() == ".pdf"
        ])
        event.acceptProposedAction()


class _OrganizeDropStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_progress = 0.0
        self._drop_pulse = 0.0
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_progress(self, drag_progress, drop_pulse):
        self._drag_progress = drag_progress
        self._drop_pulse = drop_pulse
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(30, 30, 34))
        rect = self.rect().adjusted(2, 2, -2, -2)
        glow = self._drag_progress
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(88, 101, 242, int(14 + 30 * glow + 20 * self._drop_pulse)))
        painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 13, 13)
        painter.setPen(QPen(QColor(116, 130, 255, int(68 + 120 * glow)), 1.2 + glow, Qt.DashLine))
        painter.setBrush(QColor(255, 255, 255, int(8 + 15 * glow)))
        painter.drawRoundedRect(rect, 12, 12)
        font = QFont(self.font())
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 165))
        painter.drawText(rect, Qt.AlignCenter, "Release to add pages" if glow else "Drop PDFs here to add more pages")
