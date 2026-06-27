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
    progress      = pyqtSignal(int, str)
    finished      = pyqtSignal()
    error         = pyqtSignal(int, str)
    # Emitted when a file can't reach target without quality loss:
    # (index, filename, achieved_kb, target_kb, output_path)
    quality_limit = pyqtSignal(int, str, float, float, str)

    def __init__(self, files_with_targets, output_folder, force_compress=False):
        """
        files_with_targets: list of (path, target_kb) tuples
        """
        super().__init__()
        self.files_with_targets = files_with_targets
        self.output_folder = output_folder
        self.force_compress = force_compress
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        os.makedirs(self.output_folder, exist_ok=True)
        for i, (path, target_kb) in enumerate(self.files_with_targets):
            if self._cancelled:
                break
            try:
                fn = os.path.basename(path)
                name, ext = os.path.splitext(fn)
                orig_kb = os.path.getsize(path) / 1024

                if ext.lower() == '.pdf':
                    out = os.path.join(self.output_folder, f"{name}_compressed.pdf")
                    comp_kb, hit_limit = self._compress_pdf(path, out, target_kb,
                                                            force=self.force_compress)
                else:
                    out = os.path.join(self.output_folder, f"{name}_compressed.jpg")
                    comp_kb, hit_limit = self._compress(path, out, target_kb,
                                                        force=self.force_compress)

                pct = int((1 - comp_kb / orig_kb) * 100) if orig_kb > 0 else 0

                if hit_limit and not self.force_compress:
                    # Target couldn't be met without quality loss
                    self.quality_limit.emit(i, fn, comp_kb, float(target_kb), out)
                else:
                    self.progress.emit(i, f"✓  {fn}   {orig_kb:.0f} KB  →  {comp_kb:.0f} KB   (−{pct}%)")
            except Exception as ex:
                self.error.emit(i, str(ex))
        self.finished.emit()

    @staticmethod
    def _extract_pdf_images(writer):
        """Extract all image XObjects from writer pages as a list of
        (xobj_ref, pil_image, original_width, original_height) tuples."""
        images = []
        seen_ids = set()
        for page in writer.pages:
            try:
                res = page.get("/Resources")
                if res is None:
                    continue
                xobjects = res.get("/XObject")
                if xobjects is None:
                    continue
                xobjects = xobjects.get_object()
            except Exception:
                continue

            for key in list(xobjects.keys()):
                try:
                    import pypdf
                    xobj = xobjects[key].get_object()
                    if xobj.get("/Subtype") != "/Image":
                        continue

                    obj_id = id(xobj)
                    if obj_id in seen_ids:
                        continue
                    seen_ids.add(obj_id)

                    w = int(xobj.get("/Width", 0))
                    h = int(xobj.get("/Height", 0))
                    if w < 2 or h < 2:
                        continue

                    filt = xobj.get("/Filter", "")
                    if isinstance(filt, pypdf.generic.ArrayObject):
                        filt = str(filt[0]) if filt else ""
                    else:
                        filt = str(filt)

                    pil_img = None
                    if filt == "/DCTDecode":
                        pil_img = Image.open(io.BytesIO(xobj._data))
                    else:
                        try:
                            raw = xobj.get_data()
                        except Exception:
                            continue
                        cs = str(xobj.get("/ColorSpace", ""))
                        if "/DeviceRGB" in cs or "/CalRGB" in cs:
                            if len(raw) == w * h * 3:
                                pil_img = Image.frombytes("RGB", (w, h), raw)
                        elif "/DeviceGray" in cs or "/CalGray" in cs:
                            if len(raw) == w * h:
                                pil_img = Image.frombytes("L", (w, h), raw)
                        if pil_img is None:
                            try:
                                pil_img = Image.open(io.BytesIO(raw))
                            except Exception:
                                continue

                    if pil_img is None:
                        continue
                    if pil_img.mode not in ("RGB", "L"):
                        pil_img = pil_img.convert("RGB")

                    images.append((xobj, pil_img, w, h))
                except Exception:
                    continue
        return images

    @staticmethod
    def _apply_images_to_pdf(images, quality, scale):
        """Re-encode all extracted images at the given quality and scale."""
        import pypdf
        from pypdf.generic import NameObject, NumberObject

        for xobj, pil_img, orig_w, orig_h in images:
            try:
                img = pil_img
                if scale < 1.0:
                    new_w = max(1, int(orig_w * scale))
                    new_h = max(1, int(orig_h * scale))
                    img = pil_img.resize((new_w, new_h), Image.LANCZOS)

                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True)

                xobj._data = buf.getvalue()
                xobj[NameObject("/Filter")] = NameObject("/DCTDecode")
                if img.mode == "L":
                    xobj[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
                else:
                    xobj[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
                xobj[NameObject("/BitsPerComponent")] = NumberObject(8)
                xobj[NameObject("/Width")] = NumberObject(img.width)
                xobj[NameObject("/Height")] = NumberObject(img.height)
                for rm_key in ("/DecodeParms", "/Decode"):
                    if rm_key in xobj:
                        del xobj[rm_key]
            except Exception:
                continue

    @staticmethod
    def _remove_pdf_images(writer):
        """Strip ALL image XObjects from every page (last-resort compression)."""
        from pypdf.generic import DictionaryObject, NameObject
        for page in writer.pages:
            try:
                res = page.get("/Resources")
                if res is None:
                    continue
                xobjects = res.get("/XObject")
                if xobjects is None:
                    continue
                xobjects = xobjects.get_object()
                keys_to_remove = []
                for key in list(xobjects.keys()):
                    try:
                        xobj = xobjects[key].get_object()
                        if xobj.get("/Subtype") == "/Image":
                            keys_to_remove.append(key)
                    except Exception:
                        continue
                for key in keys_to_remove:
                    del xobjects[key]
            except Exception:
                continue

    @staticmethod
    def _write_and_check(writer, dst, target_bytes):
        """Write the PDF and return (size_kb, reached_target)."""
        with open(dst, "wb") as fh:
            writer.write(fh)
        size = os.path.getsize(dst)
        return size / 1024, size <= target_bytes

    @staticmethod
    def _compress_pdf(src, dst, target_kb, force=False):
        """Compress a PDF to fit within target_kb using progressive phases:
        0) Metadata strip + object dedup + stream compression
        1) Image quality reduction (safe: quality >= 25)
        2) Image quality reduction (aggressive: quality < 25) — only if force
        3) Image downscaling — only if force
        4) Image removal (last resort) — only if force

        Returns (result_kb, quality_limited) where quality_limited is True
        if the target couldn't be met within acceptable quality bounds.
        """
        import pypdf

        target_bytes = target_kb * 1024

        # ── Phase 0: Structure optimization ─────────────────────────────
        reader = pypdf.PdfReader(src)
        writer = pypdf.PdfWriter()
        writer.append(reader)

        # Remove metadata
        writer.metadata = None
        try:
            if hasattr(writer, '_info'):
                writer._info = None
        except Exception:
            pass

        # Compress content streams
        for page in writer.pages:
            page.compress_content_streams()

        # Deduplicate identical objects
        try:
            writer.compress_identical_objects(
                remove_identicals=True,
                remove_orphans=True
            )
        except Exception:
            pass

        # Check if target already reached
        result_kb, reached = CompressWorker._write_and_check(writer, dst, target_bytes)
        if reached:
            return result_kb, False

        # ── Phase 1: Lower JPEG quality (safe range: 70 → 25) ──────────
        images = CompressWorker._extract_pdf_images(writer)

        if images:
            safe_quality_steps = [70, 50, 35, 25]
            for q in safe_quality_steps:
                CompressWorker._apply_images_to_pdf(images, q, 1.0)
                result_kb, reached = CompressWorker._write_and_check(writer, dst, target_bytes)
                if reached:
                    return result_kb, False

            # If not forcing, stop here — further compression will cause distortion
            if not force:
                return result_kb, True

            # ── Phase 2: Aggressive quality (15 → 3) — force only ──────
            aggressive_quality_steps = [15, 10, 7, 5, 3]
            for q in aggressive_quality_steps:
                CompressWorker._apply_images_to_pdf(images, q, 1.0)
                result_kb, reached = CompressWorker._write_and_check(writer, dst, target_bytes)
                if reached:
                    return result_kb, False

            # ── Phase 3: Downscale images at minimum quality — force only
            scale_steps = [0.85, 0.70, 0.55, 0.40, 0.30, 0.20, 0.10]
            for s in scale_steps:
                CompressWorker._apply_images_to_pdf(images, 3, s)
                result_kb, reached = CompressWorker._write_and_check(writer, dst, target_bytes)
                if reached:
                    return result_kb, False
        else:
            # No images to compress — structure-only PDF can't be reduced further
            if not force:
                return result_kb, True

        # ── Phase 4: Remove ALL images (last resort, force only) ───────
        CompressWorker._remove_pdf_images(writer)
        result_kb, reached = CompressWorker._write_and_check(writer, dst, target_bytes)

        return result_kb, False

    def _compress(self, src, dst, target_kb, force=False):
        """Compress an image to target_kb.

        Returns (result_kb, quality_limited) where quality_limited is True
        if the target couldn't be met without visible distortion.
        """
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

        # Quality/scale thresholds for "acceptable quality" vs "distortion"
        safe_min_quality = 20
        safe_min_scale   = 0.50
        hard_min_quality = 2
        hard_min_scale   = 0.10

        # ── Phase 1: Try safe compression (quality >= 20, scale >= 0.50) ──
        scale = 1.0
        best_buf = None

        while scale >= safe_min_scale:
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            rs = img.resize((nw, nh), Image.LANCZOS)
            buf = self._bsearch_strict(rs, tb, safe_min_quality)
            if buf is not None:
                best_buf = buf
                break
            scale -= 0.05

        if best_buf is not None:
            with open(dst, "wb") as f:
                f.write(best_buf)
            return os.path.getsize(dst) / 1024, False

        # ── Safe compression can't reach target — check force flag ────
        # First, produce the best "safe" result for reporting
        safe_scale = safe_min_scale
        nw = max(1, int(w * safe_scale))
        nh = max(1, int(h * safe_scale))
        rs = img.resize((nw, nh), Image.LANCZOS)
        safe_buf = io.BytesIO()
        rs.save(safe_buf, "JPEG", quality=safe_min_quality, optimize=True)
        safe_bytes = safe_buf.getvalue()

        if not force:
            # Save the best safe result and signal quality limit
            with open(dst, "wb") as f:
                f.write(safe_bytes)
            return os.path.getsize(dst) / 1024, True

        # ── Phase 2: Force aggressive compression ─────────────────────
        scale = 1.0
        best_buf = None

        for attempt in range(20):
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            rs = img.resize((nw, nh), Image.LANCZOS)
            buf = self._bsearch_strict(rs, tb, hard_min_quality)

            if buf is not None:
                best_buf = buf
                break
            else:
                scale -= 0.05
                if scale < hard_min_scale:
                    nw = max(1, int(w * hard_min_scale))
                    nh = max(1, int(h * hard_min_scale))
                    rs = img.resize((nw, nh), Image.LANCZOS)
                    buf = self._bsearch_strict(rs, tb, hard_min_quality)
                    if buf is not None:
                        best_buf = buf
                    else:
                        fallback = io.BytesIO()
                        rs.save(fallback, "JPEG", quality=hard_min_quality, optimize=True)
                        best_buf = fallback.getvalue()
                    break

        if best_buf is None:
            fallback = io.BytesIO()
            img.save(fallback, "JPEG", quality=hard_min_quality, optimize=True)
            best_buf = fallback.getvalue()

        with open(dst, "wb") as f:
            f.write(best_buf)
        return os.path.getsize(dst) / 1024, False

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
