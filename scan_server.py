"""
scan_server.py  –  Local-network scan-to-PC server
====================================================
Starts a tiny HTTP server on a random free port.
The phone browses to http://<PC-IP>:<port>/ and uploads photos.
The server calls `on_file_received(path)` for every image saved.
"""

import cgi
import io
import os
import socket
import threading
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Upload page
# ---------------------------------------------------------------------------
_HTML = b"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1"/>
<title>Scan to PC</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--accent:#5865f2;--green:#22c55e;--red:#f87171;--bg:#0f0f13;--card:#1a1a20;--border:rgba(255,255,255,0.08)}
body{background:var(--bg);color:#eee;font-family:system-ui,sans-serif;
     min-height:100vh;padding:20px 16px 40px;display:flex;flex-direction:column;align-items:center;gap:16px}

/* header */
.header{display:flex;flex-direction:column;align-items:center;gap:4px;padding-top:8px}
.header .icon{font-size:40px;line-height:1}
.header h1{font-size:20px;font-weight:700;color:#fff}
.header p{font-size:12px;color:rgba(255,255,255,0.4);text-align:center}

/* pick buttons row */
.pick-row{display:flex;gap:10px;width:100%;max-width:360px}
.pick-btn{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
          gap:6px;background:var(--card);border:1px solid var(--border);border-radius:16px;
          padding:18px 10px;cursor:pointer;transition:background .15s}
.pick-btn:active{background:rgba(88,101,242,0.2)}
.pick-btn .ico{font-size:28px}
.pick-btn span{font-size:12px;color:rgba(255,255,255,0.55);font-weight:500}
.pick-btn input{display:none}

/* preview grid */
#grid{display:none;width:100%;max-width:360px;flex-direction:column;gap:10px}
.grid-inner{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.thumb{position:relative;aspect-ratio:1;border-radius:10px;overflow:hidden;background:#222}
.thumb img{width:100%;height:100%;object-fit:cover}
.thumb .rm{position:absolute;top:3px;right:3px;width:20px;height:20px;border-radius:50%;
           background:rgba(0,0,0,0.7);border:none;color:#fff;font-size:13px;
           display:flex;align-items:center;justify-content:center;cursor:pointer}
.count-bar{display:flex;justify-content:space-between;align-items:center}
.count-bar span{font-size:12px;color:rgba(255,255,255,0.4)}
.add-more{font-size:12px;color:var(--accent);font-weight:600;cursor:pointer;
          background:none;border:none;padding:0}

/* progress */
#prog-wrap{display:none;width:100%;max-width:360px;flex-direction:column;gap:6px}
.prog-file{font-size:11px;color:rgba(255,255,255,0.5);white-space:nowrap;
           overflow:hidden;text-overflow:ellipsis}
.prog-track{height:5px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden}
.prog-fill{height:100%;width:0%;background:var(--accent);border-radius:3px;transition:width .1s}
.prog-size{font-size:11px;color:rgba(255,255,255,0.3);text-align:right}

/* send button */
#send{display:none;width:100%;max-width:360px;padding:16px;background:var(--green);
      color:#fff;border:none;border-radius:16px;font-size:16px;font-weight:700;cursor:pointer;
      transition:opacity .15s}
#send:disabled{opacity:.45;cursor:default}

/* last preview */
#last-wrap{display:none;width:100%;max-width:360px;flex-direction:column;gap:8px;
           background:var(--card);border:1px solid var(--border);border-radius:16px;padding:12px}
#last-wrap .lbl{font-size:11px;color:rgba(255,255,255,0.3);letter-spacing:.5px}
#last-img{width:100%;max-height:160px;object-fit:contain;border-radius:10px}
#last-name{font-size:12px;color:rgba(255,255,255,0.5);margin-top:4px}

/* status */
#msg{font-size:14px;color:var(--green);text-align:center;min-height:20px;font-weight:600}
#err{font-size:12px;color:var(--red);text-align:center;min-height:16px}
</style>
</head>
<body>

<div class="header">
  <div class="icon">&#128247;</div>
  <h1>Scan to PC</h1>
  <p>Select photos or use camera &mdash; send multiple at once</p>
</div>

<!-- Pick buttons -->
<div class="pick-row" id="pick-row">
  <label class="pick-btn">
    <div class="ico">&#128247;</div>
    <span>Camera</span>
    <input type="file" accept="image/*" capture="environment" multiple onchange="onPick(this)"/>
  </label>
  <label class="pick-btn">
    <div class="ico">&#128444;</div>
    <span>Gallery</span>
    <input type="file" accept="image/*" multiple onchange="onPick(this)"/>
  </label>
</div>

<!-- Preview grid -->
<div id="grid">
  <div class="count-bar">
    <span id="count-lbl">0 photos selected</span>
    <button class="add-more" onclick="document.getElementById('add-input').click()">+ Add more</button>
    <input id="add-input" type="file" accept="image/*" multiple style="display:none" onchange="onPick(this)"/>
  </div>
  <div class="grid-inner" id="grid-inner"></div>
</div>

<!-- Progress -->
<div id="prog-wrap">
  <div class="prog-file" id="prog-file">Preparing...</div>
  <div class="prog-track"><div class="prog-fill" id="prog-fill"></div></div>
  <div class="prog-size" id="prog-size"></div>
</div>

<!-- Send -->
<button id="send" onclick="uploadAll()">&#11014; Send to PC</button>

<!-- Last received preview -->
<div id="last-wrap">
  <div class="lbl">LAST RECEIVED</div>
  <img id="last-img" alt="last"/>
  <div id="last-name"></div>
</div>

<div id="msg"></div>
<div id="err"></div>

<script>
var files = [];

function onPick(inp) {
  var picked = Array.from(inp.files);
  picked.forEach(function(f) {
    if (!files.find(function(x){return x.name===f.name && x.size===f.size;}))
      files.push(f);
  });
  inp.value = '';
  renderGrid();
}

function renderGrid() {
  var inner = document.getElementById('grid-inner');
  inner.innerHTML = '';
  files.forEach(function(f, i) {
    var url = URL.createObjectURL(f);
    var div = document.createElement('div');
    div.className = 'thumb';
    div.innerHTML = '<img src="'+url+'"/><button class="rm" onclick="removeFile('+i+')">&#10005;</button>';
    inner.appendChild(div);
  });
  document.getElementById('count-lbl').textContent = files.length + ' photo' + (files.length!==1?'s':'') + ' selected';
  document.getElementById('grid').style.display = files.length ? 'flex' : 'none';
  document.getElementById('send').style.display = files.length ? 'block' : 'none';
  document.getElementById('msg').textContent = '';
  document.getElementById('err').textContent = '';
}

function removeFile(i) {
  files.splice(i, 1);
  renderGrid();
}

function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
  return (b/1024/1024).toFixed(1) + ' MB';
}

function uploadAll() {
  if (!files.length) return;
  var btn = document.getElementById('send');
  btn.disabled = true;
  document.getElementById('err').textContent = '';
  document.getElementById('msg').textContent = '';

  var total = files.length;
  var done = 0;

  function uploadOne(i) {
    if (i >= files.length) {
      // all done
      document.getElementById('prog-wrap').style.display = 'none';
      document.getElementById('msg').textContent = '\u2705 All ' + total + ' photo' + (total!==1?'s':'') + ' received on PC!';
      btn.disabled = false;
      files = [];
      renderGrid();
      return;
    }
    var f = files[i];
    var pw = document.getElementById('prog-wrap');
    pw.style.display = 'flex';
    document.getElementById('prog-file').textContent = (i+1) + '/' + total + '  \u2014  ' + f.name;
    document.getElementById('prog-size').textContent = fmtSize(f.size);
    document.getElementById('prog-fill').style.width = '0%';

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');

    xhr.upload.onprogress = function(e) {
      if (e.lengthComputable) {
        var pct = Math.round(e.loaded / e.total * 100);
        document.getElementById('prog-fill').style.width = pct + '%';
        document.getElementById('prog-size').textContent = fmtSize(e.loaded) + ' / ' + fmtSize(e.total);
      }
    };

    xhr.onload = function() {
      document.getElementById('prog-fill').style.width = '100%';
      try {
        var d = JSON.parse(xhr.responseText);
        if (d.ok) {
          // show last preview
          var lw = document.getElementById('last-wrap');
          lw.style.display = 'flex';
          document.getElementById('last-img').src = URL.createObjectURL(f);
          document.getElementById('last-name').textContent = f.name + '  \u00b7  ' + fmtSize(f.size);
          done++;
          uploadOne(i + 1);
        } else {
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
    fd.append('image', f, f.name);
    xhr.send(fd);
  }

  uploadOne(0);
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
    on_file_received = None   # set by ScanServer

    def log_message(self, fmt, *args):
        pass  # keep console quiet

    # -- GET /  --------------------------------------------------------------
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_HTML)))
            self.end_headers()
            self.wfile.write(_HTML)
        else:
            self.send_response(404)
            self.end_headers()

    # -- POST /upload  -------------------------------------------------------
    def do_POST(self):
        if self.path != "/upload":
            self.send_response(404)
            self.end_headers()
            return

        # Use stdlib cgi.FieldStorage — handles all boundary variants correctly
        env = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE":   self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        }
        # FieldStorage needs a file-like body
        body_len = int(env["CONTENT_LENGTH"])
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

        # find the 'image' field
        item = fs.getvalue("image")           # bytes if file upload
        if item is None:
            # FieldStorage may store it as a MiniFieldStorage or list
            raw = fs["image"] if "image" in fs else None
            if raw is None:
                self._json({"ok": False, "error": "no 'image' field in form"}, 400)
                return
            item = raw.file.read() if hasattr(raw, "file") else None

        if not item:
            self._json({"ok": False, "error": "empty file"}, 400)
            return

        # determine extension
        filename = ""
        if "image" in fs and hasattr(fs["image"], "filename"):
            filename = fs["image"].filename or ""
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"):
            ext = ".jpg"

        # save
        os.makedirs(self.save_dir, exist_ok=True)
        unique = f"scan_{uuid.uuid4().hex[:8]}{ext}"
        out_path = os.path.join(self.save_dir, unique)
        with open(out_path, "wb") as fh:
            fh.write(item if isinstance(item, (bytes, bytearray)) else item.read())

        if callable(self.on_file_received):
            try:
                self.on_file_received(out_path)
            except Exception:
                pass

        self._json({"ok": True, "file": unique})

    def _json(self, obj, code=200):
        import json
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _local_ip():
    """Best-effort LAN IP (not loopback)."""
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
        qr = qrcode.QRCode(version=None,
                           error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=8, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB")
    except ImportError:
        return None


class ScanServer:
    """
    Thread-safe HTTP server wrapper.

    Parameters
    ----------
    on_file_received : callable(path: str)
        Called from the server background thread when a file is saved.
        Use a Qt signal emit inside it to safely update the UI.
    save_dir : str
        Where incoming images are stored.
    """

    def __init__(self, on_file_received=None, save_dir="scanned"):
        self._callback = on_file_received
        self._save_dir = save_dir
        self._server   = None
        self._thread   = None
        self.port      = None
        self.ip        = None
        self.url       = None
        self.qr_pil    = None

    def start(self):
        if self._server is not None:
            return

        self.port = _free_port()
        self.ip   = _local_ip()
        self.url  = f"http://{self.ip}:{self.port}/"

        callback = self._callback
        save_dir = self._save_dir

        # Build a per-instance Handler class so class attributes don't bleed
        class Handler(_Handler):
            pass
        Handler.save_dir         = save_dir
        Handler.on_file_received = callback   # plain reference, NOT staticmethod()

        self._server = HTTPServer(("", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        self.qr_pil = _make_qr(self.url)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
        self._thread = None

    @property
    def running(self):
        return self._server is not None
