import os
import io
from PyQt5.QtCore import QObject, pyqtSignal

from PIL import Image
import threading


# =========================================
# WARMUP
# =========================================

_remover_instance = None
_remover_ready    = threading.Event()

def warmup_remover():
    global _remover_instance
    try:
        from transparent_background import Remover
        _remover_instance = Remover(mode="base", device="cpu")
        print("WARMUP OK")
    except Exception as e:
        print(f"WARMUP FAILED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _remover_ready.set()

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

        tb = target_kb * 1024          # hard upper limit in bytes
        w, h = img.size
        min_quality = 2
        min_scale = 0.10

        scale = 1.0
        best_buf = None

        for attempt in range(20):
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            rs = img.resize((nw, nh), Image.LANCZOS)

            # Binary search for the HIGHEST quality that stays <= tb
            buf = self._bsearch_strict(rs, tb, min_quality)

            if buf is not None:
                best_buf = buf
                break
            else:
                # Even min quality exceeds target — reduce dimensions
                scale -= 0.05
                if scale < min_scale:
                    # Last resort: use smallest scale with min quality
                    nw = max(1, int(w * min_scale))
                    nh = max(1, int(h * min_scale))
                    rs = img.resize((nw, nh), Image.LANCZOS)
                    buf = self._bsearch_strict(rs, tb, min_quality)
                    if buf is not None:
                        best_buf = buf
                    else:
                        # Absolute fallback: save at minimum quality
                        fallback = io.BytesIO()
                        rs.save(fallback, "JPEG", quality=min_quality, optimize=True)
                        best_buf = fallback.getvalue()
                    break

        if best_buf is None:
            fallback = io.BytesIO()
            img.save(fallback, "JPEG", quality=min_quality, optimize=True)
            best_buf = fallback.getvalue()

        with open(dst, "wb") as f:
            f.write(best_buf)
        return os.path.getsize(dst) / 1024

    @staticmethod
    def _bsearch_strict(img, target_bytes, min_quality=2):
        """Binary search for the highest JPEG quality where file size <= target_bytes.

        Returns the JPEG bytes if a valid quality is found, or None if even
        min_quality produces a file larger than target_bytes.
        """
        lo, hi = min_quality, 95
        best_buf = None

        # First check: does min quality fit?
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=lo, optimize=True)
        if buf.tell() > target_bytes:
            return None  # even lowest quality exceeds target

        best_buf = buf.getvalue()

        while lo <= hi:
            mid = (lo + hi) // 2
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=mid, optimize=True)
            sz = buf.tell()

            if sz <= target_bytes:
                # This quality fits — try higher quality
                best_buf = buf.getvalue()
                lo = mid + 1
            else:
                # Too large — try lower quality
                hi = mid - 1

        return best_buf


# =========================================
# BG REMOVE WORKER  (offline — InSPyReNet, CPU)
# =========================================

class BgRemoveWorker(QObject):
    finished = pyqtSignal(object, object)
    error    = pyqtSignal(str)

    def __init__(self, path, remover=None):
        super().__init__()
        self.path = path
        self.remover = remover

    def run(self):
        try:

            if self.remover is None:
                _remover_ready.wait()        # instant if warmup done, waits if still loading
                self.remover = _remover_instance
                if self.remover is None:     # warmup failed, load ourselves
                    from transparent_background import Remover
                    self.remover = Remover(mode="base", device="cpu")

            remover = self.remover

            orig = Image.open(self.path).convert("RGB")

            # Downscale for fast preview display only — full-size processed on save
            MAX_PREVIEW = 1200
            preview = orig.copy()
            if max(preview.size) > MAX_PREVIEW:
                preview.thumbnail((MAX_PREVIEW, MAX_PREVIEW), Image.LANCZOS)

            result = remover.process(preview, type="rgba")

            self.finished.emit(result, remover)

        except ImportError as e:
            self.error.emit(
                f"Missing dependency: {e}\n"
                "Run:  pip install transparent-background"
            )
        except Exception as ex:
            self.error.emit(str(ex))


# =========================================
# BG REMOVE SAVE WORKER  (full-resolution export)
# =========================================

class BgRemoveSaveWorker(QObject):
    """
    Re-runs background removal at full original resolution for export.
    Uses the already-loaded remover so the model is not reloaded.
    """
    finished = pyqtSignal(object)   # emits full-size RGBA PIL
    error    = pyqtSignal(str)

    def __init__(self, path, remover, bg_color=None):
        super().__init__()
        self.path     = path
        self.remover  = remover
        self.bg_color = bg_color   # QColor or None

    def run(self):
        try:
            orig   = Image.open(self.path).convert("RGB")
            result = self.remover.process(orig, type="rgba")
            if self.bg_color is not None:
                from PIL import Image as _Image
                bg = _Image.new("RGBA", result.size,
                                (self.bg_color.red(), self.bg_color.green(),
                                 self.bg_color.blue(), 255))
                bg.paste(result, mask=result.split()[3])
                result = bg.convert("RGB")
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
