# X3 Thermal Printer – technical documentation

This folder holds the **technical documentation** of the project. Everything here
is written in English. The end-user documentation sits next to the code in the
project root: [`../README.md`](../README.md) (English, main), 
[`../README.de.md`](../README.de.md), [`../README.ja.md`](../README.ja.md),
[`../README.zh.md`](../README.zh.md) and [`../INSTALL.md`](../INSTALL.md)
(step-by-step installation, German).

| I want to … | go to |
|---|---|
| install and print right away | [`../INSTALL.md`](../INSTALL.md), [`../README.md`](../README.md) |
| read the illustrated project article | [www.deadlinedriven.dev/blog/x3-thermal-printer](https://www.deadlinedriven.dev/blog/x3-thermal-printer) |
| understand how the program is built | this file |
| see the structure as a picture | [`diagrams/README.md`](diagrams/README.md) |
| see the user interface | [`images/gui-image-en.png`](images/gui-image-en.png) (image tab), [`images/gui-qr-en.png`](images/gui-qr-en.png) (barcode/QR tab) |
| change or verify something | `python3 ../tests/test_smoke.py` |

---

## 1. What the program does

The *Snap & Tag* printer **X3** (ORGBRO family) normally only works with the phone
app. This project drives it from a Linux PC in two ways:

1. **Command line** (`x3print.py`) – text, Markdown, images, layout, barcode, QR code.
2. **As a normal system printer** (`cups/`) – the queue `X3Thermo` appears in every
   print dialog (LibreOffice, browser, PDF viewer …).

Both ways use the same rendering code and the same protocol core.

## 2. Files and folders

```
x3print.py         library + command line (protocol, renderers, CLI)
x3gui.py           desktop program (GTK3), the full version
x3gui.sh           start wrapper (clears Snap environment variables)
i18n.py            translations: English source -> de / ja / zh
assets/            app icon and the example image shipped with the project
scripts/           installers, desktop entry template, Bluetooth pairing
cups/              CUPS bridge (variant A) and native driver (variant B)
docs/              this documentation, including the PlantUML diagrams
tests/             self-tests that need no printer
```

Where the program writes at runtime:

| Path | Purpose |
|---|---|
| `~/.config/x3drucker.json` | all GUI settings (theme, language, size, last values, `shortcut_created`) |
| `~/.local/share/applications/x3drucker.desktop` | menu entry (created by `scripts/install-desktop.sh` or the GUI) |
| `~/Desktop/x3drucker.desktop` | desktop shortcut (same source) |
| `~/.config/systemd/user/x3bridge.service` | user service for the CUPS bridge (variant A, no `sudo`) |

## 3. Protocol in short

* Transport: **Bluetooth SPP / RFCOMM, channel 1**, no root rights needed.
* Frame: `64 <cmd> <seq> <len_lo> <len_hi> <payload> 00 00 00 00 9b`.
* Print head: **864 dots @ 300 dpi** (108 bytes per row).
* Raster data: frames of **432 bytes = 4 rows** (108 bytes per row at 864 dots).
* Commands used: `0x80` token/init, `0x0a` speed, `0x09` density (4 bit, `0x02`…`0x0f`),
  `0x00` raster, `0x02` feed.
* Sending: the whole job leaves the PC as **one continuous stream** – no delays
  between blocks. A gap would let the printer's buffer run empty and stop the
  motor in the middle of a page; the RFCOMM flow control blocks the writes
  instead, so the pace always matches the printer.
* Before the connection is closed a status query (`0x10`) is put **behind** the
  job: its reply means the printer has read everything (fallback without a
  reply: a wait estimated from the job size).
* No ESC/POS: the "standard" path (`EscPosPrinter`, `GS v 0`) exists for other
  thermal printers and is used by `--protocol escpos`.

Details and the measured values: [`diagrams/13-hardware-facts.puml`](diagrams/13-hardware-facts.puml).

## 4. Command line (`x3print.py`)

```
status   text   file   image   ean13   qr   meme   markdown   feed   demo   calibrate
```

Common options: `--mac`, `--speed`, `--density`, `--brightness`, `--zoom`,
`--width`/`--left`, `--rotate`, `--flip`, `--stretch`, `--trim`, `--feed`,
`--mode app|fast` (both send without pauses; `app` = one raster frame per write,
`fast` = 16 KB blocks), `--protocol x3|escpos`, `--delay` (artificial delay, 0 = off),
`--out` (write a preview PNG instead of printing).

```bash
python3 x3print.py --version                  # version
python3 x3print.py status                     # connection + firmware
python3 x3print.py text 'Hello world'         # first page
python3 x3print.py image photo.jpg --dither   # image with dithering
python3 x3print.py qr --type wifi --ssid X --password Y
python3 x3print.py text Demo --out /tmp/x.png # preview only, no printer needed
```

## 5. Library (`x3print.py`)

| Group | Functions / classes |
|---|---|
| Drivers | `X3Printer` (YK/YZW), `EscPosPrinter`, `make_printer(protocol=…)`, `detect_mac()`, `list_bluetooth_devices()`, `make_frame()` |
| Content | `render_text()`, `render_markdown()`, `render_blocks()` (layout), `render_image_file()`, `render_ean13()`, `render_qrcode()`, `render_meme()`, `load_raster_image()`, `qr_payload()` |
| Helpers | `_load_font()`, `_wrap()`, `_brightness_density()`, `_embed()`, `_edit()` |

Every renderer returns a **PIL image in mode `1`** (1 bit, `0` = black dot) in the
content width; the CLI/GUI then embeds that image into the 864-dot raster.

## 6. User interface (`x3gui.py`)

* Tabs: **Text**, **Image**, **Barcode / QR**, **Settings** – controls on the left,
  live preview on the right (paper, ruler, handles in mm).
* Three themes (`dark`, `light`, `deadline`), four languages (`en`, `de`, `ja`, `zh`),
  UI zoom of the left column from 70 % to 140 %.
* First start: the example image `assets/sample.jpg`, the light theme and the
  system language are used, and the desktop shortcut is created once.
* Jobs run in a background thread (`_run_bg`) so the window stays responsive.

## 7. CUPS integration

| Variant | Files | Notes |
|---|---|---|
| A (active) | `cups/x3bridge.py`, `scripts/install-cups-user.sh` | user service, listens on `socket://127.0.0.1:9101`, works **without sudo** |
| B (alternative) | `cups/x3raster.py`, `cups/x3bt.py`, `cups/x3thermo.ppd`, `scripts/install-cups.sh` | native CUPS filter + backend, needs `sudo` once |

The bridge accepts PDF/PS (via Ghostscript at 300 dpi), images and plain text and
uses the same settings as the GUI.

## 8. Check the project without a printer

```bash
python3 tests/test_smoke.py     # renderers, frame format, ESC/POS, shipped image
./x3gui.sh --selftest           # builds the window and renders the preview
./scripts/install.sh --check    # dependencies and system state (changes nothing)
bash docs/diagrams/build.sh     # renders all diagrams and checks completeness
```

## 9. Contributing notes

* Code, comments, docstrings and CLI output are **English**; new user-visible
  strings go into `i18n.py` as an English key with `de`, `ja`, `zh` translations.
* Keep the protocol constants in one place (`x3print.py`, section *protocol*) –
  the GUI, the CLI and the CUPS bridge all use them.
* After changing a `.puml` file, run `docs/diagrams/build.sh` so that the rendered
  `.svg`/`.png` files stay in sync.
* `python3 -m py_compile x3print.py x3gui.py i18n.py cups/*.py` before committing.

## 10. License

MIT – see [`../LICENSE`](../LICENSE). Copyright (c) 2026 Marc Nauendorf.
