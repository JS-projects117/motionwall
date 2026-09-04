#!/usr/bin/env bash
# Remove Motionwall. --purge also deletes config, library, thumbnails and the local mpv runtime.
set -euo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"; CONF="${XDG_CONFIG_HOME:-$HOME/.config}"; CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"
LAUNCHER="$HOME/.local/bin/motionwall"
[ -x "$LAUNCHER" ] && "$LAUNCHER" quit-daemon >/dev/null 2>&1 || true
UUID=motionwall@motionwall
if command -v gnome-extensions >/dev/null; then
  gnome-extensions disable "$UUID" 2>/dev/null || true
  gnome-extensions uninstall "$UUID" 2>/dev/null || true
fi
rm -rf "$DATA/gnome-shell/extensions/$UUID"
[ -f "$DATA/motionwall/app/scripts/enabled-extensions.py" ] && python3 "$DATA/motionwall/app/scripts/enabled-extensions.py" remove "$UUID" 2>/dev/null || true
rm -rf "$DATA/motionwall/app"
rm -f "$LAUNCHER" "$DATA/applications/org.motionwall.Motionwall.desktop" \
      "$DATA/dbus-1/services/org.motionwall.Daemon.service" \
      "$DATA/icons/hicolor/scalable/apps/org.motionwall.Motionwall.svg" \
      "$DATA/metainfo/org.motionwall.Motionwall.metainfo.xml" \
      "$CONF/autostart/motionwall-daemon.desktop"
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$DATA/motionwall" "$CONF/motionwall" "$CACHE/motionwall"
  echo "removed Motionwall including configuration and runtime"
else
  rm -rf "$DATA/motionwall/runtime"      # the locally fetched mpv, if any
  rmdir "$DATA/motionwall" 2>/dev/null || true
  echo "removed Motionwall (config kept in $CONF/motionwall; use --purge to delete it and the thumbnails)"
fi
