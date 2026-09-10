#!/usr/bin/env bash
# ============================================================================
# Install the X3 thermal printer as the CUPS printer "X3Thermo" so it can be
# selected in every print dialog (LibreOffice, browsers, PDF viewers, ...).
#
# USAGE (needs sudo - backend/filter live in system directories):
#   sudo bash install-cups.sh [MAC]
#
# Examples:
#   sudo bash install-cups.sh                       # search the printer
#   sudo bash install-cups.sh AA:BB:CC:DD:EE:FF     # use this MAC
#
# Remove:
#   sudo bash install-cups.sh --uninstall
# ============================================================================
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPTS")"
PRINTER="X3Thermo"
MAC="${1:-}"
if [[ -z "$MAC" ]]; then
    MAC="$(python3 -c "import sys; sys.path.insert(0, '$ROOT'); import x3print; print(x3print.detect_mac())" || true)"
fi
if [[ -z "$MAC" ]]; then
    echo "No X3 found - pass the MAC address:  sudo bash install-cups.sh AA:BB:CC:DD:EE:FF"
    echo "(find it with:  bluetoothctl devices)"
    exit 1
fi
MAC="$(echo "$MAC" | tr '[:lower:]' '[:upper:]')"
echo "==> Using MAC $MAC"

if [[ "${1:-}" == "--uninstall" ]]; then
    echo "==> Removing the printer $PRINTER ..."
    lpadmin -x "$PRINTER" 2>/dev/null || true
    echo "==> Removing backend/filter ..."
    rm -f /usr/lib/cups/backend/x3bt /usr/libexec/cups/backend/x3bt
    rm -f /usr/lib/cups/filter/x3raster /usr/libexec/cups/filter/x3raster
    echo "Done."
    exit 0
fi

# --- locate the backend & filter directory -------------------------------
CP="/usr/lib/cups"
[[ -d "$CP/backend" ]] || CP="/usr/libexec/cups"
echo "==> CUPS directory: $CP"

# --- install backend + filter (must be owned by root) --------------------
echo "==> Installing backend  $CP/backend/x3bt"
install -o root -g root -m 0700 "$ROOT/cups/x3bt.py" "$CP/backend/x3bt"
echo "==> Installing filter   $CP/filter/x3raster"
install -o root -g root -m 0755 "$ROOT/cups/x3raster.py" "$CP/filter/x3raster"

# --- create the queue ----------------------------------------------------
echo "==> Creating the printer '$PRINTER' (URI x3bt://$MAC)"
lpadmin -p "$PRINTER" -E \
        -v "x3bt://$MAC" \
        -P "$ROOT/cups/x3thermo.ppd" \
        -D "X3 Thermo (Snap & Tag Bluetooth)" \
        -L "Bluetooth" \
        -o PageSize=X3Label \
        -o Resolution=300dpi

cupsenable "$PRINTER"
cupsaccept "$PRINTER"

echo
echo "Done! The printer '$PRINTER' can now be picked in every print dialog."
echo
echo "Test:"
echo "  echo 'Hello X3' | lp -d $PRINTER"
echo "  lp -d $PRINTER /path/to/file.pdf"
echo
echo "Error log in case of problems:"
echo "  grep -i x3 /var/log/cups/error_log | tail"
