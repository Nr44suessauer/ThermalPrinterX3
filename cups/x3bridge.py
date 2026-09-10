#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
x3bridge - user bridge between CUPS and the Snap & Tag printer X3.

CUPS print jobs of the queue "X3Thermo" are sent to socket://127.0.0.1:9101.
This service accepts them, renders the content (PDF/PostScript/image/text)
into the YK raster format of the X3 and sends it over Bluetooth.  It runs as
a systemd USER service - no sudo required.

Environment:
  X3_MAC         MAC address (empty = search automatically)
  X3_BRIGHTNESS  print darkness in % (default 100; 70..160 makes sense)
"""

import os
import socket
import sys
import tempfile
import json

HERE = os.path.dirname(os.path.abspath(__file__))          # .../cups
ROOT = os.path.dirname(HERE)                               # Projektwurzel
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import x3raster                                            # noqa: E402
import x3print                                             # noqa: E402
from x3print import (render_text, render_image_file,       # noqa: E402
                     _brightness_density, CONTENT_WIDTH, PAPER_LEFT,
                     DEFAULT_DOTS, DEFAULT_FEED)
from PIL import Image                                      # noqa: E402

LISTEN = ("127.0.0.1", 9101)
MAC = os.environ.get("X3_MAC", "").strip()      # empty = search automatically
BRIGHTNESS = max(10, min(200, int(os.environ.get("X3_BRIGHTNESS", "100"))))
MAX_TEXT_ROWS = 10000
CONFIG_PATH = os.path.expanduser("~/.config/x3drucker.json")


def load_gui_settings():
    """Geometry/brightness/mode from the GUI configuration (if present)."""
    geom = dict(dots=DEFAULT_DOTS, left=PAPER_LEFT, width=CONTENT_WIDTH)
    bright = BRIGHTNESS
    mode = os.environ.get("X3_MODE", "app")
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            c = json.load(f)
        geom["dots"] = int(c.get("dots", geom["dots"]))
        geom["left"] = int(c.get("left", geom["left"]))
        geom["width"] = int(c.get("width", geom["width"]))
        if "X3_BRIGHTNESS" not in os.environ:
            bright = int(c.get("brightness", bright))
        if "X3_MODE" not in os.environ:
            mode = str(c.get("mode", mode))
    except Exception:                                        # noqa: BLE001
        pass
    geom["width"] = max(8, geom["width"] - geom["width"] % 8)
    if geom["left"] + geom["width"] > geom["dots"]:
        geom["width"] = max(8, geom["dots"] - geom["left"])
    if mode not in ("app", "fast"):
        mode = "app"
    protocol = "x3"
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            c = json.load(f)
        protocol = str(c.get("protocol", "x3"))
    except Exception:                                        # noqa: BLE001
        pass
    return geom, max(10, min(200, bright)), mode, protocol


def _is_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def _is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


def _cleanup(tmp: str, files) -> None:
    for f in files:
        try:
            os.unlink(f)
        except OSError:
            pass
    try:
        os.rmdir(tmp)
    except OSError:
        pass


def process(data: bytes) -> None:
    """Turn job data into a print job (geometry from the GUI settings)."""
    geom, brightness, mode, protocol = load_gui_settings()
    dots, left, width = geom["dots"], geom["left"], geom["width"]
    mac = MAC or x3print.detect_mac()
    if not mac:
        sys.stderr.write(
            "x3bridge: no printer found - pair the X3 (scripts/pair.sh) "
            "or set X3_MAC.\n")
        return

    def embed_and_print(p, content) -> None:
        canvas = Image.new("1", (dots, content.height), 1)
        canvas.paste(content, (left, 0))
        p.print_image(canvas)

    with x3print.make_printer(protocol, mac=mac, width=dots,
                              feed=DEFAULT_FEED, mode=mode) as p:
        if hasattr(p, "density"):
            p.density = _brightness_density(brightness)

        if data[:4] == b"%PDF" or data[:2] == b"%!":
            # PDF or PostScript -> Ghostscript -> print the pages
            ext = ".pdf" if data[:4] == b"%PDF" else ".ps"
            tmp, pages = x3raster.render_pages(data, ext)
            try:
                for png in pages:
                    bw = x3raster.page_to_bw(png, brightness, width)
                    embed_and_print(p, bw)
            finally:
                _cleanup(tmp, pages)
            return

        if _is_png(data) or _is_jpeg(data):
            # image file -> rasterise directly
            suffix = ".png" if _is_png(data) else ".jpg"
            fd, path = tempfile.mkstemp(prefix="x3bridge-", suffix=suffix)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                bw = render_image_file(path, width, brightness=brightness)
                embed_and_print(p, bw)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            return

        # treat everything else as text (text/plain from CUPS)
        text = data.decode("utf-8", errors="replace")
        img = render_text(text, width, font_size=30, align="left")
        if img.height > MAX_TEXT_ROWS:
            img = img.crop((0, 0, img.width, MAX_TEXT_ROWS))
        embed_and_print(p, img)


def startup_message() -> str:
    """Log line for the startup (reads and checks the stored settings)."""
    _geom, br, md, proto = load_gui_settings()
    return (f"x3bridge listening on {LISTEN[0]}:{LISTEN[1]} "
            f"(MAC {MAC or 'auto'}, brightness {br} %, mode {md}, "
            f"protocol {proto})")


def serve() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(4)
    print(startup_message(), flush=True)
    while True:
        conn, _addr = srv.accept()
        try:
            conn.settimeout(300)
            chunks = []
            while True:
                b = conn.recv(65536)
                if not b:
                    break
                chunks.append(b)
            data = b"".join(chunks)
            if data:
                print(f"Job received ({len(data)} bytes) ...", flush=True)
                process(data)
                print("Job printed.", flush=True)
        except Exception as e:                              # noqa: BLE001
            print(f"Error while processing the job: {e}", file=sys.stderr, flush=True)
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    serve()
