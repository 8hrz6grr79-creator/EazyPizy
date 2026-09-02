import io
import math

from PIL import Image, ImageOps

from PyQt5.QtWidgets import QWidget, QSizePolicy, QPushButton
from PyQt5.QtCore import (
    Qt, QPointF, QRect, QRectF,
    pyqtSignal
)
from PyQt5.QtGui import (
    QColor, QPixmap, QCursor, QImage,
    QPainter, QPen, QBrush, QLinearGradient,
    QPainterPath, QTransform
)

from ui.helpers import checkerboard_paint


# =========================================
# CROP CANVAS  –  Microsoft Photos–style crop
#
# Paradigm: the crop FRAME is the primary element.
# • The frame starts large, centered, filling most of the canvas.
# • Dragging INSIDE the frame pans the image behind it.
# • Dragging a HANDLE resizes the frame; the image auto-scales so it
#   always fills the frame with no empty space inside.
# • Scroll wheel zooms the image inside the fixed frame.
# • L-shaped corner handles + thin edge handles (Photos style).
# • Rule-of-thirds grid appears only while dragging.
# =========================================

class CropCanvas(QWidget):
    crop_changed = pyqtSignal(int, int, int, int)

    # Drag modes
    _NONE = 0; _PAN = 1
    _TL = 2; _TC = 3; _TR = 4
    _ML = 5;           _MR = 6
    _BL = 7; _BC = 8; _BR = 9

    _CURSORS = {
        _NONE: Qt.ArrowCursor,   _PAN: Qt.SizeAllCursor,
        _TL: Qt.SizeFDiagCursor, _BR: Qt.SizeFDiagCursor,
        _TR: Qt.SizeBDiagCursor, _BL: Qt.SizeBDiagCursor,
        _TC: Qt.SizeVerCursor,   _BC: Qt.SizeVerCursor,
        _ML: Qt.SizeHorCursor,   _MR: Qt.SizeHorCursor,
    }

    _PAD       = 28    # padding from canvas edge to crop frame
    _HIT       = 14    # hit-test radius around handles (px)
    _L_LEN     = 22    # length of each arm of L-shaped corner handle
    _L_THK     = 3     # stroke thickness of corner handle
    _EDGE_LEN  = 28    # length of thin edge-centre handle
    _EDGE_THK  = 3     # stroke thickness of edge handle
    _MIN_FRAME = 40    # minimum crop frame size in canvas-px

    PREVIEW_MAX = 1080
    ROTATE_MAX  = 1080

    def __init__(self, parent=None):
        super().__init__(parent)

        # ── Full-res + display PIL images ────────────────────────────────
        self._pil                  = None   # current display proxy (rotated/flipped)
        self._pil_original         = None   # base for full-res save
        self._pil_display          = None   # downscaled preview for painting
        self._pil_display_original = None
        self._pil_rotate_original  = None
        self._pix                  = None   # QPixmap of _pil_display
        self._pending_angle        = 0

        # ── Frame geometry (canvas-pixel coordinates, float) ────────────
        # The crop frame rect on screen. Resized by handle drags.
        self._frame = QRectF()

        # ── Image pan offset (canvas-px, float) ─────────────────────────
        # Offset of the image top-left relative to the frame top-left.
        # Negative = image extends left/up beyond frame edge (normal).
        self._img_off_x = 0.0
        self._img_off_y = 0.0

        # ── Image scale: canvas-px per image-px ─────────────────────────
        # Always kept >= the minimum that fills the frame.
        self._img_scale = 1.0

        # ── Extra zoom above the fill-minimum (scroll wheel) ────────────
        self._xzoom = 1.0

        # ── Aspect ratio constraint ──────────────────────────────────────
        self._aspect = None

        # ── Drag state ───────────────────────────────────────────────────
        self._drag    = self._NONE
        self._dstart  = QPointF()
        self._frame0  = QRectF()   # frame rect at drag start
        self._off0_x  = 0.0        # pan offset at drag start
        self._off0_y  = 0.0
        self._scale0  = 1.0        # img_scale at drag start

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)

        # Prevent Qt from auto-filling the widget rect with the palette
        # background before paintEvent runs.  Without these, the rectangular
        # widget area covers the crop_panel's rounded top corners even though
        # the clip path inside paintEvent correctly rounds them.
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def load_image(self, pil_image):
        self._pil                  = pil_image
        self._pil_original         = pil_image.copy()
        self._pil_display          = self._make_display(pil_image)
        self._pil_display_original = self._pil_display.copy()
        self._pil_rotate_original  = self._make_rotate(pil_image)
        self._pending_angle        = 0
        self._xzoom                = 1.0
        self._refresh_pixmap()
        self._init_frame()
        self.update()
        self._emit()

    def unload(self):
        """Release all loaded image data (full-res + display copies).

        CropCanvas is created once and kept alive for the whole app
        session, so without this the full-resolution `_pil_original`
        (and the smaller display/rotate copies) stay resident in memory
        indefinitely after the user is done cropping — even after
        clicking Clear or switching to another tool. Call this whenever
        the loaded image is no longer needed.
        """
        self._pil                  = None
        self._pil_original         = None
        self._pil_display          = None
        self._pil_display_original = None
        self._pil_rotate_original  = None
        self._pix                  = None
        self._pending_angle        = 0
        self._frame     = QRectF()
        self._img_off_x = 0.0
        self._img_off_y = 0.0
        self._img_scale = 1.0
        self._xzoom     = 1.0
        self.update()

    def set_aspect(self, aspect):
        self._aspect = aspect
        if aspect:
            self._apply_aspect_to_frame()
        self._clamp_pan()
        self.update()
        self._emit()

    def reset_crop(self):
        if not self._pil:
            return
        self._xzoom = 1.0
        self._init_frame()
        self.update()
        self._emit()

    def reset_zoom(self):
        """Reset extra zoom; image re-fills the frame."""
        self._xzoom = 1.0
        self._recalc_scale_and_pan()
        self.update()

    def rotate_image(self, angle):
        """Rotate *angle* degrees from _pil_original (CCW-positive)."""
        if not self._pil_original:
            return
        self._pending_angle = angle
        src  = self._pil_rotate_original
        disp = src.copy() if angle == 0 else \
               src.rotate(angle, expand=True, resample=Image.NEAREST)
        self._pil_display = disp
        self._pil         = disp
        self._refresh_pixmap()
        self._xzoom = 1.0
        self._init_frame()
        self.update()

    def flip_image(self, direction):
        if not self._pil_original:
            return
        if direction == 'horizontal':
            self._pil_original = ImageOps.mirror(self._pil_original)
        else:
            self._pil_original = ImageOps.flip(self._pil_original)
        self._pil                  = self._pil_original.copy()
        self._pil_display          = self._make_display(self._pil)
        self._pil_display_original = self._pil_display.copy()
        self._pil_rotate_original  = self._make_rotate(self._pil)
        self._pending_angle        = 0
        self._refresh_pixmap()
        self.reset_crop()

    def get_crop_coords(self):
        """Return (x, y, w, h) in image-pixel coordinates."""
        if not self._pil or self._frame.isEmpty():
            return (0, 0, 0, 0)
        s  = max(self._img_scale, 1e-6)
        iw = float(self._pil.width)
        ih = float(self._pil.height)
        ix = (-self._img_off_x) / s          # frame left in image-px
        iy = (-self._img_off_y) / s          # frame top  in image-px
        cw = self._frame.width()  / s
        ch = self._frame.height() / s
        x  = max(0.0, ix)
        y  = max(0.0, iy)
        x2 = min(iw, ix + cw)
        y2 = min(ih, iy + ch)
        return (int(x), int(y), max(1, int(x2 - x)), max(1, int(y2 - y)))

    def set_crop_coords(self, x, y, w, h):
        """Set crop from spinboxes / auto-crop (image-pixel coords)."""
        if not self._pil or self._frame.isEmpty():
            return
        iw = float(self._pil.width)
        ih = float(self._pil.height)
        x  = float(max(0, x));  y  = float(max(0, y))
        w  = float(max(1, min(w, iw - x)))
        h  = float(max(1, min(h, ih - y)))
        # Resize frame proportionally to fit the requested selection
        cw, ch  = self.width(), self.height()
        pad     = self._PAD
        avail_w = cw - 2 * pad
        avail_h = ch - 2 * pad
        scale_f = min(avail_w / w, avail_h / h)
        fw = w * scale_f;  fh = h * scale_f
        fx = (cw - fw) / 2.0;  fy = (ch - fh) / 2.0
        self._frame     = QRectF(fx, fy, fw, fh)
        self._img_scale = scale_f * self._xzoom
        self._img_off_x = -x * self._img_scale
        self._img_off_y = -y * self._img_scale
        self._clamp_pan()
        self.update()
        self._emit()

    def get_pil_image(self):
        """Return the full-resolution PIL image with any pending rotation applied."""
        if not self._pil_original:
            return None
        angle = getattr(self, '_pending_angle', 0)
        if angle == 0:
            return self._pil_original.copy()
        return self._pil_original.rotate(angle, expand=True, resample=Image.BICUBIC)

    # ──────────────────────────────────────────────────────────────────────
    # Internal geometry
    # ──────────────────────────────────────────────────────────────────────

    def _make_display(self, pil_image):
        iw, ih  = pil_image.width, pil_image.height
        longest = max(iw, ih)
        if longest > self.PREVIEW_MAX:
            s  = self.PREVIEW_MAX / longest
            pw = max(1, int(iw * s)); ph = max(1, int(ih * s))
            disp = pil_image.resize((pw, ph), Image.LANCZOS)
        else:
            disp = pil_image.copy()
        disp.info.pop('icc_profile', None)
        return disp

    def _make_rotate(self, pil_image):
        iw, ih  = pil_image.width, pil_image.height
        longest = max(iw, ih)
        s = self.ROTATE_MAX / longest
        if s < 1.0:
            pw = max(1, int(iw * s)); ph = max(1, int(ih * s))
            rot = pil_image.resize((pw, ph), Image.BOX)
        else:
            rot = pil_image.copy()
        rot.info.pop('icc_profile', None)
        return rot

    def _refresh_pixmap(self):
        src = self._pil_display if self._pil_display is not None else self._pil
        if not src:
            return
        rgba      = src.convert("RGBA")
        data      = rgba.tobytes("raw", "RGBA")
        qimg      = QImage(data, rgba.width, rgba.height, rgba.width * 4,
                           QImage.Format_RGBA8888)
        self._qimg_buf = data
        self._pix  = QPixmap.fromImage(qimg)

    def _init_frame(self):
        """Set up a default large frame and fit the image to fill it."""
        if not self._pil:
            return
        cw, ch = self.width(), self.height()
        if cw < 1 or ch < 1:
            return
        pad     = self._PAD
        avail_w = cw - 2 * pad
        avail_h = ch - 2 * pad
        iw      = float(self._pil.width)
        ih      = float(self._pil.height)
        # Frame: match image aspect, fill available area
        scale_f = min(avail_w / iw, avail_h / ih)
        fw = iw * scale_f;  fh = ih * scale_f
        fx = (cw - fw) / 2.0;  fy = (ch - fh) / 2.0
        self._frame = QRectF(fx, fy, fw, fh)
        if self._aspect:
            self._apply_aspect_to_frame()
        self._recalc_scale_and_pan()

    def _recalc_scale_and_pan(self):
        """Recompute _img_scale so the image fills the current frame, centred."""
        if not self._pil or self._frame.isEmpty():
            return
        iw = float(self._pil.width);  ih = float(self._pil.height)
        fw = self._frame.width();      fh = self._frame.height()
        fill_s          = max(fw / iw, fh / ih)
        self._img_scale = fill_s * self._xzoom
        sw = iw * self._img_scale;  sh = ih * self._img_scale
        self._img_off_x = (fw - sw) / 2.0
        self._img_off_y = (fh - sh) / 2.0
        self._clamp_pan()

    def _clamp_pan(self):
        """Ensure the image always covers the full frame (no empty gaps)."""
        if not self._pil or self._frame.isEmpty():
            return
        iw = float(self._pil.width);  ih = float(self._pil.height)
        sw = iw * self._img_scale;    sh = ih * self._img_scale
        fw = self._frame.width();     fh = self._frame.height()
        self._img_off_x = max(fw - sw, min(0.0, self._img_off_x))
        self._img_off_y = max(fh - sh, min(0.0, self._img_off_y))

    def _apply_aspect_to_frame(self):
        """Resize frame to match _aspect while keeping it centred."""
        if not self._aspect or not self._pil:
            return
        cw, ch = self.width(), self.height()
        if cw < 1 or ch < 1:
            return
        aw, ah  = float(self._aspect[0]), float(self._aspect[1])
        pad     = self._PAD
        avail_w = cw - 2 * pad;  avail_h = ch - 2 * pad
        if avail_w / aw < avail_h / ah:
            fw = avail_w; fh = fw * ah / aw
        else:
            fh = avail_h; fw = fh * aw / ah
        fx = (cw - fw) / 2.0;  fy = (ch - fh) / 2.0
        self._frame = QRectF(fx, fy, fw, fh)

    def _img_rect_on_canvas(self):
        """Return the QRectF of the full scaled image on canvas."""
        if not self._pil:
            return QRectF()
        iw = float(self._pil.width);  ih = float(self._pil.height)
        ox = self._frame.x() + self._img_off_x
        oy = self._frame.y() + self._img_off_y
        return QRectF(ox, oy, iw * self._img_scale, ih * self._img_scale)

    def _emit(self):
        x, y, w, h = self.get_crop_coords()
        self.crop_changed.emit(x, y, w, h)

    def _handle_pts(self):
        """Return {mode: QPointF} for all 8 handles around the frame."""
        r  = self._frame
        cx = r.center().x(); cy = r.center().y()
        return {
            self._TL: QPointF(r.left(),  r.top()),
            self._TC: QPointF(cx,        r.top()),
            self._TR: QPointF(r.right(), r.top()),
            self._ML: QPointF(r.left(),  cy),
            self._MR: QPointF(r.right(), cy),
            self._BL: QPointF(r.left(),  r.bottom()),
            self._BC: QPointF(cx,        r.bottom()),
            self._BR: QPointF(r.right(), r.bottom()),
        }

    def _hit(self, pt):
        hit_r = self._HIT
        for mode, hp in self._handle_pts().items():
            dx = pt.x() - hp.x(); dy = pt.y() - hp.y()
            if dx * dx + dy * dy <= hit_r * hit_r:
                return mode
        if self._frame.contains(pt):
            return self._PAN
        # Allow panning even when the click starts outside the crop frame
        # (e.g. in the darkened area around it) — as long as an image is loaded.
        if self._pil is not None:
            return self._PAN
        return self._NONE

    # ──────────────────────────────────────────────────────────────────────
    # Mouse interaction
    # ──────────────────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pt           = QPointF(e.pos())
        self._drag   = self._hit(pt)
        self._dstart = pt
        self._frame0 = QRectF(self._frame)
        self._off0_x = self._img_off_x
        self._off0_y = self._img_off_y
        self._scale0 = self._img_scale

    def mouseMoveEvent(self, e):
        pt = QPointF(e.pos())
        if self._drag == self._NONE:
            cur = self._CURSORS.get(self._hit(pt), Qt.ArrowCursor)
            self.setCursor(QCursor(cur))
            return
        dx = pt.x() - self._dstart.x()
        dy = pt.y() - self._dstart.y()
        if self._drag == self._PAN:
            self._img_off_x = self._off0_x + dx
            self._img_off_y = self._off0_y + dy
            self._clamp_pan()
        else:
            self._resize_frame(dx, dy)
        self.update()
        self._emit()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = self._NONE

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.reset_crop()

    def wheelEvent(self, e):
        if not self._pil or self._frame.isEmpty():
            return
        factor   = 1.10 if e.angleDelta().y() > 0 else (1.0 / 1.10)
        new_zoom = max(1.0, min(self._xzoom * factor, 8.0))
        if new_zoom == self._xzoom:
            return
        pt   = QPointF(e.pos())
        fx   = self._frame.x(); fy = self._frame.y()
        cx_f = pt.x() - fx - self._img_off_x   # cursor pos in image-scaled px
        cy_f = pt.y() - fy - self._img_off_y
        ratio           = new_zoom / self._xzoom
        self._xzoom     = new_zoom
        iw = float(self._pil.width); ih = float(self._pil.height)
        fw = self._frame.width();    fh = self._frame.height()
        fill_s          = max(fw / iw, fh / ih)
        self._img_scale = fill_s * self._xzoom
        self._img_off_x = (pt.x() - fx) - cx_f * ratio
        self._img_off_y = (pt.y() - fy) - cy_f * ratio
        self._clamp_pan()
        self.update()
        self._emit()

    def _resize_frame(self, dx, dy):
        """Resize the crop frame by dragging a handle.

        The frame is hard-clamped to the image's canvas footprint so it can
        never extend into empty (dark) space beyond the image edges.
        """
        if not self._pil:
            return
        f0    = self._frame0
        cw_c  = self.width(); ch_c = self.height()
        pad   = self._PAD
        MIN   = float(self._MIN_FRAME)

        # Image footprint on canvas at drag-start (image position hasn't moved)
        iw = float(self._pil.width); ih = float(self._pil.height)
        img_l = self._frame0.x() + self._off0_x
        img_t = self._frame0.y() + self._off0_y
        img_r = img_l + iw * self._scale0
        img_b = img_t + ih * self._scale0

        left   = f0.left();  right  = f0.right()
        top    = f0.top();   bottom = f0.bottom()
        delta_l = 0.0;  delta_t = 0.0

        drag = self._drag
        if drag in (self._TL, self._ML, self._BL):
            # Clamp: can't go left of image left edge OR canvas pad
            new_left = max(pad, max(img_l, min(left + dx, right - MIN)))
            delta_l  = new_left - left;  left = new_left
        if drag in (self._TR, self._MR, self._BR):
            # Clamp: can't go right of image right edge OR canvas pad
            right = min(cw_c - pad, min(img_r, max(right + dx, left + MIN)))
        if drag in (self._TL, self._TC, self._TR):
            # Clamp: can't go above image top edge OR canvas pad
            new_top = max(pad, max(img_t, min(top + dy, bottom - MIN)))
            delta_t = new_top - top;  top = new_top
        if drag in (self._BL, self._BC, self._BR):
            # Clamp: can't go below image bottom edge OR canvas pad
            bottom = min(ch_c - pad, min(img_b, max(bottom + dy, top + MIN)))

        fw = right - left;  fh = bottom - top

        if self._aspect:
            aw, ah = float(self._aspect[0]), float(self._aspect[1])
            target = aw / ah
            if drag in (self._TL, self._TR, self._BL, self._BR):
                if abs(dx) >= abs(dy):
                    fh = fw / target
                else:
                    fw = fh * target
            elif drag in (self._ML, self._MR):
                fh = fw / target
            elif drag in (self._TC, self._BC):
                fw = fh * target
            cx0 = (left + right) / 2.0;  cy0 = (top + bottom) / 2.0
            left = cx0 - fw / 2.0; right  = cx0 + fw / 2.0
            top  = cy0 - fh / 2.0; bottom = cy0 + fh / 2.0
            # Re-clamp aspect frame to image bounds
            if left   < img_l:  left   = img_l;  right  = left  + fw
            if right  > img_r:  right  = img_r;  left   = right - fw
            if top    < img_t:  top    = img_t;  bottom = top   + fh
            if bottom > img_b:  bottom = img_b;  top    = bottom - fh
            fw = right - left;  fh = bottom - top

        self._frame = QRectF(left, top, fw, fh)

        # Keep image visually stable: compensate for frame-edge shift
        self._img_off_x = self._off0_x - delta_l
        self._img_off_y = self._off0_y - delta_t

        # Re-fit if the image no longer covers the enlarged frame
        # (fill_s is the bare minimum — do NOT multiply by _xzoom again)
        fill_s = max(fw / iw, fh / ih)
        if self._img_scale < fill_s:
            ratio           = fill_s / max(self._img_scale, 1e-9)
            self._img_scale = fill_s
            self._img_off_x *= ratio
            self._img_off_y *= ratio

        self._clamp_pan()

    # ──────────────────────────────────────────────────────────────────────
    # Painting
    # ──────────────────────────────────────────────────────────────────────

    def paintEvent(self, e):
        if not self._pix:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cw, ch = self.width(), self.height()

        # 0. Clip everything to the panel's left-side rounding (20px, matches
        #    PANEL_STYLE) — right corners stay square since the sidebar sits there.
        radius = 20
        panel_clip = QPainterPath()
        panel_clip.addRoundedRect(QRectF(0, 0, cw, ch), radius, radius)
        right_square = QPainterPath()
        right_square.addRect(QRectF(cw - radius, 0, radius, ch))
        p.setClipPath(panel_clip.united(right_square))

        # 1. Checkerboard canvas background (so transparent image areas are visible)
        checkerboard_paint(p, cw, ch)

        if self._frame.isEmpty():
            return

        r   = self._frame
        img = self._img_rect_on_canvas()

        # 2. Draw image clipped to crop frame (sharp, correctly exposed)
        p.save()
        clip = QPainterPath()
        clip.addRect(r)
        p.setClipPath(clip, Qt.IntersectClip)
        p.drawPixmap(img.toRect(), self._pix, self._pix.rect())
        p.restore()

        # 3. Draw image outside crop frame, then darken it (app-panel tint)
        p.save()
        outside = QPainterPath()
        outside.addRect(QRectF(0, 0, cw, ch))
        inside  = QPainterPath()
        inside.addRect(r)
        p.setClipPath(outside.subtracted(inside), Qt.IntersectClip)
        p.drawPixmap(img.toRect(), self._pix, self._pix.rect())
        p.fillRect(0, 0, cw, ch, QColor(18, 18, 22, 168))
        p.restore()

        # 4. Crop frame border — brand-blue accent with a dark shadow pass
        #    so it stays visible on both light and dark image content.
        p.setPen(QPen(QColor(0, 0, 0, 90), 3.0))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        p.setPen(QPen(QColor(88, 101, 242, 235), 1.5))
        p.drawRect(r)

        # 5. Rule-of-thirds grid — only while dragging
        if self._drag != self._NONE:
            p.setPen(QPen(QColor(165, 180, 252, 70), 1.0))
            for i in (1, 2):
                gx = r.left() + r.width()  * i / 3.0
                gy = r.top()  + r.height() * i / 3.0
                p.drawLine(QPointF(gx, r.top()),   QPointF(gx, r.bottom()))
                p.drawLine(QPointF(r.left(), gy),  QPointF(r.right(), gy))

        # 6. Handles
        self._paint_handles(p)

    def _paint_handles(self, p):
        """L-shaped corner handles + thin edge-centre handles, themed to the
        app's brand-blue accent with a dark shadow pass for contrast."""
        r   = self._frame
        L   = self._L_LEN;   T  = float(self._L_THK)
        EL  = self._EDGE_LEN; ET = float(self._EDGE_THK)
        col    = QColor(165, 180, 252, 255)   # brand-blue accent (light variant)
        shadow = QColor(0, 0, 0, 110)

        def _L(hx, hy, ax, ay):
            # shadow
            p.setPen(QPen(shadow, T + 2.0, Qt.SolidLine, Qt.SquareCap))
            p.drawLine(QPointF(hx, hy), QPointF(hx + ax * L, hy))
            p.drawLine(QPointF(hx, hy), QPointF(hx, hy + ay * L))
            # white
            p.setPen(QPen(col, T, Qt.SolidLine, Qt.SquareCap))
            p.drawLine(QPointF(hx, hy), QPointF(hx + ax * L, hy))
            p.drawLine(QPointF(hx, hy), QPointF(hx, hy + ay * L))

        def _edge(x1, y1, x2, y2):
            p.setPen(QPen(shadow, ET + 2.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.setPen(QPen(col, ET, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        _L(r.left(),  r.top(),    +1, +1)
        _L(r.right(), r.top(),    -1, +1)
        _L(r.left(),  r.bottom(), +1, -1)
        _L(r.right(), r.bottom(), -1, -1)

        cx = r.center().x(); cy = r.center().y(); h = EL / 2.0
        _edge(cx - h, r.top(),    cx + h, r.top())
        _edge(cx - h, r.bottom(), cx + h, r.bottom())
        _edge(r.left(),  cy - h,  r.left(),  cy + h)
        _edge(r.right(), cy - h,  r.right(), cy + h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._pil:
            self._init_frame()
        self.update()


# =========================================
# BG CANVAS  —  free-transform subject layer
# =========================================

class BgCanvas(QWidget):
    """
    Displays a background-removed RGBA subject over a configurable background.
    The subject can be freely moved, scaled (8 handles) and rotated (bottom handle).
    White dashed bounding box with rounded square corner handles + circular rotate knob.
    """

    SWATCHES = [
        ("None",        None),
        ("White",       QColor(255, 255, 255)),
        ("Light Grey",  QColor(220, 220, 220)),
        ("Mid Grey",    QColor(160, 160, 160)),
        ("Dark Grey",   QColor(80,  80,  80)),
        ("Black",       QColor(0,   0,   0)),
        ("Navy",        QColor(10,  20,  80)),
        ("Deep Blue",   QColor(20,  50,  160)),
        ("Sky Blue",    QColor(100, 180, 255)),
        ("Teal",        QColor(0,   150, 150)),
        ("Mint",        QColor(160, 240, 200)),
        ("Forest Green",QColor(30,  100, 50)),
        ("Lime",        QColor(180, 240, 60)),
        ("Yellow",      QColor(255, 220, 0)),
        ("Orange",      QColor(255, 140, 0)),
        ("Red",         QColor(220, 40,  40)),
        ("Pink",        QColor(255, 160, 200)),
        ("Purple",      QColor(130, 40,  180)),
        ("Lavender",    QColor(200, 180, 255)),
        ("Brown",       QColor(120, 70,  30)),
    ]

    GRADIENTS = [
        # Row 1
        ("Sunset",      [(0.0, QColor(255,94,77)),   (0.5, QColor(255,154,0)),   (1.0, QColor(255,206,84))],  135),
        ("Aurora",      [(0.0, QColor(0,200,150)),   (0.5, QColor(80,120,255)),  (1.0, QColor(180,50,200))],  135),
        ("Rose Gold",   [(0.0, QColor(200,100,120)), (0.5, QColor(230,160,100)), (1.0, QColor(255,210,160))], 135),
        ("Blueberry",   [(0.0, QColor(30,30,120)),   (0.5, QColor(80,60,200)),   (1.0, QColor(130,100,255))], 135),
        ("Mango",       [(0.0, QColor(255,160,0)),   (0.5, QColor(255,200,50)),  (1.0, QColor(200,255,100))], 135),
        # Row 2
        ("Ocean",       [(0.0, QColor(50,130,200)),  (0.5, QColor(80,200,230)),  (1.0, QColor(130,240,200))], 135),
        ("Lime Pop",    [(0.0, QColor(180,255,80)),  (0.5, QColor(220,255,100)), (1.0, QColor(255,240,60))],  135),
        # Row 3
        ("Cotton Candy",[(0.0, QColor(255,180,220)), (0.5, QColor(200,180,255)), (1.0, QColor(180,220,255))], 135),
        ("Peach Mango", [(0.0, QColor(255,200,100)), (0.5, QColor(255,150,80)),  (1.0, QColor(255,230,150))], 135),
        ("Lavender Dew",[(0.0, QColor(180,160,255)), (0.5, QColor(210,190,255)), (1.0, QColor(240,230,255))], 135),
        ("Ice Blue",    [(0.0, QColor(180,230,255)), (0.5, QColor(200,245,255)), (1.0, QColor(220,255,240))], 135),
        ("Meadow",      [(0.0, QColor(150,220,120)), (0.5, QColor(190,235,160)), (1.0, QColor(230,250,200))], 135),
        # Row 4
        ("Crimson Night",[(0.0, QColor(90,10,30)),   (0.5, QColor(180,20,50)),   (1.0, QColor(220,80,30))],  135),
        ("Desert",      [(0.0, QColor(180,100,40)),  (0.5, QColor(220,150,60)),  (1.0, QColor(240,200,120))], 135),
        ("Deep Sea",    [(0.0, QColor(10,40,80)),    (0.5, QColor(20,90,130)),   (1.0, QColor(0,150,180))],  135),
        ("Midnight",    [(0.0, QColor(10,10,30)),    (0.5, QColor(30,30,80)),    (1.0, QColor(60,30,100))],  135),
        ("Forest",      [(0.0, QColor(10,50,20)),    (0.5, QColor(30,80,40)),    (1.0, QColor(50,120,60))],  135),
    ]

    # Background mode constants
    BG_NONE     = "none"
    BG_COLOR    = "color"
    BG_GRADIENT = "gradient"
    BG_IMAGE    = "image"

    # Drag mode constants
    _D_NONE   = 0
    _D_MOVE   = 1
    _D_ROTATE = 2
    _D_TL = 10; _D_TC = 11; _D_TR = 12
    _D_ML = 13;              _D_MR = 14
    _D_BL = 15; _D_BC = 16; _D_BR = 17

    _SCALE_CURSORS = {
        _D_TL: Qt.SizeFDiagCursor, _D_BR: Qt.SizeFDiagCursor,
        _D_TR: Qt.SizeBDiagCursor, _D_BL: Qt.SizeBDiagCursor,
        _D_TC: Qt.SizeVerCursor,   _D_BC: Qt.SizeVerCursor,
        _D_ML: Qt.SizeHorCursor,   _D_MR: Qt.SizeHorCursor,
    }

    # Handle geometry
    _H  = 9   # half-size of square handle
    _RH = 10  # radius of rotate knob
    _ROTATE_OFFSET = 28  # px below bottom-center of bounding box

    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig_rgba   = None
        self._orig_source = None
        self._subject_bbox = None  # (left, top, right, bottom) bbox of the subject in _orig_rgba
        self._bg_color    = None
        self._bg_gradient = None
        self._bg_img_pix  = None
        self._bg_img_pil  = None
        self._bg_mode     = "none"
        self._base_pix    = None       # QPixmap of the RGBA subject

        # --- transform state (in canvas/widget coordinates) ---
        # Centre of the subject on screen
        self._cx = 0.0
        self._cy = 0.0
        # Displayed size (w, h) of the subject bounding box
        self._sw = 0.0
        self._sh = 0.0
        # Rotation in degrees (clockwise)
        self._angle = 0.0

        # --- drag state ---
        self._drag      = self._D_NONE
        self._drag_start_mouse  = QPointF()
        self._drag_start_cx     = 0.0
        self._drag_start_cy     = 0.0
        self._drag_start_sw     = 0.0
        self._drag_start_sh     = 0.0
        self._drag_start_angle  = 0.0
        self._drag_start_corner = QPointF()  # opposite corner for scale

        # --- snap state ---
        self._snap_cx       = False          # True while snapped to canvas centre X
        self._snap_cy       = False          # True while snapped to canvas centre Y
        self._snap_edge_x   = False          # snapped to a vertical edge/third line
        self._snap_edge_y   = False          # snapped to a horizontal edge/third line

        # --- selection / hover state ---
        self._transform_visible = False      # show transform handles only when selected

        # When the user explicitly selects "No background", lock out live-sync
        # color updates (from the custom color picker) until they pick a real bg.
        self._bg_none_locked    = False

        # btn2: whether the bg image fills the whole canvas or stays inside the photo rect
        self._bg_fill_canvas = False

        # --- build three overlay buttons ---
        BTN = 32
        _btn_style = """
QPushButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    color: white;
    font-size: 15px;
}
QPushButton:hover  { background: rgba(255,255,255,0.18); }
QPushButton:pressed{ background: rgba(255,255,255,0.28); }
QPushButton:checked{ background: rgba(88,101,242,0.45); }
"""
        self._btn_fill = QPushButton("⛶", self)
        self._btn_fill.setToolTip("Toggle: fill full canvas  ↔  match photo dimensions")
        self._btn_fill.setCheckable(True)
        self._btn_fill.setFixedSize(BTN, BTN)
        self._btn_fill.setStyleSheet(_btn_style)
        self._btn_fill.setCursor(QCursor(Qt.PointingHandCursor))
        self._btn_fill.clicked.connect(self._on_btn_fill)

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(200)

    # ------------------------------------------------------------------
    # Public API (called from main_window)
    # ------------------------------------------------------------------

    def set_image(self, pil_rgba, pil_source_rgb=None):
        self._orig_rgba   = pil_rgba
        self._orig_source = pil_source_rgb

        # Crop to the tight bounding box of non-transparent pixels so the
        # transform handles sit flush around the actual subject, not the
        # whole image canvas (which may have large transparent borders after
        # background removal).
        subject = pil_rgba
        self._subject_bbox = None   # (left, top, right, bottom) in original image px
        try:
            bbox = pil_rgba.getbbox()   # (left, top, right, bottom) of opaque pixels
            if bbox:
                subject = pil_rgba.crop(bbox)
                self._subject_bbox = bbox
        except Exception:
            pass

        buf = io.BytesIO()
        subject.save(buf, "PNG")
        buf.seek(0)
        self._base_pix = QPixmap()
        self._base_pix.loadFromData(buf.read())
        self._transform_visible = True   # show handles immediately on first load
        self._reset_transform()
        self.update()

    def clear(self):
        """Release all loaded image data (subject + original source).

        BgCanvas is created once and kept alive for the whole app
        session, so without this the full-resolution `_orig_rgba` /
        `_orig_source` images stay resident in memory indefinitely after
        the user is done — even after clicking Clear or switching to
        another tool. Call this whenever the loaded image is no longer
        needed.
        """
        self._orig_rgba    = None
        self._orig_source  = None
        self._subject_bbox = None
        self._bg_img_pix   = None
        self._bg_img_pil   = None
        self._base_pix     = None
        self._transform_visible = False
        self._cx = 0.0
        self._cy = 0.0
        self._sw = 0.0
        self._sh = 0.0
        self._angle = 0.0
        self.update()

    def _reset_transform(self):
        """Fit the subject in the canvas at its natural display size and position.

        The subject (_base_pix) may be a tight bbox-crop of a downscaled
        preview.  We must therefore scale it relative to the *image_rect*
        (the area that represents the original image at 1:1 aspect), not
        against the raw pixel dimensions of _base_pix — otherwise a small
        preview results in the subject rendering at 100 % of its tiny pixel
        size, which looks zoomed-in compared to the background frame.

        The subject is placed at the position its bbox centre occupies within
        the original image, so it appears exactly where it was rather than
        always being centred.
        """
        if not self._base_pix:
            return
        cw, ch = max(self.width(), 1), max(self.height(), 1)
        pw, ph = self._base_pix.width(), self._base_pix.height()

        img_rect = self._image_rect(cw, ch)
        irw, irh = max(img_rect.width(), 1), max(img_rect.height(), 1)

        if self._orig_rgba is not None:
            orig_w, orig_h = self._orig_rgba.size
            frac_w = pw / max(orig_w, 1)
            frac_h = ph / max(orig_h, 1)
            sw = irw * frac_w
            sh = irh * frac_h
            pad = 16
            s = min((cw - pad) / max(sw, 1), (ch - pad) / max(sh, 1), 1.0)
            self._sw = sw * s
            self._sh = sh * s

            # Place the subject at its bbox centre within the original image,
            # mapped to canvas coordinates through img_rect.
            # Note: the scale factor s only guards against the subject overflowing
            # the canvas edges — it does not affect where in the image the subject
            # sits, so we do NOT apply s to the position mapping.
            bbox = getattr(self, '_subject_bbox', None)
            if bbox is not None:
                bx1, by1, bx2, by2 = bbox
                cx_frac = (bx1 + bx2) / 2.0 / max(orig_w, 1)
                cy_frac = (by1 + by2) / 2.0 / max(orig_h, 1)
                self._cx = img_rect.x() + cx_frac * irw
                self._cy = img_rect.y() + cy_frac * irh
            else:
                self._cx = cw / 2.0
                self._cy = ch / 2.0
        else:
            s = min(irw / pw, irh / ph, 1.0)
            self._sw = pw * s
            self._sh = ph * s
            self._cx = cw / 2.0
            self._cy = ch / 2.0

        self._angle = 0.0

    def _image_rect(self, cw, ch):
        """Return a QRect centred in the canvas that matches the original image aspect ratio,
        scaled to fit. The background is drawn only within this rect."""
        if self._orig_rgba is None:
            return QRect(0, 0, cw, ch)
        iw, ih = self._orig_rgba.size
        s = min(cw / iw, ch / ih)
        dw, dh = int(iw * s), int(ih * s)
        x = (cw - dw) // 2
        y = (ch - dh) // 2
        return QRect(x, y, dw, dh)

    def set_bg_none(self):
        """Explicitly select 'no background' (transparent/checkerboard).
        Locks out live-sync color updates until a real bg is chosen."""
        self._bg_none_locked = True
        self._bg_color    = None
        self._bg_gradient = None
        self._bg_mode     = "none"
        self.update()

    def set_bg_color(self, qcolor):
        # If the user has explicitly chosen "no background", ignore live-sync
        # color updates from the custom picker until a real choice is made.
        if self._bg_none_locked and qcolor is not None:
            return
        self._bg_none_locked = False
        self._bg_color    = qcolor
        self._bg_gradient = None
        self._bg_mode     = "none" if qcolor is None else "color"
        self.update()

    def set_bg_gradient(self, gradient_spec):
        self._bg_none_locked = False
        self._bg_gradient = gradient_spec
        self._bg_color    = None
        self._bg_mode     = "gradient"
        self.update()

    def set_bg_image(self, pil_image):
        self._bg_none_locked = False
        if pil_image is None:
            self._bg_img_pil = None
            self._bg_img_pix = None
            self._bg_mode    = "none"
            self.update()
            return
        self._bg_img_pil  = pil_image.convert("RGB")
        self._bg_color    = None
        self._bg_gradient = None
        self._bg_mode     = "image"
        buf = io.BytesIO()
        self._bg_img_pil.save(buf, "PNG")
        buf.seek(0)
        self._bg_img_pix = QPixmap()
        self._bg_img_pix.loadFromData(buf.read())
        self.update()

    # ------------------------------------------------------------------
    # Overlay quick-action button handlers
    # ------------------------------------------------------------------

    def _on_btn_fill(self, checked):
        """Toggle whether the bg image fills the full canvas or stays within the photo rect."""
        self._bg_fill_canvas = checked
        self.update()

    # ------------------------------------------------------------------
    # Overlay button positioning
    # ------------------------------------------------------------------

    def _place_overlay_buttons(self):
        """Position the fill overlay button in the top-right corner."""
        PAD = 10  # padding from canvas edge
        BTN = 32
        x   = self.width() - PAD - BTN
        y   = PAD
        self._btn_fill.move(x, y)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_qt_gradient(self, dest_rect, stops, angle_deg):
        cx = dest_rect.center().x()
        cy = dest_rect.center().y()
        hw = dest_rect.width()  / 2
        hh = dest_rect.height() / 2
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        dist = abs(hw * cos_a) + abs(hh * sin_a)
        x1 = cx - dist * cos_a
        y1 = cy - dist * sin_a
        x2 = cx + dist * cos_a
        y2 = cy + dist * sin_a
        grad = QLinearGradient(x1, y1, x2, y2)
        for pos, color in stops:
            grad.setColorAt(pos, color)
        return grad

    def _render_gradient_to_pil(self, size, stops, angle_deg):
        pix = QPixmap(size[0], size[1])
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = pix.rect()
        grad = self._make_qt_gradient(rect, stops, angle_deg)
        painter.fillRect(rect, QBrush(grad))
        painter.end()
        img = pix.toImage()
        img = img.convertToFormat(img.Format_ARGB32)
        ptr = img.bits()
        ptr.setsize(img.byteCount())
        import numpy as np
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((size[1], size[0], 4))
        rgb = arr[:, :, [2, 1, 0]]
        return Image.fromarray(rgb, "RGB")

    # ------------------------------------------------------------------
    # Transform geometry helpers
    # ------------------------------------------------------------------

    def _transform(self):
        """Return a QTransform that maps local subject coords → widget coords."""
        t = QTransform()
        t.translate(self._cx, self._cy)
        t.rotate(self._angle)
        t.translate(-self._sw / 2, -self._sh / 2)
        return t

    def _inv_transform(self):
        # PyQt5: inverted() returns (QTransform, bool) — NOT (bool, QTransform)
        inv, ok = self._transform().inverted()
        return inv if ok else QTransform()

    def _bbox_corners(self):
        """4 corners of the bounding box in widget space."""
        t = self._transform()
        w, h = self._sw, self._sh
        pts = [QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)]
        return [t.map(p) for p in pts]

    def _midpoint(self, a, b):
        return QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)

    def _handle_centers(self):
        """Returns dict of drag-mode → centre point in widget space."""
        tl, tr, br, bl = self._bbox_corners()
        tc = self._midpoint(tl, tr)
        bc = self._midpoint(bl, br)
        ml = self._midpoint(tl, bl)
        mr = self._midpoint(tr, br)
        # rotate knob: offset below bottom-centre along rotated Y axis
        ang_rad = math.radians(self._angle)
        rot_dx = math.sin(ang_rad) * self._ROTATE_OFFSET
        rot_dy = math.cos(ang_rad) * self._ROTATE_OFFSET
        rh = QPointF(bc.x() + rot_dx, bc.y() + rot_dy)
        return {
            self._D_TL: tl, self._D_TC: tc, self._D_TR: tr,
            self._D_ML: ml,                  self._D_MR: mr,
            self._D_BL: bl, self._D_BC: bc, self._D_BR: br,
            self._D_ROTATE: rh,
        }

    def _hit_test(self, pt):
        hc = self._handle_centers()
        H  = self._H + 3   # slightly larger hit area
        RH = self._RH + 4

        # Rotate knob first (circle)
        rh = hc[self._D_ROTATE]
        if (pt - rh).manhattanLength() <= RH + 4:
            return self._D_ROTATE

        # Square handles
        for mode in (self._D_TL, self._D_TC, self._D_TR,
                     self._D_ML, self._D_MR,
                     self._D_BL, self._D_BC, self._D_BR):
            c = hc[mode]
            if abs(pt.x() - c.x()) <= H and abs(pt.y() - c.y()) <= H:
                return mode

        # Inside bounding box → move
        local = self._inv_transform().map(pt)
        if 0 <= local.x() <= self._sw and 0 <= local.y() <= self._sh:
            return self._D_MOVE

        return self._D_NONE

    def _cursor_for(self, mode):
        if mode == self._D_ROTATE:
            return Qt.CrossCursor
        if mode == self._D_MOVE:
            return Qt.SizeAllCursor
        return self._SCALE_CURSORS.get(mode, Qt.ArrowCursor)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self._base_pix:
            return
        pt = QPointF(e.pos())

        # When handles are hidden, only a click on the subject body activates them
        if not self._transform_visible:
            local = self._inv_transform().map(pt)
            on_subject = (0 <= local.x() <= self._sw and 0 <= local.y() <= self._sh)
            if on_subject:
                self._transform_visible = True
                self.update()
            # Don't start a drag on this press; wait for next press after selection
            return

        mode = self._hit_test(pt)

        # Clicking the empty canvas background deselects the transform box
        if mode == self._D_NONE:
            self._transform_visible = False
            self._drag = self._D_NONE
            self.setCursor(QCursor(Qt.ArrowCursor))
            self.update()
            return

        self._drag = mode
        self._drag_start_mouse  = pt
        self._drag_start_cx     = self._cx
        self._drag_start_cy     = self._cy
        self._drag_start_sw     = self._sw
        self._drag_start_sh     = self._sh
        self._drag_start_angle  = self._angle
        # For scale drags: record the opposite corner so we can anchor it
        hc = self._handle_centers()
        opposite = {
            self._D_TL: self._D_BR, self._D_TR: self._D_BL,
            self._D_BL: self._D_TR, self._D_BR: self._D_TL,
            self._D_TC: self._D_BC, self._D_BC: self._D_TC,
            self._D_ML: self._D_MR, self._D_MR: self._D_ML,
        }
        if mode in opposite:
            self._drag_start_corner = hc[opposite[mode]]
        self.setCursor(QCursor(self._cursor_for(mode)))

    def mouseMoveEvent(self, e):
        pt = QPointF(e.pos())
        if not self._base_pix:
            return
        if self._drag == self._D_NONE:
            mode = self._hit_test(pt)
            self.setCursor(QCursor(self._cursor_for(mode)))
            return

        dx = pt.x() - self._drag_start_mouse.x()
        dy = pt.y() - self._drag_start_mouse.y()

        if self._drag == self._D_MOVE:
            raw_cx = self._drag_start_cx + dx
            raw_cy = self._drag_start_cy + dy
            cw2, ch2 = self.width(), self.height()

            # ── Snap thresholds ──────────────────────────────────────────────
            SNAP_D = 8   # px — pull range for all magnets

            snapped_cx = raw_cx
            snapped_cy = raw_cy
            self._snap_cx = self._snap_cy = False
            self._snap_edge_x = self._snap_edge_y = False

            # Collect candidate X snap lines (canvas centre + thirds + edges)
            x_lines = [cw2 / 2, cw2 / 3, 2 * cw2 / 3, 0.0, float(cw2)]
            y_lines = [ch2 / 2, ch2 / 3, 2 * ch2 / 3, 0.0, float(ch2)]

            # Subject bbox extents (used for edge-of-bbox snapping)
            half_w = self._sw / 2
            half_h = self._sh / 2

            # --- X axis snaps ---
            # 1. Centre of subject to canvas-centre X
            for xl in x_lines:
                if abs(raw_cx - xl) <= SNAP_D:
                    snapped_cx = xl
                    if xl == cw2 / 2:
                        self._snap_cx = True
                    else:
                        self._snap_edge_x = True
                    break
            # 2. Left/right edge of bbox to the same lines
            if not self._snap_cx and not self._snap_edge_x:
                for xl in x_lines:
                    if abs((raw_cx - half_w) - xl) <= SNAP_D:
                        snapped_cx = xl + half_w
                        self._snap_edge_x = True
                        break
                    if abs((raw_cx + half_w) - xl) <= SNAP_D:
                        snapped_cx = xl - half_w
                        self._snap_edge_x = True
                        break

            # --- Y axis snaps ---
            for yl in y_lines:
                if abs(raw_cy - yl) <= SNAP_D:
                    snapped_cy = yl
                    if yl == ch2 / 2:
                        self._snap_cy = True
                    else:
                        self._snap_edge_y = True
                    break
            if not self._snap_cy and not self._snap_edge_y:
                for yl in y_lines:
                    if abs((raw_cy - half_h) - yl) <= SNAP_D:
                        snapped_cy = yl + half_h
                        self._snap_edge_y = True
                        break
                    if abs((raw_cy + half_h) - yl) <= SNAP_D:
                        snapped_cy = yl - half_h
                        self._snap_edge_y = True
                        break

            self._cx = snapped_cx
            self._cy = snapped_cy

        elif self._drag == self._D_ROTATE:
            # Angle from subject centre to current mouse vs. start mouse
            sx, sy = self._drag_start_cx, self._drag_start_cy
            ang0 = math.degrees(math.atan2(
                self._drag_start_mouse.y() - sy,
                self._drag_start_mouse.x() - sx))
            ang1 = math.degrees(math.atan2(pt.y() - sy, pt.x() - sx))
            raw = self._drag_start_angle + (ang1 - ang0)
            # Magnet: snap to nearest 90° when within 8° of it
            nearest_90 = round(raw / 90) * 90
            self._angle = float(nearest_90) if abs(raw - nearest_90) <= 8 else raw

        else:
            # Scale: uniform (aspect-ratio locked) — never stretches the cutout.
            # Strategy: compute a single scalar factor from how far the dragged
            # handle moved along the dominant axis, apply it to both sw and sh,
            # and reposition the centre so the *opposite* anchor corner stays fixed.
            anchor = self._drag_start_corner
            ang_rad = math.radians(self._drag_start_angle)
            cos_a = math.cos(ang_rad)
            sin_a = math.sin(ang_rad)

            # Project mouse positions into the rotated local frame of the subject
            # (origin = anchor / opposite corner).
            def to_local(p):
                vx = p.x() - anchor.x()
                vy = p.y() - anchor.y()
                return QPointF(vx * cos_a + vy * sin_a,
                               -vx * sin_a + vy * cos_a)

            start_local = to_local(self._drag_start_mouse)
            curr_local  = to_local(pt)

            MIN = 30.0
            mode = self._drag

            # Pick the primary axis for each handle type, then derive a single
            # uniform scale factor so the aspect ratio is always preserved.
            if mode in (self._D_ML, self._D_MR):
                # Horizontal-only handle: scale driven by X movement.
                ref = abs(start_local.x())
                if ref < 1:
                    ref = 1.0
                factor = abs(curr_local.x()) / ref
            elif mode in (self._D_TC, self._D_BC):
                # Vertical-only handle: scale driven by Y movement.
                ref = abs(start_local.y())
                if ref < 1:
                    ref = 1.0
                factor = abs(curr_local.y()) / ref
            else:
                # Corner handles: use the axis whose start-distance is larger
                # (more stable numerically) to drive the uniform scale.
                ref_x = abs(start_local.x())
                ref_y = abs(start_local.y())
                if ref_x >= ref_y and ref_x > 1:
                    factor = abs(curr_local.x()) / ref_x
                elif ref_y > 1:
                    factor = abs(curr_local.y()) / ref_y
                else:
                    factor = 1.0

            # Clamp so the image never becomes tinier than MIN px on either side.
            min_factor = MIN / min(self._drag_start_sw, self._drag_start_sh)
            factor = max(factor, min_factor)

            new_sw = self._drag_start_sw * factor
            new_sh = self._drag_start_sh * factor

            # Reposition centre so the anchor (opposite) corner stays fixed.
            # The dragged corner in local space is at (±new_sw, ±new_sh) relative
            # to the anchor; the new centre is halfway between them.
            # local_sign tells us which quadrant the dragged corner lives in.
            lsx = math.copysign(1.0, start_local.x()) if abs(start_local.x()) > 0.1 else 1.0
            lsy = math.copysign(1.0, start_local.y()) if abs(start_local.y()) > 0.1 else 1.0

            if mode in (self._D_ML, self._D_MR):
                opp_local = QPointF(lsx * new_sw, start_local.y())
            elif mode in (self._D_TC, self._D_BC):
                opp_local = QPointF(start_local.x(), lsy * new_sh)
            else:
                opp_local = QPointF(lsx * new_sw, lsy * new_sh)

            # Convert back to widget space
            opp_widget = QPointF(
                anchor.x() + opp_local.x() * cos_a - opp_local.y() * sin_a,
                anchor.y() + opp_local.x() * sin_a + opp_local.y() * cos_a,
            )
            self._cx = (anchor.x() + opp_widget.x()) / 2
            self._cy = (anchor.y() + opp_widget.y()) / 2
            self._sw = new_sw
            self._sh = new_sh

        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = self._D_NONE
            self._snap_cx = self._snap_cy = False
            self._snap_edge_x = self._snap_edge_y = False
            if self._base_pix:
                mode = self._hit_test(QPointF(e.pos()))
                self.setCursor(QCursor(self._cursor_for(mode)))
            self.update()   # repaint so the degree pill disappears immediately

    def mouseDoubleClickEvent(self, e):
        """Double-click resets position, scale and rotation to the default fit."""
        if e.button() == Qt.LeftButton and self._base_pix:
            self._reset_transform()
            self.update()

    def _paint_snap_lines(self, p, cw, ch):
        """Flash snap guide-lines while dragging near a magnet."""
        SNAP_COLOR_CENTER = QColor(88, 101, 242, 200)   # brand blue — canvas centre
        SNAP_COLOR_EDGE   = QColor(255, 200, 60, 200)   # amber — thirds / edges

        if self._snap_cx:
            pen = QPen(SNAP_COLOR_CENTER, 1.5, Qt.DashLine)
            pen.setDashPattern([6, 4])
            p.setPen(pen)
            p.drawLine(cw // 2, 0, cw // 2, ch)
        elif self._snap_edge_x:
            pen = QPen(SNAP_COLOR_EDGE, 1, Qt.DashLine)
            pen.setDashPattern([4, 6])
            p.setPen(pen)
            # Draw all candidate vertical lines so the user sees what snapped
            for xl in (cw / 3, 2 * cw / 3, 0.0, float(cw)):
                p.drawLine(int(xl), 0, int(xl), ch)

        if self._snap_cy:
            pen = QPen(SNAP_COLOR_CENTER, 1.5, Qt.DashLine)
            pen.setDashPattern([6, 4])
            p.setPen(pen)
            p.drawLine(0, ch // 2, cw, ch // 2)
        elif self._snap_edge_y:
            pen = QPen(SNAP_COLOR_EDGE, 1, Qt.DashLine)
            pen.setDashPattern([4, 6])
            p.setPen(pen)
            for yl in (ch / 3, 2 * ch / 3, 0.0, float(ch)):
                p.drawLine(0, int(yl), cw, int(yl))

        # Centre dot — glows blue when both axes locked to canvas centre
        if self._snap_cx and self._snap_cy:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(88, 101, 242, 220)))
            p.drawEllipse(QPointF(cw / 2, ch / 2), 5, 5)
            p.setPen(QPen(QColor(255, 255, 255, 180), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cw / 2, ch / 2), 5, 5)

    # ------------------------------------------------------------------
    # Result export
    # ------------------------------------------------------------------

    def get_result(self):
        """Render the current composition at 4K quality (3840 px on the long edge).

        Strategy
        --------
        1.  Compute export scale = max(4K target, original pixel density).
        2.  Build full-resolution QPixmaps for image backgrounds from
            their PIL originals resized to the exact output size.  This avoids
            Qt stretching a tiny screen-res pixmap to 4K (which causes
            blurry results and small file sizes).
        3.  Temporarily swap those hi-res pixmaps in, paint, then restore.
        """
        if self._orig_rgba is None:
            return None
        cw, ch = self.width(), self.height()
        if cw == 0 or ch == 0:
            return None

        # ── 1. Determine output size = original image dimensions at 4K quality ──
        iw, ih = self._orig_rgba.size
        TARGET_PX = 3840
        scale = max(TARGET_PX / max(iw, ih), 1.0)
        ow, oh = int(iw * scale), int(ih * scale)

        # Use the original image size as the virtual canvas for rendering.
        # _paint_scene paints into (vcw, vch); _image_rect will return the full
        # rect since the virtual canvas IS the image dimensions.
        vcw, vch = ow, oh

        # ── 2. Upscale background image to output resolution ───────────────
        def _pil_to_qpix(pil_img, w, h):
            """Resize PIL image to (w, h) and return a QPixmap."""
            resized = pil_img.resize((w, h), Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, "PNG")
            buf.seek(0)
            px = QPixmap()
            px.loadFromData(buf.read())
            return px

        _saved_img_pix = self._bg_img_pix
        if self._bg_mode == "image" and self._bg_img_pil is not None:
            self._bg_img_pix = _pil_to_qpix(self._bg_img_pil, ow, oh)

        # ── 3. Render into a canvas that is exactly the image size ─────────
        # Scale the subject transform from screen coords to output coords.
        cw, ch = self.width(), self.height()
        img_rect = self._image_rect(cw, ch)   # where image sits on screen
        sx = ow / img_rect.width()  if img_rect.width()  > 0 else 1.0
        sy = oh / img_rect.height() if img_rect.height() > 0 else 1.0

        # Temporarily remap transform to output space
        saved_cx, saved_cy = self._cx, self._cy
        saved_sw, saved_sh = self._sw, self._sh
        self._cx = (self._cx - img_rect.x()) * sx
        self._cy = (self._cy - img_rect.y()) * sy
        self._sw = self._sw * sx
        self._sh = self._sh * sy

        out_pix = QPixmap(ow, oh)
        out_pix.fill(Qt.transparent)
        p = QPainter(out_pix)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_scene(p, vcw, vch, draw_handles=False)
        p.end()

        # Restore transform and pixmaps
        self._cx, self._cy = saved_cx, saved_cy
        self._sw, self._sh = saved_sw, saved_sh
        self._bg_img_pix   = _saved_img_pix

        # ── 4. Convert QPixmap → PIL ───────────────────────────────────────
        import numpy as np
        img = out_pix.toImage()
        ptr = img.bits()
        ptr.setsize(img.byteCount())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((oh, ow, 4))
        rgba = arr[:, :, [2, 1, 0, 3]]
        result_pil = Image.fromarray(rgba, "RGBA")

        mode = self._bg_mode
        if mode == "none":
            return result_pil
        return result_pil.convert("RGB")

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def _paint_scene(self, p, cw, ch, draw_handles=True):
        """Paint background + subject (+ optional handles) into painter p."""
        img_rect = self._image_rect(cw, ch)   # background confined to image dimensions
        mode = self._bg_mode

        bg_pix = self._bg_img_pix

        # Determine the rect to draw the background into.
        # _bg_fill_canvas expands the custom bg to cover the whole canvas widget.
        if self._bg_fill_canvas:
            fill_rect = QRect(0, 0, cw, ch)
        else:
            fill_rect = img_rect

        if mode == "gradient" and self._bg_gradient is not None:
            _name, stops, angle_deg = self._bg_gradient
            grad = self._make_qt_gradient(fill_rect, stops, angle_deg)
            p.fillRect(fill_rect, QBrush(grad))
        elif mode == "color" and self._bg_color:
            p.fillRect(fill_rect, self._bg_color)
        elif mode == "image" and bg_pix:
            p.drawPixmap(fill_rect, bg_pix, bg_pix.rect())
        # else: transparent / checkerboard (already painted in paintEvent)

        if not self._base_pix:
            return

        # Draw the subject with transform
        p.save()
        p.translate(self._cx, self._cy)
        p.rotate(self._angle)
        p.translate(-self._sw / 2, -self._sh / 2)
        p.drawPixmap(QRect(0, 0, int(self._sw), int(self._sh)),
                     self._base_pix, self._base_pix.rect())
        p.restore()

        if not draw_handles:
            return

        # Snap guide-lines (drawn above subject, below handles)
        if self._drag == self._D_MOVE:
            self._paint_snap_lines(p, cw, ch)

        if not self._transform_visible:
            return

        # --- Draw transform handles ---
        hc = self._handle_centers()
        tl, tr, br, bl = self._bbox_corners()
        tc = hc[self._D_TC]
        bc = hc[self._D_BC]
        ml = hc[self._D_ML]
        mr = hc[self._D_MR]
        rh = hc[self._D_ROTATE]
        H  = self._H

        # Dashed bounding box — brand blue with a dark shadow pass for contrast on any bg
        # Shadow pass (dark, slightly thicker) so the box reads on white backgrounds too
        pen_shadow = QPen(QColor(0, 0, 0, 60), 3.0, Qt.DashLine)
        pen_shadow.setDashPattern([6, 4])
        p.setPen(pen_shadow)
        p.setBrush(Qt.NoBrush)
        poly = QPainterPath()
        poly.moveTo(tl); poly.lineTo(tr); poly.lineTo(br); poly.lineTo(bl); poly.closeSubpath()
        p.drawPath(poly)
        # Main brand-blue dashed line on top
        pen = QPen(QColor(88, 101, 242, 230), 1.5, Qt.DashLine)
        pen.setDashPattern([6, 4])
        p.setPen(pen)
        p.drawPath(poly)

        # Line from bottom-centre to rotate knob
        p.setPen(QPen(QColor(0, 0, 0, 50), 2.5))
        p.drawLine(bc, rh)
        p.setPen(QPen(QColor(88, 101, 242, 180), 1.5))
        p.drawLine(bc, rh)

        # Corner + edge square handles — white fill with dark border so visible on white bg
        for pt in (tl, tr, bl, br, tc, bc, ml, mr):
            rect = QRectF(pt.x() - H, pt.y() - H, H*2, H*2)
            # Dark shadow border
            p.setPen(QPen(QColor(0, 0, 0, 100), 2.0))
            p.setBrush(QBrush(QColor(255, 255, 255, 240)))
            p.drawRoundedRect(rect, 3, 3)
            # Blue inner border on top
            p.setPen(QPen(QColor(88, 101, 242, 200), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 3, 3)

        # Rotate knob — filled circle in brand blue
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        p.setBrush(QBrush(QColor(88, 101, 242, 230)))
        p.drawEllipse(rh, self._RH, self._RH)
        # Small rotation arrow icon inside knob
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
        p.setBrush(Qt.NoBrush)
        arc_rect = QRectF(rh.x() - 5, rh.y() - 5, 10, 10)
        p.drawArc(arc_rect, 30 * 16, 280 * 16)

        # Degree readout pill — shown only while actively rotating
        if self._drag == self._D_ROTATE:
            angle_text = f"{self._angle % 360:.0f}°"
            font = p.font()
            font.setPointSize(11)
            font.setBold(False)
            p.setFont(font)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(angle_text)
            th = fm.height()
            pad_x, pad_y = 10, 5
            pill_w = tw + pad_x * 2
            pill_h = th + pad_y * 2
            pill_x = self._cx - pill_w / 2
            pill_y = self._cy - pill_h / 2
            pill = QRectF(pill_x, pill_y, pill_w, pill_h)
            # Dark semi-transparent background
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(15, 15, 20, 210)))
            p.drawRoundedRect(pill, 12, 12)
            # Subtle border
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(pill, 12, 12)
            # Angle text in white
            p.setPen(QColor(255, 255, 255, 240))
            p.drawText(pill, Qt.AlignCenter, angle_text)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cw, ch = self.width(), self.height()

        # All-four-corners rounded clip (matches panel's 20px border-radius)
        rad = 20
        clip = QPainterPath()
        clip.moveTo(rad, 0)
        clip.lineTo(cw - rad, 0)
        clip.quadTo(cw, 0, cw, rad)
        clip.lineTo(cw, ch - rad)
        clip.quadTo(cw, ch, cw - rad, ch)
        clip.lineTo(rad, ch)
        clip.quadTo(0, ch, 0, ch - rad)
        clip.lineTo(0, rad)
        clip.quadTo(0, 0, rad, 0)
        clip.closeSubpath()
        p.setClipPath(clip)

        # Checkerboard (only visible where bg is transparent)
        checkerboard_paint(p, cw, ch)

        self._paint_scene(p, cw, ch, draw_handles=True)

        # ── Overlay pill bar behind the one action button ──────────────
        if self._base_pix:
            BTN = 32; PAD = 10
            bar_w = BTN + 10
            bar_h = BTN + 8
            bar_x = self.width() - PAD - BTN - 5
            bar_y = PAD - 4
            bar_rect = QRectF(bar_x, bar_y, bar_w, bar_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(10, 10, 14, 140)))
            p.drawRoundedRect(bar_rect, 12, 12)
            p.setPen(QPen(QColor(255, 255, 255, 20), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(bar_rect, 12, 12)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_overlay_buttons()
        if self._base_pix:
            self._reset_transform()