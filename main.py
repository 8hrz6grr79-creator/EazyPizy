import sys
import signal
import traceback

# Must import torch on the main thread before any background threads do —
# on Windows, torch's DLL loading fails when first imported from a thread.
try:
    import torch
except Exception:
    pass

from PyQt5.QtWidgets import QApplication
from main_window import ImageCompressor

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
