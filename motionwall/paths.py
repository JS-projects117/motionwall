"""XDG paths used by Motionwall."""

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "motionwall"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "motionwall"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "motionwall"


def runtime_dir() -> Path:
    """Directory for sockets (private, tmpfs when XDG_RUNTIME_DIR exists)."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "motionwall"
    return cache_dir() / "run"


def config_file() -> Path:
    return config_dir() / "config.json"


def library_file() -> Path:
    return config_dir() / "library.json"


def thumbs_dir() -> Path:
    return cache_dir() / "thumbs"


def autostart_file() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "motionwall-daemon.desktop"


def local_mpv_runtime() -> Path:
    override = os.environ.get("MOTIONWALL_RUNTIME")
    return Path(override) if override else data_dir() / "runtime"
