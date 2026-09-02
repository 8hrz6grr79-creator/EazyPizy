import sys
import os
import signal
import traceback
from pathlib import Path

# Allow running as `python core/main.py` directly by ensuring the project
# root is on sys.path (so `import ui`, `import tools` etc. resolve).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_local_env():
    if getattr(sys, "frozen", False):
        # Packaged (.exe) — .env must sit next to the executable itself.
        base_dir = Path(sys.executable).resolve().parent
    else:
        # Running from source — .env sits at the project root.
        base_dir = Path(__file__).resolve().parent.parent

    env_path = base_dir / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _except_hook(exc_type, exc_value, exc_tb):
    """Print unhandled exceptions so PyQt5 doesn't silently swallow them."""
    traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def run():
    """Actual app entry point — imported and called by launcher.py
    (for the packaged .exe) or run directly via `python core/main.py`
    (for dev)."""
    _load_local_env()

    # Make relative "assets/icons/..." paths used throughout main_window.py
    # resolve correctly in both dev and packaged (.exe) modes.
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile extracts bundled data (via --add-data) to
        # a temp folder exposed as sys._MEIPASS at runtime.
        os.chdir(sys._MEIPASS)
    else:
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from PyQt5.QtWidgets import QApplication
    from core.main_window import ImageCompressor

    sys.excepthook = _except_hook

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Keep the app alive in the tray when the window is hidden/closed —
    # only the tray menu's "Quit" action should actually end the process.
    app.setQuitOnLastWindowClosed(False)

    # Let Ctrl+C in the terminal close cleanly instead of crashing with traceback
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    window = ImageCompressor()
    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    run()