#!/usr/bin/env bash
# Pair the Snap & Tag thermal printer "X3" over Bluetooth (if needed).
# Legacy pairing -> the PIN is usually "0000".
#
# Usage:
#   ./pair.sh                      # find the MAC automatically (bluetoothctl)
#   ./pair.sh AA:BB:CC:DD:EE:FF    # pass the MAC directly
set -euo pipefail

SCRIPTS="$(dirname "$(readlink -f "$0")")"
ROOT="$(dirname "$SCRIPTS")"
MAC="${1:-}"
if [[ -z "$MAC" ]]; then
    MAC="$(python3 -c "import sys; sys.path.insert(0, '$ROOT'); import x3print; print(x3print.detect_mac())")"
fi
if [[ -z "$MAC" ]]; then
    echo "No X3 found. Pair it first:"
    echo "  bluetoothctl scan on     # read the MAC of the printer 'X3'"
    echo "  ./pair.sh AA:BB:CC:DD:EE:FF"
    exit 1
fi
MAC=$(echo "$MAC" | tr '[:lower:]' '[:upper:]')

echo "==> Checking the Bluetooth controller ..."
bluetoothctl power on >/dev/null

if bluetoothctl info "$MAC" | grep -q "Paired: yes"; then
    echo "==> $MAC is already paired."
else
    echo "==> Pairing $MAC (confirm the PIN '0000' if asked) ..."
    bluetoothctl agent on >/dev/null
    bluetoothctl pair "$MAC"
fi

echo "==> Marking as trusted ..."
bluetoothctl trust "$MAC" >/dev/null

echo "==> Testing the connection ..."
bluetoothctl connect "$MAC" || true

echo
echo "Done. Now test it:"
echo "  python3 x3print.py status"
echo "  python3 x3print.py text 'Hallo vom PC'"
