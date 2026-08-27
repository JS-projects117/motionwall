"""Locate the mpv binary: system install first, then the no-root local runtime."""

import os
import platform
import shutil
from pathlib import Path
from typing import Optional

from .paths import local_mpv_runtime


def _multiarch() -> str:
    machine = platform.machine()
    return {"x86_64": "x86_64-linux-gnu", "aarch64": "aarch64-linux-gnu"}.get(machine, f"{machine}-linux-gnu")


def local_mpv_path() -> Path:
    return local_mpv_runtime() / "usr" / "bin" / "mpv"


def find_mpv() -> Optional[str]:
    """Return a path to an mpv executable or None.

    Order: $MOTIONWALL_MPV, mpv on PATH, the local runtime produced by
    scripts/fetch-local-mpv.sh.
    """
    override = os.environ.get("MOTIONWALL_MPV")
    if override and os.access(override, os.X_OK):
        return override
    system = shutil.which("mpv")
    if system:
        return system
    local = local_mpv_path()
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


def mpv_env(mpv_path: str) -> dict:
    """Environment for running mpv_path; adds LD_LIBRARY_PATH for the local runtime."""
    env = dict(os.environ)
    runtime = local_mpv_runtime()
    try:
        inside = Path(mpv_path).resolve().is_relative_to(runtime.resolve())
    except (OSError, ValueError):
        inside = False
    if inside:
        libdir = runtime / "usr" / "lib" / _multiarch()
        previous = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = str(libdir) + (":" + previous if previous else "")
    return env


def mpv_missing_message() -> str:
    return (
        "mpv was not found. Install it with:\n"
        "    sudo apt install mpv\n"
        "or, without root, run:\n"
        "    scripts/fetch-local-mpv.sh"
    )
