import sys
import os
import signal
import traceback

# Allow running as `python core/main.py` directly by ensuring the project
# root is on sys.path (so `import ui`, `import tools` etc. resolve).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must import torch on the main thread before any background threads do —
# on Windows, torch's DLL loading fails when first imported from a thread.
try:
    import torch
except Exception:
    pass

from PyQt5.QtWidgets import QApplication
from core.main_window import ImageCompressor


def _except_hook(exc_type, exc_value, exc_tb):
    """Print unhandled exceptions so PyQt5 doesn't silently swallow them."""
    traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _except_hook

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Let Ctrl+C in the terminal close cleanly instead of crashing with traceback
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    window = ImageCompressor()
    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        sys.exit(0)
