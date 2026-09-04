#!/usr/bin/env bash
# Build a Debian package of Motionwall: dist/motionwall_<version>_all.deb
# Needs only dpkg-deb (part of dpkg) and python3; no root, no debhelper.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID=motionwall@motionwall
VERSION="$(PYTHONPATH="$HERE" python3 -c 'from motionwall import __version__; print(__version__)')"
NAME="${DEBFULLNAME:-$(git -C "$HERE" config user.name 2>/dev/null || echo Motionwall)}"
EMAIL="${DEBEMAIL:-$(git -C "$HERE" config user.email 2>/dev/null || echo motionwall@localhost)}"
OUT="${1:-$HERE/dist}"
BUILD="$(mktemp -d)"; trap 'rm -rf "$BUILD"' EXIT
PKG="$BUILD/motionwall"
log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

log "staging motionwall $VERSION"
install -d "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/share/motionwall" \
  "$PKG/usr/share/applications" "$PKG/usr/share/dbus-1/services" \
  "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/usr/share/metainfo" \
  "$PKG/usr/share/gnome-shell/extensions/$UUID" "$PKG/usr/share/doc/motionwall"

# the app
cp -r "$HERE/motionwall" "$HERE/bin" "$HERE/scripts" "$PKG/usr/share/motionwall/"
find "$PKG/usr/share/motionwall" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -f "$PKG/usr/share/motionwall/scripts/build-deb.sh"
cat > "$PKG/usr/bin/motionwall" <<'EOL'
#!/bin/sh
exec python3 /usr/share/motionwall/bin/motionwall "$@"
EOL

# desktop integration
sed 's|@LAUNCHER@|/usr/bin/motionwall|g' "$HERE/data/org.motionwall.Motionwall.desktop" \
  > "$PKG/usr/share/applications/org.motionwall.Motionwall.desktop"
sed 's|@LAUNCHER@|/usr/bin/motionwall|g' "$HERE/data/org.motionwall.Daemon.service" \
  > "$PKG/usr/share/dbus-1/services/org.motionwall.Daemon.service"
cp "$HERE/data/org.motionwall.Motionwall.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/"
cp "$HERE/data/org.motionwall.Motionwall.metainfo.xml" "$PKG/usr/share/metainfo/"

# GNOME Shell extension (system-wide; enabled per user on first run of the app)
cp "$HERE/extension/metadata.json" "$HERE"/extension/*.js "$PKG/usr/share/gnome-shell/extensions/$UUID/"

# docs
if [ -f "$HERE/LICENSE" ]; then
  LICENSE_TEXT="$(sed 's/^/ /' "$HERE/LICENSE")"
  LICENSE_NAME="see below"
else
  echo "warning: no LICENSE file in the source tree; the package's copyright file records that" >&2
  LICENSE_NAME="not specified"
  LICENSE_TEXT=" The source tree carries no LICENSE file. Add one to the repository to
 grant redistribution rights; until then all rights are reserved by the
 copyright holder."
fi
cat > "$PKG/usr/share/doc/motionwall/copyright" <<EOL
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: motionwall

Files: *
Copyright: $(date +%Y) $NAME
License: $LICENSE_NAME
$LICENSE_TEXT
EOL
{
  echo "motionwall ($VERSION) unstable; urgency=medium"; echo
  echo "  * See the project README for what changed."; echo
  echo " -- $NAME <$EMAIL>  $(date -R)"
} | gzip -9n > "$PKG/usr/share/doc/motionwall/changelog.Debian.gz"

# permissions: dirs 755, files 644, executables 755
find "$PKG" -type d -exec chmod 755 {} +
find "$PKG" -type f -exec chmod 644 {} +
chmod 755 "$PKG/usr/bin/motionwall" "$PKG/usr/share/motionwall/bin/motionwall" \
  "$PKG"/usr/share/motionwall/scripts/*.sh "$PKG"/usr/share/motionwall/scripts/*.py

SIZE="$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)"
sed -e "s|@VERSION@|$VERSION|" -e "s|@MAINTAINER@|$NAME <$EMAIL>|" -e "s|@SIZE@|$SIZE|" \
  "$HERE/packaging/debian/control.in" > "$PKG/DEBIAN/control"

mkdir -p "$OUT"
DEB="$OUT/motionwall_${VERSION}_all.deb"
log "building $DEB"
dpkg-deb --root-owner-group --build "$PKG" "$DEB" >/dev/null
log "done: $DEB"
echo "install with:  sudo apt install $DEB"
