import os
import io

from PIL import Image

from PyQt5.QtCore import QObject, pyqtSignal


# =========================================
# COMPRESS WORKER
# =========================================

class CompressWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal()
    error    = pyqtSignal(int, str)

    def __init__(self, files, target_kb, output_folder):
        super().__init__()
        self.files = files
        self.target_kb = target_kb
        self.output_folder = output_folder
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        os.makedirs(self.output_folder, exist_ok=True)
        for i, path in enumerate(self.files):
            if self._cancelled:
                break
            try:
                fn = os.path.basename(path)
                name, _ = os.path.splitext(fn)
                out = os.path.join(self.output_folder, f"{name}_compressed.jpg")
                orig_kb = os.path.getsize(path) / 1024
                comp_kb = self._compress(path, out, self.target_kb)
                pct = int((1 - comp_kb / orig_kb) * 100) if orig_kb > 0 else 0
                self.progress.emit(i, f"✓  {fn}   {orig_kb:.0f} KB  →  {comp_kb:.0f} KB   (−{pct}%)")
            except Exception as ex:
                self.error.emit(i, str(ex))
        self.finished.emit()

    def _compress(self, src, dst, target_kb):
        img = Image.open(src)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            tmp = img.convert("RGBA") if img.mode == "P" else img
            bg.paste(tmp, mask=tmp.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        tb = target_kb * 1024
        w, h = img.size
        scale = 1.0
        for _ in range(10):
            rs = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            self._bsearch(rs, dst, tb)
            if abs(os.path.getsize(dst) - tb) <= 2048 or os.path.getsize(dst) <= tb:
                break
            scale -= 0.07
            if scale < 0.25:
                break
        return os.path.getsize(dst) / 1024

    @staticmethod
    def _bsearch(img, dst, tb):
        lo, hi, best = 5, 95, 85
        while lo <= hi:
            mid = (lo + hi) // 2
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=mid, optimize=True)
            sz = buf.tell()
            if abs(sz - tb) < 1024:
                best = mid
                break
            elif sz > tb:
                hi = mid - 1
            else:
                best = mid
                lo = mid + 1
        img.save(dst, "JPEG", quality=best, optimize=True)


# =========================================
# BG REMOVE WORKER
# =========================================

class BgRemoveWorker(QObject):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            from rembg import remove
            img = Image.open(self.path).convert("RGBA")
            result = remove(img)
            self.finished.emit(result)
        except Exception as ex:
            self.error.emit(str(ex))


# =========================================
# PDF WORKER
# =========================================

class PdfWorker(QObject):
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            msg = self._fn(*self._args, **self._kwargs)
            self.finished.emit(msg or "Done")
        except Exception as ex:
            self.error.emit(str(ex))
