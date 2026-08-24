import io
import os
import threading

import numpy as np
import onnxruntime as ort
from PyQt5.QtCore import QObject, pyqtSignal

from PIL import Image, ImageFilter


# =========================================
# LOCAL MODEL CONFIG (BiRefNet-portrait, ONNX)
# =========================================

# Point this at wherever you keep the .onnx file. Ship it alongside your
# app (e.g. in a "models/" folder next to the executable) rather than
# downloading it at runtime.
MODEL_PATH = os.environ.get(
    "BIREFNET_MODEL_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "BiRefNet-portrait.onnx"),
)

# BiRefNet expects a fixed square input. 1024 is the standard resolution
# the released weights were trained/exported at — don't shrink this for
# "preview" mode, it hurts hair/edge quality a lot. Downscale the *output*
# instead (see BgRemoveWorker below).
INPUT_SIZE = 1024

# Feather radius applied to the mask before compositing, in pixels.
# Keeps hair edges from looking like a hard cutout. 0 disables it.
MASK_FEATHER_RADIUS = 1.5


# =========================================
# MODEL LOADING (singleton, loaded once)
# =========================================

_session = None
_session_lock = threading.Lock()
_remover_ready = threading.Event()


def _get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:  # re-check inside lock
                if not os.path.exists(MODEL_PATH):
                    raise RuntimeError(
                        f"Model file not found at {MODEL_PATH}. "
                        f"Download BiRefNet-portrait.onnx and place it there, "
                        f"or set BIREFNET_MODEL_PATH."
                    )
                providers = ort.get_available_providers()
                # Prefer GPU if present, fall back to CPU automatically.
                preferred = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in providers]

                # Trim ONNX Runtime's own memory overhead. The arena
                # allocator and memory-pattern optimizer both trade RAM
                # for speed — worth disabling on low-RAM / universal
                # targets, at a small (usually low double-digit ms) cost
                # per inference call.
                sess_options = ort.SessionOptions()
                sess_options.enable_mem_pattern = False
                sess_options.enable_cpu_mem_arena = False
                sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC

                _session = ort.InferenceSession(
                    MODEL_PATH, sess_options=sess_options, providers=preferred or ["CPUExecutionProvider"]
                )
    return _session


def warmup_remover():
    """
    Optional pre-load of the ONNX model into memory, so the first real
    removal call isn't slowed down by disk I/O and session init.

    NOT called automatically anymore — loading the model costs real RAM
    (roughly 500MB-1GB depending on platform/provider), which isn't free
    to hold for the whole app lifetime on lower-spec machines. Call this
    yourself at a point where the user has shown intent to actually use
    the feature (e.g. when they open the editor screen or pick an image),
    not unconditionally at app launch.
    """
    try:
        _get_session()
    except Exception:
        # Real error will surface on first actual use via the worker's
        # error signal — this is just a best-effort warmup.
        pass
    finally:
        _remover_ready.set()


def unload_remover():
    """
    Frees the loaded model from memory. Call this when the user leaves
    the editor, the app is minimized/idle for a while, or you otherwise
    want to give the RAM back. The next removal call will transparently
    reload the model (with the usual first-call delay).
    """
    global _session
    with _session_lock:
        _session = None
    _remover_ready.clear()


def is_remover_loaded():
    return _session is not None


# =========================================
# CORE INFERENCE
# =========================================

def _run_mask(pil_image_rgb):
    """
    Runs BiRefNet on a PIL RGB image and returns a single-channel PIL
    mask (mode 'L'), resized back to the original image's dimensions.
    """
    session = _get_session()
    orig_size = pil_image_rgb.size  # (w, h)

    resized = pil_image_rgb.resize((INPUT_SIZE, INPUT_SIZE), Image.LANCZOS)
    arr = np.array(resized).astype(np.float32) / 255.0
    arr = (arr - 0.5) / 0.5  # normalize to [-1, 1]
    arr = arr.transpose(2, 0, 1)[None, :].astype(np.float32)  # NCHW

    input_name = session.get_inputs()[0].name
    output = session.run(None, {input_name: arr})[0]

    mask = output[0]
    if mask.ndim == 3:
        mask = mask[0]
    mask = np.clip(mask, 0, 1)
    mask = (mask * 255).astype(np.uint8)

    mask_img = Image.fromarray(mask, mode="L").resize(orig_size, Image.LANCZOS)

    if MASK_FEATHER_RADIUS > 0:
        mask_img = mask_img.filter(ImageFilter.GaussianBlur(MASK_FEATHER_RADIUS))

    return mask_img


def _remove_bg_local(pil_image_rgb, bg_color=None):
    """
    Runs local background removal and returns a composited PIL image:
    - RGBA (transparent background) if bg_color is None
    - RGB (flattened onto bg_color) otherwise
    """
    mask_img = _run_mask(pil_image_rgb)

    if bg_color is None:
        result = pil_image_rgb.convert("RGBA")
        result.putalpha(mask_img)
        return result

    # Composite onto a solid background color, e.g. (255, 255, 255)
    bg = Image.new("RGB", pil_image_rgb.size, bg_color)
    result = Image.composite(pil_image_rgb, bg, mask_img)
    return result


# =========================================
# BG REMOVE WORKER  (preview — fast look)
# =========================================

class BgRemoveWorker(QObject):
    finished = pyqtSignal(object, object)
    error    = pyqtSignal(str)
    # Emitted with a short status string so the UI can show *why* it's
    # taking a moment, instead of looking frozen/broken on first use.
    # Stages: "loading_model" -> "processing" (loading_model is skipped
    # entirely on every call after the first, once the session is cached).
    status = pyqtSignal(str)

    def __init__(self, path, remover=None):
        super().__init__()
        self.path = path
        # 'remover' kept only so the call signature in main_window.py
        # doesn't need to change; the model is now a lazily-loaded
        # module-level singleton instead.
        self.remover = remover

    def run(self):
        try:
            if not is_remover_loaded():
                self.status.emit("loading_model")
                _get_session()  # first-call cost: disk read + session init

            self.status.emit("processing")

            orig = Image.open(self.path).convert("RGB")

            # Downscale before inference for a faster preview. Quality
            # takes a real hit below ~600px on the long edge, so don't
            # go smaller than that just to save time.
            MAX_PREVIEW = 800
            preview = orig.copy()
            if max(preview.size) > MAX_PREVIEW:
                preview.thumbnail((MAX_PREVIEW, MAX_PREVIEW), Image.LANCZOS)

            result = _remove_bg_local(preview)

            # second value is just a sentinel now (kept for compatibility
            # with code that stashes it as self.bg_remover)
            self.finished.emit(result, "birefnet-local")

        except Exception as ex:
            self.error.emit(str(ex))


# =========================================
# BG REMOVE SAVE WORKER  (full-resolution export)
# =========================================

class BgRemoveSaveWorker(QObject):
    """
    Re-runs background removal at full original resolution for export.
    """
    finished = pyqtSignal(object)   # emits full-size RGBA/RGB PIL
    error    = pyqtSignal(str)

    def __init__(self, path, remover=None, bg_color=None):
        super().__init__()
        self.path     = path
        self.remover  = remover   # unused, kept for interface compatibility
        self.bg_color = bg_color  # QColor or None

    def run(self):
        try:
            orig = Image.open(self.path).convert("RGB")

            rgb_color = None
            if self.bg_color is not None:
                rgb_color = (self.bg_color.red(), self.bg_color.green(), self.bg_color.blue())

            result = _remove_bg_local(orig, bg_color=rgb_color)
            self.finished.emit(result)

        except Exception as ex:
            self.error.emit(str(ex))