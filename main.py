import sys
import signal
from PyQt5.QtWidgets import QApplication
from main_window import ImageCompressor

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