#!/usr/bin/env bash
# ============================================================================
# X3 Thermal Printer - one-stop installer
#
# Checks the dependencies, installs the application menu entry and a desktop
# shortcut, pairs the printer and (optionally) sets up the CUPS queue
# "X3Thermo".  Everything is done WITHOUT sudo - the script only prints the
# system packages that are missing.
#
# Usage:
#   ./install.sh                 # interactive installation
#   ./install.sh --check         # only check the dependencies
#   ./install.sh --yes           # no questions, install everything possible
#   ./install.sh --no-cups       # skip the CUPS queue
#   ./install.sh --no-pair       # skip Bluetooth pairing
#   ./install.sh --no-shortcut   # menu entry only, no desktop shortcut
#   ./install.sh --uninstall     # remove menu entry, shortcut and CUPS queue
#   ./install.sh --help
# ============================================================================
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPTS")"
PRINTER="X3Thermo"

# localized messages (English, German, Japanese, Chinese) - see scripts/msg.sh
# shellcheck source=msg.sh
source "$SCRIPTS/msg.sh"

DO_CHECKS=1
CHECK_ONLY=0
DO_DESKTOP=1
DO_SHORTCUT=1
DO_PAIR=1
DO_CUPS=1
DO_SELFTEST=1
ASSUME_YES=0
UNINSTALL=0

# ----------------------------------------------------------------- helpers
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
warn()  { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m%s\033[0m %s\n' "$(x3_missing_label)" "$*"; }
step()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

ask() {                       # ask "question" [default=J]
    local q="$1" def="${2:-j}" ans
    if [[ "$ASSUME_YES" == 1 ]]; then echo "   $q -> yes (--yes)"; return 0; fi
    [[ -t 0 ]] || { echo "   $q -> no (no terminal, use --yes)"; return 1; }
    read -r -p "   $q [$(x3_yesno_labels)] " ans || ans=""
    [[ "${ans,,}" == "j" || "${ans,,}" == "y" || -z "$ans" && "$def" == "j" ]]
}

have() { command -v "$1" >/dev/null 2>&1; }

usage() { sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

MISSING=()

# ----------------------------------------------------------------- arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)      CHECK_ONLY=1; DO_DESKTOP=0; DO_PAIR=0; DO_CUPS=0; DO_SELFTEST=0 ;;
        --yes|-y)     ASSUME_YES=1 ;;
        --no-cups)    DO_CUPS=0 ;;
        --no-pair)    DO_PAIR=0 ;;
        --no-desktop) DO_DESKTOP=0 ;;
        --no-shortcut) DO_SHORTCUT=0 ;;
        --uninstall)  UNINSTALL=1 ;;
        -h|--help)    usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
    shift
done

# ------------------------------------------------------------- uninstalling
if [[ "$UNINSTALL" == 1 ]]; then
    step "Removing the application menu entry"
    "$SCRIPTS/install-desktop.sh" --uninstall || true
    step "Removing the CUPS queue and the bridge service"
    "$SCRIPTS/install-cups-user.sh" --uninstall || true
    echo
    bold "Done. The program files in $ROOT were kept."
    exit 0
fi

bold "X3 Thermal Printer - installer"
echo "Project folder: $ROOT"

# --------------------------------------------------------------- 1) checks
step "1/5  Checking the dependencies"

# Base system
if have python3; then
    PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    PYOK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')"
    if [[ "$PYOK" == 1 ]]; then ok "python3 $PYV"
    else fail "python3 $PYV (3.9 or newer required)"; MISSING+=("python3"); fi
else
    fail "python3 not found"; MISSING+=("python3")
fi

# GTK3 bindings (GUI)
if python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    ok "GTK3 bindings (python3-gi)"
else
    fail "python3-gi / GTK3 - needed for the desktop app (x3gui.py)"; MISSING+=("python3-gi gir1.2-gtk-3.0")
fi

# Pillow (CLI + GUI)
if python3 -c 'import PIL; print(PIL.__version__)' >/dev/null 2>&1; then
    ok "Pillow $(python3 -c 'import PIL; print(PIL.__version__)')"
else
    fail "Pillow (python3-pil) - needed for rendering"; MISSING+=("python3-pil")
fi

# Bluetooth
if have bluetoothctl; then
    ok "BlueZ (bluetoothctl)"
    if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
        ok "Bluetooth adapter is powered on"
    else
        warn "Bluetooth adapter seems to be off (try: bluetoothctl power on)"
    fi
else
    fail "bluez (bluetoothctl) - needed to talk to the printer"; MISSING+=("bluez bluez-tools")
fi

# Optional packages
if python3 -c 'import qrcode' >/dev/null 2>&1; then
    ok "qrcode (QR codes)"
else
    warn "qrcode not installed - only QR codes are unavailable:"
    echo "         pip3 install --user --break-system-packages qrcode"
fi
if have gs; then
    ok "ghostscript (printing PDF/PostScript through CUPS)"
else
    warn "ghostscript not installed - PDF/PS jobs via CUPS will not work"
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    step "Missing system packages - please install them once (needs sudo):"
    if [[ -r /etc/os-release ]] && grep -qiE '^(ID|ID_LIKE)=.*(debian|ubuntu)' /etc/os-release; then
        echo "  sudo apt install ${MISSING[*]}"
    elif [[ -r /etc/os-release ]] && grep -qiE '^(ID|ID_LIKE)=.*fedora' /etc/os-release; then
        echo "  sudo dnf install python3-pillow python3-gobject gtk3 bluez"
    else
        echo "  install: ${MISSING[*]}"
    fi
    echo "  Then run this installer again."
    [[ "$DO_SELFTEST" == 0 ]] && exit 1        # --check mode: report and stop
fi

# ------------------------------------------------------- 2) project files
step "2/5  Preparing the project files"
for f in x3gui.sh pair.sh install-desktop.sh install-cups-user.sh install-cups.sh; do
    if [[ -f "$SCRIPTS/$f" ]]; then chmod +x "$SCRIPTS/$f"; ok "scripts/$f is executable"
    else warn "$f is missing"; fi
done

# ---------------------------------------------------------- 3) self-test
if [[ "$DO_SELFTEST" == 1 ]]; then
    step "3/5  Self-test (no printer needed)"
    if python3 "$ROOT/tests/test_smoke.py" >/tmp/x3-install-test.log 2>&1; then
        ok "rendering, frames and ESC/POS checks passed"
    else
        warn "self-test reported problems - see /tmp/x3-install-test.log"
        tail -5 /tmp/x3-install-test.log | sed 's/^/         /'
    fi
else
    step "3/5  Self-test skipped (--check)"
fi

# ------------------------------------------------------------- 4) desktop
step "4/5  Application menu entry and desktop shortcut"
if [[ "$CHECK_ONLY" == 1 ]]; then
    warn "check only (--check) - nothing was changed"
elif [[ "$DO_DESKTOP" == 1 ]]; then
    if [[ "$DO_SHORTCUT" == 1 ]]; then
        if "$SCRIPTS/install-desktop.sh" >/dev/null 2>&1; then
            ok "menu entry \"X3 Thermal Printer\" installed"
            ok "desktop shortcut created"
        else
            warn "menu entry/shortcut could not be installed (see ./install-desktop.sh)"
        fi
    elif "$SCRIPTS/install-desktop.sh" --no-desktop-shortcut >/dev/null 2>&1; then
        ok "menu entry \"X3 Thermal Printer\" installed (no shortcut, --no-shortcut)"
    else
        warn "menu entry could not be installed (see ./install-desktop.sh)"
    fi
else
    warn "skipped (--no-desktop)"
fi

# --------------------------------------------------------- 5) printer setup
step "5/5  Printer (Bluetooth + CUPS)"
PAIRED=0
if [[ "$CHECK_ONLY" == 1 ]]; then
    warn "check only (--check) - nothing was changed"
elif [[ "$DO_PAIR" == 1 ]]; then
    if python3 -c "import sys; sys.path.insert(0,'$ROOT'); import x3print; sys.exit(0 if x3print.detect_mac() else 1)" 2>/dev/null; then
        ok "printer found: $(python3 -c "import sys; sys.path.insert(0,'$ROOT'); import x3print; print(x3print.detect_mac())")"
        PAIRED=1
    elif ask "No printer paired yet - pair it now (./pair.sh)?"; then
        "$SCRIPTS/pair.sh" || warn "pairing did not finish"
        PAIRED=1
    fi
else
    warn "pairing skipped (--no-pair)"
fi

if [[ "$DO_CUPS" == 1 && "$PAIRED" == 1 ]]; then
    if lpstat -p "$PRINTER" >/dev/null 2>&1; then
        ok "CUPS queue \"$PRINTER\" already exists"
    elif ask "Set up the CUPS queue \"$PRINTER\" (printing from any program)?"; then
        "$SCRIPTS/install-cups-user.sh" || warn "CUPS setup did not finish"
    fi
elif [[ "$DO_CUPS" == 1 ]]; then
    warn "CUPS queue skipped (printer not paired yet)"
fi

# ------------------------------------------------------------------ result
step "$(x3_ready_title)"
x3_summary "$ROOT" "$SCRIPTS"
if [[ "$CHECK_ONLY" == 1 ]]; then
    echo "  $(x3_check_note)"
fi
