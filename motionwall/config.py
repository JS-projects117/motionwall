"""Persistent configuration (JSON) with typed defaults and unknown-key preservation."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import config_file

SCALING_MODES = ("fill", "fit", "stretch")
HWDEC_MODES = ("auto-safe", "auto", "nvdec", "vaapi", "vulkan", "no")


@dataclass
class Config:
    video: str = ""                       # path of the active wallpaper video ("" = none)
    enabled: bool = True                  # False after `motionwall stop`; the video is remembered
    scaling: str = "fill"                 # fill | fit | stretch
    mute: bool = True                     # no audio decoding at all when muted
    volume: int = 50                      # 0-100, only used when not muted
    hwdec: str = "auto-safe"              # mpv --hwdec value
    loop: bool = True
    speed: float = 1.0                    # playback speed (0.25-4)
    monitors: str = "all"                 # "all" or a RandR output name
    pause_on_lock: bool = True
    pause_on_idle: bool = True
    idle_minutes: int = 5
    pause_on_battery: bool = True
    pause_on_fullscreen: bool = True      # needs the GNOME Shell extension
    autostart: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)  # unknown keys preserved

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra")
        merged = dict(extra)
        merged.update(data)
        return merged

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        known = {f.name: f for f in fields(cls) if f.name != "extra"}
        kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        for key, value in data.items():
            if key in known:
                kwargs[key] = _coerce(known[key].type, value, getattr(cls(), key))
            else:
                extra[key] = value
        cfg = cls(**kwargs)
        cfg.extra = extra
        return cfg.validated()

    def validated(self) -> "Config":
        if self.scaling not in SCALING_MODES:
            self.scaling = "fill"
        if self.hwdec not in HWDEC_MODES:
            self.hwdec = "auto-safe"
        self.volume = max(0, min(100, int(self.volume)))
        self.speed = max(0.25, min(4.0, float(self.speed)))
        self.idle_minutes = max(1, min(240, int(self.idle_minutes)))
        return self


def _coerce(type_hint: Any, value: Any, default: Any) -> Any:
    """Coerce JSON values to the field type, falling back to default on failure."""
    try:
        if type_hint in ("bool", bool):
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes", "on")
            return bool(value)
        if type_hint in ("int", int):
            return int(value)
        if type_hint in ("float", float):
            return float(value)
        if type_hint in ("str", str):
            return str(value)
    except (TypeError, ValueError):
        return default
    return value


def load(path: Optional[Path] = None) -> Config:
    path = path or config_file()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return Config()
        return Config.from_dict(data)
    except (OSError, ValueError):
        return Config()


def save(cfg: Config, path: Optional[Path] = None) -> None:
    path = path or config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.validated().to_dict(), indent=2, sort_keys=True) + "\n"
    # atomic write so a crash never leaves a truncated config
    fd, tmp = tempfile.mkstemp(prefix=".config-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
