import io
import os
import threading

import requests
from PyQt5.QtCore import QObject, pyqtSignal

from PIL import Image


# =========================================
# REMOVE.BG API CONFIG
# =========================================
# The key is read from the REMOVEBG_API_KEY environment variable so it
# isn't committed to source control. Set it before launching the app, e.g.
# (PowerShell):  $env:REMOVEBG_API_KEY = "your-key-here"
# or add it to a local, git-ignored .env file loaded by your launcher.

REMOVEBG_API_KEY = os.environ.get("REMOVEBG_API_KEY", "W6VWnnzXdBmwcrTPKyVsMxNG")
REMOVEBG_ENDPOINT = "https://api.remove.bg/v1.0/removebg"

# How long to wait for the API before giving up (seconds)
REQUEST_TIMEOUT = 60

_key_warned = threading.Event()


def _warn_missing_key_once():
    if not REMOVEBG_API_KEY and not _key_warned.is_set():
        _key_warned.set()
        print(
            "[background_remove] REMOVEBG_API_KEY is not set — requests to "
            "remove.bg will fail. Set the REMOVEBG_API_KEY environment "
            "variable before launching the app."
        )


# =========================================
# CORE API CALL
# =========================================

def _remove_bg_via_api(pil_image, size="auto", bg_color=None):
    """
    Sends a PIL image to the remove.bg API and returns an RGBA PIL image
    with the background removed.
    """
    _warn_missing_key_once()

    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    buf.seek(0)

    data = {"size": size}
    if bg_color is not None:
        # remove.bg accepts a hex color and will composite server-side
        data["bg_color"] = bg_color

    response = requests.post(
        REMOVEBG_ENDPOINT,
        files={"image_file": ("image.png", buf, "image/png")},
        data=data,
        headers={"X-Api-Key": REMOVEBG_API_KEY},
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == requests.codes.ok:
        result = Image.open(io.BytesIO(response.content))
        return result.convert("RGBA") if bg_color is None else result.convert("RGB")

    # Try to surface remove.bg's own error message
    try:
        err_json = response.json()
        errors = err_json.get("errors", [])
        msg = "; ".join(e.get("title", str(e)) for e in errors) or response.text
    except Exception:
        msg = response.text or f"HTTP {response.status_code}"

    raise RuntimeError(f"remove.bg API error ({response.status_code}): {msg}")


# =========================================
# BG REMOVE WORKER  (remove.bg API — preview)
# =========================================

class BgRemoveWorker(QObject):
    finished = pyqtSignal(object, object)
    error    = pyqtSignal(str)

    def __init__(self, path, remover=None):
        super().__init__()
        self.path = path
        # 'remover' isn't a loaded model for the API path — kept only so
        # the call signature in main_window.py doesn't need to change.
        self.remover = remover

    def run(self):
        try:
            orig = Image.open(self.path).convert("RGB")

            # Downscale before upload — faster round-trip and uses the
            # cheaper "preview" tier of the API for the initial look.
            MAX_PREVIEW = 1200
            preview = orig.copy()
            if max(preview.size) > MAX_PREVIEW:
                preview.thumbnail((MAX_PREVIEW, MAX_PREVIEW), Image.LANCZOS)

            result = _remove_bg_via_api(preview, size="preview")

            # second value is just a sentinel now (kept for compatibility
            # with code that stashes it as self.bg_remover)
            self.finished.emit(result, "removebg-api")

        except requests.exceptions.RequestException as e:
            self.error.emit(f"Network error contacting remove.bg: {e}")
        except Exception as ex:
            self.error.emit(str(ex))


# =========================================
# BG REMOVE SAVE WORKER  (full-resolution export)
# =========================================

class BgRemoveSaveWorker(QObject):
    """
    Re-runs background removal at full original resolution for export,
    via the remove.bg API's "auto" size tier (uses higher-res output
    when the account plan allows it).
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

            hex_color = None
            if self.bg_color is not None:
                hex_color = "{:02x}{:02x}{:02x}".format(
                    self.bg_color.red(), self.bg_color.green(), self.bg_color.blue()
                )

            result = _remove_bg_via_api(orig, size="auto", bg_color=hex_color)
            self.finished.emit(result)

        except requests.exceptions.RequestException as e:
            self.error.emit(f"Network error contacting remove.bg: {e}")
        except Exception as ex:
            self.error.emit(str(ex))