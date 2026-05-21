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
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Scan to PC</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#111;color:#eee;font-family:system-ui,sans-serif;
     display:flex;flex-direction:column;align-items:center;
     justify-content:center;min-height:100vh;padding:24px;gap:18px}
h1{font-size:22px;font-weight:700;color:#a5b4fc}
p{font-size:13px;color:#888;text-align:center}
label.pick{display:flex;align-items:center;justify-content:center;gap:10px;
           background:#5865f2;color:#fff;font-size:16px;font-weight:600;
           border-radius:14px;padding:16px 28px;cursor:pointer;
           width:100%;max-width:340px}
label.pick input{display:none}
#preview{width:100%;max-width:340px;border-radius:14px;
         display:none;object-fit:contain;max-height:260px}
#send{background:#22c55e;color:#fff;border:none;border-radius:14px;
      font-size:16px;font-weight:600;padding:16px 28px;
      width:100%;max-width:340px;cursor:pointer;display:none}
#send:disabled{opacity:.5;cursor:default}
#msg{font-size:15px;color:#4ade80;text-align:center;min-height:22px}
#err{font-size:13px;color:#f87171;text-align:center;min-height:18px}
</style>
</head>
<body>
<h1>&#128247; Scan to PC</h1>
<p>Pick or take a photo &mdash; it lands on your PC instantly.</p>
<label class="pick">
  &#128247; Choose / Take Photo
  <input type="file" accept="image/*" capture="environment" id="f" onchange="onPick(this)"/>
</label>
<img id="preview" alt="preview"/>
<button id="send" onclick="upload()">&#11014; Send to PC</button>
<div id="msg"></div>
<div id="err"></div>
<script>
function onPick(inp){
  var file=inp.files[0]; if(!file) return;
  var r=new FileReader();
  r.onload=function(e){
    var img=document.getElementById('preview');
    img.src=e.target.result; img.style.display='block';
    document.getElementById('send').style.display='block';
    document.getElementById('msg').textContent='';
    document.getElementById('err').textContent='';
  };
  r.readAsDataURL(file);
}
function upload(){
  var file=document.getElementById('f').files[0];
  if(!file){document.getElementById('err').textContent='No file chosen.';return;}
  var fd=new FormData();
  fd.append('image',file,file.name);
  var btn=document.getElementById('send');
  btn.disabled=true;
  document.getElementById('msg').textContent='Uploading\u2026';
  document.getElementById('err').textContent='';
  fetch('/upload',{method:'POST',body:fd})
    .then(function(r){return r.json();})
    .then(function(d){
      btn.disabled=false;
      if(d.ok){
        document.getElementById('msg').textContent='\u2705 Received on PC!';
        document.getElementById('f').value='';
        document.getElementById('preview').style.display='none';
        btn.style.display='none';
      } else {
        document.getElementById('err').textContent='Error: '+(d.error||'unknown');
      }
    })
    .catch(function(e){
      btn.disabled=false;
      document.getElementById('err').textContent='Upload failed: '+e;
    });
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