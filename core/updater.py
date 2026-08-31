import hashlib
import os
import tempfile

from PyQt5.QtCore import QThread, pyqtSignal

from core.version import VERSION

# Point this at your hosted manifest.json (e.g. a GitHub Release asset URL).
MANIFEST_URL = "http://127.0.0.1:8000/manifest.json"


def _version_tuple(v):
    """Turn '1.2.10' into (1, 2, 10) so version comparison is numeric,
    not lexicographic (avoids '1.9.0' > '1.10.0' bugs)."""
    return tuple(int(x) for x in v.split("."))


# =========================================
# UPDATE CHECKER
# =========================================
# Fetches the manifest in a background thread and emits update_available
# if the manifest's version is newer than the running app's VERSION.

class UpdateChecker(QThread):
    update_available = pyqtSignal(dict)   # emits the manifest dict
    no_update         = pyqtSignal()      # emits when already on the latest version
    check_failed      = pyqtSignal(str)

    def run(self):
        try:
            import requests
            resp = requests.get(MANIFEST_URL, timeout=8)
            resp.raise_for_status()
            manifest = resp.json()
            if _version_tuple(manifest["version"]) > _version_tuple(VERSION):
                self.update_available.emit(manifest)
            else:
                self.no_update.emit()
        except Exception as ex:
            # Fails silently in the UI (no popup) — an offline machine or a
            # flaky request shouldn't nag the user every launch. Callers can
            # still hook check_failed for logging if desired.
            self.check_failed.emit(str(ex))


# =========================================
# UPDATE DOWNLOADER
# =========================================
# Downloads the new installer to a temp file, reporting progress, and
# verifies its SHA-256 against the manifest before handing back the path.

class UpdateDownloader(QThread):
    progress    = pyqtSignal(int)     # 0-100
    finished_ok = pyqtSignal(str)     # path to the downloaded installer
    failed      = pyqtSignal(str)

    def __init__(self, manifest):
        super().__init__()
        self.manifest = manifest

    def run(self):
        try:
            import requests

            url = self.manifest["url"]
            expected_sha = self.manifest["sha256"]

            dest = os.path.join(tempfile.gettempdir(), "YourAppName-Setup.exe")
            resp = requests.get(url, stream=True, timeout=30)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            sha = hashlib.sha256()

            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)
                    if total:
                        self.progress.emit(int(downloaded * 100 / total))

            if sha.hexdigest() != expected_sha:
                self.failed.emit("Checksum mismatch — download may be corrupted.")
                return

            self.finished_ok.emit(dest)
        except Exception as ex:
            self.failed.emit(str(ex))


# =========================================
# INSTALLER RUNNER
# =========================================
# Runs the downloaded installer silently and blocks (in this background
# thread, not the UI thread) until it exits, since a silent Inno Setup
# install is a synchronous process we can simply wait on rather than
# fire-and-forget. This is what lets the dialog show "Installing…" and
# then reliably flip to "Restart" only once the install has actually
# finished, instead of guessing.

class InstallerRunner(QThread):
    finished_ok = pyqtSignal()
    failed      = pyqtSignal(str)

    def __init__(self, installer_path):
        super().__init__()
        self.installer_path = installer_path

    def run(self):
        import subprocess
        try:
            proc = subprocess.run(
                [self.installer_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                timeout=300,
            )
            if proc.returncode == 0:
                self.finished_ok.emit()
            else:
                self.failed.emit(f"Installer exited with code {proc.returncode}")
        except Exception as ex:
            self.failed.emit(str(ex))