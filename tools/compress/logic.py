import os
import io
from PyQt5.QtCore import QObject, pyqtSignal

from PIL import Image


# =========================================
# COMPRESS WORKER
# =========================================
# Handles both image and PDF compression to a target file size.

class CompressWorker(QObject):
    progress      = pyqtSignal(int, str)
    finished      = pyqtSignal()
    error         = pyqtSignal(int, str)
    # Emitted when a file can't reach target without quality loss:
    # (index, filename, achieved_kb, target_kb, output_path)
    quality_limit = pyqtSignal(int, str, float, float, str)

    def __init__(self, files_with_targets, output_folder, force_compress=True):
        """
        files_with_targets: list of (path, target_kb) tuples

        force_compress is always treated as True: the worker automatically
        pushes through the aggressive quality/downscale phases to get as
        close to the target size as possible, without ever pausing to ask
        the user whether to continue.
        """
        super().__init__()
        self.files_with_targets = files_with_targets
        self.output_folder = output_folder
        self.force_compress = True
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        import shutil
        os.makedirs(self.output_folder, exist_ok=True)
        for i, (path, target_kb) in enumerate(self.files_with_targets):
            if self._cancelled:
                break
            try:
                fn = os.path.basename(path)
                name, ext = os.path.splitext(fn)
                orig_kb = os.path.getsize(path) / 1024

                # Already at or below target — leave it untouched, just
                # copy the original file over as-is (same format, no
                # re-encoding, no quality loss).
                if orig_kb <= target_kb:
                    out = os.path.join(self.output_folder, f"{name}{ext}")
                    shutil.copy2(path, out)
                    self.progress.emit(
                        i,
                        f"✓  {fn}   {orig_kb:.0f} KB   already ≤ {target_kb:.0f} KB target — kept as-is"
                    )
                    continue

                if ext.lower() == '.pdf':
                    out = os.path.join(self.output_folder, f"{name}_compressed.pdf")
                    comp_kb, hit_limit = self._compress_pdf(path, out, target_kb,
                                                            force=self.force_compress)
                else:
                    out = os.path.join(self.output_folder, f"{name}_compressed.jpg")
                    comp_kb, hit_limit = self._compress(path, out, target_kb,
                                                        force=self.force_compress)

                pct = int((1 - comp_kb / orig_kb) * 100) if orig_kb > 0 else 0

                if comp_kb > target_kb:
                    # Fully compressed automatically, landed as close to
                    # target as possible without asking the user anything.
                    self.progress.emit(
                        i,
                        f"✓  {fn}   {orig_kb:.0f} KB  →  {comp_kb:.0f} KB   (−{pct}%, "
                        f"closest to {target_kb:.0f} KB target)"
                    )
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
        # Was: linear scan from scale=1.0 down to 0.50 in steps of 0.05,
        # each step running a full ~8-encode binary search over quality
        # (_bsearch_strict) — up to 11 steps x 8 encodes = ~88 JPEG
        # encodes just to find a scale that fits.
        #
        # Whether a given scale "fits" (some quality in [safe_min_quality,
        # 95] gets under target) is monotonic in scale: if a smaller scale
        # fits, every smaller scale also fits. That means the candidates
        # form a false...false,true...true sequence ordered from largest
        # scale (index 0, hardest to fit) to smallest (easiest) — exactly
        # the shape a binary search wants. This finds the same "largest
        # scale that still fits" result in ~4 steps instead of up to 11.
        n_steps = int(round((1.0 - safe_min_scale) / 0.05)) + 1
        scale_candidates = [round(1.0 - i * 0.05, 2) for i in range(n_steps)]

        best_buf = None
        lo, hi = 0, len(scale_candidates) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            sc = scale_candidates[mid]
            nw = max(1, int(w * sc))
            nh = max(1, int(h * sc))
            rs = img.resize((nw, nh), Image.LANCZOS)
            buf = self._bsearch_strict(rs, tb, safe_min_quality)
            if buf is not None:
                best_buf = buf          # this scale works — try an even larger one
                hi = mid - 1
            else:
                lo = mid + 1            # too big to fit — only smaller scales left

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