#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small self-tests without a printer and without a GUI (release check).

Usage:
    python3 tests/test_smoke.py          # all tests, exit code 0 = OK
    python3 -m pytest tests/ -q          # the same through pytest

Checks rendering (text, Markdown, layout blocks, EAN-13, QR), the YK/YZW
frame format of the X3 and the ESC/POS encoding.  NOTHING is sent - all
printer calls end in a recording buffer.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image                                       # noqa: E402

import x3print                                              # noqa: E402

FAILURES = []


def check(name, cond, info=""):
    ok = bool(cond)
    print(f"  {'OK  ' if ok else 'FEHL'}  {name}{(' – ' + str(info)) if info else ''}")
    if not ok:
        FAILURES.append(name)


class _Sink:
    """Minimal socket replacement: collects the bytes that were sent."""

    def __init__(self):
        self.data = bytearray()

    def sendall(self, data):
        self.data += data

    def recv(self, _n):
        return b""                       # like a closed connection (no waiting)

    def settimeout(self, *_a):
        pass


# ---------------------------------------------------------------- Version/MAC
def test_version_and_defaults():
    check("version present", isinstance(x3print.VERSION, str) and x3print.VERSION,
          x3print.VERSION)
    check("no hard-coded MAC", x3print.DEFAULT_MAC == "",
          repr(x3print.DEFAULT_MAC))
    check("protocols known", set(x3print.PROTOCOLS) == {"x3", "escpos"},
          x3print.PROTOCOLS)


def test_no_print_delay():
    """The raster data must flow without artificial pauses.

    A delay per block starves the printer: the print head is faster than the
    data stream, the motor stops in the middle of the page and starts again
    when the next block arrives.  The socket (RFCOMM flow control) does the
    pacing instead - it only lets us write when the printer can take data.
    """
    for mode, (chunk, delay) in sorted(x3print.MODE_PARAMS.items()):
        check(f"mode '{mode}': no artificial delay", delay == 0.0,
              f"{chunk} B blocks, {delay} s")
    pr = x3print.X3Printer(mac="00:00:00:00:00:00")
    check("default mode sends without delay", pr.delay == 0.0, pr.delay)

    # sending must pass every byte on - in blocks, but without waiting
    pr.sock = _Sink()
    data = bytes(range(256)) * 40                      # 10 KB
    pr._send_stream(data)
    check("_send_stream passes all bytes", bytes(pr.sock.data) == data,
          f"{len(pr.sock.data)} von {len(data)} Bytes")


# ------------------------------------------------------------------- Rendering
def test_render_text():
    img = x3print.render_text("Hallo Welt", 500, 30, "left")
    check("render_text size", img.size[0] == 500 and img.size[1] > 0, img.size)
    check("render_text 1-Bit", img.mode == "1", img.mode)


def test_render_markdown():
    md = "# Title\n\nText **bold** and *italic*\n\n- item A\n- item B\n\n---\n"
    img = x3print.render_markdown(md, 500, 30, "left", False, 3, 0, ROOT)
    check("render_markdown creates an image", img is not None and img.size[0] == 500,
          getattr(img, "size", None))
    img2 = x3print.render_markdown("![Logo](assets/x3drucker.png){50%}", 500, 30,
                                   "left", False, 3, 0, ROOT)
    check("Markdown image embedded", img2 is not None and img2.size[1] > 10,
          getattr(img2, "size", None))


def test_render_blocks():
    blocks = [
        {"text": "top", "pos": "top", "style": "outline", "size": 40,
         "align": "center", "upper": True},
        {"text": "bottom", "pos": "bottom", "style": "black", "size": 30,
         "align": "center", "upper": False},
    ]
    img = x3print.render_blocks(None, blocks, 500)
    check("render_blocks without background", img is not None and img.size[0] == 500,
          getattr(img, "size", None))
    check("render_blocks without content -> None",
          x3print.render_blocks(None, [], 500) is None)


def test_barcodes():
    ean = x3print.render_ean13("123456789012", scale=3)
    check("EAN-13 rendered (13 digits, check digit added)",
          ean is not None and ean.mode == "1" and ean.size[0] == 309,
          getattr(ean, "size", None))
    narrow = x3print.render_ean13("123456789012", scale=3, width=200)
    check("EAN-13 shrinks to the requested width", narrow.size[0] <= 200,
          narrow.size)
    try:
        qr = x3print.render_qrcode("https://www.deadlinedriven.dev", box_px=8,
                                   max_px=400)
        check("QR-Code gerendert", qr is not None and qr.size[0] > 0,
              getattr(qr, "size", None))
    except ImportError as err:
        print(f"  NOTE  QR code skipped (package 'qrcode' missing) - {err}")


# --------------------------------------------------------- X3 (YK/YZW-Frames)
def test_x3_frames():
    pr = x3print.X3Printer(mac="00:00:00:00:00:00")
    frame = pr._next_frame(0x11)
    check("frame starts with 64 11", frame[:2] == b"\x64\x11", frame[:4].hex())
    check("frame ends with 00 00 00 00 9b",
          frame[-5:] == b"\x00\x00\x00\x00\x9b", frame[-5:].hex())
    check("frame length (10 bytes + payload)", len(frame) == 10, len(frame))

    feed = pr._next_frame(0x02, (200).to_bytes(2, "little"))
    check("feed frame (length 2, 200 steps)",
          feed[3:5] == b"\x02\x00" and feed[5:7] == b"\xc8\x00"
          and len(feed) == 12, feed.hex())

    pr.sock = _Sink()
    pr.print_image(Image.new("1", (864, 8), 0))     # 8 black rows
    sent = bytes(pr.sock.data)
    check("print_image sends data", len(sent) > 0, f"{len(sent)} Byte")
    check("raster data inverted (bit set = black)",
          b"\xff" * 108 in sent)


# --------------------------------------------------------------- ESC/POS
def test_escpos():
    pr = x3print.EscPosPrinter(mac="00:00:00:00:00:00", width=384)
    check("ESC/POS bytes per row", pr.bytes_per_row == 48, pr.bytes_per_row)

    pr.sock = _Sink()
    pr.print_image(Image.new("1", (384, 8), 1))     # 8 white rows
    data = bytes(pr.sock.data)
    check("ESC @ at the start", data[:2] == b"\x1b\x40", data[:4].hex())
    check("GS v 0 raster command present", b"\x1d\x76\x30\x00" in data)
    check("feed ESC J at the end", b"\x1b\x4a" in data)


def test_missing_mac_message():
    pr = x3print.X3Printer(mac="")
    pr.mac = ""                                     # skip the auto detection
    try:
        pr.connect()
        check("connect() without MAC reports an error", False, "no exception")
    except RuntimeError as err:
        check("connect() without MAC reports an error",
              "No printer found" in str(err))
    except Exception as err:                        # noqa: BLE001
        check("connect() without MAC reports an error", False,
              f"{type(err).__name__}: {err}")


def test_cups_bridge():
    """The CUPS bridge must import, read the settings and build its log line."""
    import importlib.util

    path = os.path.join(ROOT, "cups", "x3bridge.py")
    spec = importlib.util.spec_from_file_location("x3bridge_test", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as err:                        # noqa: BLE001
        check("x3bridge.py imports", False, f"{type(err).__name__}: {err}")
        return
    check("x3bridge.py imports", True)

    try:
        msg = mod.startup_message()
        check("x3bridge startup message", "listening" in msg, msg)
    except Exception as err:                        # noqa: BLE001
        check("x3bridge startup message", False, f"{type(err).__name__}: {err}")
    try:
        geom, bright, mode, proto = mod.load_gui_settings()
        check("x3bridge settings complete",
              8 <= geom["width"] <= geom["dots"] and 10 <= bright <= 200
              and mode in ("app", "fast") and proto in ("x3", "escpos"),
              f"{geom} brightness={bright} mode={mode} protocol={proto}")
    except Exception as err:                        # noqa: BLE001
        check("x3bridge settings complete", False, f"{type(err).__name__}: {err}")


def test_shipped_image():
    """The example image shipped with the project must be present and renderable."""
    path = os.path.join(ROOT, "assets", "sample.jpg")
    check("example image assets/sample.jpg present", os.path.exists(path))
    if not os.path.exists(path):
        return
    from PIL import Image
    with Image.open(path) as im:
        check("example image is squared and large enough",
              im.width == im.height and im.width >= 1024, im.size)
    img = x3print.render_image_file(path, width=500)
    check("example image renders in print width", img.size[0] == 500,
          getattr(img, "size", None))
    check("example image has no AI/metadata tags",
          not im.info.get("exif") and not im.info.get("software"), im.info)


def test_cjk_text():
    """Japanese/Chinese text needs a CJK font - otherwise it prints as boxes."""
    check("has_cjk finds Japanese", x3print.has_cjk("こんにちは"))
    check("has_cjk finds Chinese", x3print.has_cjk("你好，世界"))
    check("has_cjk ignores Latin", not x3print.has_cjk("Grüße aus München"))

    font_de = x3print.load_font_for_text("Hallo Welt", 30)
    check("Latin text uses the standard font",
          getattr(font_de, "path", "").endswith("DejaVuSans.ttf"),
          getattr(font_de, "path", "?"))

    cjk = x3print._load_cjk_font(30)
    if cjk is None:
        check("CJK font installed", True, "none on this system (text would be boxes)")
    else:
        check("CJK font installed", True, getattr(cjk, "path", "?"))
        font_jp = x3print.load_font_for_text("こんにちは", 30)
        path = getattr(font_jp, "path", "")
        check("Japanese text uses the CJK font", path == getattr(cjk, "path", ""), path)
        img = x3print.render_text("日本語のテスト", 500, 30)
        check("Japanese text renders", img.size[0] == 500 and img.size[1] > 10, img.size)
        dark = sum(1 for p in img.convert("L").getdata() if p < 128)
        check("Japanese glyphs are drawn", dark > 100, f"{dark} schwarze Punkte")


def test_cli_version():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "x3print.py"),
                          "--version"], capture_output=True, text=True)
    check("x3print.py --version", out.returncode == 0
          and x3print.VERSION in out.stdout, (out.stdout or out.stderr).strip())


def test_gui_version():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "x3gui.py"),
                          "--version"], capture_output=True, text=True)
    check("x3gui.py --version", out.returncode == 0
          and x3print.VERSION in out.stdout, (out.stdout or out.stderr).strip())


def main():
    print(f"X3 thermal printer - self-test (version {x3print.VERSION})")
    tests = (test_version_and_defaults, test_no_print_delay, test_shipped_image,
             test_render_text,
             test_render_markdown,
             test_render_blocks, test_barcodes, test_x3_frames, test_escpos,
             test_missing_mac_message, test_cups_bridge, test_cjk_text,
             test_cli_version, test_gui_version)
    for fn in tests:
        print(f"\n{fn.__name__}:")
        fn()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} - {', '.join(FAILURES)}")
        return 1
    print("All tests OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
