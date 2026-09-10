#!/usr/bin/env bash
# X3 thermal printer - install the application menu entry, the icon and
# (by default) a launcher on the desktop.
#
#   ./install-desktop.sh                      # menu entry + desktop shortcut
#   ./install-desktop.sh --no-desktop-shortcut  # menu entry only
#   ./install-desktop.sh --desktop            # same as default (kept for compat)
#   ./install-desktop.sh --uninstall          # remove both again
#
# Substitutes the placeholders @EXEC@ (path to x3gui.sh) and @ICON@
# (icon name) in the template x3drucker.desktop.
# No sudo needed - everything goes below ~/.local/share/ and the desktop.
set -euo pipefail

SCRIPTS="$(dirname "$(readlink -f "$0")")"
ROOT="$(dirname "$SCRIPTS")"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"
ENTRY="$APPS/x3drucker.desktop"
WANT_DESKTOP=1

uninstall() {
    rm -f "$ENTRY" "$ICONS/x3drucker.png"
    rm -f "$(desktop_dir)"/x3drucker.desktop 2>/dev/null || true
    command -v update-desktop-database >/dev/null && \
        update-desktop-database "$APPS" >/dev/null 2>&1 || true
    echo "==> Menu entry and desktop shortcut removed."
}

desktop_dir() {
    local d
    d="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    if [[ -n "$d" && -d "$d" ]]; then echo "$d"; return; fi
    for d in "$HOME/Desktop" "$HOME/Schreibtisch"; do
        [[ -d "$d" ]] && { echo "$d"; return; }
    done
    echo ""
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

[[ "${1:-}" == "--no-desktop-shortcut" ]] && WANT_DESKTOP=0

command -v python3 >/dev/null || { echo "python3 is missing."; exit 1; }
[[ -x "$ROOT/x3gui.sh" ]] || { echo "$ROOT/x3gui.sh is missing or not executable."; exit 1; }

mkdir -p "$APPS" "$ICONS"

install -m 644 "$ROOT/assets/x3drucker.png" "$ICONS/x3drucker.png"
sed -e "s|@EXEC@|$ROOT/x3gui.sh|" \
    -e "s|@ICON@|x3drucker|" \
    "$SCRIPTS/x3drucker.desktop" > "$ENTRY"
chmod 644 "$ENTRY"

command -v update-desktop-database >/dev/null && \
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

if [[ "$WANT_DESKTOP" == 1 ]]; then
    TARGET="$(desktop_dir)"
    if [[ -n "$TARGET" ]]; then
        cp "$ENTRY" "$TARGET/x3drucker.desktop"
        chmod +x "$TARGET/x3drucker.desktop"
        echo "==> Desktop shortcut created: $TARGET/x3drucker.desktop"
    else
        echo "==> No desktop folder found - only the menu entry was installed."
    fi
fi

echo "==> Done: \"X3 Thermal Printer\" is now in the application menu."
echo "    Start from a terminal as well:  $ROOT/x3gui.sh"
echo "    Remove:                         $SCRIPTS/install-desktop.sh --uninstall"
