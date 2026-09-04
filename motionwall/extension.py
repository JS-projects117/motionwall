"""The bundled GNOME Shell extension: where it is installed and enabling it.

A system-wide install (the .deb) drops the extension into
/usr/share/gnome-shell/extensions, but GNOME only loads extensions listed in the
per-user `org.gnome.shell enabled-extensions` setting. The app adds the UUID
there the first time it runs, so the wallpaper appears after the next login
without the user touching gnome-extensions.
"""

import os
from pathlib import Path
from typing import List

EXTENSION_UUID = "motionwall@motionwall"
SHELL_SCHEMA = "org.gnome.shell"
KEY = "enabled-extensions"


def install_dirs() -> List[Path]:
    """Extension directories that would be searched by GNOME Shell, in priority order."""
    dirs = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")]
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs += [Path(d) for d in system.split(":") if d]
    return [d / "gnome-shell" / "extensions" / EXTENSION_UUID for d in dirs]


def is_installed() -> bool:
    return any((d / "metadata.json").is_file() for d in install_dirs())


def _settings():
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return None
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SHELL_SCHEMA, True) is None:
        return None       # not a GNOME session
    return Gio.Settings.new(SHELL_SCHEMA)


def is_enabled() -> bool:
    settings = _settings()
    return bool(settings) and EXTENSION_UUID in settings.get_strv(KEY)


def ensure_enabled() -> bool:
    """Enable the extension for this user if it is installed but not enabled.

    Returns True when it was just enabled, meaning a log-out/log-in is still
    needed before it renders (GNOME on Wayland loads extensions at login).
    """
    if not is_installed():
        return False
    settings = _settings()
    if settings is None:
        return False
    current = settings.get_strv(KEY)
    if EXTENSION_UUID in current:
        return False
    settings.set_strv(KEY, current + [EXTENSION_UUID])
    settings.sync()
    return True
