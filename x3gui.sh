#!/bin/bash
# Start wrapper for x3gui.py
# Removes the VS Code snap environment (GTK/GIO) so the system GTK3 is
# loaded - otherwise symbol errors (libpthread from the snap) or wrong
# icon/module paths occur.  Same pattern as LightBurn/Nextcloud.
unset GTK_PATH GIO_MODULE_DIR GSETTINGS_SCHEMA_DIR GTK_EXE_PREFIX \
      GTK_IM_MODULE_FILE LOCPATH
unset GTK_PATH_VSCODE_SNAP_ORIG GIO_MODULE_DIR_VSCODE_SNAP_ORIG \
      GSETTINGS_SCHEMA_DIR_VSCODE_SNAP_ORIG GTK_EXE_PREFIX_VSCODE_SNAP_ORIG \
      GTK_IM_MODULE_FILE_VSCODE_SNAP_ORIG LOCPATH_VSCODE_SNAP_ORIG

exec python3 "$(dirname "$(readlink -f "$0")")/x3gui.py" "$@"
