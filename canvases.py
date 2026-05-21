import io

from PIL import Image

from PyQt5.QtWidgets import QWidget, QSizePolicy
from PyQt5.QtCore import (
    Qt, QPointF, QRect, QRectF,
    pyqtSignal
)
from PyQt5.QtGui import (
    QColor, QPixmap, QCursor,
    QPainter, QPen, QBrush
)

from helpers import HANDLE, checkerboard_paint


# =========================================
# CROP CANVAS
# =========================================

class CropCanvas(QWidget):
    crop_changed = pyqtSignal(int, int, int, int)

    _NONE = 0; _MOVE = 1
    _TL = 2; _TC = 3; _TR = 4
    _ML = 5;           _MR = 6
    _BL = 7; _BC = 8; _BR = 9

    _CURSORS = {
        _NONE: Qt.ArrowCursor,   _MOVE: Qt.SizeAllCursor,
        _TL: Qt.SizeFDiagCursor, _BR: Qt.SizeFDiagCursor,
        _TR: Qt.SizeBDiagCursor, _BL: Qt.SizeBDiagCursor,
        _TC: Qt.SizeVerCursor,   _BC: Qt.SizeVerCursor,
        _ML: Qt.SizeHorCursor,   _MR: Qt.SizeHorCursor,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pil     = None
        self._pix     = None
        self._ir      = QRect()
        self._cr      = QRectF()
        self._drag    = self._NONE
        self._dstart  = QPointF()
        self._cstart  = QRectF()
        self._aspect  = None
        self._panning = False
        self._pan_x   = 0
        self._pan_y   = 0
        self._zoom    = 1.0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)

    def load_image(self, pil_image):
        self._pil     = pil_image
        self._zoom    = 1.0
        self._pan_x   = 0
        self._pan_y   = 0
        self._panning = False
        self._refresh_pixmap()
        self.reset_crop()

    def set_aspect(self, aspect):
        self._aspect = aspect
        if aspect and not self._cr.isEmpty():
            self._enforce_aspect()
            self.update()
            self._emit()

    def reset_crop(self):
        if self._ir.isEmpty():
            return
        self._cr = QRectF(self._ir)
        self.update()
        self._emit()

    def reset_zoom(self):
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self._layout()
        self.reset_crop()

    def get_crop_coords(self):
        if not self._pil or self._cr.isEmpty():
            return (0, 0, 0, 0)
        tl = self._c2i(self._cr.topLeft())
        br = self._c2i(self._cr.bottomRight())
        iw, ih = self._pil.width, self._pil.height
        x  = max(0, int(tl.x()))
        y  = max(0, int(tl.y()))
        x2 = min(iw, int(br.x()))
        y2 = min(ih, int(br.y()))
        return x, y, x2 - x, y2 - y

    def set_crop_coords(self, x, y, w, h):
        if not self._pil:
            return
        tl = self._i2c(QPointF(x, y))
        br = self._i2c(QPointF(x + w, y + h))
        self._cr = QRectF(tl, br).normalized()
        self._clamp()
        self.update()

    def _refresh_pixmap(self):
        if not self._pil:
            return
        buf = io.BytesIO()
        try:
            self._pil.save(buf, self._pil.format or "PNG")
        except Exception:
            self._pil.save(buf, "PNG")
        buf.seek(0)
        self._pix = QPixmap()
        self._pix.loadFromData(buf.read())
        self._layout()

    def _layout(self):
        if not self._pix:
            return
        cw, ch = self.width(), self.height()
        if cw == 0 or ch == 0:
            return
        iw, ih = self._pix.width(), self._pix.height()
        base = min(cw / iw, ch / ih, 1.0)
        s = base * self._zoom
        dw, dh = int(iw * s), int(ih * s)
        x = (cw - dw) // 2 + self._pan_x
        y = (ch - dh) // 2 + self._pan_y
        self._ir = QRect(x, y, dw, dh)

    def _c2i(self, pt):
        r = self._ir
        if r.width() == 0 or r.height() == 0:
            return pt
        return QPointF(
            (pt.x() - r.x()) / r.width()  * self._pil.width,
            (pt.y() - r.y()) / r.height() * self._pil.height,
        )

    def _i2c(self, pt):
        r = self._ir
        return QPointF(
            r.x() + pt.x() / self._pil.width  * r.width(),
            r.y() + pt.y() / self._pil.height * r.height(),
        )

    def _handles(self):
        r = self._cr
        cx = r.center().x()
        cy = r.center().y()
        def h(x, y): return QRectF(x - HANDLE, y - HANDLE, HANDLE * 2, HANDLE * 2)
        return {
            self._TL: h(r.left(), r.top()),    self._TC: h(cx, r.top()),
            self._TR: h(r.right(), r.top()),   self._ML: h(r.left(), cy),
            self._MR: h(r.right(), cy),        self._BL: h(r.left(), r.bottom()),
            self._BC: h(cx, r.bottom()),       self._BR: h(r.right(), r.bottom()),
        }

    def _hit(self, pt):
        for mode, rect in self._handles().items():
            if rect.contains(pt):
                return mode
        if self._cr.contains(pt):
            return self._MOVE
        return self._NONE

    def _enforce_aspect(self):
        aw, ah = self._aspect
        r = self._cr
        ir = QRectF(self._ir)
        r.setHeight(r.width() * ah / aw)
        if r.bottom() > ir.bottom():
            r.moveBottom(ir.bottom())
            r.setWidth(r.height() * aw / ah)
        self._cr = r.intersected(ir)

    def _clamp(self):
        if not self._cr.isEmpty():
            self._cr = self._cr.intersected(QRectF(self._ir))

    def _emit(self):
        x, y, w, h = self.get_crop_coords()
        self.crop_changed.emit(x, y, w, h)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning   = True
            self._pan_start = e.globalPos()
            self._pan_ox    = self._pan_x
            self._pan_oy    = self._pan_y
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            return
        if e.button() == Qt.LeftButton:
            pt = QPointF(e.pos())
            self._drag = self._hit(pt)
            self._dstart = pt
            self._cstart = QRectF(self._cr)

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.globalPos() - self._pan_start
            self._pan_x = self._pan_ox + d.x()
            self._pan_y = self._pan_oy + d.y()
            self._layout()
            self._clamp()
            self.update()
            return
        pt = QPointF(e.pos())
        if self._drag == self._NONE:
            self.setCursor(QCursor(self._CURSORS.get(self._hit(pt), Qt.ArrowCursor)))
            return
        d = pt - self._dstart
        r = QRectF(self._cstart)
        ir = QRectF(self._ir)
        MIN = 20.0
        if self._drag == self._MOVE:
            r.translate(d)
            if r.left()   < ir.left():   r.moveLeft(ir.left())
            if r.top()    < ir.top():    r.moveTop(ir.top())
            if r.right()  > ir.right():  r.moveRight(ir.right())
            if r.bottom() > ir.bottom(): r.moveBottom(ir.bottom())
        else:
            if self._drag in (self._TL, self._ML, self._BL):
                r.setLeft(min(r.left() + d.x(), r.right() - MIN))
            if self._drag in (self._TR, self._MR, self._BR):
                r.setRight(max(r.right() + d.x(), r.left() + MIN))
            if self._drag in (self._TL, self._TC, self._TR):
                r.setTop(min(r.top() + d.y(), r.bottom() - MIN))
            if self._drag in (self._BL, self._BC, self._BR):
                r.setBottom(max(r.bottom() + d.y(), r.top() + MIN))
            r = r.intersected(ir)
        self._cr = r.normalized()
        if self._aspect:
            self._enforce_aspect()
        self.update()
        self._emit()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(QCursor(Qt.ArrowCursor))
            return
        self._drag = self._NONE

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._zoom = 1.0
            self._pan_x = 0
            self._pan_y = 0
            self._layout()
            self._clamp()
            self.update()

    def wheelEvent(self, e):
        if not self._pil:
            return
        factor = 1.1 if e.angleDelta().y() > 0 else 0.9
        self._zoom = max(0.2, min(self._zoom * factor, 8.0))
        self._layout()
        self._clamp()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        checkerboard_paint(p, self.width(), self.height())
        if self._pix:
            p.drawPixmap(self._ir, self._pix, self._pix.rect())
        if self._cr.isEmpty():
            return
        r = self._cr.toRect()
        ir = self._ir
        overlay = QColor(0, 0, 0, 150)
        for region in [
            QRect(ir.left(), ir.top(),   ir.width(), r.top()    - ir.top()),
            QRect(ir.left(), r.bottom(), ir.width(), ir.bottom()- r.bottom()),
            QRect(ir.left(), r.top(),    r.left()   - ir.left(), r.height()),
            QRect(r.right(), r.top(),    ir.right() - r.right(), r.height()),
        ]:
            if region.isValid():
                p.fillRect(region, overlay)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.DashLine))
        for i in (1, 2):
            x = int(r.left() + r.width()  * i / 3)
            y = int(r.top()  + r.height() * i / 3)
            p.drawLine(x, r.top(), x, r.bottom())
            p.drawLine(r.left(), y, r.right(), y)
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
        p.drawRect(r)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 230)))
        hs = HANDLE
        cx = r.center().x()
        cy = r.center().y()
        for hx, hy in [
            (r.left(), r.top()),            (cx - hs, r.top()),         (r.right() - hs*2, r.top()),
            (r.left(), cy - hs),                                         (r.right() - hs*2, cy - hs),
            (r.left(), r.bottom() - hs*2),  (cx - hs, r.bottom()-hs*2), (r.right()-hs*2, r.bottom()-hs*2),
        ]:
            p.drawRoundedRect(QRectF(hx, hy, hs*2, hs*2), 3, 3)

    def resizeEvent(self, e):
        if self._pil and not self._cr.isEmpty():
            coords = self.get_crop_coords()
            self._layout()
            self.set_crop_coords(*coords)
        else:
            self._layout()
        self.update()


# =========================================
# BG REMOVE CANVAS
# =========================================

class BgCanvas(QWidget):
    SWATCHES = [
        ("Transparent", None),
        ("White",       QColor(255, 255, 255)),
        ("Black",       QColor(0,   0,   0)),
        ("Light Gray",  QColor(200, 200, 200)),
        ("Dark Gray",   QColor(60,  60,  60)),
        ("Red",         QColor(220, 50,  50)),
        ("Orange",      QColor(230, 120, 40)),
        ("Yellow",      QColor(240, 210, 50)),
        ("Green",       QColor(50,  180, 80)),
        ("Teal",        QColor(40,  180, 160)),
        ("Sky Blue",    QColor(80,  160, 230)),
        ("Navy",        QColor(30,  60,  160)),
        ("Purple",      QColor(130, 60,  200)),
        ("Pink",        QColor(230, 100, 160)),
        ("Beige",       QColor(240, 220, 190)),
        ("Brown",       QColor(120, 70,  30)),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig_rgba = None
        self._bg_color  = None
        self._composite = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(200)

    def set_image(self, pil_rgba):
        self._orig_rgba = pil_rgba
        self._rebuild()
        self.update()

    def set_bg_color(self, qcolor):
        self._bg_color = qcolor
        self._rebuild()
        self.update()

    def get_result(self):
        if self._orig_rgba is None:
            return None
        if self._bg_color is None:
            return self._orig_rgba.copy()
        bg = Image.new("RGBA", self._orig_rgba.size,
                       (self._bg_color.red(), self._bg_color.green(),
                        self._bg_color.blue(), 255))
        bg.paste(self._orig_rgba, mask=self._orig_rgba.split()[3])
        return bg.convert("RGB")

    def _rebuild(self):
        if self._orig_rgba is None:
            return
        if self._bg_color is None:
            img = self._orig_rgba
        else:
            bg = Image.new("RGBA", self._orig_rgba.size,
                           (self._bg_color.red(), self._bg_color.green(),
                            self._bg_color.blue(), 255))
            bg.paste(self._orig_rgba, mask=self._orig_rgba.split()[3])
            img = bg
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        self._composite = QPixmap()
        self._composite.loadFromData(buf.read())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        checkerboard_paint(p, self.width(), self.height())
        if self._composite:
            cw, ch = self.width(), self.height()
            pw, ph = self._composite.width(), self._composite.height()
            s = min(cw / pw, ch / ph, 1.0)
            dw, dh = int(pw * s), int(ph * s)
            x = (cw - dw) // 2
            y = (ch - dh) // 2
            p.drawPixmap(QRect(x, y, dw, dh), self._composite, self._composite.rect())
