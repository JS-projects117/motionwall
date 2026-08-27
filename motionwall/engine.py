"""mpv-based render engine: one mpv process per desktop window, JSON IPC control.

Efficiency choices (see README):
  * hardware decoding (--hwdec=auto-safe -> nvdec / vaapi / vulkan)
  * audio track disabled entirely when muted (no demux/decode/mixing)
  * --profile=fast (bilinear scaling, no dithering) unless quality mode is chosen
  * no OSD/OSC/input handling, no screensaver inhibition
  * pausing drops the process to ~0% CPU (GPU + decoder idle)
"""

import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import runtime
from .config import Config
from .paths import cache_dir, runtime_dir

log = logging.getLogger("motionwall.engine")

MAX_RETRIES = 5
HWDEC_RETRIES = 5          # re-attempt hardware decoding this many times if it fell back to software
STATS_CACHE_S = 1.0
STABLE_RUN_S = 60.0        # a player alive this long gets its crash counter reset
STATS_PROPERTIES = ("hwdec-current", "estimated-vf-fps", "container-fps", "frame-drop-count",
                    "video-params/w", "video-params/h", "pause", "path", "vo-configured")


def mpv_log_path(name: str) -> str:
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"mpv-{name}.log")


def backoff_delay(attempt: int) -> float:
    """Seconds to wait before restart attempt `attempt` (0-based): 1,2,4,8,16 capped at 30."""
    return float(min(30, 2 ** attempt))


def scaling_args(mode: str) -> List[str]:
    if mode == "fit":
        return ["--keepaspect=yes", "--panscan=0"]
    if mode == "stretch":
        return ["--keepaspect=no"]
    return ["--keepaspect=yes", "--panscan=1.0"]  # fill (crop to cover)


def build_mpv_args(mpv: str, cfg: Config, wid: int, socket_path: str, video: Optional[str]) -> List[str]:
    quality = cfg.extra.get("quality", "fast")
    args = [mpv, "--no-config"]
    if quality != "quality":
        args.append("--profile=fast")
    args += [
        f"--wid={wid}",
        f"--input-ipc-server={socket_path}",
        "--idle=yes",
        "--force-window=yes",
        "--loop-file=inf" if cfg.loop else "--loop-file=no",
        f"--hwdec={cfg.hwdec}",
        "--audio=no" if cfg.mute else f"--volume={cfg.volume}",
        f"--speed={cfg.speed:g}",
        "--osc=no", "--osd-bar=no", "--osd-level=0",
        "--input-default-bindings=no", "--input-vo-keyboard=no", "--input-cursor=no",
        "--cursor-autohide=no",
        "--stop-screensaver=no",
        "--msg-level=all=warn", "--terminal=yes",
        "--background=color", "--background-color=#000000",
        # local looping file: tiny read-ahead, no cache thread
        "--cache=no", "--demuxer-readahead-secs=1", "--demuxer-max-bytes=32MiB",
    ]
    args += scaling_args(cfg.scaling)
    extra = cfg.extra.get("mpv_args")
    if isinstance(extra, list):
        args += [str(a) for a in extra]
    if video:
        args.append(video)
    return args


class MpvIpc:
    """Minimal synchronous JSON IPC client for mpv's --input-ipc-server."""

    def __init__(self, path: str):
        self.path = path
        self.sock: Optional[socket.socket] = None
        self._buf = b""
        self._next_id = 1

    def connect(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(self.path)
                self.sock = s
                return True
            except OSError:
                time.sleep(0.05)
        return False

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self._buf = b""

    def command(self, *cmd: Any) -> Any:
        if not self.sock:
            raise ConnectionError("mpv IPC not connected")
        req_id = self._next_id
        self._next_id += 1
        payload = json.dumps({"command": list(cmd), "request_id": req_id}).encode() + b"\n"
        self.sock.sendall(payload)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            for line in self._read_lines():
                if line.get("request_id") == req_id:
                    if line.get("error") != "success":
                        raise RuntimeError(f"mpv {cmd[0]}: {line.get('error')}")
                    return line.get("data")
        raise TimeoutError(f"mpv did not answer {cmd[0]}")

    def _read_lines(self) -> List[Dict[str, Any]]:
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return []
        if not chunk:
            raise ConnectionError("mpv IPC closed")
        self._buf += chunk
        lines: List[Dict[str, Any]] = []
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            if raw.strip():
                try:
                    lines.append(json.loads(raw))
                except ValueError:
                    pass
        return lines

    def get_property(self, name: str) -> Any:
        return self.command("get_property", name)

    def set_property(self, name: str, value: Any) -> None:
        self.command("set_property", name, value)


@dataclass
class Player:
    wid: int
    name: str
    socket_path: str
    proc: Optional[subprocess.Popen] = None
    ipc: Optional[MpvIpc] = None
    attempts: int = 0
    next_restart: float = 0.0
    failed: bool = False
    hwdec_retries: int = 0
    started_at: float = 0.0
    unloaded: bool = False      # file released (mpv idle) to free decoder/GPU memory

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class Engine:
    """Owns one mpv Player per desktop window."""

    def __init__(self, cfg: Config, mpv_path: Optional[str] = None):
        self.cfg = cfg
        self.mpv = mpv_path or runtime.find_mpv()
        self.players: List[Player] = []
        self.video: Optional[str] = None
        self.paused = False
        self.released = False
        self._stats_cache: Optional[List[Dict[str, Any]]] = None
        self._stats_time = 0.0

    @property
    def available(self) -> bool:
        return bool(self.mpv)

    # -- lifecycle ---------------------------------------------------------
    def start(self, targets: List[Any], video: Optional[str]) -> None:
        """targets: objects with .xid and .monitor.name (DesktopWindow)."""
        self.stop()
        self.video = video
        rdir = runtime_dir()
        rdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for t in targets:
            sock = str(rdir / f"mpv-{t.monitor.name}.sock")
            self.players.append(Player(t.xid, t.monitor.name, sock))
        for p in self.players:
            self._spawn(p)

    def _spawn(self, p: Player) -> bool:
        if not self.mpv:
            p.failed = True
            return False
        if os.path.exists(p.socket_path):
            try:
                os.unlink(p.socket_path)
            except OSError:
                pass
        args = build_mpv_args(self.mpv, self.cfg, p.wid, p.socket_path, self.video)
        log.info("starting mpv for %s: %s", p.name, " ".join(args))
        try:
            logfile = open(mpv_log_path(p.name), "w")
        except OSError:
            logfile = subprocess.DEVNULL
        try:
            p.proc = subprocess.Popen(args, env=runtime.mpv_env(self.mpv), stdin=subprocess.DEVNULL,
                                      stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            log.error("cannot start mpv: %s", exc)
            p.failed = True
            return False
        p.ipc = MpvIpc(p.socket_path)
        p.started_at = time.monotonic()
        p.unloaded = False
        if not p.ipc.connect():
            log.error("mpv IPC socket never appeared for %s; killing it so supervision can retry", p.name)
            self._kill(p)
            p.proc = None
            return False
        if self.released:
            self._release(p)
        elif self.paused:
            self._safe(p, "set_property", "pause", True)
        return True

    def stop(self) -> None:
        for p in self.players:
            self._kill(p)
        self.players = []
        self.video = None

    def _kill(self, p: Player) -> None:
        if p.ipc:
            p.ipc.close()
            p.ipc = None
        if p.proc and p.proc.poll() is None:
            p.proc.terminate()
            try:
                p.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.proc.kill()
                p.proc.wait()
        p.proc = None
        try:
            os.unlink(p.socket_path)
        except OSError:
            pass

    # -- supervision -------------------------------------------------------
    def check(self, now: Optional[float] = None) -> None:
        """Restart dead players with exponential backoff. Call periodically."""
        now = time.monotonic() if now is None else now
        for p in self.players:
            if p.alive():
                if p.attempts and now - p.started_at > STABLE_RUN_S:
                    p.attempts = 0          # a long healthy run forgives earlier crashes
                continue
            if p.failed:
                continue
            if p.proc is not None and p.next_restart == 0.0:
                log.warning("mpv for %s exited with %s", p.name, p.proc.returncode)
                p.next_restart = now + backoff_delay(p.attempts)
                continue
            if now < p.next_restart:
                continue
            if p.attempts >= MAX_RETRIES:
                log.error("mpv for %s keeps crashing; giving up", p.name)
                p.failed = True
                continue
            p.attempts += 1
            p.next_restart = 0.0
            if self._spawn(p) and p.alive():
                log.info("mpv for %s restarted (attempt %d)", p.name, p.attempts)

    # -- control -----------------------------------------------------------
    def _safe(self, p: Player, *cmd: Any) -> Any:
        if not p.ipc:
            return None
        try:
            return p.ipc.command(*cmd)
        except OSError as exc:
            # socket dropped: reconnect once, then retry the command
            log.debug("ipc %s failed for %s (%s); reconnecting", cmd[0], p.name, exc)
            p.ipc.close()
            if p.alive() and p.ipc.connect(timeout=0.5):
                try:
                    return p.ipc.command(*cmd)
                except (OSError, RuntimeError) as exc2:
                    log.debug("ipc %s failed again for %s: %s", cmd[0], p.name, exc2)
            return None
        except RuntimeError as exc:
            log.debug("ipc %s rejected for %s: %s", cmd[0], p.name, exc)
            return None

    def set_playback(self, mode: str) -> None:
        """mode: 'play' | 'pause' | 'release'.

        'release' unloads the file (mpv stays idle in the window) so the decoder,
        frame pool and GPU buffers are freed - used for long pauses (idle, lock).
        """
        self.paused = mode != "play"
        self.released = mode == "release"
        for p in self.players:
            if mode == "release":
                self._release(p)
            elif mode == "pause":
                if p.unloaded:
                    self._reload(p)
                self._safe(p, "set_property", "pause", True)
            else:
                if p.unloaded:
                    self._reload(p)
                self._safe(p, "set_property", "pause", False)
        self._stats_cache = None

    def set_paused(self, paused: bool) -> None:
        self.set_playback("pause" if paused else "play")

    def _release(self, p: Player) -> None:
        if not p.unloaded:
            self._safe(p, "stop")
            p.unloaded = True

    def _reload(self, p: Player) -> None:
        if self.video:
            self._safe(p, "loadfile", self.video, "replace")
        p.unloaded = False

    def load(self, video: str) -> None:
        self.video = video
        self.released = False
        for p in self.players:
            self._safe(p, "loadfile", video, "replace")
            p.unloaded = False
            p.attempts = 0
            p.hwdec_retries = 0
        self._stats_cache = None

    def retry_hwdec(self) -> int:
        """Reload the file on players stuck in software decoding (e.g. after a transient GPU OOM).

        Returns the number of players reloaded. mpv re-probes --hwdec on every file load.
        """
        if self.cfg.hwdec == "no" or not self.video:
            return 0
        reloaded = 0
        for p in self.players:
            if not p.alive() or p.unloaded or p.hwdec_retries >= HWDEC_RETRIES:
                continue
            if self._safe(p, "get_property", "hwdec-current") != "no":
                continue
            p.hwdec_retries += 1
            log.warning("%s is decoding in software; retrying hardware decoding (%d/%d)",
                        p.name, p.hwdec_retries, HWDEC_RETRIES)
            self._safe(p, "loadfile", self.video, "replace")
            reloaded += 1
        return reloaded

    def apply_config(self, cfg: Config) -> None:
        """Apply settings that mpv can change live; others need a restart (caller decides)."""
        self.cfg = cfg
        for p in self.players:
            self._safe(p, "set_property", "speed", cfg.speed)
            self._safe(p, "set_property", "loop-file", "inf" if cfg.loop else "no")
            if cfg.scaling == "stretch":
                self._safe(p, "set_property", "keepaspect", False)
            else:
                self._safe(p, "set_property", "keepaspect", True)
                self._safe(p, "set_property", "panscan", 1.0 if cfg.scaling == "fill" else 0.0)
            if not cfg.mute:
                self._safe(p, "set_property", "volume", cfg.volume)

    def needs_restart(self, old: Config, new: Config) -> bool:
        return (old.mute != new.mute or old.hwdec != new.hwdec or old.monitors != new.monitors
                or old.extra.get("quality") != new.extra.get("quality")
                or old.extra.get("mpv_args") != new.extra.get("mpv_args"))

    def stats(self, max_age: float = STATS_CACHE_S) -> List[Dict[str, Any]]:
        """Per-player mpv properties; cached for max_age seconds (each property is an IPC round trip)."""
        now = time.monotonic()
        if self._stats_cache is not None and now - self._stats_time < max_age:
            return self._stats_cache
        out = []
        for p in self.players:
            entry: Dict[str, Any] = {"monitor": p.name, "pid": p.proc.pid if p.proc else None,
                                     "alive": p.alive(), "failed": p.failed, "restarts": p.attempts}
            entry["unloaded"] = p.unloaded
            for prop in STATS_PROPERTIES:
                entry[prop] = self._safe(p, "get_property", prop) if p.ipc else None
            out.append(entry)
        self._stats_cache, self._stats_time = out, now
        return out

    def any_failed(self) -> bool:
        return any(p.failed for p in self.players)
