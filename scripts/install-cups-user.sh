#!/usr/bin/env bash
# ============================================================================
# Install the X3 as the CUPS printer "X3Thermo" - WITHOUT sudo.
#
# Requirements:
#   - the user is in the group "lpadmin" (check: groups | grep lpadmin)
#   - CUPS is running
# A queue on socket://127.0.0.1:9101 is created and a systemd USER service
# (x3bridge) is started which forwards the jobs to the Bluetooth printer.
#
# Usage:     ./install-cups-user.sh [MAC]      (no MAC = search automatically)
# Remove:    ./install-cups-user.sh --uninstall
# ============================================================================
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPTS")"
PRINTER="X3Thermo"
MAC="${1:-}"
PORT=9101
SERVICE_DIR="$HOME/.config/systemd/user"

if [[ "${1:-}" == "--uninstall" ]]; then
    echo "==> Stopping/removing the service ..."
    systemctl --user disable --now x3bridge.service 2>/dev/null || true
    rm -f "$SERVICE_DIR/x3bridge.service"
    systemctl --user daemon-reload
    echo "==> Removing the printer $PRINTER ..."
    lpadmin -x "$PRINTER" 2>/dev/null || true
    echo "Done."
    exit 0
fi

if [[ -n "$MAC" ]]; then
    MAC="$(echo "$MAC" | tr '[:lower:]' '[:upper:]')"
    ENV_LINE="Environment=X3_MAC=$MAC"
    echo "==> Using MAC $MAC"
else
    ENV_LINE="# X3_MAC not set - the printer is searched automatically"
    echo "==> No MAC given - the printer will be searched automatically"
fi

echo "==> Installing the systemd user service ..."
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_DIR/x3bridge.service" <<EOF
[Unit]
Description=X3 Thermo Bluetooth bridge (CUPS -> Snap & Tag X3)
After=bluetooth.target

[Service]
ExecStart=/usr/bin/python3 "$ROOT/cups/x3bridge.py"
$ENV_LINE
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now x3bridge.service
sleep 1
systemctl --user --no-pager status x3bridge.service | head -12

echo "==> Creating the CUPS printer '$PRINTER' (socket://127.0.0.1:$PORT) ..."
lpadmin -p "$PRINTER" -E \
        -v "socket://127.0.0.1:$PORT" \
        -D "X3 Thermo (Snap & Tag Bluetooth)" \
        -L "Bluetooth" \
        -o printer-is-shared=false
cupsenable "$PRINTER" || true
cupsaccept "$PRINTER" || true

echo
lpstat -p "$PRINTER" -l 2>/dev/null | head -8
echo
echo "Done! The printer '$PRINTER' can now be picked in every print dialog."
echo "Test:  echo 'Hello X3' | lp -d $PRINTER"
