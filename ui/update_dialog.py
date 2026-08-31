import os
import subprocess
import sys
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QApplication
)
from PyQt5.QtGui import QCursor

from core.updater import UpdateDownloader, InstallerRunner
from ui.styles import (
    ACTION_BTN_STYLE, SECONDARY_BTN_STYLE, PROGRESS_STYLE,
    PDF_HEADER_STYLE, PDF_SUBTITLE_STYLE, PDF_STATUS_OK, PDF_STATUS_ERR
)


class UpdateFlyout(QWidget):
    """Small anchored dropdown shown below the update icon — same visual
    pattern as QrFlyout (frameless, rounded card, Qt.Popup so it closes
    itself the moment the user clicks anywhere outside it), instead of a
    separate modal window with its own titlebar/taskbar entry.

    Walks through, in place inside this one popup:
        info  ->  downloading  ->  installing  ->  restart
    """

    def __init__(self, manifest, anchor_btn, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self._anchor_btn = anchor_btn

        # Only meaningful once packaged with PyInstaller — while running
        # from source, sys.frozen is unset and there's nothing sane to
        # relaunch, so the Restart button just closes the app instead.
        self._current_exe_path = sys.executable if getattr(sys, "frozen", False) else None

        # Qt.Popup = auto-closes on outside click / Escape, exactly like a
        # dropdown menu. WA_DeleteOnClose avoids leaking one instance per
        # click since the caller doesn't hold a long-lived reference.
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("updateCard")
        card.setFixedWidth(320)
        card.setStyleSheet("""
            QFrame#updateCard {
                background: #1e1e22;
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.10);
            }
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(12)

        title = QLabel(f"Version {manifest.get('version', '')} is available")
        title.setStyleSheet(PDF_HEADER_STYLE)
        title.setWordWrap(True)
        cl.addWidget(title)

        changelog = QLabel(manifest.get("changelog") or "No changelog provided.")
        changelog.setStyleSheet(PDF_SUBTITLE_STYLE)
        changelog.setWordWrap(True)
        cl.addWidget(changelog)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(PDF_SUBTITLE_STYLE)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.hide()
        cl.addWidget(self.status_lbl)

        self.progress = QProgressBar()
        self.progress.setStyleSheet(PROGRESS_STYLE)
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.hide()
        cl.addWidget(self.progress)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("Later")
        self.cancel_btn.setStyleSheet(SECONDARY_BTN_STYLE)
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cancel_btn.clicked.connect(self.close)
        btn_row.addWidget(self.cancel_btn)

        self.action_btn = QPushButton("Download && Install")
        self.action_btn.setStyleSheet(ACTION_BTN_STYLE)
        self.action_btn.setFixedHeight(36)
        self.action_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.action_btn.setProperty("stage", "download")
        self.action_btn.clicked.connect(self._on_action_clicked)
        btn_row.addWidget(self.action_btn)

        cl.addLayout(btn_row)
        outer.addWidget(card)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        # Record when this popup closed, on the button that opened it —
        # lets the button's click handler tell "the click that just closed
        # this popup" apart from "a fresh click asking to reopen it" (see
        # note in main_window._on_update_btn_clicked).
        self._anchor_btn.setProperty("_flyout_closed_at", time.time())
        super().closeEvent(event)

    def show_below(self, screen_geo):
        """Position this flyout right-aligned under its anchor button —
        the button sits at the far right of the top bar, so centering
        (like QrFlyout does) would push part of the card off-screen."""
        self.adjustSize()
        self.show()
        btn_global = self._anchor_btn.mapToGlobal(self._anchor_btn.rect().bottomRight())
        x = btn_global.x() - self.width()
        y = btn_global.y() + 8
        x = max(0, min(x, screen_geo.width() - self.width()))
        y = max(0, min(y, screen_geo.height() - self.height()))
        self.move(x, y)

    # ------------------------------------------------------------------
    def _on_action_clicked(self):
        stage = self.action_btn.property("stage")
        if stage == "download":
            self._start_download()
        elif stage == "restart":
            self._restart_app()

    def _start_download(self):
        self.cancel_btn.setEnabled(False)
        self.action_btn.setEnabled(False)
        self.action_btn.setText("Downloading…")
        self.status_lbl.setStyleSheet(PDF_SUBTITLE_STYLE)
        self.status_lbl.setText("Downloading update…")
        self.status_lbl.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self.adjustSize()

        self._downloader = UpdateDownloader(self.manifest)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished_ok.connect(self._on_downloaded)
        self._downloader.failed.connect(self._on_failed)
        self._downloader.start()

    def _on_progress(self, pct):
        self.progress.setValue(pct)
        self.status_lbl.setText(f"Downloading update…  {pct}%")

    def _on_downloaded(self, installer_path):
        self.status_lbl.setText("Installing update…")
        self.progress.setRange(0, 0)  # indeterminate — a silent installer reports no progress

        self._installer_runner = InstallerRunner(installer_path)
        self._installer_runner.finished_ok.connect(self._on_installed)
        self._installer_runner.failed.connect(self._on_failed)
        self._installer_runner.start()

    def _on_installed(self):
        self.progress.hide()
        self.status_lbl.setStyleSheet(PDF_STATUS_OK)
        self.status_lbl.setText("Update installed successfully.")
        self.cancel_btn.hide()
        self.action_btn.setEnabled(True)
        self.action_btn.setText("Restart now")
        self.action_btn.setProperty("stage", "restart")
        self.adjustSize()

    def _on_failed(self, msg):
        self.progress.hide()
        self.status_lbl.setStyleSheet(PDF_STATUS_ERR)
        self.status_lbl.setText(f"Update failed: {msg}")
        self.cancel_btn.setEnabled(True)
        self.action_btn.setEnabled(True)
        self.action_btn.setText("Retry")
        self.action_btn.setProperty("stage", "download")
        self.adjustSize()

    def _restart_app(self):
        # The installer overwrote files in place at the same path, so
        # relaunching the exe we were already running from is enough —
        # no need to ask the installer where it put anything.
        if self._current_exe_path and os.path.exists(self._current_exe_path):
            subprocess.Popen([self._current_exe_path])
        QApplication.instance().quit()