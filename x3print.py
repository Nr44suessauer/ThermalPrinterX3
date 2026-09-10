#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"\"\"\"\nx3print.py - connector for the \"Snap & Tag\" Bluetooth thermal printer (X3)\n=========================================================================\n\nThe printer does NOT speak ESC/POS but a proprietary \"YK/YZW\" frame protocol\n(ORGBRO X3 family).  Reverse engineering sources:\n  * https://github.com/isma-co/ORGBRO-X3-Windows     (classic SPP, tested)\n  * https://github.com/a-gians/Open-Orgbro           (BLE, protocol docs)\n\nFrame format (all commands):\n    64 <cmd> <seq> <len_lo> <len_hi> <payload ...> 00 00 00 00 9b\n    cmd: 0x80 token/init | 0x11 firmware query | 0x0a speed\n         0x09 density | 0x02 paper feed | 0x00 raster image data\n\nImage data: 1 bit, MSB first, a set bit means black.\n  The ORGBRO X3 head has 864 dots @ 300 dpi (108 bytes per row).\n  Every raster frame carries 432 bytes = 4 rows.\n  (432 dot / 203 dpi variants with 54 bytes per row also exist.)\n\nPAPER (this device): the printable window sits around dot 300..845 of the\n864 dot raster (measured via `calibrate`).  Content is centred in a 500 dot\nwide area (approx. 42 mm) at raster x 320.  For different paper: print\n`calibrate` and adjust `--left`/`--width`.\n\nRequirements: Python 3 + Pillow (optional \"qrcode\" for QR codes).\n\"\"\""

import argparse
import os
import re
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------
VERSION = "1.0.0"

DEVICE_NAME = "X3"                          # Name im Bluetooth-Scan
DEFAULT_MAC = ""                            # empty = search automatically
RFCOMM_CHANNEL = 1                          # SPP channel (cannot be discovered via SDP -> 1)

DEFAULT_DOTS = 864                          # print head width for the raster framing
# printable window on the paper (measured on this very device,
# see `calibrate`): about dot 300..845.
PAPER_LEFT = 320                            # left edge of the content inside the raster
CONTENT_WIDTH = 500                         # usable content width (dots) ~ 42 mm

DEFAULT_SPEED = 0x55                        # print speed (from capture)
DEFAULT_DENSITY = 0x0c                      # print density (from capture)
DEFAULT_FEED = 200                          # feed steps after printing
# print mode - both modes send continuously, only the block size differs.
# The RFCOMM socket has flow control: while the printer works off its buffer
# the writes block, so the motor never runs out of data (no pause in the
# middle of a page).  An artificial delay per block would starve the printer
# and make it stop and restart, therefore the default delay is 0.
#   "app"  = blocks the size of one raster frame (like the phone app) - the
#            gentlest variant for slow/flaky Bluetooth links.
#   "fast" = 16 KB blocks, fewer and larger writes (same data rate).
DEFAULT_MODE = "app"
MODE_PARAMS = {"app": (432, 0.0), "fast": (16384, 0.0)}
DRAIN_RATE = 15000                          # fallback: bytes/s while the printer reads
DRAIN_MIN, DRAIN_MAX = 0.5, 8.0             # limits for the wait before close()
DROP_STALE_LINK = True                      # drop a stale Bluetooth session before retrying

# supported protocols / printer templates
PROTOCOLS = ("x3", "escpos")
MODELS = {
    # id: (label, protocol, raster width dots, paper width mm, margin mm)
    "x3":    ("X3 / Snap & Tag  (53 mm, 864 dots)", "x3", 864, 53.0, 1.5),
    "esc58": ("ESC/POS 58 mm  (48 mm breit, 384 dots)", "escpos", 384, 48.0, 2.0),
    "esc80": ("ESC/POS 80 mm  (72 mm breit, 576 dots)", "escpos", 576, 72.0, 2.0),
}


def list_bluetooth_devices(scan: bool = False,
                           seconds: float = 8.0) -> list:
    "List the Bluetooth devices of the system: [(MAC, name, paired), ...].\n\n    `scan=True` runs a device scan first (takes `seconds` seconds).\n    Only `bluetoothctl` is used (no sudo required).\n    "
    def run(cmd, timeout):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=timeout)
            return res.stdout or ""
        except Exception:                                    # noqa: BLE001
            return ""

    if scan:
        run(["bluetoothctl", "--timeout", str(int(seconds)), "scan", "on"],
            timeout=seconds + 5)

    def parse(text):
        found = {}
        for line in text.splitlines():
            parts = line.split(maxsplit=2)
            if len(parts) >= 2 and parts[0] == "Device":
                found[parts[1].upper()] = parts[2] if len(parts) > 2 else ""
        return found

    paired = set(parse(run(["bluetoothctl", "devices", "Paired"], 6)))
    devs = parse(run(["bluetoothctl", "devices"], 6))
    items = [(mac, name, mac in paired) for mac, name in devs.items()]

    def rank(it):
        name = (it[1] or "").lower()
        is_printer = any(k in name for k in ("x3", "print", "thermo", "pos"))
        return (0 if it[2] else 1, 0 if is_printer else 1, name)

    return sorted(items, key=rank)


class EscPosPrinter:
    "Standard thermal printer (ESC/POS) over Bluetooth SPP.\n\n    Uses the standard raster command `GS v 0` (1 bit per pixel, MSB first,\n    1 = black) and `ESC J` for the feed.  Speed, density and mode are X3\n    specific and ignored here.\n    "

    def __init__(self, mac: str | None = None, width: int = 384,
                 feed: int = DEFAULT_FEED, points: int = 8, **_ignored):
        if width % 8 != 0:
            raise ValueError("print head width must be divisible by 8")
        self.mac = (mac or detect_mac()).upper()
        self.width = width
        self.bytes_per_row = width // 8
        self.default_feed = feed
        self.points = max(1, int(points))         # dots per feed step
        self.sock = None

    def connect(self, retries: int = 3) -> "EscPosPrinter":
        if not self.mac:
            raise RuntimeError(
                "No printer found - pair the Bluetooth printer (scripts/pair.sh) or "
                "pass a MAC address (--device/--mac).")
        last_err = None
        for i in range(retries):
            try:
                self.sock = socket.socket(
                    socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
                )
                self.sock.settimeout(3.0)
                self.sock.connect((self.mac, RFCOMM_CHANNEL))
                self.sock.settimeout(0.8)
                return self
            except OSError as e:
                last_err = e
                if self.sock:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                    self.sock = None
                if i < retries - 1:
                    time.sleep(2.0)
        raise ConnectionError(
            f"No connection to {self.mac} (channel {RFCOMM_CHANNEL}): {last_err}\n"
            "Hint: switch the printer on and pair it first if needed:\n"
            "  bluetoothctl pair <MAC>   (PIN is usually 0000)"
        )

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _send(self, data: bytes) -> None:
        if not self.sock:
            raise ConnectionError("Not connected")
        self.sock.sendall(data)

    def firmware(self) -> bytes:
        return b""                                   # not common for ESC/POS

    def status(self) -> bytes:
        "Real-time status (DLE EOT 1): 1 byte response if supported."
        try:
            self._send(b"\x10\x04\x01")
            self.sock.settimeout(1.0)
            data = self.sock.recv(16)
            self.sock.settimeout(0.8)
            return data or b""
        except OSError:
            return b""

    def feed(self, steps: int | None = None) -> None:
        steps = self.default_feed if steps is None else steps
        n = max(0, min(255, int(steps * self.points)))
        self._send(b"\x1b\x4a" + bytes([n]))        # ESC J n  (n dots)

    def print_image(self, bw_image) -> None:
        "Print a 1-bit image (mode \"1\", 0 = black) in raster width."
        if bw_image.width != self.width:
            raise ValueError(
                f"Image width {bw_image.width} != raster width {self.width}"
            )
        raw = bw_image.convert("1").tobytes()
        data = bytes(b ^ 0xFF for b in raw)          # 1 = black
        h = bw_image.height
        out = bytearray()
        out += b"\x1b\x40"                           # ESC @  (Initialisieren)
        out += b"\x1d\x76\x30\x00"                   # GS v 0  m=0 (raster)
        out += bytes([self.bytes_per_row & 0xFF, self.bytes_per_row >> 8])
        out += bytes([h & 0xFF, h >> 8])
        out += data
        out += b"\x1b\x4a" + bytes([max(0, min(255, self.default_feed * self.points))])
        self._send(bytes(out))


def make_printer(protocol: str = "x3", **kw):
    "Create the matching printer driver (\"x3\" or \"escpos\")."
    if (protocol or "x3").lower() in ("escpos", "esc/pos", "escp", "pos"):
        return EscPosPrinter(**kw)
    return X3Printer(**kw)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

# font files per style: (family, bold, italic) -> candidates in order
_FONT_FILES = {
    ("sans", False, False): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
    ("sans", True, False): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    ("sans", False, True): [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    ("sans", True, True): [
        "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    ("mono", False, False): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"],
    ("mono", True, False): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"],
}
_FONT_CACHE: dict = {}

# CJK fonts (Japanese/Chinese/Korean) - used automatically for text that contains
# such characters, because DejaVu has no glyphs for them (they would print as boxes).
FONT_CJK_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]
FONT_CJK_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
_CJK_FONT_CACHE: dict = {}


def has_cjk(text: str) -> bool:
    "True if the text contains CJK (Japanese/Chinese/Korean) characters."
    return any("\u2e80" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff"
               or "\uff00" <= ch <= "\uffef" for ch in text or "")


def _load_cjk_font(size: int, bold: bool = False):
    "Load a CJK capable font (cached) - None if none is installed."
    from PIL import ImageFont

    key = (int(size), bool(bold))
    if key in _CJK_FONT_CACHE:
        return _CJK_FONT_CACHE[key]
    cands = list(FONT_CJK_CANDIDATES)
    if bold and os.path.exists(FONT_CJK_BOLD):
        cands.insert(0, FONT_CJK_BOLD)
    font = None
    for path in cands:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, int(size))
                break
            except Exception:                            # noqa: BLE001
                font = None
    _CJK_FONT_CACHE[key] = font
    return font


def load_font_for_text(text: str, size: int, bold: bool = False,
                       italic: bool = False, mono: bool = False):
    """Font for `text`: a CJK font when needed, otherwise the standard font."""
    if has_cjk(text):
        font = _load_cjk_font(size, bold=bold)
        if font is not None:
            return font
    return _load_font(size, bold=bold, italic=italic, mono=mono)


# --------------------------------------------------------------------------
# Frame- & Verbindungs-Ebene
# --------------------------------------------------------------------------
def make_frame(cmd: int, seq: int, payload: bytes = b"") -> bytes:
    "Build a YK frame: 64 <cmd> <seq> <len_le16> <payload> 00 00 00 00 9b."
    plen = len(payload)
    return (
        b"\x64"
        + bytes([cmd & 0xFF, seq & 0xFF, plen & 0xFF, (plen >> 8) & 0xFF])
        + payload
        + b"\x00\x00\x00\x00\x9b"
    )


def detect_mac() -> str:
    "Search the printer in the Bluetooth device list (bluetoothctl).\n\n    Returns \"\" when nothing was found (then pass the MAC in the GUI/CLI).\n    "
    try:
        out = subprocess.run(
            ["bluetoothctl", "devices"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return DEFAULT_MAC
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and DEVICE_NAME.lower() in parts[2].lower():
            return parts[1]
    return DEFAULT_MAC


def drop_bluetooth_link(mac: str, timeout: float = 15.0) -> bool:
    """Drop a (stale) Bluetooth connection to `mac`.

    The X3 accepts only one client.  If a previous session was not closed
    cleanly, the PC still shows "Connected: yes" while the RFCOMM channel
    cannot be opened any more ("connection timed out").  Disconnecting at the
    Bluetooth level clears that state; the next connect works again.
    """
    if not mac:
        return False
    try:
        out = subprocess.run(["bluetoothctl", "disconnect", mac],
                             capture_output=True, text=True, timeout=timeout)
    except Exception:                                        # noqa: BLE001
        return False
    return "disconnect" in (out.stdout or "").lower()


class X3Printer:
    "Connection to a YK/YZW thermal printer over Bluetooth SPP."

    def __init__(self, mac: str | None = None, width: int = DEFAULT_DOTS,
                 speed: int = DEFAULT_SPEED, density: int = DEFAULT_DENSITY,
                 feed: int = DEFAULT_FEED, mode: str = DEFAULT_MODE,
                 delay: float | None = None):
        if width % 8 != 0:
            raise ValueError("print head width must be divisible by 8")
        self.mac = (mac or detect_mac()).upper()
        self.width = width                     # raster width (framing)
        self.bytes_per_row = width // 8
        self.rows_per_chunk = 432 // self.bytes_per_row
        self.speed = speed
        self.density = density
        self.default_feed = feed
        chunk, d = MODE_PARAMS.get(mode, MODE_PARAMS[DEFAULT_MODE])
        self.mode = mode
        self.chunk = chunk
        self.delay = d if delay is None else float(delay)
        self.sock = None
        self.seq = 1

    # -- connection -------------------------------------------------------
    def connect(self, retries: int = 3) -> "X3Printer":
        if not self.mac:
            raise RuntimeError(
                "No printer found - pair the Bluetooth printer (scripts/pair.sh) or "
                "pass a MAC address (--device/--mac).")
        last_err = None
        for i in range(retries):
            try:
                self.sock = socket.socket(
                    socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
                )
                self.sock.settimeout(3.0)
                self.sock.connect((self.mac, RFCOMM_CHANNEL))
                self.sock.settimeout(0.8)
                self.seq = 1
                return self
            except OSError as e:
                last_err = e
                if self.sock:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                    self.sock = None
                if i < retries - 1:
                    # A timeout almost always means a stale Bluetooth session:
                    # the PC still shows "Connected: yes", but the printer's
                    # single RFCOMM channel is blocked.  Drop the link once and
                    # try again.  "Device or resource busy" only means that the
                    # printer is still busy with the previous job - then we just
                    # wait (dropping the link could cut off that job).
                    dropped = False
                    if i == 0 and DROP_STALE_LINK and isinstance(e, TimeoutError):
                        dropped = drop_bluetooth_link(self.mac)
                    if not dropped:
                        time.sleep(2.0)
        raise ConnectionError(
            f"No connection to {self.mac} (channel {RFCOMM_CHANNEL}): {last_err}\n"
            "Hint: switch the printer on and pair it first if needed:\n"
            "  bluetoothctl pair <MAC>   (PIN is usually 0000)\n"
            "  bluetoothctl trust <MAC>\n"
            "If it worked before, the Bluetooth link may be stale - the driver\n"
            "drops it automatically on the next attempt, manually it is:\n"
            f"  bluetoothctl disconnect {self.mac}"
        )

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # -- Senden ------------------------------------------------------------
    def _next_frame(self, cmd: int, payload: bytes = b"") -> bytes:
        "Build a frame with the current sequence number (without sending it)."
        frame = make_frame(cmd, self.seq, payload)
        self.seq = (self.seq + 1) & 0xFF
        return frame

    def _send_stream(self, data: bytes, chunk: int | None = None) -> None:
        """Send the job in blocks, as fast as the printer accepts data.

        The socket does the pacing: RFCOMM flow control blocks `sendall`
        while the printer is busy, so the data stream never breaks off and
        the motor keeps running - a stop in the middle of a page means the
        printer had to wait for data.  A delay per block (`--delay`) is only
        meant for special cases.
        """
        mv = memoryview(data)
        step = chunk or self.chunk
        for i in range(0, len(mv), step):
            self.sock.sendall(mv[i:i + step])
            if self.delay > 0:
                time.sleep(self.delay)

    def _send_frame(self, cmd: int, payload: bytes = b"") -> bytes:
        frame = self._next_frame(cmd, payload)
        self.sock.sendall(frame)
        if self.delay > 0:
            time.sleep(self.delay)
        return frame

    def _read_available(self, wait: float = 0.35) -> bytes:
        "Read whatever the printer sends back."
        time.sleep(wait)
        data = b""
        self.sock.settimeout(0.3)
        try:
            while True:
                chunk = self.sock.recv(256)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        return data

    # -- Befehle -----------------------------------------------------------
    def firmware(self) -> bytes:
        "Firmware query (0x11). The reply starts with 0x64 0xf1 ..."
        self._send_frame(0x11)
        return self._read_available()

    def status(self) -> bytes:
        "Status query (0x10). The reply starts with 0x64 0xff ..."
        self._send_frame(0x10)
        return self._read_available()

    def feed(self, steps: int | None = None) -> None:
        """Feed the paper by `steps` steps (0x02, payload LE16)."""
        steps = self.default_feed if steps is None else steps
        self._send_frame(0x02, steps.to_bytes(2, "little"))

    def print_image(self, bw_image) -> None:
        "\n        Print a PIL image (mode \"1\", 0 = black) across the full raster width\n        (`self.width`).  Same order as in the Android capture:\n        speed -> density -> raster -> feed.\n        "
        from PIL import Image

        if bw_image.width != self.width:
            raise ValueError(
                f"Image width {bw_image.width} != raster width {self.width}"
            )

        # pad the height to full chunk rows
        h = bw_image.height
        rem = h % self.rows_per_chunk
        if rem:
            padded = Image.new("1", (self.width, h + (self.rows_per_chunk - rem)), 1)
            padded.paste(bw_image, (0, 0))
            bw_image = padded

        raster = self._raster_bytes(bw_image)

        # build the whole job in ONE buffer and send it in large blocks:
        # speed -> density -> raster frames -> feed (without interruptions).
        out = bytearray()
        out += self._next_frame(0x0A, bytes([self.speed]))       # speed
        out += self._next_frame(0x09, bytes([self.density]))     # density
        for pos in range(0, len(raster), 432):                   # raster frames
            out += self._next_frame(0x00, raster[pos:pos + 432])
        out += self._next_frame(0x02, self.default_feed.to_bytes(2, "little"))  # feed
        self._send_stream(bytes(out))

        # Make sure the printer received everything before the socket is
        # closed - closing early would cut off the end of the page.
        self._drain(len(out))

    def _drain(self, nbytes: int) -> None:
        """Wait until the printer has read the whole job (best effort).

        Closing the RFCOMM socket discards data that is still queued in the
        kernel.  A status query sent *behind* the print data is answered only
        after the printer has read everything before it, so its reply is the
        "all arrived" signal.  If no reply comes (other firmware), the wait
        falls back to an estimate from the job size.
        """
        limit = min(DRAIN_MAX, max(DRAIN_MIN, nbytes / DRAIN_RATE))
        try:
            self.sock.sendall(make_frame(0x10, self.seq & 0xFF))
        except OSError:
            time.sleep(limit)
            return
        end = time.time() + limit
        self.sock.settimeout(0.2)
        got_reply = False
        while time.time() < end:
            try:
                if self.sock.recv(256):            # reply -> all input was read
                    got_reply = True
                    time.sleep(0.05)
                    continue                       # ... and clear the leftovers
                break                              # the printer closed the link
            except socket.timeout:
                if got_reply:
                    return
            except OSError:
                break

    @staticmethod
    def _raster_bytes(bw_image: "Image.Image") -> bytes:
        "1-bit PIL image -> raw bytes (MSB first, bit = 1 means black).\n\n        Fast path: PIL delivers packed 1-bit rows (white = 1, MSB first) -\n        a bitwise complement turns that into black = 1.  The result is\n        identical to the pixel-by-pixel method (verified) but orders of\n        magnitude faster (matters for large images).\n        "
        w, h = bw_image.size
        if w % 8 == 0:
            return bytes(b ^ 0xFF for b in bw_image.tobytes())
        # fallback for widths that are not divisible by 8
        px = bw_image.load()
        bpr = (w + 7) // 8
        out = bytearray()
        for y in range(h):
            for bx in range(bpr):
                v = 0
                for bit in range(8):
                    x = bx * 8 + bit
                    if x < w and px[x, y] == 0:
                        v |= 1 << (7 - bit)
                out.append(v)
        return bytes(out)


# --------------------------------------------------------------------------
# image creation (Pillow)
# --------------------------------------------------------------------------
def _load_font(size: int, bold: bool = False, italic: bool = False,
               mono: bool = False):
    "Load a font - with a cache, also bold/italic/monospace.\n\n    The cache matters for Markdown: many pieces of text are measured and\n    drawn with different styles there.\n    "
    from PIL import ImageFont

    key = (int(size), bool(bold), bool(italic), bool(mono))
    font = _FONT_CACHE.get(key)
    if font is not None:
        return font
    fam = "mono" if mono else "sans"
    cands = _FONT_FILES.get((fam, bool(bold), bool(italic)))
    if cands is None and italic:                 # e.g. mono-italic: without italic
        cands = _FONT_FILES.get((fam, bool(bold), False))
    if not mono and not italic:                  # legacy behaviour: defaults first
        cands = list(FONT_CANDIDATES) + list(cands or [])
    for p in (cands or FONT_CANDIDATES):
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, size)
                _FONT_CACHE[key] = font
                return font
            except Exception:
                pass
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _text_width(text: str, font) -> float:
    from PIL import Image, ImageDraw

    d = ImageDraw.Draw(Image.new("L", (10, 10)))
    return d.textlength(text, font=font)


def _wrap(text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w_ in words:
            trial = (cur + " " + w_).strip()
            if _text_width(trial, font) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w_
        lines.append(cur)
    return lines


def render_text(text: str, width: int, font_size: int = 30, align: str = "left",
                bold: bool = False, spacing: int = 3,
                thicken: int = 0) -> "Image.Image":
    "Build a 1-bit image (0 = black) from text, in the content width.\n\n    `thicken` = stroke width in px around the letters (0 = none).  On 300 dpi\n    heads 0 is usually enough; try 1 for pale printouts.\n    "
    from PIL import Image, ImageDraw

    font = load_font_for_text(text, font_size, bold=bold)
    lines = _wrap(text, font, width)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    total_h = max(1, line_h * len(lines) + spacing * (len(lines) - 1))
    img = Image.new("L", (width, total_h), 255)
    d = ImageDraw.Draw(img)
    y = 0
    for ln in lines:
        w_ = _text_width(ln, font)
        if align == "center":
            x = (width - w_) / 2
        elif align == "right":
            x = width - w_
        else:
            x = 0
        if thicken:
            d.text((x, y), ln, font=font, fill=0,
                   stroke_width=thicken, stroke_fill=0)
        else:
            d.text((x, y), ln, font=font, fill=0)
        y += line_h + spacing
    return img.point(lambda p: 0 if p < 128 else 255, mode="1")


# --------------------------------------------------------------------------
# Markdown (styled text) and meme (text on an image)
# --------------------------------------------------------------------------
_MD_INLINE_RE = re.compile(
    r"\*\*.+?\*\*"                          # **fett**
    r"|__[^_].*?__"                         # __fett__
    r"|~~.+?~~"                             # ~~durchgestrichen~~
    r"|!\[[^\]]*\]\([^)]+\)(?:\{[^}]*\})?"  # ![image](path){50%}
    r"|\[[^\]]*\]\([^)]+\)"                 # [text](target)
    r"|`[^`\n]+`"                           # `Code`
    r"|\*[^*\n]+?\*"                        # *kursiv*
    r"|(?<!\w)_[^_\n]+?_(?!\w)"             # _kursiv_
)
_MD_IMG_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)(?:\{([^}]*)\})?$")
_MD_LINK_RE = re.compile(r"^\[([^\]]*)\]\(([^)]+)\)$")
_MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_MD_OL_RE = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_MD_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_MD_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_MD_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MD_IMG_ONLY_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+?)\)\s*(?:\{([^}]*)\})?$")
_MD_SIZE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(%|dots?|px)?$")

MD_HELP = "# Large heading\n## Medium heading\n### Small heading\n\n**bold**   *italic*   ~~strikethrough~~   `code`\n\n- bullet list\n1. numbered list\n\n> quote (indented with a bar)\n\n--- draws a separator line\n\n![image](path/to/image.png)       image at full width\n![image](path/image.png){50%}     half width\n![image](image.png){300}          fixed width in dots\n\nImages may also sit inside a line of text: like ![image](x.png) here.\nBlank lines separate paragraphs."


def _md_font(size: int, style: dict, base_bold: bool = False):
    "Load a font for a Markdown style (bold/italic/mono)."
    return _load_font(size, bool(style.get("bold")) or bool(base_bold),
                      bool(style.get("italic")), bool(style.get("mono")))


def _md_inline(text: str) -> list:
    "Split inline markup into (text, style) runs."
    runs = []
    pos = 0
    for m in _MD_INLINE_RE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], {}))
        pos = m.end()
        tok = m.group(0)
        if tok.startswith("!["):
            mi = _MD_IMG_RE.match(tok)
            runs.append((mi.group(1) or "Image",
                         {"image": mi.group(2), "spec": mi.group(3) or ""}))
        elif tok.startswith("["):
            ml = _MD_LINK_RE.match(tok)
            runs.append((ml.group(1) or ml.group(2), {"link": ml.group(2)}))
        elif tok.startswith("**") or tok.startswith("__"):
            runs.append((tok[2:-2], {"bold": True}))
        elif tok.startswith("~~"):
            runs.append((tok[2:-2], {"strike": True}))
        elif tok.startswith("`"):
            runs.append((tok[1:-1], {"mono": True}))
        else:
            runs.append((tok[1:-1], {"italic": True}))
    if pos < len(text):
        runs.append((text[pos:], {}))
    return runs


def _md_open_image(path: str, base_dir: str | None = None):
    "Load an image for Markdown (also relative to the document folder)."
    for c in ([path] if (not base_dir or os.path.isabs(path))
              else [path, os.path.join(base_dir, path)]):
        try:
            if os.path.exists(c):
                return load_raster_image(c, px_width=600)
        except Exception:                                    # noqa: BLE001
            pass
    return None


def _md_atoms(runs: list, size: int, base_bold: bool,
              base_dir: str | None) -> list:
    "Turn runs into atoms: (\"t\", text, style, width) or (\"i\", image, ...)."
    from PIL import Image

    atoms = []
    for text, style in runs:
        if "image" in style:
            img = _md_open_image(style["image"], base_dir)
            if img is not None:
                h = max(10, int(round(size * 1.35)))
                w = max(1, int(round(img.width * h / max(1, img.height))))
                img = img.convert("L").resize((w, h), Image.Resampling.LANCZOS)
                atoms.append(("i", img, style, w, h))
                continue
            style = {k: v for k, v in style.items()
                     if k not in ("image", "spec")}
            text = text or "Image"
        for piece in re.split(r"(\s+)", text):
            if not piece.strip():
                continue
            f = _md_font(size, style, base_bold)
            atoms.append(("t", piece, style, _text_width(piece, f)))
    return atoms


def _md_break_long(atoms: list, avail: int, size: int, base_bold: bool) -> list:
    "Hard-wrap words that are too wide (e.g. long URLs)."
    out = []
    for a in atoms:
        if a[0] != "t" or a[3] <= avail:
            out.append(a)
            continue
        _, txt, style, _w = a
        f = _md_font(size, style, base_bold)
        chunk = ""
        for ch in txt:
            if chunk and _text_width(chunk + ch, f) > avail:
                out.append(("t", chunk, style, _text_width(chunk, f)))
                chunk = ch
            else:
                chunk += ch
        if chunk:
            out.append(("t", chunk, style, _text_width(chunk, f)))
    return out


def _md_wrap(atoms: list, avail: int, space_w: float) -> list:
    "Distribute atoms over lines (breaking at word boundaries)."
    lines, cur, cur_w = [], [], 0.0
    for a in atoms:
        add = a[3] if not cur else a[3] + space_w
        if cur and cur_w + add > avail:
            lines.append(cur)
            cur, cur_w = [a], float(a[3])
        else:
            cur.append(a)
            cur_w += add
    if cur:
        lines.append(cur)
    return lines


def _md_plan(text: str, width: int, size: int, base_bold: bool, align: str,
             spacing: int, base_dir: str | None) -> list:
    """Turn Markdown into drawing instructions (lines, rules, images)."""
    from PIL import Image

    plan: list = []
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def add_text(txt, s, bold=False, italic=False, indent=0, prefix="",
                 gap=None, bar=False, mono=False):
        runs = _md_inline(txt)
        atoms = _md_atoms(runs, s, base_bold or bold, base_dir)
        f0 = _load_font(s, base_bold or bold, italic, mono)
        pw = 0.0
        if prefix:
            pw = _text_width(prefix, f0)
            atoms = [("t", prefix, {"bold": bold}, pw)] + atoms
        avail = max(24, width - indent - int(pw))
        atoms = _md_break_long(atoms, avail, s, base_bold or bold)
        asc, desc = f0.getmetrics()
        first = True
        for ln in _md_wrap(atoms, avail, _text_width(" ", f0)):
            plan.append({"type": "text", "atoms": ln, "size": s, "font": f0,
                         "h": asc + desc, "base_bold": base_bold or bold,
                         "indent": int(indent + (0 if first else pw)),
                         "align": align, "bar": bar and first,
                         "gap": spacing if gap is None else gap})
            first = False

    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        if _MD_FENCE_RE.match(s):                        # ``` Codeblock ```
            i += 1
            block = []
            while i < len(lines) and not _MD_FENCE_RE.match(lines[i].strip()):
                block.append(lines[i])
                i += 1
            i += 1
            for cl in (block or [""]):
                add_text(cl if cl.strip() else " ", max(9, int(size * 0.92)),
                         indent=8, mono=True)
            continue
        if _MD_HR_RE.match(s):                           # --- Trennlinie
            plan.append({"type": "hr", "h": 3, "gap": spacing + 8})
            i += 1
            continue
        m = _MD_HEAD_RE.match(s)                         # # heading
        if m:
            lvl = len(m.group(1))
            factor = (1.9, 1.55, 1.28, 1.10)[min(lvl, 4) - 1]
            add_text(m.group(2), max(9, int(round(size * factor))), bold=True,
                     gap=spacing + (6 if lvl <= 2 else 3))
            i += 1
            continue
        m = _MD_QUOTE_RE.match(s)                        # > Zitat
        if m:
            add_text(m.group(1), size, italic=True, indent=12, bar=True)
            i += 1
            continue
        m = _MD_UL_RE.match(raw)                         # - list
        if m:
            add_text(m.group(2), size, indent=len(m.group(1)) + 2,
                     prefix="• ")
            i += 1
            continue
        m = _MD_OL_RE.match(raw)                         # 1. numbered
        if m:
            add_text(m.group(3), size, indent=len(m.group(1)) + 2,
                     prefix=f"{m.group(2)}. ")
            i += 1
            continue
        m = _MD_IMG_ONLY_RE.match(s)                     # image on a line of its own
        if m:
            img = _md_open_image(m.group(2), base_dir)
            if img is not None:
                spec = _md_size_spec(m.group(3), width)
                target = max(16, min(width, spec or width))
                img = img.convert("L")
                ih = max(1, int(round(img.height * target / img.width)))
                img = img.resize((target, ih), Image.Resampling.LANCZOS)
                plan.append({"type": "image", "img": img, "h": ih,
                             "gap": spacing, "align": align})
            else:
                add_text(m.group(1) or m.group(2), size)
            i += 1
            continue
        add_text(s, size)                                # normaler Absatz
        i += 1
    return plan


def _md_size_spec(spec: str | None, avail: int):
    "Parse the size hint after an image (\"60%\", \"300\" = dots)."
    m = _MD_SIZE_RE.match((spec or "").strip())
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    if (m.group(2) or "") == "%":
        return max(8, int(round(avail * value / 100.0)))
    return max(8, int(round(value)))


def render_markdown(text: str, width: int, font_size: int = 30,
                    align: str = "left", bold: bool = False, spacing: int = 3,
                    thicken: int = 0, base_dir: str | None = None,
                    threshold: int = 128, dither: bool = True) -> "Image.Image":
    "Render Markdown text as a 1-bit image (headings, lists, images ...).\n\n    Supports # ## ###, **bold**, *italic*, ~~strikethrough~~, `code`,\n    - bullets, 1. lists, > quotes, --- rules and ![image](path) - also\n    inside a line of text.  Returns None when there is nothing to draw.\n    "
    from PIL import Image, ImageDraw

    plan = _md_plan(text, width, font_size, bold, align, spacing, base_dir)
    if not plan:
        return None
    total = sum(it["gap"] + it["h"] for it in plan)
    img = Image.new("L", (width, max(1, total)), 255)
    d = ImageDraw.Draw(img)
    y = 0
    for it in plan:
        y += it["gap"]
        if it["type"] == "hr":
            d.rectangle([0, y + 1, width - 1, y + 1], fill=0)
            y += it["h"]
            continue
        if it["type"] == "image":
            im = it["img"]
            x = 0 if it.get("align") != "center" else (width - im.width) // 2
            img.paste(im, (int(x), y))
            y += it["h"]
            continue
        atoms = it["atoms"]
        space_w = _text_width(" ", it["font"])
        tot = sum(a[3] for a in atoms) + space_w * max(0, len(atoms) - 1)
        x0, avail = it["indent"], width - it["indent"]
        if it["align"] == "center":
            x = x0 + max(0.0, (avail - tot) / 2.0)
        elif it["align"] == "right":
            x = x0 + max(0.0, avail - tot)
        else:
            x = float(x0)
        if it["bar"]:
            d.rectangle([max(0, x0 - 10), y, max(0, x0 - 7),
                         y + it["h"] - 1], fill=0)
        for a in atoms:
            if a[0] == "t":
                _, txt, style, w_a = a
                f = _md_font(it["size"], style, it["base_bold"])
                asc, desc = f.getmetrics()
                d.text((x, y), txt, font=f, fill=0, stroke_width=thicken,
                       stroke_fill=0)
                if style.get("link"):
                    d.line([(x, y + asc + 1), (x + w_a, y + asc + 1)], fill=0)
                if style.get("strike"):
                    d.line([(x, y + (asc + desc) * 0.45),
                            (x + w_a, y + (asc + desc) * 0.45)], fill=0)
                x += w_a
            else:
                _, im, _st, w_a, h_a = a
                img.paste(im, (int(x), int(y + it["h"] - h_a)))
                x += w_a
            x += space_w
        y += it["h"]
    if dither:
        return img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return img.point(lambda p: 255 if p >= threshold else 0, mode="1")


def render_meme(bg_path: str, top_text: str = "", bottom_text: str = "",
                width: int = 500, font_size: int = 60, upper: bool = True,
                align: str = "center", margin: int = 6, brightness: int = 100,
                dither: bool = True, threshold: int = 140, outline: int = 0,
                base_dir: str | None = None) -> "Image.Image":
    "Meme: background image + text at the top/bottom with a black outline.\n\n    Like in a meme generator the text is shrunk automatically until it fits\n    the width and then drawn on the image with a thick black outline - the\n    outline stays solidly black on thermal paper and keeps the text\n    readable even on busy photos.\n    "
    from PIL import Image, ImageDraw, ImageOps

    path = bg_path
    if base_dir and not os.path.isabs(bg_path):
        if not os.path.exists(path):
            path = os.path.join(base_dir, bg_path)
    img = load_raster_image(path, px_width=max(900, int(width) * 2)).convert("L")
    img = ImageOps.autocontrast(img)
    img = img.resize((width, max(1, round(img.height * width / img.width))),
                     Image.Resampling.LANCZOS)
    if brightness != 100:
        f = 100.0 / brightness          # >100 -> Faktor <1 -> dunkler
        img = img.point(lambda p: max(0, min(255, int(p * f))))
    rgb = img.convert("RGB")
    d = ImageDraw.Draw(rgb)
    limit = max(20, width - 2 * margin)

    def draw_block(txt, at_top):
        txt = (txt or "").strip()
        if not txt:
            return
        if upper:
            txt = txt.upper()
        for size in range(int(font_size), 8, -2):
            f = load_font_for_text(txt, size, bold=True)
            lines = _wrap(txt, f, limit)
            if all(_text_width(ln, f) <= limit for ln in lines):
                break
        else:
            size = 9
            f = load_font_for_text(txt, size, bold=True)
            lines = _wrap(txt, f, limit)
        asc, desc = f.getmetrics()
        lh = asc + desc
        stroke = max(2, int(round(outline or size / 9.0)))
        y = margin if at_top else max(margin, rgb.height - margin - lh * len(lines))
        for ln in lines:
            w_ = _text_width(ln, f)
            x = ((width - w_) / 2 if align == "center"
                 else width - margin - w_ if align == "right" else margin)
            d.text((x, y), ln, font=f, fill=(255, 255, 255),
                   stroke_width=stroke, stroke_fill=(0, 0, 0))
            y += lh

    draw_block(top_text, True)
    draw_block(bottom_text, False)
    if dither:
        return rgb.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return rgb.convert("L").point(lambda p: 255 if p >= threshold else 0,
                                  mode="1")


def _layout_text(txt: str, size: int, width: int, margin: int, upper: bool):
    "Wrap the text of one block - shrink the font until everything fits."
    txt = (txt or "").strip()
    if not txt:
        return None
    if upper:
        txt = txt.upper()
    limit = max(20, width - 2 * margin)
    for size in range(int(size), 8, -2):
        f = load_font_for_text(txt, size, bold=True)
        lines = _wrap(txt, f, limit)
        if all(_text_width(ln, f) <= limit for ln in lines):
            return f, lines
    f = load_font_for_text(txt, 9, bold=True)
    return f, _wrap(txt, f, limit)


def render_blocks(bg_path: str | None = None, blocks: list | None = None,
                  width: int = 500, margin: int = 8, threshold: int = 140,
                  dither: bool = True, invert: bool = False,
                  brightness: int = 100, base_dir: str | None = None):
    "Layout built from a background image plus any number of text blocks.\n\n    `blocks` = list of dicts:\n        text  - content (\\n = line break)\n        pos   - \"top\" | \"middle\" | \"bottom\" (default \"top\")\n        style - \"outline\" (white text, black outline - meme look),\n                \"bar\" (white text on a black bar) or\n                \"black\" (plain black text)\n        size  - font size in px (shrunk automatically if needed)\n        align - \"left\" | \"center\" | \"right\"\n        upper - upper case (default True)\n\n    The background image is processed with the same values as in the image\n    tab (threshold/dithering/invert/brightness).  Without a background a\n    white sheet of the required height is created.\n    "
    from PIL import Image, ImageDraw, ImageOps

    blocks = [b for b in (blocks or [])
              if str(b.get("text", "")).strip()]
    # ---- Hintergrund vorbereiten ----
    bg = None
    if bg_path:
        path = bg_path
        if base_dir and not os.path.isabs(bg_path) and not os.path.exists(path):
            path = os.path.join(base_dir, bg_path)
        bg = load_raster_image(path,
                               px_width=max(900, int(width) * 2)).convert("L")
        bg = ImageOps.autocontrast(bg)
        bg = bg.resize((width, max(1, round(bg.height * width / bg.width))),
                       Image.Resampling.LANCZOS)
        if brightness != 100:
            f_ = 100.0 / brightness          # >100 -> Faktor <1 -> dunkler
            bg = bg.point(lambda p: max(0, min(255, int(p * f_))))
        if invert:
            bg = ImageOps.invert(bg)

    # ---- measure the blocks (shrink the font if needed) ----
    items = []
    for b in blocks:
        res = _layout_text(str(b.get("text", "")), int(b.get("size", 60)),
                           width, margin, bool(b.get("upper", True)))
        if res is None:
            continue
        f, lines = res
        asc, desc = f.getmetrics()
        lh = asc + desc
        items.append({
            "font": f, "lines": lines, "lh": lh, "h": lh * len(lines),
            "pos": (b.get("pos") or "top").lower(),
            "style": (b.get("style") or "outline").lower(),
            "align": (b.get("align") or "center").lower(),
            "pad": max(2, int(round(getattr(f, "size", 20) / 7.0))),
            "width": max(_text_width(ln, f) for ln in lines),
        })
    if not items:
        if bg is None:
            return None
        # only the background image (no text filled in yet)
        if dither:
            return bg.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        return bg.point(lambda p: 255 if p >= threshold else 0, mode="1")

    gap = 8
    top = [i for i in items if i["pos"] == "top"]
    mid = [i for i in items if i["pos"] == "middle"]
    bottom = [i for i in items if i["pos"] == "bottom"]
    rest = [i for i in items if i not in top + mid + bottom]
    top += rest                                     # unknown -> top
    need = sum(i["h"] + 2 * i["pad"] + gap for i in top + mid + bottom) + margin

    height = max(bg.height if bg is not None else 1, need)
    canvas = (bg.convert("RGB") if bg is not None
              else Image.new("RGB", (width, 1), (255, 255, 255)))
    if canvas.height != height:                      # extend the canvas
        ext = Image.new("RGB", (width, height), (255, 255, 255))
        ext.paste(canvas, (0, 0))
        canvas = ext
    d = ImageDraw.Draw(canvas)
    H = canvas.height

    def draw_block(it, y0):
        "Draw one block at y0 (returns the y below it)."
        pad = it["pad"]
        style = it["style"]
        if style == "outline" and bg is None:
            style = "black"                 # outline text would vanish on white
        y = y0 + pad
        if style == "bar":                            # black bar
            bx0 = {"left": margin, "right": width - margin - it["width"]}.get(
                it["align"], (width - it["width"]) / 2.0)
            d.rectangle([max(0, bx0 - pad), max(0, y0),
                         min(width - 1, bx0 + it["width"] + pad),
                         min(H - 1, y0 + it["h"] + 2 * pad)], fill=0)
        for ln in it["lines"]:
            w_ = _text_width(ln, it["font"])
            x = {"left": margin, "right": width - margin - w_}.get(
                it["align"], (width - w_) / 2.0)
            if style == "bar":
                x = {"left": margin + pad,
                     "right": width - margin - w_ - pad}.get(
                         it["align"], (width - w_) / 2.0)
            if style == "outline":
                d.text((x, y), ln, font=it["font"], fill=(255, 255, 255),
                       stroke_width=max(2, it["pad"]), stroke_fill=(0, 0, 0))
            elif style == "bar":
                d.text((x, y), ln, font=it["font"], fill=(255, 255, 255))
            else:
                d.text((x, y), ln, font=it["font"], fill=(0, 0, 0))
            y += it["lh"]
        return y0 + it["h"] + 2 * pad

    y = margin                                        # top (in order)
    for it in top:
        y = draw_block(it, y) + gap
    if bottom:                                           # bottom (from the bottom up)
        y = H - margin - sum(i["h"] + 2 * i["pad"] for i in bottom) \
            - gap * (len(bottom) - 1)
        for it in bottom:
            y = draw_block(it, y) + gap
    if mid:                                           # middle (centred)
        y = (H - sum(i["h"] + 2 * i["pad"] for i in mid)
             - gap * (len(mid) - 1)) / 2.0
        for it in mid:
            y = draw_block(it, y) + gap
    if dither:
        return canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return canvas.convert("L").point(lambda p: 255 if p >= threshold else 0,
                                     mode="1")


def _flatten_alpha(img):
    "Flatten transparency onto white (otherwise transparent areas print black)."
    from PIL import Image

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def load_raster_image(path: str, px_width: int = 900):
    "Load an image as a PIL image - including **SVG** (vector graphics).\n\n    SVG is rendered by the system renderer (GdkPixbuf/rsvg loader); if that\n    is missing, `cairosvg` or finally Inkscape is used.\n    "
    from io import BytesIO

    from PIL import Image

    low = str(path).lower()
    if not low.endswith((".svg", ".svgz")):
        try:
            return _flatten_alpha(Image.open(path))
        except Exception:                                    # noqa: BLE001
            pass                                             # maybe SVG without an extension

    err = None
    try:                                                     # 1) GdkPixbuf
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, int(px_width), -1)
        ok, buf = pb.save_to_bufferv("png", [], [])
        if ok and buf:
            return _flatten_alpha(Image.open(BytesIO(buf)))
    except Exception as e:                                   # noqa: BLE001
        err = e
    try:                                                     # 2) cairosvg
        import cairosvg

        png = cairosvg.svg2png(url=path, output_width=int(px_width))
        return _flatten_alpha(Image.open(BytesIO(png)))
    except Exception as e:                                   # noqa: BLE001
        err = e
    import shutil                                            # 3) Inkscape
    import tempfile

    exe = shutil.which("inkscape") or os.path.expanduser("~/bin/inkscape")
    if exe and os.path.exists(exe):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "svg.png")
                env = dict(os.environ)
                for k in ("GTK_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR",
                          "GTK_EXE_PREFIX", "GTK_IM_MODULE_FILE", "LOCPATH"):
                    env.pop(k, None)
                subprocess.run([exe, "--export-type=png",
                                f"--export-filename={out}",
                                f"--export-width={int(px_width)}", path],
                               check=True, capture_output=True, timeout=180,
                               env=env)
                return _flatten_alpha(Image.open(out))
        except Exception as e:                               # noqa: BLE001
            err = e
    raise SystemExit(
        f"SVG could not be rendered ({path}): {err}\n"
        "Install a renderer (no sudo needed):\n"
        "  pip3 install --user --break-system-packages cairosvg"
    )


def render_image_file(path: str, width: int, threshold: int = 140,
                      dither: bool = True, invert: bool = False,
                      brightness: int = 100) -> "Image.Image":
    "Load an image (PNG/JPG/SVG/...), scale it to the content width, 1 bit.\n\n    `brightness` = print darkness in %: 100 = default, >100 darker (the\n    image is darkened before binarisation), <100 lighter.\n    "
    from PIL import Image, ImageOps

    img = load_raster_image(path, px_width=max(900, int(width) * 2)).convert("L")
    img = ImageOps.autocontrast(img)
    if img.width != width:
        new_h = max(1, round(img.height * width / img.width))
        img = img.resize((width, new_h), Image.Resampling.LANCZOS)
    if brightness != 100:
        f = 100.0 / brightness          # >100 -> Faktor <1 -> dunkler
        img = img.point(lambda p: max(0, min(255, int(p * f))))
    if invert:
        img = ImageOps.invert(img)
    if dither:
        bw = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    else:
        bw = img.point(lambda p: 255 if p >= threshold else 0, mode="1")
    return bw


# --------------------------------------------------------------------------
# Barcodes / QR
# --------------------------------------------------------------------------
_EAN_L = {
    "0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101",
    "4": "0100011", "5": "0110001", "6": "0101111", "7": "0111011",
    "8": "0110111", "9": "0001011",
}
_EAN_G = {
    "0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001",
    "4": "0011101", "5": "0111001", "6": "0000101", "7": "0010001",
    "8": "0001001", "9": "0010111",
}
_EAN_R = {
    "0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010",
    "4": "1011100", "5": "1001110", "6": "1010000", "7": "1000100",
    "8": "1001000", "9": "1110100",
}
_EAN_PARITY = {
    "0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL",
    "4": "LGLLGG", "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG",
    "8": "LGLGGL", "9": "LGGLGL",
}


def _ean_check(digits12: str) -> str:
    s = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits12))
    return str((10 - s % 10) % 10)


def render_ean13(digits: str, scale: int = 3, width: int | None = None,
                 show_text: bool = True) -> "Image.Image":
    "Draw an EAN-13 barcode as a 1-bit image (0 = black)."
    from PIL import Image, ImageDraw

    digits = re.sub(r"\D", "", digits)
    if len(digits) == 12:
        digits += _ean_check(digits)
    if len(digits) != 13:
        raise ValueError("EAN-13 needs 12 or 13 digits")

    first, rest = digits[0], digits[1:]
    parity = _EAN_PARITY[first]
    left = rest[:6]
    right = rest[6:]
    bits = "101"
    for d, p in zip(left, parity):
        bits += _EAN_G[d] if p == "G" else _EAN_L[d]
    bits += "01010"
    for d in right:
        bits += _EAN_R[d]
    bits += "101"

    quiet = 4
    if width and (len(bits) + 2 * quiet) * scale > width:
        # shrink the scale until barcode + quiet zones fit
        scale = max(1, width // (len(bits) + 2 * quiet))
    bar_w = len(bits) * scale + 2 * quiet * scale
    font = _load_font(max(10, scale * 3))
    asc, desc = font.getmetrics()
    text_h = (asc + desc) if show_text else 0
    img = Image.new("1", (bar_w, scale * 92 + text_h + 2), 1)
    d = ImageDraw.Draw(img)
    x = quiet * scale
    for ch in bits:
        if ch == "1":
            d.rectangle([x, 0, x + scale - 1, scale * 92 - 1], fill=0)
        x += scale
    if show_text:
        txt = digits[0] + "  " + digits[1:7] + "  " + digits[7:]
        tw = _text_width(txt, font)
        d.text(((bar_w - tw) / 2, scale * 92 + 1), txt, font=font, fill=0)
    return img


def _qr_escape(value: str) -> str:
    "Escape special characters for WLAN QR strings (\\ ; , : \")."
    return "".join("\\" + c if c in "\\;,:\"" else c for c in str(value))


def qr_payload(kind: str = "text", **f) -> str:
    "Build the QR payload for the common use cases.\n\n    kind: text | url | wifi | vcard | email | tel | sms | geo\n    fields: text, url, ssid, password, security, hidden, name, phone, email,\n            org, homepage, to, subject, body, sms_to, sms_text, lat, lon\n    "
    kind = (kind or "text").strip().lower()

    def get(key, default=""):
        v = f.get(key, default)
        return "" if v is None else str(v)

    if kind == "url":
        url = get("url") or get("text")
        if url and "://" not in url and not url.lower().startswith("mailto:"):
            url = "https://" + url
        return url
    if kind == "wifi":
        sec = (get("security") or "WPA").strip() or "WPA"
        out = [f"WIFI:T:{sec};", f"S:{_qr_escape(get('ssid'))};"]
        if sec.lower() not in ("nopass", "none"):
            out.append(f"P:{_qr_escape(get('password'))};")
        if get("hidden").lower() in ("1", "true", "yes", "ja"):
            out.append("H:true;")
        return "".join(out) + ";"
    if kind == "vcard":
        lines = ["BEGIN:VCARD", "VERSION:3.0"]
        name = get("name")
        if name:
            lines.append(f"N:{_qr_escape(name)};;;;")
            lines.append(f"FN:{_qr_escape(name)}")
        if get("org"):
            lines.append(f"ORG:{_qr_escape(get('org'))}")
        if get("phone"):
            lines.append(f"TEL;TYPE=CELL:{get('phone')}")
        if get("email"):
            lines.append(f"EMAIL:{get('email')}")
        if get("homepage"):
            lines.append(f"URL:{get('homepage')}")
        lines.append("END:VCARD")
        return "\r\n".join(lines)
    if kind == "email":
        from urllib.parse import quote

        to = get("to")
        q = []
        if get("subject"):
            q.append("subject=" + quote(get("subject")))
        if get("body"):
            q.append("body=" + quote(get("body")))
        return f"mailto:{to}" + (("?" + "&".join(q)) if q else "")
    if kind == "tel":
        return "tel:" + get("phone").replace(" ", "")
    if kind == "sms":
        to = get("sms_to") or get("to") or get("phone")
        msg = get("sms_text") or get("body") or get("text")
        return f"SMSTO:{to}:{msg}"
    if kind == "geo":
        return f"geo:{get('lat')},{get('lon')}"
    return get("text") or get("url")


def render_qrcode(content: str, box_px: int = 12, ecc: str = "M",
                  max_px: int | None = None) -> "Image.Image":
    "QR code via the optional 'qrcode' library.  Raises a helpful error.\n\n    `ecc` = error correction L/M/Q/H (H is the most robust against dirt).\n    `max_px` = maximum image width: the module size is then chosen so the\n    code fits exactly (which keeps it sharp).\n    "
    try:
        import qrcode
        from qrcode.constants import (ERROR_CORRECT_H, ERROR_CORRECT_L,
                                      ERROR_CORRECT_M, ERROR_CORRECT_Q)
    except ImportError:
        raise SystemExit(
            "QR codes need the 'qrcode' library.\nInstall it (no sudo needed):\n  pip3 install --user --break-system-packages qrcode"
        )
    levels = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M,
              "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
    qr = qrcode.QRCode(
        error_correction=levels.get((ecc or "M").upper(), ERROR_CORRECT_M),
        box_size=box_px, border=2)
    qr.add_data(content)
    qr.make(fit=True)
    if max_px:
        modules = qr.modules_count + 2 * qr.border
        qr.box_size = max(1, min(box_px, int(max_px) // max(1, modules)))
    return qr.make_image(fill_color="black", back_color="white").convert("1")


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def _brightness_density(brightness: int) -> int:
    "Print darkness in % (100 = default) -> density byte for command 0x09.\n\n    IMPORTANT (measured on the device): the density field is only 4 bits\n    wide (0..0x0f).  Larger values (e.g. 0x13) are read by the printer as a\n    smaller value and print LIGHTER.  Therefore: curve 100 % = 0x0c, darker\n    up to 0x0f, lighter down to 0x02 - strictly monotonic so a higher\n    percentage never prints lighter.\n    "
    b = max(0, min(200, brightness))
    if b <= 100:
        d = 0x02 + round((0x0C - 0x02) * b / 100.0)
    else:
        d = 0x0C + round((0x0F - 0x0C) * (b - 100) / 100.0)
    return max(0x02, min(0x0F, d))


def _common_opts(p: argparse.ArgumentParser):
    p.add_argument("--mac", default=None, help="Bluetooth MAC (default: auto)")
    p.add_argument("--width", type=int, default=CONTENT_WIDTH,
                   help=f"content width in dots (default {CONTENT_WIDTH})")
    p.add_argument("--dots", type=int, default=DEFAULT_DOTS,
                   help=f"raster width of the print head (default {DEFAULT_DOTS})")
    p.add_argument("--left", type=int, default=PAPER_LEFT,
                   help=f"left edge of the content area inside the raster (default {PAPER_LEFT})")
    p.add_argument("--speed", type=lambda s: int(s, 0), default=DEFAULT_SPEED,
                   help="print speed (hex, default 0x55)")
    p.add_argument("--density", type=lambda s: int(s, 0), default=None,
                   help="print density (hex); default: computed from --brightness")
    p.add_argument("--brightness", type=int, default=100, metavar="PERCENT",
                   help="print darkness in %% (100 = default, >100 darker, <100 lighter; default 100)")
    p.add_argument("--zoom", type=int, default=100, metavar="PERCENT",
                   help="content size in %% (100 = default/fitted to the print window, <100 smaller, >100 larger)")
    p.add_argument("--rotate", type=float, default=0.0, metavar="DEG",
                   help="rotate the content (degrees, positive = counter-clockwise)")
    p.add_argument("--flip", default="", metavar="h,v",
                   help="flip: 'h' = horizontal, 'v' = vertical (e.g. hv)")
    p.add_argument("--stretch", default=None, metavar="X[,Y]",
                   help="stretch/squeeze the content in %% (e.g. 120 or 120,80)")
    p.add_argument("--trim", action="store_true",
                   help="trim empty (white) borders off the content")
    p.add_argument("--feed", type=int, default=DEFAULT_FEED,
                   help="feed steps after printing")
    p.add_argument("--mode", choices=["app", "fast"], default=DEFAULT_MODE,
                   help="print mode (both send without pauses, the printer's "
                        "buffer sets the pace): 'app' = one raster frame "
                        "(432 B) per block, 'fast' = 16 KB blocks "
                        f"(default: {DEFAULT_MODE})")
    p.add_argument("--protocol", choices=list(PROTOCOLS), default="x3",
                   help="printer protocol: 'x3' (Snap & Tag / ORGBRO) or 'escpos' (standard thermal printers, e.g. 58/80 mm)")
    p.add_argument("--delay", type=float, default=None,
                   help="artificial delay per block in s (0 = off; only needed for special printers)")
    p.add_argument("--out", default=None, metavar="FILE",
                   help="preview only: save as PNG instead of printing")


def _make_printer(args) -> X3Printer:
    density = getattr(args, "density", None)
    if density is None:
        density = _brightness_density(getattr(args, "brightness", 100))
    return make_printer(getattr(args, "protocol", "x3"),
                        mac=args.mac, width=args.dots, speed=args.speed,
                        density=density, feed=args.feed,
                        mode=getattr(args, "mode", DEFAULT_MODE),
                        delay=getattr(args, "delay", None))


def _edit_opts(args):
    "Read the image-editing options (CLI strings or GUI values).\n\n    Returns: (flip_h, flip_v, angle, stretch_x, stretch_y, trim, threshold)\n    "
    flip = str(getattr(args, "flip", "") or "").lower()
    flip_h = bool(getattr(args, "flip_h", False)) or ("h" in flip)
    flip_v = bool(getattr(args, "flip_v", False)) or ("v" in flip)
    angle = float(getattr(args, "rotate", 0) or 0)
    st = getattr(args, "stretch", None)
    sx = sy = 100
    if isinstance(st, str) and st.strip():
        parts = st.replace("x", ",").replace(":", ",").split(",")
        try:
            sx = int(round(float(parts[0])))
            sy = (int(round(float(parts[1])))
                  if len(parts) > 1 and parts[1].strip() else sx)
        except ValueError:
            sx = sy = 100
    else:
        sx = int(round(float(getattr(args, "stretch_x", 100) or 100)))
        sy = int(round(float(getattr(args, "stretch_y", 100) or 100)))
    sx = max(5, min(400, sx))
    sy = max(5, min(400, sy))
    trim = bool(getattr(args, "trim", False))
    thr = int(float(getattr(args, "threshold", 128) or 128))
    return flip_h, flip_v, angle, sx, sy, trim, max(1, min(254, thr))


def _trim(img):
    "Trim empty (white) borders all around."
    from PIL import Image

    inv = Image.eval(img.convert("L"), lambda v: 255 - v)
    bbox = inv.getbbox()
    if bbox and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
        return img.crop(bbox)
    return img


def _edit(content_img, args):
    "Image editing before the resize.\n\n    Order: flip -> rotate (limited to the window width if needed) ->\n    trim borders -> stretch (X/Y separately).  Without active options the\n    image comes back unchanged.\n    "
    from PIL import Image

    flip_h, flip_v, angle, sx, sy, trim, thr = _edit_opts(args)
    img = content_img
    if flip_h:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if flip_v:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    if abs(angle) % 360.0 > 0.01:
        a = angle % 360.0
        if abs(a - round(a / 90) * 90) < 0.01:        # 90/180/270 lossless
            img = img.transpose({90: Image.ROTATE_90, 180: Image.ROTATE_180,
                                 270: Image.ROTATE_270}[int(round(a)) % 360])
        else:
            img = (img.convert("L")
                   .rotate(a, resample=Image.BICUBIC, expand=True, fillcolor=255)
                   .point(lambda v: 0 if v < thr else 255, mode="1"))
        wmax = int(getattr(args, "width", 0) or 0)
        if wmax and img.width > wmax:          # do not run out of the print window
            nh = max(1, int(round(img.height * wmax / img.width)))
            img = (img.convert("L").resize((wmax, nh), Image.LANCZOS)
                   .point(lambda v: 0 if v < thr else 255, mode="1"))
    if trim:
        img = _trim(img)
    if sx != 100 or sy != 100:
        nw = max(1, int(round(img.width * sx / 100.0)))
        nh = max(1, int(round(img.height * sy / 100.0)))
        img = (img.convert("L").resize((nw, nh), Image.LANCZOS)
               .point(lambda v: 0 if v < thr else 255, mode="1"))
    return img


def _scaled(content_img, args):
    "Edit the content (rotate/flip/...) and scale it by `args.scale` %.\n\n    Returns: (image, left edge inside the raster).  100 % = unchanged\n    (fitted to the window), <100 smaller (more margin), >100 larger\n    (sticks out over the window/paper - that part is not printed).\n    "
    pct = getattr(args, "content_scale", None)
    if pct is None:
        pct = getattr(args, "zoom", 100)
    pct = int(pct or 100)
    f = max(5, min(400, pct)) / 100.0

    edited = _edit(content_img, args)
    if abs(f - 1.0) < 0.005:
        img = edited
    else:
        from PIL import Image

        nw = max(8, int(round(edited.width * f)))
        nh = max(1, int(round(edited.height * f)))
        img = (edited.convert("L")
               .resize((nw, nh), Image.LANCZOS)
               .point(lambda v: 0 if v < 128 else 255, mode="1"))
    left = (int(getattr(args, "left", 0))
            + (int(getattr(args, "width", img.width)) - img.width) // 2)
    return img, left


def _embed(content_img, args):
    "Embed a content image (width args.width) into the raster.\n\n    The content may be positioned freely (even negative or beyond the\n    border) - everything outside the raster is cut off."
    from PIL import Image

    if content_img.width != args.width:
        raise ValueError(f"Content width {content_img.width} != --width {args.width}")
    canvas = Image.new("1", (args.dots, content_img.height), 1)
    canvas.paste(content_img, (int(args.left), 0))
    return canvas


def _print_job(args, content_image):
    content_image, left = _scaled(content_image, args)
    canvas = _embed(content_image, SimpleNamespace(width=content_image.width,
                                                   left=left, dots=args.dots))
    if getattr(args, "out", None):
        # preview only: save as PNG, do not print anything
        scale = 2
        canvas.resize((canvas.width * scale, canvas.height * scale),
                      __import__("PIL").Image.Resampling.NEAREST).save(args.out)
        print(f"Preview saved: {args.out} "
              f"(content {content_image.width}x{content_image.height} px "
              f"inside the {args.dots}-dot raster)")
        return
    with _make_printer(args).connect() as p:
        p.print_image(canvas)
    print(f"Print job sent (content {content_image.width}x{content_image.height} "
          f"px inside the {args.dots}-dot raster).")


def cmd_status(args):
    with _make_printer(args).connect() as p:
        print(f"Connected to {p.mac} (channel {RFCOMM_CHANNEL})")
        fw = p.firmware()
        print(f"Firmware response: {fw.hex()}")
        st = p.status()
        print(f"Status response  : {st.hex()}")
        if not fw and not st:
            print("Hint: the printer does not respond - is it switched on and paired?")


def cmd_text(args):
    text = args.text or sys.stdin.read()
    if not text.strip():
        raise SystemExit("No text given.")
    img = render_text(text, args.width, font_size=args.size, align=args.align,
                      bold=args.bold, spacing=args.spacing, thicken=args.thicken)
    _print_job(args, img)


def cmd_image(args):
    img = render_image_file(args.file, args.width, threshold=args.threshold,
                            dither=args.dither, invert=args.invert,
                            brightness=args.brightness)
    _print_job(args, img)


def cmd_ean13(args):
    img = render_ean13(args.code, scale=args.scale, width=args.width)
    _print_job(args, img)


def cmd_markdown(args):
    "Print Markdown (# headings, lists, images, ...)."
    if getattr(args, "file", None):
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = " ".join(args.text) if args.text else sys.stdin.read()
    if not text.strip():
        raise SystemExit("No text given.")
    img = render_markdown(text, args.width, font_size=args.size,
                          align=args.align, bold=args.bold,
                          spacing=args.spacing, thicken=args.thicken,
                          base_dir=(os.path.dirname(os.path.abspath(args.file))
                                    if getattr(args, "file", None) else os.getcwd()),
                          dither=args.dither, threshold=args.threshold)
    if img is None:
        raise SystemExit("Nothing to print (empty text).")
    _print_job(args, img)


def cmd_meme(args):
    "Print a meme: text at the top/bottom of a background image."
    img = render_meme(args.image, args.top, args.bottom, width=args.width,
                      font_size=args.size, upper=not args.no_upper,
                      align=args.align, margin=args.margin,
                      brightness=args.brightness, dither=args.dither,
                      threshold=args.threshold)
    _print_job(args, img)


def cmd_qr(args):
    payload = qr_payload(args.type, text=args.content or "", url=args.url,
                         ssid=args.ssid, password=args.password,
                         security=args.security, hidden=args.hidden,
                         name=args.name, phone=args.phone, email=args.email,
                         org=args.org, homepage=args.homepage, to=args.to,
                         subject=args.subject, body=args.body,
                         sms_to=args.sms_to, sms_text=args.sms_text,
                         lat=args.lat, lon=args.lon)
    if not payload.strip():
        raise SystemExit("No QR payload given (pass content or --ssid/--name/...).")
    print(f"QR payload: {payload}")
    img = render_qrcode(payload, box_px=args.box, ecc=args.ecc,
                        max_px=args.width)
    if img.width > args.width:
        from PIL import Image
        img = img.resize(
            (args.width, max(1, round(img.height * args.width / img.width))),
            Image.Resampling.NEAREST)
    _print_job(args, img)


def cmd_feed(args):
    with _make_printer(args).connect() as p:
        p.feed(args.steps)
    print(f"Feed: {args.steps} steps sent.")


def cmd_demo(args):
    from PIL import Image

    parts = []
    parts.append(render_text("X3 Connector", args.width,
                             font_size=52, align="center", bold=True))
    parts.append(render_text("PC -> Bluetooth -> printer", args.width,
                             font_size=28, align="center"))
    parts.append(render_text("www.deadlinedriven.dev", args.width,
                             font_size=28, align="center"))
    parts.append(render_text("Ä Ö Ü ß € - umlauts", args.width,
                             font_size=28, align="center"))
    parts.append(render_ean13("4006381333931", scale=3, width=args.width))
    try:
        qr = render_qrcode("https://www.deadlinedriven.dev", box_px=9)
        if qr.width <= args.width:
            parts.append(qr)
    except SystemExit:
        pass
    parts.append(render_text("— End —", args.width,
                             font_size=24, align="center"))

    total_h = sum(im.height for im in parts)
    canvas = Image.new("1", (args.width, total_h), 1)
    y = 0
    for im in parts:
        canvas.paste(im, (0, y))
        y += im.height
    _print_job(args, canvas)


def cmd_calibrate(args):
    "Print marks across the full raster width (--dots) to find the\n    real printable area on the paper."
    from PIL import Image, ImageDraw

    W = args.dots
    H = 200
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, 2], fill=0)          # top
    d.rectangle([0, H - 3, W - 1, H - 1], fill=0)  # bottom
    step = W // 16
    for i, x in enumerate(range(0, W, step)):
        t = (i % 4) + 1
        d.rectangle([x, 4, min(x + t - 1, W - 1), H - 4], fill=0)
    d.rectangle([W - 30, 4, W - 1, H - 4], fill=0)  # right block
    # labelling of the content area [left, left+width)
    d.rectangle([args.left, H - 24, args.left + args.width - 1, H - 22], fill=0)
    with _make_printer(args).connect() as p:
        p.print_image(img)
    print(f"Calibration over {W} dots printed (marks every {step} dots).")


def main():
    p = argparse.ArgumentParser(
        prog="x3print.py",
        description="Connector for the Snap & Tag thermal printer X3 (YK/YZW protocol over Bluetooth SPP, 864 dots @ 300 dpi).",
    )
    p.add_argument("--version", action="version",
                   version=f"x3print.py {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="check connection + firmware/status")
    _common_opts(sp)

    sp = sub.add_parser("text", help="print text (argument or stdin)")
    sp.add_argument("text", nargs="*", help="text; without an argument stdin is read")
    sp.add_argument("--size", type=int, default=30, help="font size (px)")
    sp.add_argument("--align", choices=["left", "center", "right"], default="left")
    sp.add_argument("--bold", action="store_true")
    sp.add_argument("--spacing", type=int, default=3, help="extra px between lines")
    sp.add_argument("--thicken", type=int, default=0,
                    help="stroke width around the letters in px (0 = none)")
    _common_opts(sp)

    sp = sub.add_parser("file", help="print a text file")
    sp.add_argument("file")
    sp.add_argument("--size", type=int, default=30)
    sp.add_argument("--align", choices=["left", "center", "right"], default="left")
    sp.add_argument("--bold", action="store_true")
    sp.add_argument("--spacing", type=int, default=3)
    sp.add_argument("--thicken", type=int, default=0)
    _common_opts(sp)

    sp = sub.add_parser("image", help="print an image file (png/jpg/...)")
    sp.add_argument("file")
    sp.add_argument("--threshold", type=int, default=140)
    sp.add_argument("--no-dither", dest="dither", action="store_false", default=True)
    sp.add_argument("--invert", action="store_true")
    _common_opts(sp)

    sp = sub.add_parser("ean13", help="print an EAN-13 barcode")
    sp.add_argument("code", help="12 or 13 digits")
    sp.add_argument("--scale", type=int, default=3, help="module width in px")
    _common_opts(sp)

    sp = sub.add_parser("qr", help="print a QR code (website, WLAN, contact, ...)")
    sp.add_argument("content", nargs="?", default=None,
                    help="content for --type text/url (e.g. the address)")
    sp.add_argument("--type", default="text",
                    choices=["text", "url", "wifi", "vcard", "email",
                             "tel", "sms", "geo"],
                    help="kind of QR code (default: text)")
    sp.add_argument("--ecc", default="M", choices=["L", "M", "Q", "H"],
                    help="error correction (default M; H = more robust)")
    sp.add_argument("--url", default="", help="web address (--type url)")
    sp.add_argument("--ssid", default="", help="WLAN name (--type wifi)")
    sp.add_argument("--password", default="", help="WLAN password (--type wifi)")
    sp.add_argument("--security", default="WPA", choices=["WPA", "WEP", "nopass"],
                    help="WLAN encryption (default WPA)")
    sp.add_argument("--hidden", action="store_true",
                    help="WLAN with a hidden SSID (--type wifi)")
    sp.add_argument("--name", default="", help="name (--type vcard)")
    sp.add_argument("--phone", default="", help="phone number (vcard/tel)")
    sp.add_argument("--email", default="", help="e-mail address (--type vcard)")
    sp.add_argument("--org", default="", help="company/organisation (--type vcard)")
    sp.add_argument("--homepage", default="", help="website (--type vcard)")
    sp.add_argument("--to", default="", help="recipient (--type email)")
    sp.add_argument("--subject", default="", help="subject (--type email)")
    sp.add_argument("--body", default="", help="message (--type email)")
    sp.add_argument("--sms-to", default="", help="number (--type sms)")
    sp.add_argument("--sms-text", default="", help="text (--type sms)")
    sp.add_argument("--lat", default="", help="latitude (--type geo)")
    sp.add_argument("--lon", default="", help="longitude (--type geo)")
    sp.add_argument("--box", type=int, default=12, help="module size in px")
    _common_opts(sp)

    sp = sub.add_parser("meme", help="print a meme: text on a background image")
    sp.add_argument("image", help="background image (png/jpg/svg)")
    sp.add_argument("--top", default="", help="text at the top")
    sp.add_argument("--bottom", default="", help="text at the bottom")
    sp.add_argument("--size", type=int, default=60, help="font size (px)")
    sp.add_argument("--no-upper", dest="no_upper", action="store_true",
                    help="do NOT upper-case the text")
    sp.add_argument("--align", choices=["left", "center", "right"],
                    default="center")
    sp.add_argument("--margin", type=int, default=6, help="margin in px")
    sp.add_argument("--threshold", type=int, default=140)
    sp.add_argument("--no-dither", dest="dither", action="store_false",
                    default=True)
    _common_opts(sp)

    sp = sub.add_parser("markdown", help="print Markdown (lists, images, ...)")
    sp.add_argument("text", nargs="*", help="text; without an argument stdin is read")
    sp.add_argument("--file", help="Markdown file (.md/.txt)")
    sp.add_argument("--size", type=int, default=30, help="base size (px)")
    sp.add_argument("--align", choices=["left", "center", "right"], default="left")
    sp.add_argument("--bold", action="store_true")
    sp.add_argument("--spacing", type=int, default=3, help="extra px between lines")
    sp.add_argument("--thicken", type=int, default=0)
    sp.add_argument("--threshold", type=int, default=128)
    sp.add_argument("--no-dither", dest="dither", action="store_false",
                    default=True)
    _common_opts(sp)

    sp = sub.add_parser("feed", help="only feed the paper")
    sp.add_argument("steps", nargs="?", type=int, default=DEFAULT_FEED)
    _common_opts(sp)

    sp = sub.add_parser("demo", help="print a demo page with text, barcode & QR")
    _common_opts(sp)

    sp = sub.add_parser("calibrate", help="print marks across the full raster width")
    _common_opts(sp)

    args = p.parse_args()
    try:
        if args.cmd == "status":
            cmd_status(args)
        elif args.cmd == "text":
            args.text = " ".join(args.text)
            cmd_text(args)
        elif args.cmd == "file":
            with open(args.file, encoding="utf-8") as f:
                args.text = f.read()
            cmd_text(args)
        elif args.cmd == "image":
            cmd_image(args)
        elif args.cmd == "ean13":
            cmd_ean13(args)
        elif args.cmd == "markdown":
            cmd_markdown(args)
        elif args.cmd == "meme":
            cmd_meme(args)
        elif args.cmd == "qr":
            cmd_qr(args)
        elif args.cmd == "feed":
            cmd_feed(args)
        elif args.cmd == "demo":
            cmd_demo(args)
        elif args.cmd == "calibrate":
            cmd_calibrate(args)
    except (ConnectionError, OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
