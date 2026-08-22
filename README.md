# Floating Tool Bar

A floating, always-on-top desktop toolbar with five tools built in:
**Compress** (images + PDFs), **Crop**, **Background Removal**, **PDF Tools**
(merge/split/organize), and **LAN Scan/Share** (receive files from a phone
over Wi-Fi via QR code, no app needed on the phone).

This is your existing, working PyQt5 application, reorganized into the
folder architecture we agreed on. **No logic was rewritten** — every
function and class body is byte-for-byte the same as what you uploaded.
Only *where* each file lives, and the `import` lines that point between
them, were changed.

## Run it

```bash
pip install -r requirements.txt
python core/main.py
```

Notes on requirements:
- `pdf2image` needs [poppler](https://github.com/oschwartz10612/poppler-windows) installed separately on Windows and on your PATH.
- `torch` is imported first, on the main thread, in `core/main.py` — this is a deliberate workaround already in your original code, because on Windows `torch`'s DLL loading fails if it's first imported from a background thread. Don't remove that import.

## What moved where

| Original file | New location | Changed? |
|---|---|---|
| `main.py` | `core/main.py` | import path only |
| `main_window.py` | `core/main_window.py` | import paths only |
| `styles.py` | `ui/styles.py` | no change |
| `helpers.py` | `ui/helpers.py` | import path only |
| `canvases.py` | `ui/canvases.py` | import path only |
| `pdf_canvas.py` | `ui/pdf_canvas.py` | no change |
| `pdf_panel.py` | `ui/pdf_panel.py` | import paths only |
| `scan_server.py` | `tools/lan_share/server.py` | no change |
| `workers.py` → `CompressWorker` | `tools/compress/logic.py` | split out, no logic changed |
| `workers.py` → `warmup_remover`, `BgRemoveWorker`, `BgRemoveSaveWorker` | `tools/background_remove/logic.py` | split out, no logic changed |
| `workers.py` → `PdfWorker` | `tools/pdf_tools/logic.py` | split out, no logic changed |

`workers.py` was the one file actually split apart — it contained four
unrelated worker classes back to back. Each was cut along a clean class
boundary (verified there's no shared state between them except the
background-removal model cache, which stayed together in
`background_remove/logic.py`) and dropped into the tool folder it belongs to.

## Verified so far

- ✅ Every file compiles (syntax-checked)
- ✅ Every module imports successfully with real dependencies installed
  (PyQt5, Pillow, numpy, pypdf, PyPDF2, qrcode, pyperclip)
- ✅ The full app — including the 3,900-line `main_window.py` — imports
  and boots its event loop without error in a headless test, even
  *without* `torch` or `transparent-background` installed (confirming
  those optional/heavy dependencies fail gracefully, as originally designed)
- ⚠️ Not yet run on a real Windows display. Please run it for real and
  confirm the bar, drag behavior, tray, and each mode (Compress / Crop /
  BG Remove / PDF / Scan) all still work exactly as before.

## Honest note: how this app's structure differs from our original plan

Worth being upfront about, since it affects how you add future tools.

Early on we designed a **plugin-loader pattern**: the shell knows nothing
about tools, each tool lives in its own folder with a `plugin.py`
(`TOOL_NAME` + `launch()`), and clicking a button opens an **independent
window** built in `ui/windows/`.

Your actual app doesn't work that way — and that's fine, it's a
legitimate, different (arguably more polished) pattern. Instead of
separate popup windows, `ImageCompressor` (`core/main_window.py`) is
**one persistent window that morphs**: `_switch_mode()` swaps which panel
is visible inside the same floating bar, and every mode's UI-building
code lives together in that one large class, alongside a fair amount of
mode-specific logic (crop math, auto-crop, background-color blending,
etc.) that's genuinely intertwined with the UI rather than separated out
like `CompressWorker` was.

Practically, this means:
- **`tools/*/logic.py` is not "zero UI code" for every tool the way we
  originally planned.** `compress`, `background_remove`, and the PdfWorker
  wrapper cleanly are. But a lot of Crop's and PDF Tools' actual behavior
  still lives inside `core/main_window.py` and `ui/pdf_panel.py` as
  methods, not as separable pure functions — pulling those apart further
  would mean carefully extracting logic out of a stateful 100+ method
  class, which is real surgery, not a mechanical move, and risks
  introducing bugs without the ability to test on a real Windows display
  along the way.
- **This reorganization (what's been done so far) is the safe, purely
  mechanical layer**: same behavior, organized by concern (shell / visual
  design / tool logic) at the file level. It does *not* yet achieve full
  logic/UI separation for every tool.

If you want that deeper separation later — e.g. so a new tool could be
added by dropping in a folder without touching `main_window.py` — that's
a real follow-up project, best done gradually (one mode at a time, tested
after each step) rather than all at once.
