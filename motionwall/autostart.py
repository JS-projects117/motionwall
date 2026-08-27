"""Login autostart (~/.config/autostart) management."""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .paths import autostart_file

DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name=Motionwall wallpaper daemon
Comment=Plays the animated wallpaper
Exec={exec_line}
Icon=org.motionwall.Motionwall
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
OnlyShowIn=GNOME;Unity;ubuntu;X-Cinnamon;MATE;XFCE;
"""


def launcher_path() -> str:
    """Absolute path of the `motionwall` launcher to reference from desktop files."""
    override = os.environ.get("MOTIONWALL_LAUNCHER")
    if override:
        return override
    local = Path.home() / ".local" / "bin" / "motionwall"
    if local.exists():
        return str(local)
    on_path = shutil.which("motionwall")
    if on_path:
        return on_path
    repo = Path(__file__).resolve().parent.parent / "bin" / "motionwall"
    if repo.exists():
        return str(repo)
    return f"{sys.executable} -m motionwall.cli"


def set_enabled(enabled: bool, path: Optional[Path] = None) -> None:
    path = path or autostart_file()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DESKTOP_TEMPLATE.format(exec_line=f"{launcher_path()} daemon"), encoding="utf-8")
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def is_enabled(path: Optional[Path] = None) -> bool:
    path = path or autostart_file()
    return path.exists()
