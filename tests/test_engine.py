import json
import os
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace

from motionwall import engine
from motionwall.config import Config


class ArgTests(unittest.TestCase):
    def test_default_args_are_efficient(self):
        args = engine.build_mpv_args("/usr/bin/mpv", Config(), 42, "/tmp/s.sock", "/v.mp4")
        for flag in ("--no-config", "--profile=fast", "--wid=42", "--input-ipc-server=/tmp/s.sock",
                     "--hwdec=auto-safe", "--audio=no", "--loop-file=inf", "--osc=no", "--osd-bar=no",
                     "--input-default-bindings=no", "--msg-level=all=warn", "--stop-screensaver=no",
                     "--keepaspect=yes", "--panscan=1.0", "--speed=1"):
            self.assertIn(flag, args, flag)
        self.assertEqual(args[-1], "/v.mp4")
        self.assertEqual(args[0], "/usr/bin/mpv")

    def test_scaling_modes(self):
        self.assertEqual(engine.scaling_args("fit"), ["--keepaspect=yes", "--panscan=0"])
        self.assertEqual(engine.scaling_args("stretch"), ["--keepaspect=no"])
        self.assertEqual(engine.scaling_args("fill"), ["--keepaspect=yes", "--panscan=1.0"])

    def test_unmuted_uses_volume_and_quality_profile(self):
        cfg = Config(mute=False, volume=30, hwdec="vaapi", speed=0.5, loop=False)
        cfg.extra["quality"] = "quality"
        cfg.extra["mpv_args"] = ["--gpu-api=opengl"]
        args = engine.build_mpv_args("mpv", cfg, 1, "s", None)
        self.assertIn("--volume=30", args)
        self.assertNotIn("--audio=no", args)
        self.assertNotIn("--profile=fast", args)
        self.assertIn("--hwdec=vaapi", args)
        self.assertIn("--speed=0.5", args)
        self.assertIn("--loop-file=no", args)
        self.assertIn("--gpu-api=opengl", args)
        self.assertNotEqual(args[-1], None)

    def test_backoff(self):
        self.assertEqual([engine.backoff_delay(i) for i in range(6)], [1, 2, 4, 8, 16, 30])


class FakeMpvServer:
    """Speaks just enough of mpv's JSON IPC to test the client."""

    def __init__(self, path):
        self.path = path
        self.props = {"pause": False, "hwdec-current": "nvdec"}
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.srv.accept()
        conn.sendall(b'{"event":"file-loaded"}\n')  # unsolicited event must be ignored
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                req = json.loads(line)
                cmd = req["command"]
                reply = {"request_id": req["request_id"], "error": "success"}
                if cmd[0] == "get_property":
                    if cmd[1] in self.props:
                        reply["data"] = self.props[cmd[1]]
                    else:
                        reply["error"] = "property not found"
                elif cmd[0] == "set_property":
                    self.props[cmd[1]] = cmd[2]
                conn.sendall(json.dumps(reply).encode() + b"\n")
        conn.close()


class IpcTests(unittest.TestCase):
    def test_ipc_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mpv.sock")
            FakeMpvServer(path)
            ipc = engine.MpvIpc(path)
            self.assertTrue(ipc.connect(timeout=2))
            self.assertEqual(ipc.get_property("hwdec-current"), "nvdec")
            ipc.set_property("pause", True)
            self.assertTrue(ipc.get_property("pause"))
            with self.assertRaises(RuntimeError):
                ipc.get_property("nonexistent")
            ipc.close()

    def test_connect_times_out_without_server(self):
        ipc = engine.MpvIpc("/nonexistent/mpv.sock")
        self.assertFalse(ipc.connect(timeout=0.2))


class SupervisionTests(unittest.TestCase):
    def test_restart_backoff_and_give_up(self):
        eng = engine.Engine(Config(), mpv_path="/bin/false")
        p = engine.Player(1, "m", "/tmp/none.sock")
        p.proc = SimpleNamespace(poll=lambda: 1, returncode=1)
        eng.players = [p]
        spawned = []
        eng._spawn = lambda player: spawned.append(player) or False
        eng.check(now=100.0)                      # death noticed -> schedule
        self.assertEqual(p.next_restart, 101.0)
        eng.check(now=100.5)                      # too early
        self.assertEqual(spawned, [])
        eng.check(now=101.0)                      # first restart attempt
        self.assertEqual(len(spawned), 1)
        self.assertEqual(p.attempts, 1)
        # simulate repeated failure until give-up
        for i in range(20):
            eng.check(now=1000.0 + i * 100)
        self.assertTrue(p.failed)
        self.assertEqual(p.attempts, engine.MAX_RETRIES)


class HwdecRetryTests(unittest.TestCase):
    def _engine(self, hwdec_current):
        eng = engine.Engine(Config(), mpv_path="/bin/true")
        eng.video = "/v.mp4"
        p = engine.Player(1, "m", "/tmp/none.sock")
        p.proc = SimpleNamespace(poll=lambda: None, returncode=None)
        eng.players = [p]
        calls = []

        def fake_safe(player, *cmd):
            calls.append(cmd)
            return hwdec_current if cmd[:2] == ("get_property", "hwdec-current") else None
        eng._safe = fake_safe
        return eng, p, calls

    def test_reloads_when_software_and_gives_up(self):
        eng, p, calls = self._engine("no")
        for i in range(1, engine.HWDEC_RETRIES + 1):
            self.assertEqual(eng.retry_hwdec(), 1)
            self.assertEqual(p.hwdec_retries, i)
        self.assertEqual(eng.retry_hwdec(), 0)
        self.assertIn(("loadfile", "/v.mp4", "replace"), calls)

    def test_no_reload_when_hardware_or_disabled(self):
        eng, p, calls = self._engine("vulkan")
        self.assertEqual(eng.retry_hwdec(), 0)
        eng, p, calls = self._engine("no")
        eng.cfg.hwdec = "no"
        self.assertEqual(eng.retry_hwdec(), 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
