#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
CUPS filter "x3raster" for the Snap & Tag thermal printer X3 (YK/YZW protocol).

Reads a job (PDF or PostScript) from stdin and writes the finished YK frames
to stdout.  CUPS hands the output to the backend "x3bt", which sends the
frames to the printer over Bluetooth/RFCOMM.

Rendering: Ghostscript renders every page as a grey-scale PNG at 300 dpi.
The page is scaled to the content width (500 dots, window x=320..820 inside
the 864 dot raster), converted to 1 bit (black) with a threshold, white
borders are trimmed and everything is packed into raster frames (432 bytes =
4 rows of 108 bytes).  Between pages: short feed, at the end a long one.

Called by CUPS:  x3raster < job.pdf > frames.bin
"""

import os
import subprocess
import sys
import tempfile

from PIL import Image

# geometry (identical to x3print.py)
DOTS = 864            # raster width of the print head
BYTES_PER_ROW = DOTS // 8      # 108
ROWS_PER_CHUNK = 432 // BYTES_PER_ROW   # 4
LEFT = 320            # left edge of the content inside the raster
CONTENT_WIDTH = 500   # usable content width
PAGE_DPI = 300

# print parameters
SPEED = 0x55
FEED_BETWEEN = 60     # feed between pages (steps)
FEED_END = 200        # feed at the end

GS = "/usr/bin/gs"


def _density_from_brightness(brightness: int) -> int:
    """Print darkness in % (100 = default) -> density byte (0x09).

    The density field is only 4 bits wide (0..0x0f); higher values print
    LIGHTER on the X3.  Curve therefore 100 % = 0x0c, max 0x0f, min 0x02.
    """
    b = max(0, min(200, brightness))
    if b <= 100:
        d = 0x02 + round((0x0C - 0x02) * b / 100.0)
    else:
        d = 0x0C + round((0x0F - 0x0C) * (b - 100) / 100.0)
    return max(0x02, min(0x0F, d))


def parse_options(argv) -> int:
    """Read the print darkness from the CUPS options (X3Brightness=NNN).
    CUPS passes options as 'name=value,...' in the arguments."""
    b = 100
    for arg in argv[1:]:
        for tok in arg.split(","):
            if "=" in tok:
                k, _, v = tok.partition("=")
                if k.strip().lower() == "x3brightness":
                    try:
                        b = int(float(v))
                    except ValueError:
                        pass
    return max(10, min(300, b))


def make_frame(cmd: int, seq: int, payload: bytes = b"") -> bytes:
    """YK-Frame: 64 <cmd> <seq> <len_le16> <payload> 00 00 00 00 9b."""
    n = len(payload)
    return (
        b"\x64"
        + bytes([cmd & 0xFF, seq & 0xFF, n & 0xFF, (n >> 8) & 0xFF])
        + payload
        + b"\x00\x00\x00\x00\x9b"
    )


def render_pages(data: bytes, ext: str):
    """Render PDF/PS with Ghostscript into grey-scale PNGs (300 dpi)."""
    tmp = tempfile.mkdtemp(prefix="x3raster-")
    src = os.path.join(tmp, "in" + ext)
    with open(src, "wb") as f:
        f.write(data)
    pat = os.path.join(tmp, "page-%03d.png")
    cmd = [
        GS, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
        "-sDEVICE=pnggray", f"-r{PAGE_DPI}",
        "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
        f"-sOutputFile={pat}", src,
    ]
    subprocess.run(cmd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pages = sorted(
        os.path.join(tmp, f) for f in os.listdir(tmp)
        if f.startswith("page-") and f.endswith(".png")
    )
    return tmp, pages


def _row_has_black(px, w, y) -> bool:
    for x in range(w):
        if px[x, y] == 0:
            return True
    return False


def page_to_bw(path: str, brightness: int = 100,
               width: int = CONTENT_WIDTH) -> "Image.Image":
    """PNG -> 1 bit (0 = black), scaled to the content width, borders trimmed.

    `brightness` = print darkness in %: >100 darker (the image is darkened
    before binarisation), <100 lighter.  `width` = target width in dots.
    """
    im = Image.open(path).convert("L")
    if im.width != width:
        h = max(1, round(im.height * width / im.width))
        im = im.resize((width, h), Image.LANCZOS)
    if brightness != 100:
        f = 100.0 / brightness          # >100 -> Faktor <1 -> dunkler
        im = im.point(lambda p: max(0, min(255, int(p * f))))
    bw = im.point(lambda p: 0 if p < 128 else 255, mode="1")

    # remove white borders top/bottom (keep a small margin)
    w, h = bw.size
    px = bw.load()
    top = 0
    while top < h and not _row_has_black(px, w, top):
        top += 1
    bottom = h - 1
    while bottom >= 0 and not _row_has_black(px, w, bottom):
        bottom -= 1
    if top > bottom:                    # empty page
        return Image.new("1", (width, 8), 1)
    top = max(0, top - 12)
    bottom = min(h - 1, bottom + 8)
    return bw.crop((0, top, w, bottom + 1))


def embed(bw: "Image.Image") -> "Image.Image":
    """Embed the content image into the 864 dot raster at x=LEFT and pad the
    height to full chunk rows (4)."""
    h = bw.height
    rem = h % ROWS_PER_CHUNK
    if rem:
        h += ROWS_PER_CHUNK - rem
    canvas = Image.new("1", (DOTS, h), 1)
    canvas.paste(bw, (LEFT, 0))
    return canvas


def raster_bytes(bw: "Image.Image") -> bytes:
    """1-bit PIL image -> raw bytes (MSB first, bit = 1 means black)."""
    px = bw.load()
    w, h = bw.size
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


def build_job(page_pngs, brightness: int = 100) -> bytes:
    """Speed -> density -> (raster per page, feed in between) -> final feed."""
    out = bytearray()
    seq = 1
    out += make_frame(0x0A, seq, bytes([SPEED]))
    seq += 1
    out += make_frame(0x09, seq, bytes([_density_from_brightness(brightness)]))
    seq += 1
    first = True
    for png in page_pngs:
        if not first:
            out += make_frame(0x02, seq, FEED_BETWEEN.to_bytes(2, "little"))
            seq += 1
        first = False
        bw = page_to_bw(png, brightness)
        canvas = embed(bw)
        rb = raster_bytes(canvas)
        for pos in range(0, len(rb), 432):
            out += make_frame(0x00, seq, rb[pos:pos + 432])
            seq += 1
    out += make_frame(0x02, seq, FEED_END.to_bytes(2, "little"))
    return bytes(out)


def main() -> int:
    brightness = parse_options(sys.argv)
    # for spooled jobs CUPS passes the file name as the last
    # argument; otherwise the data arrives on stdin.
    data = None
    if len(sys.argv) >= 7 and sys.argv[6] not in ("-", ""):
        with open(sys.argv[6], "rb") as f:
            data = f.read()
    if data is None:
        data = sys.stdin.buffer.read()
    if not data:
        return 0
    ext = ".ps" if data.startswith(b"%") else ".pdf"
    tmp, pages = render_pages(data, ext)
    try:
        frames = build_job(pages, brightness)
    finally:
        for f in pages:
            try:
                os.unlink(f)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    sys.stdout.buffer.write(frames)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
