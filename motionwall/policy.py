"""Pure playback policy: decides whether the wallpaper should be animating."""

from dataclasses import dataclass
from typing import Optional, Tuple

from .config import Config


@dataclass
class PolicyState:
    user_play: bool = True      # Play/Pause pressed by the user
    locked: bool = False        # screen locked / screensaver active
    idle: bool = False          # no input for cfg.idle_minutes
    on_battery: bool = False    # UPower reports discharging
    occluded: bool = False      # a fullscreen/maximized window covers every monitor


REASON_LABELS = {
    "user": "paused by you",
    "locked": "screen locked",
    "idle": "you are away",
    "battery": "on battery power",
    "fullscreen": "a full-screen app is covering the desktop",
}


def decide(state: PolicyState, cfg: Config) -> Tuple[bool, Optional[str]]:
    """Return (should_play, reason). reason is None when playing."""
    if not state.user_play:
        return False, "user"
    if state.locked and cfg.pause_on_lock:
        return False, "locked"
    if state.idle and cfg.pause_on_idle:
        return False, "idle"
    if state.on_battery and cfg.pause_on_battery:
        return False, "battery"
    if state.occluded and cfg.pause_on_fullscreen:
        return False, "fullscreen"
    return True, None
