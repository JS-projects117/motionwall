#!/usr/bin/env bash
# Install Motionwall for the current user (no root needed).
#   ./install.sh [--local-mpv] [--extension] [--autostart]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPDIR="$DATA/motionwall/app"
BIN="$HOME/.local/bin"
LAUNCHER="$BIN/motionwall"
WANT_LOCAL_MPV=0; WANT_EXT=0; WANT_AUTOSTART=0
for a in "$@"; do case "$a" in
  --local-mpv) WANT_LOCAL_MPV=1;; --extension) WANT_EXT=1;; --autostart) WANT_AUTOSTART=1;;
  -h|--help) sed -n 2,3p "$0"; exit 0;; *) echo "unknown option $a"; exit 2;; esac; done
log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*"; }

# ---- dependency check --------------------------------------------------------
MISSING=()
python3 -c 'import gi' 2>/dev/null || MISSING+=(python3-gi)
python3 -c 'import gi; gi.require_version("Gtk","4.0"); from gi.repository import Gtk' 2>/dev/null || MISSING+=(gir1.2-gtk-4.0)
python3 -c 'import gi; gi.require_version("Adw","1"); from gi.repository import Adw' 2>/dev/null || MISSING+=(gir1.2-adw-1)
python3 -c 'import Xlib' 2>/dev/null || MISSING+=(python3-xlib)
command -v ffmpeg >/dev/null || MISSING+=(ffmpeg)
[ "$WANT_EXT" = 1 ] && ! command -v zip >/dev/null && MISSING+=(zip)
HAVE_MPV=0
if command -v mpv >/dev/null || [ -x "$DATA/motionwall/runtime/usr/bin/mpv" ]; then HAVE_MPV=1; fi
if [ "$HAVE_MPV" = 0 ] && [ "$WANT_LOCAL_MPV" = 1 ]; then
  log "fetching mpv into a private runtime (no root)"; "$HERE/scripts/fetch-local-mpv.sh"; HAVE_MPV=1
fi
[ "$HAVE_MPV" = 1 ] || MISSING+=(mpv)
if [ "${#MISSING[@]}" -gt 0 ]; then
  warn "missing packages: ${MISSING[*]}"
  echo "    sudo apt install ${MISSING[*]}"
  if printf '%s\n' "${MISSING[@]}" | grep -qx mpv; then
    echo "    (or run ./install.sh --local-mpv to fetch mpv without root)"
  fi
fi

# ---- files ---------------------------------------------------------------------
log "installing to $APPDIR"
rm -rf "$APPDIR"; mkdir -p "$APPDIR" "$BIN"
cp -r "$HERE/motionwall" "$HERE/bin" "$HERE/scripts" "$HERE/data" "$HERE/extension" "$APPDIR/"
find "$APPDIR" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$LAUNCHER" <<EOL
#!/bin/sh
exec python3 "$APPDIR/bin/motionwall" "\$@"
EOL
chmod +x "$LAUNCHER"

mkdir -p "$DATA/applications" "$DATA/dbus-1/services" "$DATA/icons/hicolor/scalable/apps"
LAUNCHER_ESC="$(printf '%s' "$LAUNCHER" | sed 's/[&|\\]/\\&/g')"
sed "s|@LAUNCHER@|$LAUNCHER_ESC|g" "$HERE/data/org.motionwall.Motionwall.desktop" > "$DATA/applications/org.motionwall.Motionwall.desktop"
sed "s|@LAUNCHER@|$LAUNCHER_ESC|g" "$HERE/data/org.motionwall.Daemon.service" > "$DATA/dbus-1/services/org.motionwall.Daemon.service"
cp "$HERE/data/org.motionwall.Motionwall.svg" "$DATA/icons/hicolor/scalable/apps/"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true
# make dbus-daemon notice the new activation file
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true

if [ "$WANT_AUTOSTART" = 1 ]; then
  MOTIONWALL_LAUNCHER="$LAUNCHER" PYTHONPATH="$APPDIR" python3 -c "from motionwall import autostart, config; autostart.set_enabled(True); c=config.load(); c.autostart=True; config.save(c)"
  log "autostart enabled"
fi

# ---- optional GNOME Shell extension ---------------------------------------------------
if [ "$WANT_EXT" = 1 ]; then
  if command -v gnome-extensions >/dev/null; then
    "$HERE/scripts/install-extension.sh" || warn "extension install failed"
  else
    warn "gnome-extensions not found; skipping extension"
  fi
fi

# ---- renderer handoff ---------------------------------------------------------------
# On Wayland the GNOME Shell extension is the renderer; make sure the old X11 daemon
# (which cannot layer correctly under Wayland) is not left running or autostarted.
if [ "$WANT_EXT" = 1 ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  "$LAUNCHER" quit-daemon >/dev/null 2>&1 || true
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/motionwall-daemon.desktop"
  log "using the GNOME Shell extension as the renderer (log out and back in to load it)"
elif gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
     --method org.freedesktop.DBus.NameHasOwner org.motionwall.Daemon 2>/dev/null | grep -q true; then
  log "restarting the running X11 daemon"
  "$LAUNCHER" quit-daemon >/dev/null 2>&1 || true
  sleep 1
  "$LAUNCHER" resume >/dev/null 2>&1 || true
fi

case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH; log out and back in, or run $LAUNCHER";; esac
log "done. Launch 'Motionwall' from the app grid, or: motionwall set ~/Videos/loop.mp4"
