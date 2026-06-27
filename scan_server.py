"""
scan_server.py  –  Local-network scan-to-PC server + shared clipboard
======================================================================
Starts a tiny HTTP server on a random free port.
The phone browses to http://<PC-IP>:<port>/ and can:

  • Send text   → goes silently into the PC clipboard (pyperclip)
  • Send images → camera or gallery, saved to disk, on_file_received() called
  • Send files  → any file, saved to disk, on_file_received() called

Callbacks
---------
on_file_received(path: str)   – called for every image/file saved
on_text_received(text: str)   – called when phone sends text to clipboard
"""

import cgi
import io
import json
import os
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Clipboard helper
# ---------------------------------------------------------------------------
try:
    import pyperclip
    HAS_CLIPBOARD = True
except ImportError:
    HAS_CLIPBOARD = False


def _set_clipboard(text: str) -> bool:
    """Copy text to the PC clipboard. Returns True on success."""
    if not HAS_CLIPBOARD:
        return False
    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------
_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1"/>
<title>Send to PC</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --accent:#5865f2;--green:#22c55e;--red:#f87171;
  --bg:#0f0f13;--card:#1a1a20;--border:rgba(255,255,255,0.08)
}
body{
  background:var(--bg);color:#eee;font-family:system-ui,sans-serif;
  min-height:100vh;padding:20px 16px 48px;
  display:flex;flex-direction:column;align-items:center;gap:14px
}

/* header */
.header{display:flex;flex-direction:column;align-items:center;gap:4px;padding-top:6px}
.header .icon{font-size:36px;line-height:1}
.header h1{font-size:19px;font-weight:700;color:#fff}
.header p{font-size:12px;color:rgba(255,255,255,0.35);text-align:center}

/* section label */
.sec-lbl{
  width:100%;max-width:360px;
  font-size:10px;font-weight:600;letter-spacing:1px;
  color:rgba(255,255,255,0.25);text-transform:uppercase;
  padding-left:2px
}

/* ── TEXT SECTION ── */
#text-card{
  width:100%;max-width:360px;
  background:var(--card);border:1px solid var(--border);
  border-radius:16px;overflow:hidden
}
#text-input{
  width:100%;min-height:110px;padding:14px;
  background:transparent;border:none;outline:none;
  color:#fff;font-size:15px;font-family:inherit;resize:vertical;
  line-height:1.5
}
#text-input::placeholder{color:rgba(255,255,255,0.20)}
#text-footer{
  display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;border-top:1px solid var(--border)
}
#char-count{font-size:11px;color:rgba(255,255,255,0.25)}
#send-text{
  padding:7px 18px;background:var(--accent);
  border:none;border-radius:10px;color:#fff;
  font-size:13px;font-weight:600;cursor:pointer;transition:opacity .15s
}
#send-text:disabled{opacity:.4;cursor:default}
#send-text:active{opacity:.75}

/* ── DIVIDER ── */
.or-row{
  display:flex;align-items:center;gap:10px;
  width:100%;max-width:360px
}
.or-row hr{flex:1;border:none;border-top:1px solid rgba(255,255,255,0.07)}
.or-row span{font-size:11px;color:rgba(255,255,255,0.20)}

/* ── FILE / IMAGE SECTION ── */
.pick-row{display:flex;gap:10px;width:100%;max-width:360px}
.pick-btn{
  flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:6px;
  background:var(--card);border:1px solid var(--border);
  border-radius:16px;padding:16px 10px;cursor:pointer;transition:background .15s
}
.pick-btn:active{background:rgba(88,101,242,0.18)}
.pick-btn .ico{font-size:26px}
.pick-btn span{font-size:11px;color:rgba(255,255,255,0.50);font-weight:500}
.pick-btn input{display:none}

/* queue list */
#queue{display:none;width:100%;max-width:360px;flex-direction:column;gap:6px}
.q-header{display:flex;justify-content:space-between;align-items:center}
.q-header span{font-size:12px;color:rgba(255,255,255,0.40)}
.q-clear{font-size:12px;color:var(--red);background:none;border:none;cursor:pointer;font-weight:600}
.q-list{display:flex;flex-direction:column;gap:4px}
.q-item{
  display:flex;align-items:center;gap:10px;
  background:var(--card);border:1px solid var(--border);
  border-radius:10px;padding:8px 10px
}
.q-thumb{width:36px;height:36px;border-radius:6px;object-fit:cover;background:#222;flex-shrink:0}
.q-thumb.file-icon{display:flex;align-items:center;justify-content:center;font-size:20px}
.q-info{flex:1;min-width:0}
.q-name{font-size:12px;color:rgba(255,255,255,0.80);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.q-size{font-size:10px;color:rgba(255,255,255,0.30);margin-top:2px}
.q-rm{background:none;border:none;color:rgba(255,255,255,0.30);font-size:16px;cursor:pointer;padding:0 4px}
.q-rm:active{color:var(--red)}

/* progress */
#prog-wrap{display:none;width:100%;max-width:360px;flex-direction:column;gap:6px}
.prog-file{font-size:11px;color:rgba(255,255,255,0.45);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prog-track{height:4px;background:rgba(255,255,255,0.07);border-radius:3px;overflow:hidden}
.prog-fill{height:100%;width:0%;background:var(--accent);border-radius:3px;transition:width .08s}
.prog-size{font-size:10px;color:rgba(255,255,255,0.25);text-align:right}

/* send files button */
#send-files{
  display:none;width:100%;max-width:360px;padding:15px;
  background:var(--green);color:#fff;border:none;
  border-radius:16px;font-size:15px;font-weight:700;cursor:pointer;transition:opacity .15s
}
#send-files:disabled{opacity:.4;cursor:default}
#send-files:active{opacity:.8}

/* feedback */
#msg{font-size:13px;color:var(--green);text-align:center;min-height:18px;font-weight:600}
#err{font-size:12px;color:var(--red);text-align:center;min-height:16px}
</style>
</head>
<body>

<div class="header">
  <div class="icon">&#128187;</div>
  <h1>Send to PC</h1>
  <p>Text goes to clipboard &middot; files save to disk</p>
</div>

<!-- TEXT -->
<div class="sec-lbl">Clipboard</div>
<div id="text-card">
  <textarea id="text-input" placeholder="Type or paste text here…" oninput="onTextInput()"></textarea>
  <div id="text-footer">
    <span id="char-count">0 chars</span>
    <button id="send-text" disabled onclick="sendText()">Copy to PC</button>
  </div>
</div>

<!-- OR divider -->
<div class="or-row"><hr/><span>OR</span><hr/></div>

<!-- FILES / IMAGES -->
<div class="sec-lbl">Files &amp; Photos</div>
<div class="pick-row">
  <label class="pick-btn">
    <div class="ico">&#128247;</div>
    <span>Camera</span>
    <input type="file" accept="image/*" capture="environment" multiple onchange="onPick(this)"/>
  </label>
  <label class="pick-btn">
    <div class="ico">&#128444;&#65039;</div>
    <span>Gallery</span>
    <input type="file" accept="image/*" multiple onchange="onPick(this)"/>
  </label>
  <label class="pick-btn">
    <div class="ico">&#128196;</div>
    <span>File</span>
    <input type="file" accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv" multiple onchange="onPick(this)"/>
  </label>
</div>

<!-- Queue -->
<div id="queue">
  <div class="q-header">
    <span id="q-count">0 items</span>
    <button class="q-clear" onclick="clearQueue()">Clear all</button>
  </div>
  <div class="q-list" id="q-list"></div>
</div>

<!-- Progress -->
<div id="prog-wrap">
  <div class="prog-file" id="prog-file">Preparing…</div>
  <div class="prog-track"><div class="prog-fill" id="prog-fill"></div></div>
  <div class="prog-size" id="prog-size"></div>
</div>

<!-- Send files button -->
<button id="send-files" onclick="sendFiles()">&#11014;&#65039; Send to PC</button>

<div id="msg"></div>
<div id="err"></div>

<script>
var queue = [];   // {file, isImage}

// ── text ──────────────────────────────────────────────────────────────────
function onTextInput() {
  var v = document.getElementById('text-input').value;
  document.getElementById('char-count').textContent = v.length + ' char' + (v.length!==1?'s':'');
  document.getElementById('send-text').disabled = v.trim().length === 0;
  clearFeedback();
}

function sendText() {
  var text = document.getElementById('text-input').value.trim();
  if (!text) return;
  var btn = document.getElementById('send-text');
  btn.disabled = true;
  btn.textContent = 'Sending…';
  fetch('/clipboard', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: text})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (d.ok) {
      document.getElementById('msg').textContent = '\\u2705 Copied to PC clipboard!';
      document.getElementById('text-input').value = '';
      document.getElementById('char-count').textContent = '0 chars';
    } else {
      document.getElementById('err').textContent = d.error || 'Failed';
    }
    btn.textContent = 'Copy to PC';
    btn.disabled = false;
  })
  .catch(function(){
    document.getElementById('err').textContent = 'Connection failed';
    btn.textContent = 'Copy to PC';
    btn.disabled = false;
  });
}

// ── files ─────────────────────────────────────────────────────────────────
var MAX_BYTES = 500 * 1024 * 1024;

function onPick(inp) {
  var skipped = 0;
  var blocked = 0;
  Array.from(inp.files).forEach(function(f) {
    if (f.type.startsWith('video/')) { blocked++; return; }
    if (f.size > MAX_BYTES) { skipped++; return; }
    if (!queue.find(function(x){ return x.file.name===f.name && x.file.size===f.size; })) {
      var isImg = f.type.startsWith('image/');
      queue.push({file: f, isImage: isImg});
    }
  });
  inp.value = '';
  renderQueue();
  clearFeedback();
  if (blocked > 0) {
    document.getElementById('err').textContent =
      blocked + ' video file' + (blocked!==1?'s':'') + ' not allowed \u2014 only images, text & docs';
  } else if (skipped > 0) {
    document.getElementById('err').textContent =
      skipped + ' file' + (skipped!==1?'s':'') + ' skipped \u2014 max 500 MB per file';
  }
}

function renderQueue() {
  var list = document.getElementById('q-list');
  list.innerHTML = '';
  queue.forEach(function(item, i) {
    var f = item.file;
    var row = document.createElement('div');
    row.className = 'q-item';
    var thumbHTML;
    if (item.isImage) {
      var url = URL.createObjectURL(f);
      thumbHTML = '<img class="q-thumb" src="'+url+'"/>';
    } else {
      thumbHTML = '<div class="q-thumb file-icon">&#128196;</div>';
    }
    row.innerHTML = thumbHTML +
      '<div class="q-info">' +
        '<div class="q-name">'+escHtml(f.name)+'</div>' +
        '<div class="q-size">'+fmtSize(f.size)+'</div>' +
      '</div>' +
      '<button class="q-rm" onclick="removeItem('+i+')">&#10005;</button>';
    list.appendChild(row);
  });
  document.getElementById('q-count').textContent =
    queue.length + ' item' + (queue.length!==1?'s':'');
  document.getElementById('queue').style.display     = queue.length ? 'flex' : 'none';
  document.getElementById('send-files').style.display = queue.length ? 'block' : 'none';
}

function removeItem(i) { queue.splice(i, 1); renderQueue(); }
function clearQueue()  { queue = []; renderQueue(); }

function sendFiles() {
  if (!queue.length) return;
  var btn = document.getElementById('send-files');
  btn.disabled = true;
  clearFeedback();
  var total = queue.length;
  var done  = 0;

  function sendOne(i) {
    if (i >= queue.length) {
      document.getElementById('prog-wrap').style.display = 'none';
      document.getElementById('msg').textContent =
        '\\u2705 ' + total + ' file' + (total!==1?'s':'') + ' received on PC!';
      btn.disabled = false;
      queue = [];
      renderQueue();
      return;
    }
    var f = queue[i].file;
    setProgress(i+1, total, f.name, f.size);

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.upload.onprogress = function(e) {
      if (e.lengthComputable) {
        var pct = Math.round(e.loaded / e.total * 100);
        document.getElementById('prog-fill').style.width = pct + '%';
        document.getElementById('prog-size').textContent =
          fmtSize(e.loaded) + ' / ' + fmtSize(f.size);
      }
    };
    xhr.onload = function() {
      try {
        var d = JSON.parse(xhr.responseText);
        if (d.ok) { done++; sendOne(i+1); }
        else {
          document.getElementById('err').textContent = 'Failed: ' + (d.error||'unknown');
          btn.disabled = false;
        }
      } catch(e) {
        document.getElementById('err').textContent = 'Server error';
        btn.disabled = false;
      }
    };
    xhr.onerror = function() {
      document.getElementById('err').textContent = 'Connection failed';
      btn.disabled = false;
    };
    var fd = new FormData();
    fd.append('file', f, f.name);
    xhr.send(fd);
  }
  sendOne(0);
}

// ── helpers ───────────────────────────────────────────────────────────────
function setProgress(cur, total, name, size) {
  var pw = document.getElementById('prog-wrap');
  pw.style.display = 'flex';
  document.getElementById('prog-file').textContent = cur+'/'+total+'  —  '+name;
  document.getElementById('prog-size').textContent = fmtSize(size);
  document.getElementById('prog-fill').style.width = '0%';
}
function clearFeedback() {
  document.getElementById('msg').textContent = '';
  document.getElementById('err').textContent = '';
}
function fmtSize(b) {
  if (b < 1024)       return b+' B';
  if (b < 1048576)    return (b/1024).toFixed(1)+' KB';
  return (b/1048576).toFixed(1)+' MB';
}
function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    save_dir         = "scanned"
    on_file_received = None   # callable(path: str) or None
    on_text_received = None   # callable(text: str) or None
    received_files   = None   # list[dict]
    _lock            = None   # threading.Lock

    def log_message(self, fmt, *args):
        pass  # keep console quiet

    def handle_error(self, request, client_address):
        # Suppress ConnectionResetError (WinError 10054) — mobile browsers
        # aggressively close connections mid-request; this is harmless noise.
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionResetError):
            return
        super().handle_error(request, client_address)

    # -- GET / ---------------------------------------------------------------
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = _HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/files":
            with self._lock:
                data = list(self.received_files)
            self._json({"ok": True, "files": data})
        else:
            self.send_response(404)
            self.end_headers()

    # -- POST ----------------------------------------------------------------
    def do_POST(self):
        if self.path == "/clipboard":
            self._handle_clipboard()
        elif self.path == "/upload":
            self._handle_upload()
        elif self.path == "/delete":
            self._handle_delete()
        else:
            self.send_response(404)
            self.end_headers()

    # -- /clipboard ----------------------------------------------------------
    def _handle_clipboard(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "bad JSON"}, 400)
            return

        text = body.get("text", "")
        if not isinstance(text, str) or not text.strip():
            self._json({"ok": False, "error": "empty text"}, 400)
            return

        # Set PC clipboard
        ok = _set_clipboard(text)
        if not ok and not HAS_CLIPBOARD:
            self._json({"ok": False,
                        "error": "pyperclip not installed — run: pip install pyperclip"}, 501)
            return

        # Fire callback so the UI can react (e.g. flash the tray icon)
        if callable(self.on_text_received):
            try:
                self.on_text_received(text)
            except Exception:
                pass

        self._json({"ok": True})

    _MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

    # -- /upload -------------------------------------------------------------
    def _handle_upload(self):
        env = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE":   self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        }
        body_len = int(env["CONTENT_LENGTH"])

        if body_len > self._MAX_UPLOAD_BYTES:
            self._json({"ok": False,
                        "error": f"File too large — max 500 MB"}, 413)
            return

        body_data = self.rfile.read(body_len)

        try:
            fs = cgi.FieldStorage(
                fp=io.BytesIO(body_data),
                headers=self.headers,
                environ=env,
                keep_blank_values=True,
            )
        except Exception as ex:
            self._json({"ok": False, "error": f"parse error: {ex}"}, 400)
            return

        # Accept field named 'file' (new) or 'image' (legacy)
        field_name = "file" if "file" in fs else ("image" if "image" in fs else None)
        if field_name is None:
            self._json({"ok": False, "error": "no file field in form"}, 400)
            return

        item = fs.getvalue(field_name)
        if item is None:
            raw = fs[field_name]
            item = raw.file.read() if hasattr(raw, "file") else None

        if not item:
            self._json({"ok": False, "error": "empty file"}, 400)
            return

        # Original filename + extension
        filename = ""
        if hasattr(fs[field_name], "filename"):
            filename = fs[field_name].filename or ""
        ext = os.path.splitext(filename)[1].lower()
        if not ext:
            ext = ".bin"

        _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv",
                       ".wmv", ".m4v", ".3gp", ".mpeg", ".mpg"}
        if ext in _VIDEO_EXTS:
            self._json({"ok": False,
                        "error": "Video files are not allowed — only images, text & docs"}, 415)
            return

        os.makedirs(self.save_dir, exist_ok=True)
        unique   = f"scan_{uuid.uuid4().hex[:8]}{ext}"
        out_path = os.path.join(self.save_dir, unique)
        with open(out_path, "wb") as fh:
            fh.write(item if isinstance(item, (bytes, bytearray)) else item.read())

        size_kb = os.path.getsize(out_path) / 1024
        entry   = {
            "file":          unique,
            "path":          out_path,
            "size_kb":       round(size_kb, 1),
            "original_name": filename or unique,
        }
        with self._lock:
            self.received_files.append(entry)

        if callable(self.on_file_received):
            try:
                self.on_file_received(out_path)
            except Exception:
                pass

        self._json({"ok": True, "file": unique})

    # -- /delete -------------------------------------------------------------
    def _handle_delete(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "bad JSON"}, 400)
            return
        fname = body.get("file", "")
        with self._lock:
            self.received_files[:] = [
                f for f in self.received_files if f["file"] != fname
            ]
        full = os.path.join(self.save_dir, fname)
        try:
            if os.path.exists(full):
                os.remove(full)
        except Exception:
            pass
        self._json({"ok": True})

    # -- helper --------------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _make_qr(url: str):
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8, border=3,
        )
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB")
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ScanServer:
    """
    Thread-safe HTTP server for phone → PC transfers.

    Parameters
    ----------
    on_file_received : callable(path: str), optional
        Called (from background thread) when a file is saved to disk.
        Use a Qt signal emit inside to update the UI safely.
    on_text_received : callable(text: str), optional
        Called (from background thread) when text is copied to the PC clipboard.
        Use a Qt signal emit inside to update the UI safely.
    save_dir : str
        Directory where incoming files are stored.

    Attributes
    ----------
    url            : str            — local HTTP URL
    qr_pil         : PIL Image|None — QR code for the URL
    received_files : list[dict]     — grows as files arrive
    clipboard      : bool           — True if pyperclip is available
    """

    def __init__(self, on_file_received=None, on_text_received=None,
                 save_dir="scanned"):
        self._file_cb       = on_file_received
        self._text_cb       = on_text_received
        self._save_dir      = save_dir
        self._server        = None
        self._thread        = None
        self.port           = None
        self.ip             = None
        self.url            = None
        self.qr_pil         = None
        self.clipboard      = HAS_CLIPBOARD
        self.received_files = []
        self._lock          = threading.Lock()

    def start(self):
        if self._server is not None:
            return

        self.port = _free_port()
        self.ip   = _local_ip()
        self.url  = f"http://{self.ip}:{self.port}/"

        file_cb        = self._file_cb
        text_cb        = self._text_cb
        save_dir       = self._save_dir
        received_files = self.received_files
        lock           = self._lock

        class Handler(_Handler):
            pass
        Handler.save_dir         = save_dir
        Handler.on_file_received = file_cb
        Handler.on_text_received = text_cb
        Handler.received_files   = received_files
        Handler._lock            = lock

        self._server = HTTPServer(("", self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # Build QR in background — never blocks the caller
        threading.Thread(target=self._build_qr, daemon=True).start()

    def _build_qr(self):
        self.qr_pil = _make_qr(self.url)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._server is not None
