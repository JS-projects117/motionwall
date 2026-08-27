#!/usr/bin/env bash
# Package and install the Motionwall GNOME Shell extension for the current user.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID=motionwall@motionwall
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
( cd "$HERE/extension" && zip -q -r "$TMP/$UUID.zip" metadata.json *.js )
gnome-extensions install --force "$TMP/$UUID.zip"
EXT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"
[ -f "$EXT_DIR/extension.js" ] || { echo "extension files not found in $EXT_DIR"; exit 1; }
gnome-extensions enable "$UUID" >/dev/null 2>&1 || true
# also record it in the enabled list so it starts on the next login even if the
# running shell has not indexed it yet
python3 "$HERE/scripts/enabled-extensions.py" add "$UUID"
state="$( (gnome-extensions info "$UUID" 2>/dev/null || true) | sed -n 's/^ *State: //p')"
echo "extension $UUID installed in $EXT_DIR (state: ${state:-not loaded yet})"
case "$state" in
  ACTIVE) echo "The extension is running: Motionwall will pause behind full-screen apps.";;
  *) echo "GNOME on Wayland only loads new extensions at login: log out and back in (it is already enabled).";;
esac
