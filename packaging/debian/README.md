# Debian packaging

`scripts/build-deb.sh` turns the source tree into `dist/motionwall_<version>_all.deb`
using nothing but `dpkg-deb` (no debhelper, no root). The layout inside the package:

| Path | Contents |
| --- | --- |
| `/usr/share/motionwall/` | the Python app (`motionwall/`, `bin/`, `scripts/`) |
| `/usr/bin/motionwall` | launcher wrapper |
| `/usr/share/applications/org.motionwall.Motionwall.desktop` | app-grid entry, "Open With" for video files |
| `/usr/share/dbus-1/services/org.motionwall.Daemon.service` | D-Bus activation of the Xorg daemon |
| `/usr/share/gnome-shell/extensions/motionwall@motionwall/` | the GNOME Shell extension |
| `/usr/share/icons/hicolor/scalable/apps/` | app icon |
| `/usr/share/metainfo/` | AppStream data for software centres |

Desktop-database and icon-cache updates happen through dpkg file triggers, so
the package needs no maintainer scripts. The extension is enabled per user the
first time the app runs (`motionwall.extension.ensure_enabled`), and GNOME on
Wayland loads it at the next login.

`control.in` is the control-file template; `@VERSION@` comes from
`motionwall/__init__.py`, `@MAINTAINER@` from git (or `$DEBFULLNAME`/`$DEBEMAIL`).
