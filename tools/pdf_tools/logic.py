from PyQt5.QtCore import QObject, pyqtSignal


# =========================================
# PDF WORKER
# =========================================
# Generic background-thread wrapper: runs any callable (the actual PDF
# operations — merge, split, organize, etc. — live in ui/pdf_panel.py and
# ui/pdf_canvas.py, and are passed in here as `fn`).

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
