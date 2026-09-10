#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
x3gui.py - desktop application (GTK3) for the Snap & Tag thermal printer X3.

Layout (clearly grouped):
  * content tabs: Text / Layout | Image | Barcode/QR | Settings
  * group "Paper & position": paper width (mm), margin (mm),
    horizontal offset (mm), raster width (dots); fine tuning in dots
    (paper centre, print window left/width) inside the expander.
  * group "Print image": brightness (with density display/override),
    speed, feed, delay.
  * group "Device": MAC address.
  * on the right: preview in mm (mm grid + scale bar) matching all values.
    The print can be **dragged with the mouse** (drag & drop) there to
    place it sideways; the centre line of the paper is marked in blue.

Settings are stored in ~/.config/x3drucker.json.
Start: ./x3gui.sh
"""

import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from io import BytesIO
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango   # noqa: E402

from PIL import Image, ImageDraw                         # noqa: E402

import x3print                                            # noqa: E402
import i18n                                               # noqa: E402
from i18n import t, set_lang, LANGS as I18N_LANGS         # noqa: E402
from x3print import (DEFAULT_DOTS, DEFAULT_SPEED, DEFAULT_FEED,  # noqa: E402
                     DEFAULT_MODE, X3Printer, render_text,
                     render_image_file, render_ean13, render_qrcode,
                     render_markdown, render_blocks, MD_HELP,
                     _embed, _scaled, _brightness_density, _load_font,
                     load_font_for_text, has_cjk)

CONFIG_PATH = os.path.expanduser("~/.config/x3drucker.json")


def _saved_lang():
    """Read the stored language from the configuration (before building the UI)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            code = json.load(f).get("lang")
    except Exception:                                        # noqa: BLE001
        code = None
    if code in ("de", "en", "ja", "zh"):
        return code
    return i18n.system_lang()                                # system language, else English


# --------------------------------------------------------------- shortcuts
SHORTCUT_NAME = "x3drucker.desktop"      # file name of the launcher
SHORTCUT_TEMPLATE = os.path.join("scripts", SHORTCUT_NAME)   # shipped template


def _desktop_dir():
    """Folder that holds the desktop shortcuts (XDG user dir, with fallbacks)."""
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if out and os.path.isdir(out):
            return out
    except Exception:                                        # noqa: BLE001
        pass
    for cand in ("~/Desktop", "~/Schreibtisch"):
        path = os.path.expanduser(cand)
        if os.path.isdir(path):
            return path
    return ""


def _desktop_entry_text():
    """Content of the launcher (.desktop) with absolute paths for this folder."""
    exec_path = os.path.join(HERE, "x3gui.sh")
    icon_path = os.path.join(HERE, "assets", "x3drucker.png")
    try:
        with open(os.path.join(HERE, SHORTCUT_TEMPLATE), encoding="utf-8") as f:
            text = f.read()
    except OSError:                                          # template missing
        text = ("[Desktop Entry]\nType=Application\nName=X3 Thermal Printer\n"
                "Name[de]=X3 Thermodrucker\nName[ja]=X3 サーマルプリンター\n"
                "Name[zh]=X3 热敏打印机\n"
                "GenericName=Label printer\nGenericName[de]=Etikettendrucker\n"
                "GenericName[ja]=ラベルプリンター\nGenericName[zh]=标签打印机\n"
                "Exec=@EXEC@\nIcon=@ICON@\nTerminal=false\n"
                "Categories=Utility;Printing;HardwareSettings;\n"
                "StartupWMClass=x3gui\n")
    return text.replace("@EXEC@", exec_path).replace("@ICON@", icon_path)


def _write_text(path, text):
    """Write a text file (creates the folder if needed)."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

# defaults (measured on this device)
DEF_WIDTH, DEF_LEFT = 500, 320
DEF_PAPER_MM = 45.0     # printable paper width (measured ~300..814 dots)
DEF_MARGIN_MM = 1.5     # margin left/right
DEF_OFFSET_MM = 0.0     # side offset (fine tuning)
DEF_CENTER = 558        # centre of the paper in the raster (dots, measured)
# defaults for the print image (tab "Image")
DEF_ROTATE = 0.0        # rotation (degrees)
DEF_THRESHOLD = 190     # black/white threshold
DEF_DITHER = True       # Dithering an

# initial content (first start only, or when no values are stored yet)
DEFAULT_TEXT = "www.deadlinedriven.dev"      # text in the "Text / Layout" tab
DEFAULT_QR_KIND = "url"                      # QR type: website
DEFAULT_QR_URL = "www.deadlinedriven.dev"    # default web address
DEF_SIZE = 35                                # font size for text

# --------------------------------------------------------------------------
# themes (colour schemes of the UI)
# --------------------------------------------------------------------------
# "deadline" uses the colours of deadlinedriven.dev
# (design system: black #000, turquoise #00d2be, orange #ff6a00,
#  text #f0ede8 / #a89880, borders rgba(255,255,255,0.08))
THEMES = {
    "dark": dict(
        label=t("Dark"),
        bg="#2b2b2e", panel="#33333a", base="#1e1e21", fg="#e8e8ea",
        dim="#b0b0b8", accent="#4a90d9", accent_fg="#ffffff",
        border="#46464c", line="#6a6a74", hover="#3c3c44", sel="#2f4f78",
        tip="#1b1b1e",
    ),
    "light": dict(
        label=t("Light"),
        bg="#e9ebf0", panel="#f8f9fb", base="#ffffff", fg="#15151a",
        dim="#4a4a55", accent="#1f5fae", accent_fg="#ffffff",
        border="#b7bac4", line="#7e808c", hover="#dde3ee", sel="#cfe0f5",
        tip="#ffffff",
    ),
    "deadline": dict(
        label="DeadlineDriven",
        bg="#000000", panel="#0d0d0d", base="#121212", fg="#f0ede8",
        dim="#a89880", accent="#00d2be", accent_fg="#001b18",
        border="rgba(255,255,255,0.16)", line="rgba(255,255,255,0.30)",
        hover="rgba(0,210,190,0.18)", sel="rgba(0,210,190,0.30)",
        tip="#0d0d0d",
    ),
}
THEME_ORDER = ("dark", "light", "deadline")

# preview accent colours per theme: (border/handles, centre line, text)
PV_ACCENTS = {
    "dark": ((50, 95, 175), (150, 175, 230), (70, 100, 160)),
    "light": ((31, 95, 174), (140, 170, 225), (30, 70, 130)),
    "deadline": ((0, 150, 135), (0, 190, 170), (0, 120, 108)),
}
# background of the preview area (around the "paper")
PV_MARGIN = {"dark": (236, 238, 242), "light": (216, 219, 228),
             "deadline": (236, 238, 242)}


def _mix_hex(a, b, f):
    """Mix two hex colours (f = share of b). rgba() values stay unchanged."""
    try:
        x, y = a.lstrip("#"), b.lstrip("#")
        if len(x) != 6 or len(y) != 6:
            return a
        av = [int(x[i:i + 2], 16) for i in (0, 2, 4)]
        bv = [int(y[i:i + 2], 16) for i in (0, 2, 4)]
        m = [int(round(p + (q - p) * f)) for p, q in zip(av, bv)]
        return "#%02x%02x%02x" % tuple(m)
    except Exception:                                        # noqa: BLE001
        return a


def _theme_css(c):
    """Build the CSS for one colour scheme (c = colour dict from THEMES)."""
    acc_hi = _mix_hex(c["accent"], c["fg"], 0.25)      # lighter/darker
    return f"""
/* IMPORTANT: the system theme (e.g. dark Yaru/Adwaita) paints buttons,
   fields & tabs with gradients (background-image). Those would cover our
   background-color - in the light theme widgets would stay dark and the
   text invisible. So switch them off everywhere. */
.x3theme button, .x3theme button:hover, .x3theme button:active,
.x3theme button:checked, .x3theme entry, .x3theme spinbutton,
.x3theme spinbutton button, .x3theme combobox, .x3theme comboboxtext,
.x3theme combobox button, .x3theme comboboxtext button,
.x3theme notebook, .x3theme notebook > header, .x3theme notebook tab,
.x3theme notebook stack, .x3theme notebook > stack,
.x3theme notebook tab:hover, .x3theme notebook tab:checked,
.x3theme checkbutton check, .x3theme radiobutton radio,
.x3theme scale trough, .x3theme scale highlight, .x3theme scale slider,
.x3theme scrollbar, .x3theme scrollbar slider, .x3theme frame,
.x3theme progressbar trough, .x3theme progressbar progress,
.x3theme menu, .x3theme popover, .x3theme tooltip, .x3theme tooltip.background,
.x3theme menuitem, .x3theme menu menuitem, .x3theme menu menuitem:hover,
.x3theme menu menuitem:selected, .x3theme checkmenuitem, .x3theme radiomenuitem,
.x3theme treeview, .x3theme list, .x3theme headerbar, .x3theme paned,
.x3theme textview, .x3theme textview text, .x3theme separator {{
    background-image: none; }}
.x3theme {{ background-color: {c['bg']}; color: {c['fg']}; }}
.x3theme label, .x3theme checkbutton, .x3theme radiobutton {{
    color: {c['fg']}; }}
.x3theme frame {{ background-color: {c['panel']}; border-radius: 6px; }}
.x3theme frame > border {{ border: 1px solid {c['border']}; }}
.x3theme frame > label {{ color: {c['accent']}; }}
.x3theme notebook {{ background-color: {c['bg']}; }}
.x3theme notebook > header {{
    background-color: {c['bg']}; border-color: {c['border']}; }}
/* IMPORTANT: the notebook content area ("stack") is otherwise painted
   dark grey by the system theme - dark text on a dark background. */
.x3theme notebook stack, .x3theme notebook > stack {{
    background-color: {c['bg']}; }}
.x3theme notebook tab {{
    background-color: {c['bg']}; color: {c['dim']};
    border-bottom: 2px solid transparent; padding: 2px 7px; }}
.x3theme notebook tab:hover {{
    color: {c['fg']}; background-color: {c['hover']}; }}
.x3theme notebook tab:checked {{
    color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
.x3theme button {{
    background-color: {c['panel']}; color: {c['fg']};
    border: 1px solid {c['border']}; border-radius: 5px; }}
.x3theme button:hover {{
    background-color: {c['hover']}; border-color: {c['accent']}; }}
.x3theme button:active, .x3theme button:checked {{
    background-color: {c['accent']}; color: {c['accent_fg']};
    border-color: {c['accent']}; }}
.x3theme button:disabled {{ color: {c['dim']}; }}
/* highlighted actions: print = filled, tools = outline */
.x3theme button.x3primary {{
    background-color: {c['accent']}; color: {c['accent_fg']};
    border: 2px solid {c['accent']}; border-radius: 6px;
    font-weight: bold; padding: 5px 12px; }}
.x3theme button.x3primary:hover {{
    background-color: {acc_hi}; border-color: {acc_hi};
    color: {c['accent_fg']}; }}
.x3theme button.x3primary:active {{
    background-color: {c['accent']}; }}
.x3theme button.x3tools {{
    background-color: {c['panel']}; color: {c['accent']};
    border: 1px solid {c['accent']}; border-radius: 6px;
    font-weight: bold; padding: 3px 7px; }}
.x3theme button.x3tools:hover {{
    background-color: {c['hover']}; color: {acc_hi}; }}
.x3theme button.x3tools:active {{
    background-color: {c['accent']}; color: {c['accent_fg']}; }}
.x3theme entry, .x3theme spinbutton, .x3theme combobox, .x3theme comboboxtext {{
    background-color: {c['base']}; color: {c['fg']};
    border: 1px solid {c['border']}; border-radius: 5px; }}
.x3theme entry:focus, .x3theme spinbutton:focus {{
    border-color: {c['accent']}; }}
.x3theme combobox button, .x3theme comboboxtext button {{
    background-color: {c['base']}; color: {c['fg']};
    border-color: {c['border']}; }}
.x3theme textview, .x3theme textview text {{
    background-color: {c['base']}; color: {c['fg']}; }}
.x3theme scrolledwindow, .x3theme viewport {{ background-color: {c['bg']}; }}
.x3theme scrollbar {{ background-color: {c['bg']}; }}
.x3theme scrollbar slider {{
    background-color: {c['border']}; border-radius: 6px;
    min-width: 8px; min-height: 8px; }}
.x3theme scrollbar slider:hover {{ background-color: {c['accent']}; }}
.x3theme checkbutton check, .x3theme radiobutton radio {{
    background-color: {c['base']}; border: 2px solid {c['line']}; }}
.x3theme checkbutton check:hover, .x3theme radiobutton radio:hover {{
    border-color: {c['accent']}; }}
.x3theme checkbutton check:checked, .x3theme radiobutton radio:checked {{
    background-color: {c['accent']}; border-color: {c['accent']};
    color: {c['accent_fg']}; }}
.x3theme checkbutton:checked, .x3theme radiobutton:checked {{
    color: {c['fg']}; }}
.x3theme spinbutton button {{ color: {c['fg']}; }}
.x3theme expander title {{ color: {c['fg']}; }}
.x3theme checkbutton:disabled, .x3theme button:disabled,
.x3theme label:disabled {{ color: {c['dim']}; }}
.x3theme selection, .x3theme entry selection, .x3theme textview selection {{
    background-color: {c['accent']}; color: {c['accent_fg']}; }}
.x3theme notebook header tabs {{ background-color: {c['bg']}; }}
.x3theme dialog, .x3theme messagedialog {{ background-color: {c['bg']}; }}
.x3theme dialog label, .x3theme messagedialog label {{ color: {c['fg']}; }}
.x3theme scale trough {{
    background-color: {c['base']}; border-color: {c['border']}; }}
.x3theme scale highlight {{ background-color: {c['accent']}; }}
.x3theme scale slider {{ background-color: {c['fg']}; }}
.x3theme separator {{ background-color: {c['border']}; }}
.x3theme paned > separator {{
    background-color: {c['border']}; min-width: 4px; }}
.x3theme treeview, .x3theme list {{
    background-color: {c['base']}; color: {c['fg']}; }}
.x3theme treeview:selected, .x3theme list row:selected {{
    background-color: {c['sel']}; color: {c['fg']}; }}
.x3theme menu, .x3theme popover, .x3theme combobox window,
.x3theme window.popup, .x3theme popover.background {{
    background-color: {c['panel']}; color: {c['fg']}; }}
/* drop-down lists: the selected row (mouse over it) otherwise comes from
   the system theme in dark colours - unreadable in the light theme. */
.x3theme menuitem, .x3theme menu menuitem {{
    color: {c['fg']}; background-color: transparent; }}
.x3theme menuitem:hover, .x3theme menuitem:selected,
.x3theme menu menuitem:hover, .x3theme menu menuitem:selected {{
    background-color: {c['accent']}; color: {c['accent_fg']}; }}
.x3theme checkmenuitem check, .x3theme radiomenuitem radio {{
    color: {c['accent_fg']}; }}
.x3theme menuitem:disabled, .x3theme menu menuitem:disabled {{
    color: {c['dim']}; }}
.x3theme tooltip, tooltip.background {{
    background-color: {c['tip']}; color: {c['fg']};
    border: 1px solid {c['accent']}; }}
.x3theme progressbar trough {{ background-color: {c['base']}; }}
.x3theme progressbar progress {{ background-color: {c['accent']}; }}
"""

# Start image shipped with the project (used while the configuration does not
# contain an image) - so a fresh installation looks the same everywhere.
DEFAULT_IMAGE = os.path.join(HERE, "assets", "sample.jpg")
if not os.path.exists(DEFAULT_IMAGE):
    DEFAULT_IMAGE = ""

# Default theme of a fresh installation (change here to ship another one)
DEFAULT_THEME = "light"

VERSION = x3print.VERSION                    # version number (from x3print.py)

# margin around the raster area in the preview (room for the labels)
PV_GX, PV_GT, PV_GB = 14, 26, 44


def _frame_with_grid(title):
    """Frame with title + grid (compact, uniform layout)."""
    frame = Gtk.Frame(label=title)
    grid = Gtk.Grid(column_spacing=7, row_spacing=4)
    grid.set_border_width(5)
    frame.add(grid)
    return frame, grid


class X3App:
    def __init__(self):
        set_lang(_saved_lang())     # language BEFORE the UI is built
        self.image_path = None
        self.meme_bg = None           # background image for layout mode
        self.blocks = []              # text blocks (layout mode)
        self.preview_timeout = None
        self.busy = False
        self.pixbuf = None
        self._loading = True
        self._drag_x0 = None          # drag in the preview (start point)
        self._drag_y0 = None
        self._drag_ox = 0             # image origin inside the EventBox (display px)
        self._drag_oy = 0
        self._drag_off0 = 0.0         # offset when the drag started
        self._drag_center0 = 0        # paper centre at the start (right-drag)
        self._drag_mode = None        # "content" | "paper" | "resize"
        self._resize_side = None      # "left"|"right"|"tl"|"tr"|"bl"|"br"
        self._resize_scale0 = 100.0   # scale (%) when the drag started
        self._resize_w0 = 0.0         # content width (dots) when the drag started
        self._resize_off0 = 0.0       # offset when the drag started
        self._resize_anchor = None    # opposite corner (diagonal drag)
        self._resize_d0 = 1.0         # distance anchor <-> corner at the start
        self._pl_left = 0             # placement of the content (preview)
        self._pl_cw = 0
        self._pl_h = 0
        self._disp_scale = 1.0        # raster dots -> display px
        self._cursor_name = None      # current mouse cursor (preview pane)
        self._preview_size = (0, 0)   # last size of the preview area
        self._split_ratio = 0.45      # share of the left column in the width
        self.ui_zoom = 1.0            # UI size of the left column (70-140 %)
        self._tab_switch_pending = False   # tab switch -> redraw the preview
        self._rebuilding = False      # the window is currently being rebuilt
        self._shortcut_created = False     # desktop shortcut already placed?

        self.win = Gtk.Window(title=t("X3 Thermal Printer"))
        self.win.set_default_size(1320, 940)
        self.win.set_size_request(520, 380)      # can always be smaller (it scrolls then)
        self.win.set_border_width(10)
        self.win.connect("destroy", self.on_destroy)

        # colour scheme (theme picker in the "Settings" tab)
        self.theme = DEFAULT_THEME
        self._theme_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._theme_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.win.get_style_context().add_class("x3theme")

        root = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned = root
        self.win.add(root)
        root.connect("button-release-event", self.on_split_drag)

        # ================= left column =====================================
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        left.get_style_context().add_class("x3left")
        self.left = left
        # Register the provider globally - the rules reach the left menu only
        # through the ".x3left" class (add_provider on the widget itself would
        # NOT reach the child widgets).
        self._left_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._left_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._left_scale = 1.0
        # So the window can be shrunk freely the left column lives inside a
        # ScrolledWindow (scrolls in small windows).
        left_sw = Gtk.ScrolledWindow()
        left_sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        left_sw.set_min_content_width(460)
        left_sw.set_min_content_height(200)
        left_sw.add(left)
        self.left_sw = left_sw
        left.set_size_request(380, -1)
        # resize=True for BOTH children: the paned distributes size changes
        # proportionally -> the left column scales together with the window.
        root.pack1(left_sw, True, False)

        # ---- view (UI size, freely adjustable) --------------------------
        zrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        zrow.pack_start(Gtk.Label(label=t("View:"), xalign=0), False, False, 0)
        zmin = Gtk.Button(label="-")
        zmin.set_tooltip_text(t("Show interface smaller"))
        zmin.connect("clicked", lambda *_: self.ui_zoom_step(-0.05))
        zrow.pack_start(zmin, False, False, 0)
        self.ui_zoom_label = Gtk.Label(label="100 %", xalign=0)
        zrow.pack_start(self.ui_zoom_label, False, False, 0)
        zplus = Gtk.Button(label="+")
        zplus.set_tooltip_text(t("Show interface larger"))
        zplus.connect("clicked", lambda *_: self.ui_zoom_step(0.05))
        zrow.pack_start(zplus, False, False, 0)
        zreset = Gtk.Button(label="100 %")
        zreset.set_tooltip_text(t("Reset to 100 %"))
        zreset.connect("clicked", lambda *_: self.set_ui_zoom(1.0))
        zrow.pack_start(zreset, False, False, 0)
        left.pack_start(zrow, False, False, 0)

        # ---- content (tabs) -----------------------------------------------
        self.notebook = Gtk.Notebook()
        # no expand: otherwise a big empty area appears in the tab region
        left.pack_start(self.notebook, False, False, 0)
        self._build_text_tab()
        self._build_image_tab()
        self._build_code_tab()
        self._build_expert_tab()

        # ---- actions (right below the tabs, highlighted) -------------------
        arow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.print_button = Gtk.Button(label=t("🖨  Print"))
        self.print_button.get_style_context().add_class("x3primary")
        self.print_button.connect("clicked", self.on_print)
        arow.pack_start(self.print_button, True, True, 0)
        self.status_button = Gtk.Button(label=t("Check status"))
        self.status_button.connect("clicked", self.on_status)
        arow.pack_start(self.status_button, False, False, 0)
        left.pack_start(arow, False, False, 0)

        # ---- tools (highlighted as secondary) ------------------------------
        trow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        for label, cb in ((t("Test page"), self.on_tool_demo),
                          (t("Calibration"), self.on_tool_calibrate),
                          (t("Feed only"), self.on_tool_feed)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("x3tools")
            b.connect("clicked", cb)
            trow.pack_start(b, True, True, 0)
        left.pack_start(trow, False, False, 0)

        # ---- group: content (size) -----------------------------------------
        inf, ig = _frame_with_grid(t("Content"))
        ig.attach(Gtk.Label(label=t("Size (%):"), xalign=0), 0, 0, 1, 1)
        self.scale_spin = Gtk.SpinButton.new_with_range(5, 400, 5)
        self.scale_spin.set_value(100)
        self.scale_spin.set_tooltip_text(
            t("Scales all content: 100 % = fitted to the print window · smaller = more margin · larger = sticks out (red areas are not printed) · easy with the mouse: drag the blue corners in the preview diagonally"))
        ig.attach(self.scale_spin, 1, 0, 1, 1)
        self.scale_label = Gtk.Label(label="100 %", xalign=0)
        ig.attach(self.scale_label, 2, 0, 1, 1)
        fit = Gtk.Button(label=t("Fit to window"))
        fit.set_tooltip_text(t("Reset size to 100 %"))
        fit.connect("clicked", lambda *_: self.scale_spin.set_value(100))
        ig.attach(fit, 3, 0, 1, 1)

        # -- image editing (applies to all tabs) -----------------------------
        ig.attach(Gtk.Label(label=t("Rotation:"), xalign=0), 0, 1, 1, 1)
        rrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.rotate_spin = Gtk.SpinButton.new_with_range(-180, 180, 1)
        self.rotate_spin.set_value(0)
        self.rotate_spin.set_tooltip_text(
            t("Rotate content (degrees, positive = counter-clockwise) – no exact quarter turns needed"))
        rrow.pack_start(self.rotate_spin, False, False, 0)
        rrow.pack_start(Gtk.Label(label="°"), False, False, 0)
        for label, delta, tip in (("⟲", 90, t("rotate 90° counter-clockwise")),
                                  ("⟳", -90, t("rotate 90° clockwise")),
                                  ("180°", 180, t("rotate 180°")),
                                  ("0°", 0, t("reset rotation"))):
            b = Gtk.Button(label=label)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, d=delta: self.rotate_step(d))
            rrow.pack_start(b, False, False, 0)
        self.flip_h_check = Gtk.CheckButton(label=t("↔ h"))
        self.flip_h_check.set_tooltip_text(t("Flip content horizontally"))
        self.flip_v_check = Gtk.CheckButton(label=t("↕ v"))
        self.flip_v_check.set_tooltip_text(t("Flip content vertically"))
        rrow.pack_start(self.flip_h_check, False, False, 4)
        rrow.pack_start(self.flip_v_check, False, False, 0)
        ig.attach(rrow, 1, 1, 4, 1)

        ig.attach(Gtk.Label(label=t("Stretch:"), xalign=0), 0, 2, 1, 1)
        srow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.stretch_x = Gtk.SpinButton.new_with_range(25, 400, 5)
        self.stretch_x.set_value(100)
        self.stretch_x.set_tooltip_text(t("Stretch content width (<100 compresses)"))
        self.stretch_y = Gtk.SpinButton.new_with_range(25, 400, 5)
        self.stretch_y.set_value(100)
        self.stretch_y.set_tooltip_text(t("Stretch content height (<100 compresses)"))
        srow.pack_start(Gtk.Label(label=t("Width")), False, False, 0)
        srow.pack_start(self.stretch_x, False, False, 0)
        srow.pack_start(Gtk.Label(label=t("%  Height")), False, False, 0)
        srow.pack_start(self.stretch_y, False, False, 0)
        srow.pack_start(Gtk.Label(label="%"), False, False, 0)
        self.trim_check = Gtk.CheckButton(label=t("Trim margins"))
        self.trim_check.set_tooltip_text(
            t("Cuts off empty (white) margins of the content"))
        srow.pack_start(self.trim_check, False, False, 6)
        ig.attach(srow, 1, 2, 4, 1)
        left.pack_start(inf, False, False, 0)

        # ---- group: paper & position ---------------------------------------
        pf, pg = _frame_with_grid(t("Paper & position"))
        pg.attach(Gtk.Label(label=t("Paper width:"), xalign=0), 0, 0, 1, 1)
        self.paper_mm = Gtk.SpinButton.new_with_range(10, 90, 1)
        self.paper_mm.set_value(DEF_PAPER_MM)
        pg.attach(self.paper_mm, 1, 0, 1, 1)
        pg.attach(Gtk.Label(label="mm", xalign=0), 2, 0, 1, 1)
        pg.attach(Gtk.Label(label=t("Margin:"), xalign=0), 3, 0, 1, 1)
        self.margin_mm = Gtk.SpinButton.new_with_range(0, 10, 0.5)
        self.margin_mm.set_digits(1)
        self.margin_mm.set_value(DEF_MARGIN_MM)
        pg.attach(self.margin_mm, 4, 0, 1, 1)
        pg.attach(Gtk.Label(label="mm", xalign=0), 5, 0, 1, 1)

        pg.attach(Gtk.Label(label=t("Side offset:"), xalign=0), 0, 1, 1, 1)
        self.offset_mm = Gtk.SpinButton.new_with_range(-100, 100, 0.5)
        self.offset_mm.set_digits(1)
        self.offset_mm.set_value(DEF_OFFSET_MM)
        pg.attach(self.offset_mm, 1, 1, 1, 1)
        pg.attach(Gtk.Label(label=t("mm (right +)"), xalign=0), 2, 1, 1, 1)
        pg.attach(Gtk.Label(label=t("Raster width:"), xalign=0), 3, 1, 1, 1)
        self.dots_spin = Gtk.SpinButton.new_with_range(8, 2048, 8)
        self.dots_spin.set_value(DEFAULT_DOTS)
        pg.attach(self.dots_spin, 4, 1, 1, 1)
        pg.attach(Gtk.Label(label="dots", xalign=0), 5, 1, 1, 1)

        self.geo_info = Gtk.Label(label="", xalign=0)
        self.geo_info.set_line_wrap(True)
        self.geo_info.set_lines(2)                    # fixed height (2 lines)
        self.geo_info.set_ellipsize(Pango.EllipsizeMode.END)
        pg.attach(self.geo_info, 0, 2, 5, 1)
        zb = Gtk.Button(label=t("Centre"))
        zb.set_tooltip_text(t("Set side offset to 0"))
        zb.connect("clicked", lambda *_: self.offset_mm.set_value(0.0))
        pg.attach(zb, 5, 2, 1, 1)

        self.fine_expander = Gtk.Expander(label=t("Fine tuning (dots) – only if needed"))
        fg = Gtk.Grid(column_spacing=10, row_spacing=6)
        fg.set_border_width(4)
        fg.attach(Gtk.Label(label=t("Paper centre:"), xalign=0), 0, 0, 1, 1)
        self.center_spin = Gtk.SpinButton.new_with_range(0, 2048, 4)
        self.center_spin.set_value(DEF_CENTER)
        fg.attach(self.center_spin, 1, 0, 1, 1)
        fg.attach(Gtk.Label(label=t("Print window left:"), xalign=0), 2, 0, 1, 1)
        self.left_spin = Gtk.SpinButton.new_with_range(0, 2040, 4)
        self.left_spin.set_value(DEF_LEFT)
        fg.attach(self.left_spin, 3, 0, 1, 1)
        fg.attach(Gtk.Label(label=t("Width:"), xalign=0), 4, 0, 1, 1)
        self.width_spin = Gtk.SpinButton.new_with_range(8, 2040, 8)
        self.width_spin.set_value(DEF_WIDTH)
        fg.attach(self.width_spin, 5, 0, 1, 1)
        self.fine_expander.add(fg)
        pg.attach(self.fine_expander, 0, 3, 6, 1)
        left.pack_start(pf, False, False, 0)

        # ---- group: print image --------------------------------------------
        df, dg = _frame_with_grid(t("Print image"))
        dg.attach(Gtk.Label(label=t("Brightness:"), xalign=0), 0, 0, 1, 1)
        self.bright_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 40, 200, 5)
        self.bright_scale.set_value(100)
        self.bright_scale.set_draw_value(False)
        self.bright_scale.set_hexpand(True)
        dg.attach(self.bright_scale, 1, 0, 3, 1)
        self.bright_label = Gtk.Label(label=t("100 % · density 0x0c"), xalign=0)
        dg.attach(self.bright_label, 4, 0, 3, 1)

        self.density_check = Gtk.CheckButton(label=t("Density:"))
        self.density_check.set_tooltip_text(t("Set print density (command 0x09) manually"))
        dg.attach(self.density_check, 0, 1, 1, 1)
        self.density_spin = Gtk.SpinButton.new_with_range(0, 255, 1)
        self.density_spin.set_value(0x0C)
        dg.attach(self.density_spin, 1, 1, 1, 1)
        self.density_label = Gtk.Label(label="0x0c", xalign=0)
        dg.attach(self.density_label, 2, 1, 1, 1)
        dg.attach(Gtk.Label(label="Speed:", xalign=0), 3, 1, 1, 1)
        self.speed_entry = Gtk.Entry()
        self.speed_entry.set_text("0x55")
        self.speed_entry.set_width_chars(7)
        self.speed_entry.set_tooltip_text(t("Print speed (hex)"))
        dg.attach(self.speed_entry, 4, 1, 1, 1)
        dg.attach(Gtk.Label(label="Feed:", xalign=0), 5, 1, 1, 1)
        self.feed_spin = Gtk.SpinButton.new_with_range(0, 800, 10)
        self.feed_spin.set_value(DEFAULT_FEED)
        self.feed_spin.set_tooltip_text(t("Feed steps after printing"))
        dg.attach(self.feed_spin, 6, 1, 1, 1)

        dg.attach(Gtk.Label(label=t("Print mode:"), xalign=0), 0, 2, 1, 1)
        self.mode_combo = Gtk.ComboBoxText()
        self.mode_combo.append("app", t("Smooth (frame by frame)"))
        self.mode_combo.append("fast", t("Turbo (large 16 KB blocks)"))
        self.mode_combo.set_active_id(DEFAULT_MODE)
        self.mode_combo.set_tooltip_text(
            t("Both modes send without pauses - the printer's buffer sets the pace and the motor runs through. Smooth = one raster frame (432 bytes) per write, Turbo = 16 KB blocks (fewer, larger writes)."))
        dg.attach(self.mode_combo, 1, 2, 2, 1)
        left.pack_start(df, False, False, 0)

        # ---- group: device (settings tab only) ------------------------------

        self.status = Gtk.Label(label=t("Ready."), xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_lines(2)                      # fixed height (2 lines)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        left.pack_start(self.status, False, False, 0)

        # ================= right column: preview ============================
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.pack2(right, True, False)
        sw2 = Gtk.ScrolledWindow()
        self.preview_sw = sw2
        sw2.set_tooltip_text(
            t("white = printable · hatched = no paper · red = outside (not printed) · blue = paper centre"))
        # small minimum size so the preview does not inflate the window
        sw2.set_min_content_width(240)
        sw2.set_min_content_height(160)
        self.preview_image = Gtk.Image()
        # Anchor the image top left: otherwise GTK draws it centred inside the
        # (larger) EventBox - mouse coordinates would then not match the handles
        # and dragging would do nothing.
        self.preview_image.set_halign(Gtk.Align.START)
        self.preview_image.set_valign(Gtk.Align.START)
        self.preview_box = Gtk.EventBox()
        self.preview_box.add(self.preview_image)
        self.preview_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                    Gdk.EventMask.BUTTON_RELEASE_MASK |
                                    Gdk.EventMask.POINTER_MOTION_MASK)
        self.preview_box.set_tooltip_text(
            t("Drag left = move content · drag blue corners = size (diagonal) · drag blue edges = width · drag right = move paper"))
        self.preview_box.connect("button-press-event", self.on_preview_press)
        self.preview_box.connect("motion-notify-event", self.on_preview_motion)
        self.preview_box.connect("button-release-event", self.on_preview_release)
        sw2.add(self.preview_box)
        sw2.connect("size-allocate", self.on_preview_resize)
        right.pack_start(sw2, True, True, 0)

        self._connect_signals()
        self.load_config()
        self.set_theme(self.theme_combo.get_active_id() or DEFAULT_THEME)
        if not self.image_path and os.path.exists(DEFAULT_IMAGE):
            self.image_path = DEFAULT_IMAGE
        self._update_image_label()
        self.on_code_type_changed()
        self.on_device_selected()
        self.on_protocol_changed()
        self._refresh_geometry_labels()
        self._loading = False
        self.win.show_all()
        if getattr(self, "_start_maximized", False):
            self.win.maximize()
        GLib.timeout_add(150, self.init_split)
        self.win.connect("size-allocate", self.on_win_size_allocate)
        self.collect_left_metrics()
        self.set_ui_zoom(self.ui_zoom)
        GLib.timeout_add(200, self._set_preview_cursor)
        GLib.timeout_add(600, self._maybe_create_shortcut)   # launcher on the desktop
        self.queue_preview()
        self.set_status(t("Ready. Settings are saved automatically."))

    # ------------------------------------------------------------ Aufbau
    def _build_text_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(4)
        # ---- tool row: Markdown + insert image -----------------------------
        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.md_check = Gtk.CheckButton(label=t("Markdown"))
        self.md_check.set_active(True)
        self.md_check.set_tooltip_text(
            t("Apply Markdown formatting when printing (# heading, **bold**, *italic*, - list, 1. list, > quote, --- rule, ![image](path)) – off = plain text"))
        tools.pack_start(self.md_check, False, False, 0)
        b = Gtk.Button(label=t("Insert image …"))
        b.set_tooltip_text(
            t("Insert an image at the cursor (e.g. as a template with text above/below); the path is inserted as ![image](path)"))
        b.connect("clicked", self.on_insert_image)
        tools.pack_start(b, False, False, 0)
        b = Gtk.Button(label=t("Format help"))
        b.set_tooltip_text(t("Short Markdown reference"))
        b.connect("clicked", self.on_md_help)
        tools.pack_start(b, False, False, 0)
        box.pack_start(tools, False, False, 0)
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(42)
        self.textview = Gtk.TextView()
        self.textview.get_buffer().set_text(DEFAULT_TEXT)
        self.textview.set_tooltip_text(
            t("Enter Markdown – the preview on the right shows the print immediately. Use “Insert image …” to add an image."))
        sw.add(self.textview)
        box.pack_start(sw, True, True, 0)
        g = Gtk.Grid(column_spacing=8, row_spacing=6)
        box.pack_start(g, False, False, 0)
        g.attach(Gtk.Label(label=t("Font size:"), xalign=0), 0, 0, 1, 1)
        self.size_spin = Gtk.SpinButton.new_with_range(8, 96, 1)
        self.size_spin.set_value(DEF_SIZE)
        g.attach(self.size_spin, 1, 0, 1, 1)
        g.attach(Gtk.Label(label=t("Alignment:"), xalign=0), 2, 0, 1, 1)
        self.align_combo = Gtk.ComboBoxText()
        for key, label in (("left", t("Left")), ("center", t("Middle")),
                           ("right", t("Right"))):
            self.align_combo.append(key, label)
        self.align_combo.set_active_id("left")
        g.attach(self.align_combo, 3, 0, 1, 1)
        self.bold_check = Gtk.CheckButton(label=t("Bold"))
        g.attach(self.bold_check, 0, 1, 1, 1)
        g.attach(Gtk.Label(label=t("Stroke width:"), xalign=0), 2, 1, 1, 1)
        self.thicken_spin = Gtk.SpinButton.new_with_range(0, 3, 1)
        g.attach(self.thicken_spin, 3, 1, 1, 1)
        g.attach(Gtk.Label(label=t("Line spacing:"), xalign=0), 4, 1, 1, 1)
        self.spacing_spin = Gtk.SpinButton.new_with_range(0, 40, 1)
        self.spacing_spin.set_value(3)
        g.attach(self.spacing_spin, 5, 1, 1, 1)

        # ---- layout: background image + text blocks (used to be a tab) -----
        lay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lay.set_border_width(0)
        bf, bg2 = _frame_with_grid(t("Background image (layout)"))
        lay.pack_start(bf, False, False, 0)
        brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mb = Gtk.Button(label=t("Background image …"))
        mb.set_tooltip_text(
            t("Image as background – printed with the settings from the “Image” tab (threshold, dithering, invert) and the brightness from “Print image”. As soon as a background image or a text block is set, this layout is printed – otherwise the text above."))
        mb.connect("clicked", self.on_choose_meme_bg)
        brow.pack_start(mb, False, False, 0)
        self.meme_label = Gtk.Label(label=t("no image"), xalign=0)
        self.meme_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        brow.pack_start(self.meme_label, True, True, 0)
        mx = Gtk.Button(label="✕")
        mx.set_tooltip_text(t("Remove background (print text blocks only)"))
        mx.connect("clicked", lambda *_: self.set_meme_bg(None))
        brow.pack_start(mx, False, False, 0)
        bg2.attach(brow, 0, 0, 4, 1)

        kf, kg = _frame_with_grid(t("Text blocks (layout)"))
        lay.pack_start(kf, False, False, 0)
        addb = Gtk.Button(label=t("＋ Add text block"))
        addb.set_tooltip_text(t("Create another text block (top, middle or bottom – several blocks are possible)"))
        addb.connect("clicked", lambda *_: self.add_block())
        kg.attach(addb, 0, 0, 4, 1)
        self.blocks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=5)
        kg.attach(self.blocks_box, 0, 1, 4, 1)

        box.pack_start(lay, False, False, 0)
        self.notebook.append_page(box, Gtk.Label(label=t("Text")))

    def _build_image_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(4)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b = Gtk.Button(label=t("Choose image file …"))
        b.set_tooltip_text(
            t("Load image (PNG/JPG/BMP/GIF/WebP/SVG) – sets rotation to 0°, threshold to 190 and dithering on, and refreshes the preview immediately"))
        b.connect("clicked", self.on_choose_image)
        row.pack_start(b, False, False, 0)
        self.img_label = Gtk.Label(label=t("no file"), xalign=0)
        row.pack_start(self.img_label, True, True, 0)
        box.pack_start(row, False, False, 0)
        g = Gtk.Grid(column_spacing=8, row_spacing=6)
        box.pack_start(g, False, False, 0)
        g.attach(Gtk.Label(label=t("Threshold:"), xalign=0), 0, 0, 1, 1)
        self.thresh_spin = Gtk.SpinButton.new_with_range(0, 255, 5)
        self.thresh_spin.set_value(DEF_THRESHOLD)
        g.attach(self.thresh_spin, 1, 0, 1, 1)
        self.dither_check = Gtk.CheckButton(label=t("Dithering"))
        self.dither_check.set_active(DEF_DITHER)
        g.attach(self.dither_check, 2, 0, 1, 1)
        self.invert_check = Gtk.CheckButton(label=t("Invert"))
        g.attach(self.invert_check, 3, 0, 1, 1)
        self.notebook.append_page(box, Gtk.Label(label=t("Image")))

    def _build_code_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(4)
        g = Gtk.Grid(column_spacing=8, row_spacing=6)
        box.pack_start(g, False, False, 0)
        g.attach(Gtk.Label(label=t("Type:"), xalign=0), 0, 0, 1, 1)
        self.code_combo = Gtk.ComboBoxText()
        self.code_combo.append("ean13", t("EAN-13 (barcode)"))
        self.code_combo.append("qr", t("QR code"))
        self.code_combo.set_active_id("ean13")
        g.attach(self.code_combo, 1, 0, 1, 1)
        g.attach(Gtk.Label(label=t("Size:"), xalign=0), 2, 0, 1, 1)
        self.code_scale = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.code_scale.set_value(3)
        g.attach(self.code_scale, 3, 0, 1, 1)

        # ---- EAN-13 (barcode) ---------------------------------------
        self.barcode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   spacing=8)
        self.barcode_box.pack_start(
            Gtk.Label(label=t("Content (12/13 digits):"), xalign=0), False, False, 0)
        self.code_entry = Gtk.Entry()
        self.code_entry.set_text("4006381333931")
        self.barcode_box.pack_start(self.code_entry, True, True, 0)
        box.pack_start(self.barcode_box, False, False, 0)

        # ---- QR-Code --------------------------------------------------
        self.qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(Gtk.Label(label=t("QR type:"), xalign=0), False, False, 0)
        self.qr_kind = Gtk.ComboBoxText()
        for key, label in (("text", t("Free text")), ("url", t("Website (URL)")),
                           ("wifi", t("Wi-Fi access")), ("vcard", t("Contact (vCard)")),
                           ("email", t("E-mail")), ("tel", t("Phone")),
                           ("sms", t("SMS")), ("geo", t("Location (geo)"))):
            self.qr_kind.append(key, label)
        self.qr_kind.set_active_id(DEFAULT_QR_KIND)
        self.qr_kind.set_tooltip_text(
            t("Type of QR code: website, Wi-Fi access, contact, e-mail, phone, SMS or location"))
        row.pack_start(self.qr_kind, False, False, 0)
        row.pack_start(Gtk.Label(label=t("Error correction:"), xalign=0),
                       False, False, 0)
        self.qr_ecc = Gtk.ComboBoxText()
        for key, label in (("L", "L · 7 %"), ("M", "M · 15 %"),
                           ("Q", "Q · 25 %"), ("H", "H · 30 %")):
            self.qr_ecc.append(key, label)
        self.qr_ecc.set_active_id("M")
        self.qr_ecc.set_tooltip_text(
            t("More error correction = more robust against dirt, scratches and fading (H recommended for thermal paper), but a denser pattern"))
        row.pack_start(self.qr_ecc, False, False, 0)
        self.qr_box.pack_start(row, False, False, 0)

        self.qr_stack = Gtk.Stack()
        for name, page in (("text", self._qr_page_text),
                           ("url", self._qr_page_url),
                           ("wifi", self._qr_page_wifi),
                           ("vcard", self._qr_page_vcard),
                           ("email", self._qr_page_email),
                           ("tel", self._qr_page_tel),
                           ("sms", self._qr_page_sms),
                           ("geo", self._qr_page_geo)):
            self.qr_stack.add_named(page(), name)
        self.qr_box.pack_start(self.qr_stack, False, False, 0)

        pframe = Gtk.Frame(label=t("What the QR code contains (exactly this text)"))
        self.qr_preview = Gtk.Label(label="", xalign=0)
        self.qr_preview.set_selectable(True)
        self.qr_preview.set_line_wrap(True)
        self.qr_preview.set_margin_top(4)
        self.qr_preview.set_margin_bottom(4)
        self.qr_preview.set_margin_start(6)
        self.qr_preview.set_margin_end(6)
        pframe.add(self.qr_preview)
        self.qr_box.pack_start(pframe, False, False, 0)
        box.pack_start(self.qr_box, False, False, 0)

        self.notebook.append_page(box, Gtk.Label(label=t("Barcode / QR")))

    # -- QR input pages per type --------------------------
    @staticmethod
    def _hint(text):
        """Wrapping hint label (keeps the minimum width small)."""
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(42)
        return lbl

    @staticmethod
    def _qr_page():
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        page.pack_start(grid, False, False, 0)
        return page, grid

    def _qr_page_text(self):
        page, gr = self._qr_page()
        gr.attach(Gtk.Label(label=t("Text / content:"), xalign=0), 0, 0, 1, 1)
        self.qr_text = Gtk.Entry()
        gr.attach(self.qr_text, 1, 0, 3, 1)
        return page

    def _qr_page_url(self):
        page, gr = self._qr_page()
        gr.attach(Gtk.Label(label=t("Web address:"), xalign=0), 0, 0, 1, 1)
        self.qr_url = Gtk.Entry()
        self.qr_url.set_text(DEFAULT_QR_URL)
        self.qr_url.set_placeholder_text(t("e.g. www.deadlinedriven.dev"))
        gr.attach(self.qr_url, 1, 0, 3, 1)
        gr.attach(self._hint(
            t("Tip: “https://” is added automatically – the code opens the page directly in the browser.")), 1, 1, 3, 1)
        return page

    def _qr_page_wifi(self):
        page, gr = self._qr_page()
        gr.attach(Gtk.Label(label=t("Network name (SSID):"), xalign=0), 0, 0, 1, 1)
        self.qr_ssid = Gtk.Entry()
        gr.attach(self.qr_ssid, 1, 0, 3, 1)
        gr.attach(Gtk.Label(label=t("Password:"), xalign=0), 0, 1, 1, 1)
        self.qr_pass = Gtk.Entry()
        self.qr_pass.set_visibility(False)
        gr.attach(self.qr_pass, 1, 1, 3, 1)
        self.qr_show_pass = Gtk.CheckButton(label=t("show"))
        self.qr_show_pass.connect(
            "toggled", lambda b: self.qr_pass.set_visibility(b.get_active()))
        gr.attach(self.qr_show_pass, 4, 1, 1, 1)
        gr.attach(Gtk.Label(label=t("Encryption:"), xalign=0), 0, 2, 1, 1)
        self.qr_sec = Gtk.ComboBoxText()
        for key, label in (("WPA", "WPA/WPA2/WPA3"), ("WEP", "WEP"),
                           ("nopass", t("open (no password)"))):
            self.qr_sec.append(key, label)
        self.qr_sec.set_active_id("WPA")
        gr.attach(self.qr_sec, 1, 2, 1, 1)
        self.qr_hidden = Gtk.CheckButton(label=t("SSID hidden"))
        gr.attach(self.qr_hidden, 2, 2, 2, 1)
        self.qr_ssid.set_tooltip_text(
            t("Network name (SSID). Scan with the phone camera – the Wi-Fi is set up automatically."))
        self.qr_pass.set_tooltip_text(
            t("Wi-Fi password. Special characters (; , : \" \\) are escaped automatically."))
        return page

    def _qr_page_vcard(self):
        page, gr = self._qr_page()
        for row_, label, attr, ph, col in (
                (0, t("Name:"), "qr_vname", "Max Mustermann", 0),
                (0, t("Phone:"), "qr_vphone", "+49 170 1234567", 2),
                (1, t("E-mail:"), "qr_vmail", "max@example.org", 0),
                (1, t("Company:"), "qr_vorg", "", 2),
                (2, t("Website:"), "qr_vhome", "example.org", 0)):
            gr.attach(Gtk.Label(label=label, xalign=0), col, row_, 1, 1)
            e = Gtk.Entry()
            if ph:
                e.set_placeholder_text(ph)
            setattr(self, attr, e)
            gr.attach(e, col + 1, row_, 1, 1)
        return page

    def _qr_page_email(self):
        page, gr = self._qr_page()
        gr.attach(Gtk.Label(label=t("To:"), xalign=0), 0, 0, 1, 1)
        self.qr_mail_to = Gtk.Entry()
        self.qr_mail_to.set_placeholder_text("empfaenger@example.org")
        gr.attach(self.qr_mail_to, 1, 0, 3, 1)
        gr.attach(Gtk.Label(label=t("Subject:"), xalign=0), 0, 1, 1, 1)
        self.qr_mail_subj = Gtk.Entry()
        gr.attach(self.qr_mail_subj, 1, 1, 3, 1)
        gr.attach(Gtk.Label(label=t("Message:"), xalign=0), 0, 2, 1, 1)
        self.qr_mail_body = Gtk.Entry()
        gr.attach(self.qr_mail_body, 1, 2, 3, 1)
        return page

    def _qr_page_tel(self):
        page, gr = self._qr_page()
        gr.attach(Gtk.Label(label=t("Phone number:"), xalign=0), 0, 0, 1, 1)
        self.qr_tel = Gtk.Entry()
        self.qr_tel.set_placeholder_text("+49 30 1234567")
        gr.attach(self.qr_tel, 1, 0, 3, 1)
        gr.attach(self._hint(t("Scanning shows the number to call.")),
                  1, 1, 3, 1)
        return page

    def _qr_page_sms(self):
        page, gr = self._qr_page()
        gr.attach(Gtk.Label(label=t("Number:"), xalign=0), 0, 0, 1, 1)
        self.qr_sms_to = Gtk.Entry()
        gr.attach(self.qr_sms_to, 1, 0, 3, 1)
        gr.attach(Gtk.Label(label=t("Message:"), xalign=0), 0, 1, 1, 1)
        self.qr_sms_text = Gtk.Entry()
        gr.attach(self.qr_sms_text, 1, 1, 3, 1)
        return page

    def _qr_page_geo(self):
        page, gr = self._qr_page()
        gr.attach(Gtk.Label(label=t("Latitude:"), xalign=0), 0, 0, 1, 1)
        self.qr_lat = Gtk.Entry()
        self.qr_lat.set_placeholder_text("52.5163")
        gr.attach(self.qr_lat, 1, 0, 1, 1)
        gr.attach(Gtk.Label(label=t("Longitude:"), xalign=0), 2, 0, 1, 1)
        self.qr_lon = Gtk.Entry()
        self.qr_lon.set_placeholder_text("13.3777")
        gr.attach(self.qr_lon, 3, 0, 1, 1)
        gr.attach(self._hint(
            t("Tip: read the coordinates e.g. from Google Maps (right-click the place).")), 1, 1, 3, 1)
        return page

    def _build_expert_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(4)

        # ---- theme (colour scheme of the UI) -------------------------------
        df, dg = _frame_with_grid(t("Design"))
        dg.attach(Gtk.Label(label=t("Colour scheme:"), xalign=0), 0, 0, 1, 1)
        self.theme_combo = Gtk.ComboBoxText()
        for key in THEME_ORDER:
            self.theme_combo.append(key, t(THEMES[key]["label"]))
        self.theme_combo.set_active_id(DEFAULT_THEME)
        self.theme_combo.set_tooltip_text(
            t("Dark = dark interface\nLight = light interface for bright surroundings\nDeadlineDriven = colour scheme of the website (black, turquoise #00d2be, orange #ff6a00)"))
        dg.attach(self.theme_combo, 1, 0, 3, 1)
        dg.attach(Gtk.Label(label=t("Language:"), xalign=0), 0, 1, 1, 1)
        self.lang_combo = Gtk.ComboBoxText()
        for _code, _name, _flag in I18N_LANGS:
            self.lang_combo.append(_code, f"{_flag}  {_name}")
        self.lang_combo.set_active_id(i18n.get_lang())
        self.lang_combo.set_tooltip_text(
            t("Interface language – applied immediately"))
        dg.attach(self.lang_combo, 1, 1, 3, 1)
        box.pack_start(df, False, False, 0)

        # ---- desktop launcher (shortcut on the desktop) -------------------
        srow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        srow.pack_start(Gtk.Label(label=t("Desktop shortcut:"), xalign=0),
                        False, False, 0)
        sc_new = Gtk.Button(label=t("Create"))
        sc_new.set_tooltip_text(t("Put a launcher for this program on the "
                                  "desktop (and into the application menu)"))
        sc_new.connect("clicked", lambda *_: self.create_desktop_shortcut())
        srow.pack_start(sc_new, False, False, 0)
        sc_del = Gtk.Button(label=t("Remove"))
        sc_del.set_tooltip_text(t("Delete the desktop shortcut (the menu entry stays)"))
        sc_del.connect("clicked", lambda *_: self.remove_desktop_shortcut())
        srow.pack_start(sc_del, False, False, 0)
        box.pack_start(srow, False, False, 0)

        # ---- printer selection (makes the program universal) ---------------
        gf, gg = _frame_with_grid(t("Printer"))
        gg.attach(Gtk.Label(label=t("Device:"), xalign=0), 0, 0, 1, 1)
        self.device_combo = Gtk.ComboBoxText()
        self.device_combo.append("auto", t("automatic (search for X3)"))
        self.device_combo.append("manual", t("manual (MAC below)"))
        self.device_combo.set_active_id("auto")
        self.device_combo.set_tooltip_text(
            t("Select Bluetooth printer – “Search” lists nearby devices (takes a few seconds)"))
        gg.attach(self.device_combo, 1, 0, 1, 1)
        sb = Gtk.Button(label=t("Search"))
        sb.set_tooltip_text(t("Search nearby Bluetooth devices"))
        sb.connect("clicked", self.on_scan_devices)
        gg.attach(sb, 2, 0, 1, 1)
        tb = Gtk.Button(label=t("Test"))
        tb.set_tooltip_text(t("Test the connection to the selected printer"))
        tb.connect("clicked", self.on_test_printer)
        gg.attach(tb, 3, 0, 1, 1)

        gg.attach(Gtk.Label(label="MAC:", xalign=0), 0, 1, 1, 1)
        self.mac_entry = Gtk.Entry()
        self.mac_entry.set_text(x3print.detect_mac())
        self.mac_entry.set_width_chars(14)
        self.mac_entry.set_tooltip_text(t("Printer's Bluetooth address"))
        gg.attach(self.mac_entry, 1, 1, 3, 1)

        gg.attach(Gtk.Label(label=t("Protocol:"), xalign=0), 0, 2, 1, 1)
        self.proto_combo = Gtk.ComboBoxText()
        self.proto_combo.append("x3", "X3 / Snap & Tag (YK)")
        self.proto_combo.append("escpos", t("ESC/POS (standard thermal)"))
        self.proto_combo.set_active_id("x3")
        self.proto_combo.set_tooltip_text(
            t("X3 = Snap & Tag printer (also ORGBRO)\nESC/POS = common thermal printers (58/80 mm)"))
        gg.attach(self.proto_combo, 1, 2, 1, 1)

        gg.attach(Gtk.Label(label=t("Model:"), xalign=0), 2, 2, 1, 1)
        self.model_combo = Gtk.ComboBoxText()
        for key in x3print.MODELS:
            self.model_combo.append(key, x3print.MODELS[key][0])
        self.model_combo.set_active_id("x3")
        self.model_combo.set_tooltip_text(
            t("Preset: sets protocol, paper width and raster width accordingly"))
        gg.attach(self.model_combo, 3, 2, 1, 1)
        box.pack_start(gf, False, False, 0)

        b = Gtk.Button(label=t("Read firmware + status"))
        b.connect("clicked", self.on_read_device)
        box.pack_start(b, False, False, 0)
        g = Gtk.Grid(column_spacing=8, row_spacing=6)
        box.pack_start(g, False, False, 0)
        g.attach(Gtk.Label(label=t("Raw command (hex):"), xalign=0), 0, 0, 1, 1)
        self.raw_entry = Gtk.Entry()
        self.raw_entry.set_text("64 11 03 00 00 00 00 00 00 9b")
        self.raw_entry.set_width_chars(12)
        g.attach(self.raw_entry, 1, 0, 1, 1)
        sb = Gtk.Button(label=t("Send"))
        sb.connect("clicked", self.on_send_raw)
        g.attach(sb, 2, 0, 1, 1)
        g.attach(Gtk.Label(label=t("(raw command for X3 only)"), xalign=0), 3, 0, 1, 1)
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(34)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        css = Gtk.CssProvider()
        css.load_from_data(b"textview { font-family: monospace; font-size: 9pt; }")
        self.log_view.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        sw.add(self.log_view)
        box.pack_start(sw, True, True, 0)
        ver = Gtk.Label(xalign=0)
        ver.set_markup(f"<small>X3 Thermal Printer {VERSION} · "
                       f"x3print.py {x3print.VERSION}</small>")
        ver.get_style_context().add_class("dim-label")
        ver.set_tooltip_text(t("Software version"))
        box.pack_start(ver, False, False, 0)
        self.notebook.append_page(box, Gtk.Label(label=t("Settings")))

    def _connect_signals(self):
        # tab switch (text / image / barcode) -> refresh the preview immediately
        self.notebook.connect("switch-page", self.on_notebook_switch)
        self.textview.get_buffer().connect("changed", self.queue_preview)
        for w in (self.bold_check, self.dither_check, self.invert_check,
                  self.density_check, self.flip_h_check, self.flip_v_check,
                  self.trim_check):
            w.connect("toggled", self.queue_preview)
        for w in (self.size_spin, self.thicken_spin, self.thresh_spin,
                  self.code_scale, self.feed_spin, self.scale_spin,
                  self.left_spin, self.width_spin, self.rotate_spin,
                  self.stretch_x, self.stretch_y,
                  self.density_spin, self.spacing_spin):
            w.connect("value-changed", self.queue_preview)
        for w in (self.dots_spin, self.paper_mm, self.margin_mm,
                  self.offset_mm, self.center_spin):
            w.connect("value-changed", self.on_geometry_change)
        for w in (self.align_combo, self.code_combo, self.mode_combo):
            w.connect("changed", self.queue_preview)
        for w in (self.code_entry, self.speed_entry, self.mac_entry):
            w.connect("changed", self.queue_preview)
        # QR code: type + fields
        self.code_combo.connect("changed", self.on_code_type_changed)
        # printer selection (settings tab)
        self.device_combo.connect("changed", self.on_device_selected)
        self.proto_combo.connect("changed", self.on_protocol_changed)
        self.model_combo.connect("changed", self.on_model_changed)
        self.qr_kind.connect("changed", self.on_qr_changed)
        self.qr_sec.connect("changed", self.on_qr_changed)
        self.qr_ecc.connect("changed", self.queue_preview)
        self.qr_hidden.connect("toggled", self.on_qr_changed)
        for w in (self.qr_text, self.qr_url, self.qr_ssid, self.qr_pass,
                  self.qr_vname, self.qr_vphone, self.qr_vmail, self.qr_vorg,
                  self.qr_vhome, self.qr_mail_to, self.qr_mail_subj,
                  self.qr_mail_body, self.qr_tel, self.qr_sms_to,
                  self.qr_sms_text, self.qr_lat, self.qr_lon):
            w.connect("changed", self.on_qr_changed)
        self.md_check.connect("toggled", self.queue_preview)
        self.theme_combo.connect("changed", self.on_theme_changed)
        self.lang_combo.connect("changed", self.on_lang_changed)
        self.dither_check.connect("toggled", self.queue_preview)
        self.invert_check.connect("toggled", self.queue_preview)
        self.bright_scale.connect("value-changed", self.on_brightness_changed)
        self.density_spin.connect("value-changed", self.on_density_changed)

    # ---------------------------------------------------------- settings
    def load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                c = json.load(f)
        except Exception:                                    # noqa: BLE001
            return

        def setv(w, v, conv=None):
            try:
                if conv:
                    w.set_text(conv(v))
                elif isinstance(w, (Gtk.SpinButton, Gtk.Scale)):
                    w.set_value(float(v))
                elif isinstance(w, Gtk.ComboBoxText):
                    w.set_active_id(str(v))
                elif isinstance(w, Gtk.CheckButton):
                    w.set_active(bool(v))
                elif isinstance(w, Gtk.Entry):
                    w.set_text(str(v))
            except Exception:                                # noqa: BLE001
                pass

        setv(self.mac_entry, c.get("mac"), str)
        setv(self.proto_combo, c.get("protocol", "x3"))
        setv(self.model_combo, c.get("model", "x3"))
        img = c.get("image")
        if isinstance(img, str) and img.strip() and os.path.exists(img):
            self.image_path = img
        setv(self.dots_spin, c.get("dots", DEFAULT_DOTS))
        setv(self.left_spin, c.get("left", DEF_LEFT))
        setv(self.width_spin, c.get("width", DEF_WIDTH))
        setv(self.paper_mm, c.get("paper_mm", DEF_PAPER_MM))
        setv(self.margin_mm, c.get("margin_mm", DEF_MARGIN_MM))
        setv(self.offset_mm, c.get("offset_mm", DEF_OFFSET_MM))
        setv(self.center_spin, c.get("center_dots", DEF_CENTER))
        setv(self.bright_scale, c.get("brightness", 100))
        setv(self.density_spin, c.get("density", 0x0C))
        setv(self.density_check, c.get("density_manual", False))
        setv(self.speed_entry, c.get("speed", "0x55"), str)
        setv(self.feed_spin, c.get("feed", DEFAULT_FEED))
        setv(self.mode_combo, c.get("mode", DEFAULT_MODE))
        setv(self.size_spin, c.get("size", DEF_SIZE))
        setv(self.align_combo, c.get("align", "left"))
        setv(self.bold_check, c.get("bold", False))
        setv(self.thicken_spin, c.get("thicken", 0))
        setv(self.spacing_spin, c.get("spacing", 3))
        setv(self.thresh_spin, c.get("threshold", DEF_THRESHOLD))
        setv(self.dither_check, c.get("dither", DEF_DITHER))
        setv(self.invert_check, c.get("invert", False))
        setv(self.md_check, c.get("md", True))
        mb = c.get("layout_bg") or c.get("meme_bg")
        if isinstance(mb, str) and mb.strip() and os.path.exists(mb):
            self.meme_bg = mb
        self._update_meme_label()
        blocks = c.get("blocks")
        if not isinstance(blocks, list):                # old meme fields
            blocks = []
            up = c.get("meme_upper", True)
            size = c.get("meme_size", 60)
            if str(c.get("meme_top", "")).strip():
                blocks.append(dict(text=c.get("meme_top", ""), pos="top",
                                   style="outline", size=size, align="center",
                                   upper=up))
            if str(c.get("meme_bottom", "")).strip():
                blocks.append(dict(text=c.get("meme_bottom", ""),
                                   pos="bottom", style="outline", size=size,
                                   align="center", upper=up))
        self.set_blocks(blocks)
        txt = c.get("text")
        if isinstance(txt, str) and txt.strip():
            self.textview.get_buffer().set_text(txt)
        setv(self.code_combo, c.get("code_type", "ean13"))
        setv(self.code_entry, c.get("code_content", "4006381333931"), str)
        setv(self.code_scale, c.get("code_scale", 3))
        setv(self.scale_spin, c.get("scale", 100))
        setv(self.rotate_spin, c.get("rotate", DEF_ROTATE))
        setv(self.flip_h_check, c.get("flip_h", False))
        setv(self.flip_v_check, c.get("flip_v", False))
        setv(self.stretch_x, c.get("stretch_x", 100))
        setv(self.stretch_y, c.get("stretch_y", 100))
        setv(self.trim_check, c.get("trim", False))
        # reuse the window size of the last run (limited to the screen)
        try:
            w, h = int(c.get("win_w", 0) or 0), int(c.get("win_h", 0) or 0)
            if w > 400 and h > 300:
                disp = Gdk.Display.get_default()
                mon = (disp.get_primary_monitor() or disp.get_monitor(0)
                       if disp else None)
                if mon:
                    geo = mon.get_workarea()          # without panels/bars
                    w = min(w, geo.width - 40)
                    h = min(h, geo.height - 60)
                self.win.set_default_size(w, h)
        except Exception:                                    # noqa: BLE001
            pass
        self._start_maximized = bool(c.get("win_max", False))
        try:
            self._split_ratio = max(0.20, min(0.70,
                                             float(c.get("split_ratio", 0.40))))
        except Exception:                                    # noqa: BLE001
            self._split_ratio = 0.40
        self._split_user = bool(c.get("split_user", False))
        self._shortcut_created = bool(c.get("shortcut_created", False))
        try:
            self.ui_zoom = max(0.70, min(1.40, float(c.get("ui_zoom", 1.0))))
        except Exception:                                    # noqa: BLE001
            self.ui_zoom = 1.0
        self._theme_start = str(c.get("theme", DEFAULT_THEME))
        setv(self.theme_combo, self._theme_start)
        setv(self.lang_combo, c.get("lang") or i18n.system_lang())
        q = c.get("qr")
        if isinstance(q, dict):
            setv(self.qr_kind, q.get("kind", DEFAULT_QR_KIND))
            setv(self.qr_ecc, q.get("ecc", "M"))
            setv(self.qr_sec, q.get("security", "WPA"))
            setv(self.qr_hidden, q.get("hidden", False))
            for key, w in (("text", self.qr_text), ("url", self.qr_url),
                           ("ssid", self.qr_ssid), ("password", self.qr_pass),
                           ("name", self.qr_vname), ("phone", self.qr_vphone),
                           ("email", self.qr_vmail), ("org", self.qr_vorg),
                           ("homepage", self.qr_vhome), ("to", self.qr_mail_to),
                           ("subject", self.qr_mail_subj),
                           ("body", self.qr_mail_body),
                           ("sms_to", self.qr_sms_to),
                           ("sms_text", self.qr_sms_text),
                           ("lat", self.qr_lat), ("lon", self.qr_lon)):
                setv(w, q.get(key, ""), str)
            if not str(q.get("url") or "").strip():
                setv(self.qr_url, DEFAULT_QR_URL, str)
        b = int(self.bright_scale.get_value())
        self.bright_label.set_text(
            t("Density:") + f" {_brightness_density(b):#04x} · {b} %")
        self.density_label.set_text(f"{int(self.density_spin.get_value()):#04x}")

    def save_config(self):
        c = dict(
            mac=self.mac_entry.get_text().strip(),
            protocol=self.proto_combo.get_active_id() or "x3",
            model=self.model_combo.get_active_id() or "x3",
            image=self.image_path or "",
            dots=int(self.dots_spin.get_value()),
            left=int(self.left_spin.get_value()),
            width=int(self.width_spin.get_value()),
            paper_mm=float(self.paper_mm.get_value()),
            margin_mm=float(self.margin_mm.get_value()),
            offset_mm=float(self.offset_mm.get_value()),
            center_dots=int(self.center_spin.get_value()),
            brightness=int(self.bright_scale.get_value()),
            density=int(self.density_spin.get_value()),
            density_manual=self.density_check.get_active(),
            speed=self.speed_entry.get_text().strip(),
            feed=int(self.feed_spin.get_value()),
            mode=self.mode_combo.get_active_id() or DEFAULT_MODE,
            size=int(self.size_spin.get_value()),
            align=self.align_combo.get_active_id() or "left",
            bold=self.bold_check.get_active(),
            thicken=int(self.thicken_spin.get_value()),
            spacing=int(self.spacing_spin.get_value()),
            threshold=int(self.thresh_spin.get_value()),
            dither=self.dither_check.get_active(),
            invert=self.invert_check.get_active(),
            md=self.md_check.get_active(),
            text=self.textview.get_buffer().get_text(
                self.textview.get_buffer().get_start_iter(),
                self.textview.get_buffer().get_end_iter(), False),
            meme_bg=self.meme_bg or "",
            layout_bg=self.meme_bg or "",
            blocks=self.collect_blocks(),
            code_type=self.code_combo.get_active_id() or "ean13",
            code_content=self.code_entry.get_text(),
            code_scale=int(self.code_scale.get_value()),
            scale=int(self.scale_spin.get_value()),
            rotate=float(self.rotate_spin.get_value()),
            flip_h=self.flip_h_check.get_active(),
            flip_v=self.flip_v_check.get_active(),
            stretch_x=int(self.stretch_x.get_value()),
            stretch_y=int(self.stretch_y.get_value()),
            trim=self.trim_check.get_active(),
            win_w=int(self.win.get_size().width),
            win_h=int(self.win.get_size().height),
            win_max=bool(self.win.is_maximized()),
            split_ratio=round(float(self._split_ratio), 3),
            split_user=bool(getattr(self, "_split_user", False)),
            shortcut_created=bool(getattr(self, "_shortcut_created", False)),
            ui_zoom=round(float(self.ui_zoom), 2),
            theme=self.theme or DEFAULT_THEME,
            lang=i18n.get_lang(),
            qr=dict(kind=self.qr_kind.get_active_id() or DEFAULT_QR_KIND,
                    ecc=self.qr_ecc.get_active_id() or "M",
                    text=self.qr_text.get_text(),
                    url=self.qr_url.get_text(),
                    ssid=self.qr_ssid.get_text(),
                    password=self.qr_pass.get_text(),
                    security=self.qr_sec.get_active_id() or "WPA",
                    hidden=self.qr_hidden.get_active(),
                    name=self.qr_vname.get_text(),
                    phone=self.qr_vphone.get_text(),
                    email=self.qr_vmail.get_text(),
                    org=self.qr_vorg.get_text(),
                    homepage=self.qr_vhome.get_text(),
                    to=self.qr_mail_to.get_text(),
                    subject=self.qr_mail_subj.get_text(),
                    body=self.qr_mail_body.get_text(),
                    sms_to=self.qr_sms_to.get_text(),
                    sms_text=self.qr_sms_text.get_text(),
                    lat=self.qr_lat.get_text(),
                    lon=self.qr_lon.get_text()),
        )
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(c, f, indent=1, ensure_ascii=False)
        except Exception:                                    # noqa: BLE001
            pass

    def on_destroy(self, *_a):
        if getattr(self, "_rebuilding", False):
            return                       # the old window gets replaced
        self.save_config()
        Gtk.main_quit()

    # ------------------------------------------------------------- Helfer
    def _dots(self):
        d = int(self.dots_spin.get_value())
        return d - (d % 8)

    def px_per_mm(self):
        """Dots per mm: 864 dots = 300 dpi, 432 dots = 203 dpi."""
        dpi = 300.0 if self._dots() >= 600 else 203.0
        return dpi / 25.4

    def geometry(self):
        """Paper and content window from paper width/margin/offset (mm)."""
        dots = self._dots()
        ppm = self.px_per_mm()
        paper_px = int(round(float(self.paper_mm.get_value()) * ppm))
        margin_px = int(round(float(self.margin_mm.get_value()) * ppm))
        offset_px = int(round(float(self.offset_mm.get_value()) * ppm))
        paper_px = max(8, min(dots, paper_px))
        center = int(self.center_spin.get_value())
        pl = max(0, min(center - paper_px // 2, dots - paper_px))
        pr = pl + paper_px
        cl = pl + margin_px + offset_px
        cw = max(8, paper_px - 2 * margin_px)
        return dict(dots=dots, ppm=ppm, paper_left=pl, paper_right=pr,
                    paper_px=paper_px, margin_px=margin_px,
                    offset_px=offset_px, content_left=cl, content_width=cw)

    def _refresh_geometry_labels(self):
        g = self.geometry()
        ppm = g["ppm"]
        info = t("Window: {w} dots at x={x} ({mm} mm)").format(
            w=g['content_width'], x=g['content_left'],
            mm=f"{g['content_width'] / ppm:.1f}")
        cl, cw = g["content_left"], g["content_width"]
        if cl < g["paper_left"] or cl + cw > g["paper_right"]:
            info += t(" - extends beyond the paper (red, not printed)")
        if g["paper_px"] >= g["dots"] or g["paper_left"] in (0, g["dots"] - g["paper_px"]):
            info += t(" - paper limited by raster edge")
        self.geo_info.set_text(info)

    def on_geometry_change(self, *_a):
        """Paper/position values changed -> recompute the print window."""
        if self._loading:
            return
        g = self.geometry()
        self.left_spin.set_value(g["content_left"])
        self.width_spin.set_value(g["content_width"])
        self._refresh_geometry_labels()
        self.queue_preview()

    def rotate_step(self, delta):
        """Rotate further by `delta` degrees (0 = reset to 0 degrees)."""
        if not delta:
            self.rotate_spin.set_value(0)
            return
        v = float(self.rotate_spin.get_value()) + float(delta)
        v = ((v + 180.0) % 360.0) - 180.0
        self.rotate_spin.set_value(v)

    # ---------------------------------------------------------- QR-Codes
    def qr_content(self):
        """Build the QR payload from the fields of the selected QR type."""
        return x3print.qr_payload(
            self.qr_kind.get_active_id() or "text",
            text=self.qr_text.get_text(),
            url=self.qr_url.get_text(),
            ssid=self.qr_ssid.get_text(),
            password=self.qr_pass.get_text(),
            security=self.qr_sec.get_active_id() or "WPA",
            hidden=self.qr_hidden.get_active(),
            name=self.qr_vname.get_text(),
            phone=self.qr_vphone.get_text(),
            email=self.qr_vmail.get_text(),
            org=self.qr_vorg.get_text(),
            homepage=self.qr_vhome.get_text(),
            to=self.qr_mail_to.get_text(),
            subject=self.qr_mail_subj.get_text(),
            body=self.qr_mail_body.get_text(),
            sms_to=self.qr_sms_to.get_text(),
            sms_text=self.qr_sms_text.get_text(),
            lat=self.qr_lat.get_text(),
            lon=self.qr_lon.get_text())

    def update_qr_preview(self, *_a):
        """Show what exactly is encoded in the QR code."""
        try:
            text = self.qr_content() or t("(still empty)")
        except Exception as e:                              # noqa: BLE001
            text = t("(error: {err})").format(err=e)
        self.qr_preview.set_text(text)

    def on_qr_changed(self, *_a):
        """QR type or one of its fields changed -> matching page + preview."""
        self.qr_stack.set_visible_child_name(
            self.qr_kind.get_active_id() or "text")
        self.update_qr_preview()
        self.queue_preview()

    def on_code_type_changed(self, *_a):
        """Switch between barcode and QR code."""
        is_qr = (self.code_combo.get_active_id() or "ean13") == "qr"
        self.qr_box.set_visible(is_qr)
        self.barcode_box.set_visible(not is_qr)
        self.update_qr_preview()
        self.queue_preview()

    def params(self):
        """Current geometry/parameters from the UI (with plausibility checks)."""
        dots = self._dots()
        left = int(self.left_spin.get_value())
        width = int(self.width_spin.get_value())
        width -= width % 8
        try:
            speed = int(self.speed_entry.get_text(), 0)
        except ValueError:
            speed = DEFAULT_SPEED
        if self.density_check.get_active():
            density = int(self.density_spin.get_value())
        else:
            density = _brightness_density(int(self.bright_scale.get_value()))
        return dict(dots=dots, left=left, width=width, speed=speed,
                    density=density, feed=int(self.feed_spin.get_value()),
                    mode=self.mode_combo.get_active_id() or DEFAULT_MODE,
                    protocol=self.proto_combo.get_active_id() or "x3",
                    mac=self.mac_entry.get_text().strip())

    def content_args(self):
        p = self.params()
        return SimpleNamespace(
            width=p["width"], left=p["left"], dots=p["dots"],
            content_scale=int(self.scale_spin.get_value()),
            rotate=float(self.rotate_spin.get_value()),
            flip_h=self.flip_h_check.get_active(),
            flip_v=self.flip_v_check.get_active(),
            stretch_x=int(self.stretch_x.get_value()),
            stretch_y=int(self.stretch_y.get_value()),
            trim=self.trim_check.get_active(),
            threshold=int(self.thresh_spin.get_value()))

    def build_canvas(self):
        """Build and scale the content, embed it into the raster -> (canvas, img, left)."""
        content = self.build_content()
        if content is None:
            return None, None, None
        ca = self.content_args()
        img, left = _scaled(content, ca)
        canvas = _embed(img, SimpleNamespace(width=img.width, left=left,
                                             dots=ca.dots))
        return canvas, img, left

    def set_status(self, text):
        self.status.set_text(text)

    def log(self, text):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), text + "\n")

    def queue_preview(self, *args):
        if self._loading:
            return
        if self.preview_timeout:
            GLib.source_remove(self.preview_timeout)
        self.preview_timeout = GLib.timeout_add(250, self.update_preview)

    def flush_preview(self, *args):
        """Redraw the preview immediately (without the 250 ms delay)."""
        if self._loading:
            return
        if self.preview_timeout:
            GLib.source_remove(self.preview_timeout)
            self.preview_timeout = None
        self.update_preview()

    def on_brightness_changed(self, scale):
        if self._loading:
            return
        b = int(scale.get_value())
        self.bright_label.set_text(
            t("Density:") + f" {_brightness_density(b):#04x} · {b} %")
        self.queue_preview()

    def on_density_changed(self, spin):
        self.density_label.set_text(f"{int(spin.get_value()):#04x}")
        self.queue_preview()

    # ----------------------------------------------------- preview dragging
    def _set_preview_cursor(self):
        self._set_cursor("grab")
        return False

    def _set_cursor(self, name):
        """Set the mouse cursor inside the preview area (with a cache)."""
        if name == self._cursor_name:
            return
        try:
            win = self.preview_box.get_window()
            if win:
                win.set_cursor(Gdk.Cursor.new_from_name(
                    Gdk.Display.get_default(), name))
                self._cursor_name = name
        except Exception:                                    # noqa: BLE001
            pass

    def _preview_viewport(self):
        """Visible area of the preview (width, height) in pixels."""
        try:
            a = self.preview_sw.get_allocation()
            w, h = a.width - 16, a.height - 34      # room for scrollbars/borders
            if w > 80 and h > 80:
                return w, h
        except Exception:                                    # noqa: BLE001
            pass
        return 560, 700

    def on_preview_resize(self, _w, alloc):
        """Window/column changed -> fit the preview to the area again."""
        if self._loading:
            return
        if (abs(alloc.width - self._preview_size[0]) > 4
                or abs(alloc.height - self._preview_size[1]) > 4):
            self._preview_size = (alloc.width, alloc.height)
            self.queue_preview()

    def on_split_drag(self, *_a):
        """Divider moved -> remember the new share (used when scaling)."""
        try:
            w = max(1, self.paned.get_allocation().width)
            r = self.paned.get_position() / w
            self._split_ratio = max(0.20, min(0.70, r))
            self._split_user = True      # keep the user's own choice
        except Exception:                                    # noqa: BLE001
            pass
        return False

    def init_split(self):
        """Initial split: left column as wide as its content needs (compact)."""
        self._fit_left_pane()
        return False

    def on_win_size_allocate(self, *_a):
        """Window changed -> never cut off the left column."""
        GLib.idle_add(self._fit_left_pane)
        return False

    def _fit_left_pane(self):
        """Give the left column exactly the width it needs - the rest goes to the preview.

        Widgets are never cut off (no horizontal scrolling) while the preview
        still gets as much room as possible on the right.  If the user dragged
        the divider themselves, their ratio wins.
        """
        try:
            need = self.left.get_preferred_width()[1] + 6
            w = self.paned.get_allocation().width
            if w < 200:
                return False
            if getattr(self, "_split_user", False):
                want = max(need, int(round(w * self._split_ratio)))
            else:
                want = need
            want = min(want, int(round(w * 0.72)))
            if abs(self.paned.get_position() - want) > 2:
                self.paned.set_position(int(want))
        except Exception:                                    # noqa: BLE001
            pass
        return False

    # ------------------------------------ view (UI zoom) of the left column
    def set_ui_zoom(self, value):
        """Scale the UI of the left column up/down (70-140 %)."""
        self.ui_zoom = max(0.70, min(1.40, round(float(value), 2)))
        self.ui_zoom_label.set_text(f"{int(round(self.ui_zoom * 100))} %")
        self.apply_left_scale(self.ui_zoom)

    def ui_zoom_step(self, delta):
        self.set_ui_zoom(self.ui_zoom + delta)

    # ------------------------------------- scale the left column along
    def collect_left_metrics(self):
        """Remember spacing/borders of the left column (base for scaling)."""
        items = []

        def walk(w):
            if isinstance(w, Gtk.Grid):
                items.append(["grid", w, w.get_row_spacing(),
                              w.get_column_spacing()])
            elif isinstance(w, Gtk.Frame) and isinstance(w.get_child(), Gtk.Grid):
                items.append(["border", w.get_child(),
                              w.get_child().get_border_width(), 0])
            elif isinstance(w, Gtk.Box):
                items.append(["box", w, w.get_spacing(), 0])
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    walk(c)

        walk(self.left)
        self._left_metrics = items
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                ctx = self.left.get_style_context()
                self._left_font_px = ctx.get_font(
                    Gtk.StateFlags.NORMAL).get_size() / 1024.0
        except Exception:                                    # noqa: BLE001
            self._left_font_px = 11.0
        return False

    def apply_left_scale(self, s):
        """Scale the left column by factor `s` (font, spacing, paddings)."""
        for kind, w, a, b in getattr(self, "_left_metrics", []):
            if kind == "grid":
                w.set_row_spacing(max(0, int(round(a * s))))
                w.set_column_spacing(max(0, int(round(b * s))))
            elif kind == "border":
                w.set_border_width(max(1, int(round(a * s))))
            else:
                w.set_spacing(max(0, int(round(a * s))))
        base = getattr(self, "_left_font_px", 14.0)
        font = max(6.0, base * s)
        pad_v = max(0, int(round(3 * s)))
        pad_h = max(1, int(round(6 * s)))
        h_min = max(8, int(round(18 * s)))
        css = (
            f".x3left label, .x3left button, .x3left entry, .x3left spinbutton,\n"
            f".x3left combobox, .x3left comboboxtext, .x3left checkbutton,\n"
            f".x3left textview, .x3left notebook tab {{ font-size: {font:.1f}px; }}\n"
            f".x3left button {{ padding: {pad_v}px {pad_h}px; "
            f"min-height: {h_min}px; min-width: 0; }}\n"
            f".x3left entry, .x3left spinbutton {{ min-height: {h_min}px; }}\n"
            f".x3left notebook > header {{ padding: 0; }}\n"
        )
        self._left_css.load_from_data(css.encode("utf-8"))
        self._left_scale = s
        GLib.idle_add(self._fit_left_pane)

    def _preview_origin(self):
        """Position of the raster area in EventBox coordinates (px)."""
        try:
            alloc = self.preview_image.get_allocation()
            pb = self.preview_image.get_pixbuf()
            if pb is None:
                return 0, 0
            ox = max(0, (alloc.width - pb.get_width()) // 2)
            oy = max(0, (alloc.height - pb.get_height()) // 2)
            f = self._disp_scale or 1.0
            # image sits top left; the raster area is inset by the margin
            return ox + PV_GX * f, oy + PV_GT * f
        except Exception:                                    # noqa: BLE001
            return 0, 0

    def _corner_points(self):
        """Corner points of the content in raster dots (tl/tr top, bl/br bottom)."""
        l, w, h = self._pl_left, self._pl_cw, self._pl_h
        return {"tl": (l, 0), "tr": (l + w, 0),
                "bl": (l, h - 1), "br": (l + w, h - 1)}

    def _start_resize(self, side, ev):
        """Start dragging a handle (left/right edge or corner)."""
        self._drag_mode = "resize"
        self._resize_side = side
        self._drag_x0 = ev.x - self._drag_ox
        self._drag_y0 = ev.y - self._drag_oy
        self._resize_scale0 = float(self.scale_spin.get_value())
        self._resize_w0 = float(self._pl_cw)
        self._resize_off0 = float(self.offset_mm.get_value())
        if side in ("tl", "tr", "bl", "br"):
            corners = self._corner_points()
            opp = {"tl": "br", "br": "tl", "tr": "bl", "bl": "tr"}[side]
            ax, ay = corners[opp]
            hx, hy = corners[side]
            self._resize_anchor = (ax, ay)
            self._resize_d0 = max(8.0, math.hypot(hx - ax, hy - ay))
        self._set_cursor("nwse-resize" if side in ("tl", "br") else
                         "nesw-resize" if side in ("tr", "bl") else
                         "ew-resize")
        return True

    def _update_cursor(self, ev):
        """Show the mouse cursor according to the position (arrows on handles)."""
        name = "grab"
        f = self._disp_scale or 1.0
        ox, oy = self._preview_origin()
        px, py = ev.x - ox, ev.y - oy
        if self._pl_cw:
            for side, (hx, hy) in self._corner_points().items():
                if abs(px - hx * f) <= 16 and abs(py - hy * f) <= 16:
                    name = ("nwse-resize" if side in ("tl", "br")
                            else "nesw-resize")
                    break
            else:
                hy = self._pl_h // 2
                if ((abs(px - self._pl_left * f) <= 10
                     or abs(px - (self._pl_left + self._pl_cw) * f) <= 10)
                        and abs(py - hy * f) <= 14):
                    name = "ew-resize"
        self._set_cursor(name)

    def on_preview_press(self, _w, ev):
        ox, oy = self._preview_origin()
        self._drag_ox, self._drag_oy = ox, oy
        px, py = ev.x - ox, ev.y - oy       # coordinates inside the preview image
        if ev.button == 1:
            f = self._disp_scale or 1.0
            if self._pl_cw:
                # 1) Eck-Griffe: diagonal skalieren
                for side, (hx, hy) in self._corner_points().items():
                    if abs(px - hx * f) <= 16 and abs(py - hy * f) <= 16:
                        return self._start_resize(side, ev)
                # 2) edge handles: change the width
                hy = self._pl_h // 2
                for side, hx in (("left", self._pl_left),
                                 ("right", self._pl_left + self._pl_cw)):
                    if abs(px - hx * f) <= 10 and abs(py - hy * f) <= 14:
                        return self._start_resize(side, ev)
            self._drag_mode = "content"
            self._drag_x0 = px
            self._drag_y0 = py
            self._drag_off0 = float(self.offset_mm.get_value())
            return True
        if ev.button == 3:                      # right button: move the paper
            self._drag_mode = "paper"
            self._drag_x0 = px
            self._drag_y0 = py
            self._drag_center0 = int(self.center_spin.get_value())
            return True
        return False

    def on_preview_motion(self, _w, ev):
        if self._drag_x0 is None:
            self._update_cursor(ev)
            return False
        f = self._disp_scale or 1.0
        px = ev.x - self._drag_ox           # image coordinates (origin from the drag start)
        py = ev.y - self._drag_oy
        dx_dots = (px - self._drag_x0) / f

        if self._drag_mode == "resize":
            ppm = self.px_per_mm()
            if self._resize_side in ("tl", "tr", "bl", "br"):
                # diagonal: factor from the distance to the opposite corner
                ax, ay = self._resize_anchor
                dist = math.hypot(px / f - ax, py / f - ay)
                new_scale = (self._resize_scale0 * dist
                             / max(8.0, self._resize_d0))
            else:
                if self._resize_side == "right":
                    nw = self._resize_w0 + dx_dots
                else:
                    nw = self._resize_w0 - dx_dots
                new_scale = (self._resize_scale0 * max(8.0, nw)
                             / max(1.0, self._resize_w0))
            new_scale = max(5.0, min(400.0, new_scale))
            if abs(new_scale - float(self.scale_spin.get_value())) >= 0.5:
                w_actual = self._resize_w0 * new_scale / max(1.0, self._resize_scale0)
                d_w = w_actual - self._resize_w0
                # keep the opposite edge fixed: move the canvas by d_w/2
                if self._resize_side in ("right", "br", "tr"):
                    new_off = self._resize_off0 + (d_w / 2.0) / ppm
                else:
                    new_off = self._resize_off0 - (d_w / 2.0) / ppm
                new_off = max(-100.0, min(100.0, round(new_off * 2) / 2))
                self.scale_spin.set_value(round(new_scale))
                self.offset_mm.set_value(new_off)
                self.update_preview()
            self._set_cursor("nwse-resize" if self._resize_side in ("tl", "br") else
                             "nesw-resize" if self._resize_side in ("tr", "bl") else
                             "ew-resize")
            return True

        if self._drag_mode == "paper":
            # move the paper window (match the model to the real paper)
            g = self.geometry()
            half = g["paper_px"] // 2
            new_center = int(round(self._drag_center0 + dx_dots))
            new_center = max(half, min(self._dots() - half, new_center))
            if new_center != int(self.center_spin.get_value()):
                self.center_spin.set_value(new_center)
                self.update_preview()
            return True

        # move the content freely - no limits (outside the paper is marked red)
        ppm = self.px_per_mm()
        new_off = self._drag_off0 + dx_dots / ppm
        new_off = max(-100.0, min(100.0, new_off))
        new_off = round(new_off * 2) / 2                   # 0.5 mm steps
        if abs(new_off - float(self.offset_mm.get_value())) >= 0.25:
            self.offset_mm.set_value(new_off)      # recomputes the geometry
            self.update_preview()                  # immediately, not delayed
        return True

    def on_preview_release(self, _w, _ev):
        if self._drag_x0 is not None:
            self._drag_x0 = None
            self._drag_mode = None
            self._refresh_geometry_labels()
            self.save_config()
        return True

    def _update_image_label(self):
        """Show the file name of the image (empty = none selected)."""
        if self.image_path:
            self.img_label.set_text(os.path.basename(self.image_path))
            self.img_label.set_tooltip_text(self.image_path)
        else:
            self.img_label.set_text(t("no file"))
            self.img_label.set_tooltip_text("")

    def _update_meme_label(self):
        """Show the file name of the meme background image."""
        if self.meme_bg:
            self.meme_label.set_text(os.path.basename(self.meme_bg))
            self.meme_label.set_tooltip_text(self.meme_bg)
        else:
            self.meme_label.set_text(t("no image"))
            self.meme_label.set_tooltip_text("")

    def _choose_image_file(self, title=t("Choose image"), start=None):
        """File dialog for images (PNG/JPG/SVG ...) - returns the path."""
        dlg = Gtk.FileChooserDialog(title=title, parent=self.win,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filt = Gtk.FileFilter()
        filt.set_name(t("Images (including SVG)"))
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.gif", "*.webp",
                    "*.svg", "*.svgz"):
            filt.add_pattern(pat)
        dlg.add_filter(filt)
        svg = Gtk.FileFilter()
        svg.set_name(t("SVG vector graphics"))
        svg.add_pattern("*.svg")
        svg.add_pattern("*.svgz")
        dlg.add_filter(svg)
        folder = start
        if folder:
            folder = folder if os.path.isdir(folder) else os.path.dirname(folder)
            if folder and os.path.isdir(folder):
                dlg.set_current_folder(folder)
        ok = dlg.run() == Gtk.ResponseType.OK
        name = dlg.get_filename() if ok else None
        dlg.destroy()
        return name

    def md_base_dir(self):
        """Folder that relative image paths in Markdown refer to."""
        for p in (self.image_path, self.meme_bg):
            if p:
                d = os.path.dirname(p)
                if d:
                    return d
        return os.getcwd()

    def on_insert_image(self, _btn):
        """Insert an image at the cursor - as ![image](path)."""
        name = self._choose_image_file(t("Insert image"),
                                       self.image_path or self.meme_bg)
        if not name:
            return
        base = self.md_base_dir()
        try:
            rel = os.path.relpath(name, base)
        except ValueError:                                   # anderes Laufwerk
            rel = name
        marker = f"![{os.path.basename(name)}]({rel})"
        buf = self.textview.get_buffer()
        cur = buf.get_iter_at_mark(buf.get_insert())
        prefix = "" if cur.starts_line() else "\n"   # image on its own line
        buf.insert_at_cursor(prefix + marker + "\n")
        self.textview.grab_focus()
        self.flush_preview()
        self.save_config()

    def on_md_help(self, _btn):
        """Show a short overview of the Markdown syntax."""
        self.show_info(t("Markdown formatting"), MD_HELP)

    def on_choose_meme_bg(self, _btn):
        """Choose a background image for the meme mode."""
        name = self._choose_image_file(t("Meme background image"),
                                       self.meme_bg or self.image_path)
        if name:
            self.set_meme_bg(name)

    def set_meme_bg(self, path):
        """Set the background image for layout mode (None = none)."""
        self.meme_bg = path or None
        self._update_meme_label()
        self.flush_preview()
        self.save_config()

    # ------------------------------------------------------- text blocks
    def add_block(self, values=None):
        """Create a text block (values = initial values)."""
        v = values or {}
        frame = Gtk.Frame()
        grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        grid.set_border_width(5)
        frame.add(grid)
        ent = Gtk.Entry()
        ent.set_text(str(v.get("text", "")))
        ent.set_placeholder_text(t("Text for this block"))
        ent.set_tooltip_text(t("Text of this block (one line; several blocks for several positions in the image)"))
        grid.attach(ent, 0, 0, 4, 1)
        rm = Gtk.Button(label="✕")
        rm.set_tooltip_text(t("Remove this text block"))
        grid.attach(rm, 4, 0, 1, 1)

        pos = Gtk.ComboBoxText()
        for k, lbl in (("top", t("Top")), ("middle", t("Middle")),
                       ("bottom", t("Bottom"))):
            pos.append(k, lbl)
        pos.set_active_id(str(v.get("pos", "top")) or "top")
        pos.set_tooltip_text(t("Position: top, middle or bottom – several blocks per position are stacked"))
        grid.attach(pos, 0, 1, 1, 1)

        style = Gtk.ComboBoxText()
        for k, lbl in (("outline", t("Outline")), ("bar", t("Bar")),
                       ("black", t("Black"))):
            style.append(k, lbl)
        style.set_active_id(str(v.get("style", "outline")) or "outline")
        style.set_tooltip_text(
            t("Outline = white text with a black border (meme look, for photos) · Bar = white text on a black bar (most readable) · Black = normal black text"))
        grid.attach(style, 1, 1, 2, 1)

        size = Gtk.SpinButton.new_with_range(10, 200, 2)
        size.set_value(float(v.get("size", 60) or 60))
        size.set_tooltip_text(t("Font size in dots – reduced automatically if needed so the text fits"))
        grid.attach(size, 3, 1, 2, 1)

        align = Gtk.ComboBoxText()
        for k, lbl in (("left", t("Left")), ("center", t("Middle")),
                       ("right", t("Right"))):
            align.append(k, lbl)
        align.set_active_id(str(v.get("align", "center")) or "center")
        grid.attach(align, 0, 2, 2, 1)
        upper = Gtk.CheckButton(label=t("UPPERCASE"))
        upper.set_active(bool(v.get("upper", True)))
        grid.attach(upper, 2, 2, 3, 1)

        entry = {"frame": frame, "text": ent, "pos": pos, "style": style,
                 "size": size, "align": align, "upper": upper}
        ent.connect("changed", self.queue_preview)
        for w in (pos, style, align):
            w.connect("changed", self.queue_preview)
        size.connect("value-changed", self.queue_preview)
        upper.connect("toggled", self.queue_preview)
        rm.connect("clicked", lambda *_: self.remove_block(entry))
        self.blocks.append(entry)
        self.blocks_box.pack_start(frame, False, False, 0)
        frame.show_all()
        return entry

    def remove_block(self, entry):
        """Remove one text block."""
        if entry in self.blocks:
            self.blocks.remove(entry)
        entry["frame"].destroy()
        self.flush_preview()
        self.save_config()

    def _clear_blocks(self):
        for e in list(self.blocks):
            e["frame"].destroy()
        self.blocks = []

    def set_blocks(self, blocks):
        """Copy the text blocks from the configuration."""
        self._clear_blocks()
        for b in (blocks or []):
            if isinstance(b, dict):
                self.add_block(b)
        if not self.blocks:                       # always one block to type into
            self.add_block({"pos": "top", "style": "outline"})

    def collect_blocks(self):
        """Read the current text blocks from the UI."""
        return [dict(text=e["text"].get_text(),
                     pos=e["pos"].get_active_id() or "top",
                     style=e["style"].get_active_id() or "outline",
                     size=int(e["size"].get_value()),
                     align=e["align"].get_active_id() or "center",
                     upper=e["upper"].get_active())
                for e in self.blocks]

    # ------------------------------------------------------ settings etc.
    def on_choose_image(self, _btn):
        name = self._choose_image_file(t("Choose image"), self.image_path)
        if not name:
            return
        self.image_path = name
        self._update_image_label()
        self.apply_image_defaults()        # 0°, Schwelle 190, Dithering an
        self.notebook.set_current_page(1)  # show the image tab (preview = image)
        self._tab_switch_pending = False   # no second redraw needed
        self.flush_preview()              # show it right away (not after 250 ms)
        self.save_config()

    def set_theme(self, name):
        """Farbschema anwenden (dark / light / deadline)."""
        if name not in THEMES:
            name = DEFAULT_THEME
        self.theme = name
        self.theme_combo.set_active_id(name)
        try:
            self._theme_css.load_from_data(
                _theme_css(THEMES[name]).encode("utf-8"))
        except Exception as e:                               # noqa: BLE001
            self.log(t("Could not load the theme: {err}").format(err=e))
        if not self._loading:
            self.save_config()

    def on_theme_changed(self, combo):
        """Theme selection changed."""
        self.set_theme(combo.get_active_id() or DEFAULT_THEME)

    def on_lang_changed(self, combo):
        """Language switched - rebuild the UI immediately."""
        code = combo.get_active_id() or i18n.system_lang()
        if self._loading or code == i18n.get_lang():
            return
        i18n.set_lang(code)
        self.save_config()
        self.restart_ui()

    # ------------------------------------------------- desktop shortcut
    def desktop_shortcut_path(self):
        """Full path of the desktop shortcut ("" when there is no desktop)."""
        folder = _desktop_dir()
        return os.path.join(folder, SHORTCUT_NAME) if folder else ""

    def desktop_shortcut_exists(self):
        path = self.desktop_shortcut_path()
        return bool(path) and os.path.exists(path)

    def create_desktop_shortcut(self, silent=False):
        """Create the menu entry and the desktop shortcut (both idempotent)."""
        text = _desktop_entry_text()
        try:
            entry = os.path.join(os.path.expanduser("~/.local/share/applications"),
                                 SHORTCUT_NAME)
            _write_text(entry, text)
            target = self.desktop_shortcut_path()
            if not target:
                if not silent:
                    self.show_error(t("No desktop folder found - "
                                      "shortcut not created."))
                return False
            _write_text(target, text)
            os.chmod(target, 0o755)          # executable = launchable icon
            self._shortcut_created = True
            if not self._loading:
                self.save_config()
            self.log(t("Desktop shortcut created: {path}").format(path=target))
            return True
        except OSError as err:
            if not silent:
                self.show_error(t("Could not create the desktop shortcut: {err}")
                                .format(err=err))
            return False

    def remove_desktop_shortcut(self):
        """Delete the desktop shortcut (the menu entry stays)."""
        target = self.desktop_shortcut_path()
        try:
            if target and os.path.exists(target):
                os.remove(target)
                self.log(t("Desktop shortcut removed."))
        except OSError as err:
            self.show_error(t("Could not remove the desktop shortcut: {err}")
                            .format(err=err))

    def _maybe_create_shortcut(self):
        """First start: put a launcher on the desktop (once)."""
        if not self._shortcut_created and not self.desktop_shortcut_exists():
            if self.create_desktop_shortcut(silent=True):
                self.set_status(t("Desktop shortcut created."))
        return False

    def restart_ui(self):
        """Rebuild the window in the new language (position is kept)."""
        try:
            pos = self.win.get_position()
            size = self.win.get_size()
            maximized = self.win.is_maximized()
        except Exception:                                    # noqa: BLE001
            pos, size, maximized = None, None, False
        self._rebuilding = True
        if self.preview_timeout:
            GLib.source_remove(self.preview_timeout)
            self.preview_timeout = None
        new = X3App()
        if size:
            new.win.resize(*size)
        if pos:
            new.win.move(*pos)
        if maximized:
            new.win.maximize()
        self.win.destroy()

    def apply_image_defaults(self):
        """Set the standard print-image values when a new image is chosen.

        0 degree rotation, threshold 190, dithering on - so a freshly picked
        image looks as intended right away; adjustable afterwards.
        """
        self.rotate_spin.set_value(DEF_ROTATE)
        self.thresh_spin.set_value(DEF_THRESHOLD)
        self.dither_check.set_active(DEF_DITHER)

    def on_notebook_switch(self, _nb, _child, _num):
        """Tab switched (text/image/barcode) -> rebuild the preview."""
        self._tab_switch_pending = True
        GLib.idle_add(self._apply_tab_switch)

    def _apply_tab_switch(self):
        """Draw only after the switch (then the new page is fixed)."""
        if not self._tab_switch_pending:
            return False
        self._tab_switch_pending = False
        if self._loading:
            return False
        self.on_code_type_changed()      # show the matching barcode/QR fields
        self.flush_preview()
        return False

    # ------------------------------------------------------ build content
    def build_content(self):
        """1-bit content image (width = print window) for the active tab."""
        page = self.notebook.get_current_page()
        width = self.params()["width"]
        try:
            if page == 0:                                  # Text / Layout
                width = self.params()["width"]
                blocks = self.collect_blocks()
                if self.meme_bg or any(b["text"].strip() for b in blocks):
                    return render_blocks(                     # Layout
                        self.meme_bg, blocks, width,
                        invert=self.invert_check.get_active(),
                        brightness=int(self.bright_scale.get_value()),
                        dither=self.dither_check.get_active(),
                        threshold=int(self.thresh_spin.get_value()),
                        base_dir=self.md_base_dir())
                buf = self.textview.get_buffer()
                text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
                if not text.strip():
                    return None
                if self.md_check.get_active():             # Markdown
                    return render_markdown(
                        text, width,
                        font_size=int(self.size_spin.get_value()),
                        align=self.align_combo.get_active_id() or "left",
                        bold=self.bold_check.get_active(),
                        spacing=int(self.spacing_spin.get_value()),
                        thicken=int(self.thicken_spin.get_value()),
                        base_dir=self.md_base_dir(),
                        dither=self.dither_check.get_active(),
                        threshold=int(self.thresh_spin.get_value()))
                return render_text(
                    text, width,
                    font_size=int(self.size_spin.get_value()),
                    align=self.align_combo.get_active_id() or "left",
                    bold=self.bold_check.get_active(),
                    thicken=int(self.thicken_spin.get_value()),
                    spacing=int(self.spacing_spin.get_value()))
            if page == 1:                                  # image
                if not self.image_path:
                    return None
                return render_image_file(
                    self.image_path, width,
                    threshold=int(self.thresh_spin.get_value()),
                    dither=self.dither_check.get_active(),
                    invert=self.invert_check.get_active(),
                    brightness=int(self.bright_scale.get_value()))
            if page == 2:                                  # Barcode/QR
                if self.code_combo.get_active_id() == "qr":
                    payload = self.qr_content().strip()
                    if not payload:
                        return None
                    return render_qrcode(
                        payload,
                        box_px=max(4, int(self.code_scale.get_value()) * 3),
                        ecc=self.qr_ecc.get_active_id() or "M",
                        max_px=width)
                content = self.code_entry.get_text().strip()
                if not content:
                    return None
                return render_ean13(content,
                                    scale=int(self.code_scale.get_value()),
                                    width=width)
        except SystemExit as e:                            # qrcode-Paket fehlt
            self.show_error(str(e))
        except Exception as e:                             # noqa: BLE001
            self.show_error(t("Error while rendering: {err}").format(err=e))
        return None

    # ---------------------------------------------------------- preview
    def compose_preview_image(self, canvas, fs=None):
        g = self.geometry()
        h = canvas.height
        W = canvas.width
        pl, pr = g["paper_left"], g["paper_right"]
        pv_main, pv_soft, pv_text = PV_ACCENTS.get(self.theme,
                                                  PV_ACCENTS["dark"])

        # ---- raster area (the actual print image) ---------------------------
        raster = Image.new("RGB", (W, h), (208, 210, 216))
        d = ImageDraw.Draw(raster)
        for x in range(-h, W + h, 14):
            d.line([(x, 0), (x + h, h)], fill=(196, 198, 206))

        # highlight the printable area (the paper) in a light colour
        d.rectangle([pl, 0, pr - 1, h - 1], fill=(255, 255, 255))

        # 10 mm raster lines
        step = max(4, int(round(10 * g["ppm"])))
        x = pl + step
        while x < pr - 2:
            d.line([(x, 0), (x, h - 1)], fill=(232, 234, 240))
            x += step

        # centre line of the paper (accent colour, dashed)
        cx = (pl + pr) // 2
        for yy in range(0, h, 14):
            d.line([(cx, yy), (cx, min(h - 1, yy + 7))], fill=pv_soft)

        # content: RED outside the paper, BLACK inside
        mask = canvas.convert("L")
        inv = Image.eval(mask, lambda v: 255 - v)        # 255 = content
        outside = inv.copy()
        ImageDraw.Draw(outside).rectangle([pl, 0, pr - 1, h - 1], fill=0)
        inside = inv.copy()
        di = ImageDraw.Draw(inside)
        if pl > 0:
            di.rectangle([0, 0, pl - 1, h - 1], fill=0)
        if pr < W:
            di.rectangle([pr, 0, W - 1, h - 1], fill=0)
        raster.paste((255, 140, 140), (0, 0), outside)
        raster.paste((0, 0, 0), (0, 0), inside)

        # strong border around the printable area
        d.rectangle([pl, 0, pr - 1, h - 1], outline=pv_main, width=2)

        # handles: corners = scale diagonally · edge centres = change the width
        if self._pl_cw:
            if fs is None:                          # without a factor: old approximation
                fs = (min(560, W) / W) if W else 1.0
            r = max(6, min(60, int(round(8 / (fs or 1)))))    # visible after downscaling
            hy = h // 2
            spots = [(self._pl_left, hy),
                     (self._pl_left + self._pl_cw, hy),
                     (self._pl_left, 0),
                     (self._pl_left + self._pl_cw, 0),
                     (self._pl_left, h - 1),
                     (self._pl_left + self._pl_cw, h - 1)]
            for hx, hyy in spots:
                y1, y2 = max(0, hyy - r), min(h - 1, hyy + r)
                d.rectangle([hx - r, y1, hx + r, y2], fill=(255, 255, 255))
                d.rectangle([hx - r + 2, y1 + 2, hx + r - 2, y2 - 2],
                            fill=pv_main)

        # ---- labels in their own margin (they cover nothing) ---------------
        gx, gt, gb = PV_GX, PV_GT, PV_GB
        disp = Image.new("RGB", (W + 2 * gx, h + gt + gb),
                         PV_MARGIN.get(self.theme, PV_MARGIN["dark"]))
        disp.paste(raster, (gx, gt))
        dd = ImageDraw.Draw(disp)
        try:
            f = _load_font(16)                      # small labels (no CJK inside)
            cw_mm = self._pl_cw / g["ppm"]
            ch_mm = self._pl_h / g["ppm"]
            caption = t("Paper {p} mm · content {w} x {h} mm ({pct} %)").format(
                p=f"{float(self.paper_mm.get_value()):g}",
                w=f"{cw_mm:.1f}", h=f"{ch_mm:.1f}",
                pct=int(self.scale_spin.get_value()))
            # the caption and the "no paper" note are translated - in Japanese or
            # Chinese they need a CJK font, otherwise they appear as boxes
            dd.text((gx + 6, gt + h + 12), caption,
                    font=load_font_for_text(caption, 16), fill=pv_text)
            no_paper = t("no paper")
            f_np = load_font_for_text(no_paper, 16)
            if pl > 110:
                dd.text((gx + max(4, (pl - 110) // 2), 6), no_paper,
                        font=f_np, fill=(125, 127, 135))
            if W - pr > 110:
                dd.text((gx + pr + max(4, (W - pr - 110) // 2), 6), no_paper,
                        font=f_np, fill=(125, 127, 135))
            # 10 mm scale bar at the right of the bottom margin (no clash with text)
            bar = int(round(10 * g["ppm"]))
            bx = gx + max(6, W - bar - 66)
            by = gt + h + 16
            dd.rectangle([bx, by, bx + bar, by + 5], fill=(60, 60, 60))
            dd.text((bx + bar + 8, by - 9), "10 mm", font=f, fill=(60, 60, 60))
        except Exception:                                    # noqa: BLE001
            pass
        return disp

    def update_preview(self):
        self.preview_timeout = None
        p = self.params()
        canvas, content, left = self.build_canvas()
        if canvas is None:
            self.preview_image.clear()
            self._pl_cw = 0
            self.pixbuf = None
            return False
        self._pl_left = left
        self._pl_cw = content.width
        self._pl_h = canvas.height
        # always fully visible: fit BOTH width and height into the area
        # (including the label margin of the preview)
        full_w = canvas.width + 2 * PV_GX
        full_h = canvas.height + PV_GT + PV_GB
        vw, vh = self._preview_viewport()
        fs = max(0.04, min(vw / full_w, vh / full_h, 1.0))
        disp = self.compose_preview_image(canvas, fs)
        target_w = max(1, int(round(full_w * fs)))
        if target_w != disp.width:
            disp = disp.resize(
                (target_w, max(1, round(disp.height * target_w / disp.width))),
                Image.Resampling.LANCZOS)
        self._disp_scale = target_w / full_w
        bio = BytesIO()
        disp.save(bio, format="PNG")
        loader = GdkPixbuf.PixbufLoader()
        loader.write(bio.getvalue())
        loader.close()
        self.pixbuf = loader.get_pixbuf()
        self.preview_image.set_from_pixbuf(self.pixbuf)
        g = self.geometry()
        mm_w = content.width / g["ppm"]
        mm_h = content.height / g["ppm"]
        pct = int(self.scale_spin.get_value())
        # always show the dimensions (also while dragging the corners)
        self.scale_label.set_text(f"{pct} %  ({mm_w:.1f} x {mm_h:.1f} mm)")
        self.scale_spin.set_tooltip_text(
            t("Current: {w} x {h} dots = {mw} x {mh} mm at {p} % · 100 % = fitted to the print window · easy with the mouse: drag the blue corners in the preview diagonally").format(
                  w=content.width, h=content.height, mw=f"{mm_w:.1f}",
                  mh=f"{mm_h:.1f}", p=pct))
        note = ""
        if left < g["paper_left"] or left + content.width > g["paper_right"]:
            note = t(" · extends beyond the paper (red)")
        self.set_status(
            t("Content {cw} x {ch} mm ({p} %) · paper {paper} mm · window [{l}..{r}] of {dots} · offset {off} mm · density {dens}{note}").format(
                  cw=f"{mm_w:.1f}", ch=f"{mm_h:.1f}", p=pct,
                  paper=f"{float(self.paper_mm.get_value()):g}",
                  l=left, r=left + content.width, dots=p['dots'],
                  off=f"{float(self.offset_mm.get_value()):+.1f}",
                  dens=f"{p['density']:#04x}", note=note))
        return False

    # --------------------------------------------------------------- print
    def _run_bg(self, running_text, fn, done_text):
        if self.busy:
            self.show_error(t("A job is already running."))
            return
        self.busy = True
        GLib.idle_add(self.print_button.set_sensitive, False)
        GLib.idle_add(self.set_status, running_text)

        def worker():
            t0 = time.time()
            try:
                fn()
                GLib.idle_add(self.set_status,
                              f"✓ {done_text} ({time.time() - t0:.1f} s)")
            except Exception as e:                          # noqa: BLE001
                GLib.idle_add(self.set_status, t("✗ Error: {err}").format(err=e))
                GLib.idle_add(self.show_error,
                              t("Failed: {err}").format(err=e))
            finally:
                self.busy = False
                GLib.idle_add(self.print_button.set_sensitive, True)

        threading.Thread(target=worker, daemon=True).start()

    def on_print(self, _btn):
        canvas, _content, _left = self.build_canvas()
        if canvas is None:
            self.show_error(t("Nothing to print (check your input)."))
            return
        p = self.params()
        self.save_config()

        def job():
            with x3print.make_printer(p["protocol"], mac=p["mac"],
                                      width=p["dots"], speed=p["speed"],
                                      density=p["density"], feed=p["feed"],
                                      mode=p["mode"]) as pr:
                pr.print_image(canvas)
        self._run_bg(t("Sending print job …"), job, t("Printed"))

    def on_status(self, _btn):
        def job():
            p = self.params()
            with x3print.make_printer(p["protocol"], mac=p["mac"],
                                      width=p["dots"]) as pr:
                fw = pr.firmware()
                st = pr.status()
            GLib.idle_add(self.log, f"Firmware: {fw.hex()}")
            GLib.idle_add(self.log, f"Status  : {st.hex()}")
        self._run_bg(t("Checking connection …"), job, t("Connection OK"))

    # ------------------------------------------------------------ Werkzeuge
    def _tool_args(self):
        p = self.params()
        return SimpleNamespace(mac=p["mac"], dots=p["dots"], speed=p["speed"],
                               density=p["density"], feed=p["feed"],
                               protocol=p["protocol"],
                               mode=p["mode"], delay=None, width=p["width"],
                               left=p["left"], out=None)

    def on_tool_demo(self, _btn):
        args = self._tool_args()
        self._run_bg(t("Sending test page …"),
                     lambda: x3print.cmd_demo(args), t("Test page printed"))

    def on_tool_calibrate(self, _btn):
        args = self._tool_args()
        self._run_bg(t("Sending calibration …"),
                     lambda: x3print.cmd_calibrate(args), t("Calibration printed"))

    def on_tool_feed(self, _btn):
        p = self.params()

        def job():
            with x3print.make_printer(p["protocol"], mac=p["mac"],
                                      width=p["dots"], feed=p["feed"],
                                      mode=p["mode"]) as pr:
                pr.feed(p["feed"])
        self._run_bg(t("Feeding …"), job, t("Paper advanced"))

    # --------------------------------------------------- printer selection
    def on_scan_devices(self, _btn=None):
        """Search for Bluetooth devices (in the background, takes a few seconds)."""
        self.set_status(t("Searching Bluetooth devices … (may take ~10 s)"))
        self.device_combo.set_sensitive(False)

        def work():
            try:
                devs = x3print.list_bluetooth_devices(scan=True, seconds=8)
            except Exception as e:                          # noqa: BLE001
                devs = []
                GLib.idle_add(self.show_error,
                              t("Search failed: {err}").format(err=e))
            GLib.idle_add(self.apply_devices, devs)

        threading.Thread(target=work, daemon=True).start()

    def apply_devices(self, devs):
        """Copy the devices that were found into the drop-down list."""
        cur = self.mac_entry.get_text().strip().upper()
        self.device_combo.remove_all()
        self.device_combo.append("auto", t("automatic (search for X3)"))
        for mac, name, paired in devs:
            self.device_combo.append(
                mac, f"{name or t("Unknown")} — {mac}"
                     + (t("  (paired)") if paired else ""))
        self.device_combo.append("manual", t("manual (MAC below)"))
        if cur and cur in [d[0] for d in devs]:
            self.device_combo.set_active_id(cur)
        else:
            self.device_combo.set_active_id("auto")
        self.device_combo.set_sensitive(True)
        n_paired = sum(1 for d in devs if d[2])
        self.set_status(t("{n} Bluetooth device(s) found ({p} paired).")
                        .format(n=len(devs), p=n_paired))
        return False

    def on_device_selected(self, *_a):
        """Pick a device from the list -> adopt its MAC address."""
        did = self.device_combo.get_active_id() or "auto"
        if did not in ("auto", "manual"):
            self.mac_entry.set_text(did)
            self.mac_entry.set_sensitive(False)
        else:
            self.mac_entry.set_sensitive(did == "manual")
            if did == "auto" and not self.mac_entry.get_text().strip():
                self.mac_entry.set_text(x3print.detect_mac())

    def on_protocol_changed(self, *_a):
        """Disable the X3 specific controls for ESC/POS."""
        is_x3 = (self.proto_combo.get_active_id() or "x3") == "x3"
        for w in (self.density_check, self.density_spin, self.speed_entry,
                  self.mode_combo):
            w.set_sensitive(is_x3)
        self.proto_combo.set_tooltip_text(
            t("X3 = Snap & Tag printer (YK protocol)")
            if is_x3 else
            t("ESC/POS = standard thermal printers (58/80 mm); density, speed and print mode apply to X3 only"))
        self.queue_preview()

    def on_model_changed(self, *_a):
        """Model template: set protocol + paper size + raster width."""
        m = x3print.MODELS.get(self.model_combo.get_active_id() or "x3")
        if not m:
            return
        label, proto, dots, paper, margin = m
        self.proto_combo.set_active_id(proto)
        self.dots_spin.set_value(dots)
        self.paper_mm.set_value(paper)
        self.margin_mm.set_value(margin)
        self.set_status(t("Preset \"{label}\": {dots} dots, {paper} mm paper.")
                        .format(label=label, dots=dots, paper=f"{paper:g}"))
        self.on_protocol_changed()

    def on_test_printer(self, _btn):
        """Test the connection to the selected printer."""
        p = self.params()

        def job():
            with x3print.make_printer(p["protocol"], mac=p["mac"],
                                      width=p["dots"], feed=1) as pr:
                GLib.idle_add(self.log, t("Connected to {mac} ({proto})")
                              .format(mac=pr.mac, proto=p["protocol"]))
                st = pr.status()
                GLib.idle_add(self.log, t("Status: ")
                              + (st.hex() if st else t("no response")))
        self._run_bg(t("Checking printer …"), job, t("Printer checked"))

    def on_read_device(self, _btn):
        def job():
            p = self.params()
            with x3print.make_printer(p["protocol"], mac=p["mac"],
                                      width=p["dots"]) as pr:
                fw = pr.firmware()
                st = pr.status()
            GLib.idle_add(self.log, f"→ Firmware: {fw.hex()}")
            GLib.idle_add(self.log, f"→ Status  : {st.hex()}")
        self._run_bg(t("Reading firmware/status …"), job, t("Device answer received"))

    def on_send_raw(self, _btn):
        text = (self.raw_entry.get_text().strip()
                .replace(",", " ").replace("0x", ""))
        try:
            data = bytes.fromhex(text.replace(" ", ""))
        except ValueError as e:
            self.show_error(t("Invalid hex value: {err}").format(err=e))
            return
        p = self.params()

        def job():
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                              socket.BTPROTO_RFCOMM)
            s.settimeout(6)
            s.connect((p["mac"], 1))
            s.sendall(data)
            time.sleep(0.35)
            resp = b""
            s.settimeout(0.4)
            try:
                while True:
                    chunk = s.recv(256)
                    if not chunk:
                        break
                    resp += chunk
            except socket.timeout:
                pass
            s.close()
            GLib.idle_add(self.log, f"TX: {data.hex()}")
            GLib.idle_add(self.log,
                          f"RX: {resp.hex() if resp else t('no response')}")
        self._run_bg(t("Sending raw command …"), job, t("Raw command sent"))

    # ------------------------------------------------------------------ dialogs
    def show_info(self, title, text):
        """Info dialog (for example the Markdown help)."""
        dlg = Gtk.MessageDialog(parent=self.win, flags=0,
                                message_type=Gtk.MessageType.INFO,
                                buttons=Gtk.ButtonsType.CLOSE, text=title)
        dlg.set_property("secondary-use-markup", False)
        dlg.format_secondary_text(str(text))
        dlg.run()
        dlg.destroy()

    def show_error(self, text):
        dlg = Gtk.MessageDialog(parent=self.win, flags=0,
                                message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.OK,
                                text=t("X3 printer"))
        dlg.format_secondary_text(str(text))
        dlg.run()
        dlg.destroy()


def main():
    GLib.set_prgname("x3gui")
    if "--version" in sys.argv:
        print(f"X3 Thermal Printer {VERSION}")
        return 0
    if "--selftest" in sys.argv:
        app = X3App()                                       # noqa: F841
        app.update_preview()
        GLib.timeout_add(1500, Gtk.main_quit)
        Gtk.main()
        print("Self-test OK - UI built, preview rendered.")
        return 0
    app = X3App()                                           # noqa: F841
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
