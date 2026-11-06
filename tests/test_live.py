import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import live, meters
from aise.fake_mixer import FakeMixer
from aise.live import LiveMonitor, LiveRules, labels_from_dump
from aise.server import ROOT, App, serve


class Rules(unittest.TestCase):
    def test_clip_with_cooldown(self):
        r = LiveRules({0: "CH 01 Vokal 1"})
        a = r.feed([-0.5], 100.0)
        self.assertEqual([x.kind for x in a], ["clip"])
        self.assertIn("Vokal 1", a[0].message)
        self.assertEqual(r.feed([-0.5], 101.0), [])          # masih cooldown
        self.assertEqual(len(r.feed([-0.5], 100.0 + live.COOLDOWN["clip"] + 0.1)), 1)

    def test_hot_needs_repeats(self):
        r = LiveRules()
        self.assertEqual(r.feed([-2.0], 0.0), [])
        self.assertEqual(r.feed([-2.0], 1.0), [])
        self.assertEqual([x.kind for x in r.feed([-2.0], 2.0)], ["hot"])

    def test_hot_window_expires(self):
        r = LiveRules()
        r.feed([-2.0], 0.0)
        r.feed([-2.0], 1.0)
        self.assertEqual(r.feed([-2.0], 10.0), [])           # dua sebelumnya sudah lewat jendela

    def test_weak_signal_and_silence(self):
        r = LiveRules()
        kinds = []
        for i in range(0, 32):
            kinds += [x.kind for x in r.feed([-50.0, -90.0, -20.0], float(i))]
        self.assertEqual(kinds.count("weak"), 1)             # hanya indeks 0

    def test_weak_only_index0(self):
        r = LiveRules()
        got = []
        for i in range(0, 32):
            got += r.feed([-50.0, -90.0, -20.0], float(i))
        self.assertEqual({x.index for x in got if x.kind == "weak"}, {0})

    def test_labels_from_dump(self):
        d = {"values": {"/ch/01/config/name": ["Vokal 1"], "/ch/02/config/name": [""]}}
        lab = labels_from_dump(d)
        self.assertEqual(lab[0], "CH 01 Vokal 1")
        self.assertEqual(lab[1], "CH 02")
        self.assertEqual(labels_from_dump(None)[15], "CH 16")


class Monitor(unittest.TestCase):
    def test_stream_from_fake_mixer(self):
        with FakeMixer({}) as mixer, tempfile.TemporaryDirectory() as tmp:
            mixer.meter_generator = lambda: meters.make_blob([-20.0, -0.5, -30.0])
            done = threading.Event()
            mon = LiveMonitor("127.0.0.1", mixer.port, "/meters/1", {1: "CH 02 Vokal 2"},
                              Path(tmp) / "live.jsonl", on_stop=done.set)
            mon.start()
            time.sleep(1.0)
            snap = mon.snapshot()
            self.assertTrue(snap["running"])
            self.assertGreater(snap["frames"], 5)
            self.assertEqual(snap["levels"], [-20.0, -0.5, -30.0])
            self.assertIn("clip", [a["kind"] for a in snap["alerts"]])
            mon.stop()
            self.assertTrue(done.wait(2))
            self.assertFalse(mon.snapshot()["running"])
            self.assertEqual(mixer.write_attempts, [])
            self.assertTrue((Path(tmp) / "live.jsonl").read_text().strip())

    def test_stale_when_no_data(self):
        with FakeMixer({}) as mixer, tempfile.TemporaryDirectory() as tmp:
            done = threading.Event()
            mon = LiveMonitor("127.0.0.1", mixer.port, "/meters/1", {}, Path(tmp) / "live.jsonl", on_stop=done.set)
            mon.start()
            mon.started -= live.STALE_S + 1  # pura-pura sudah lama berjalan
            snap = mon.snapshot()
            mon.stop()
            self.assertTrue(done.wait(3))  # tunggu benar-benar berhenti sebelum folder dihapus
            self.assertIn("stale", [a["kind"] for a in snap["alerts"]])


class ServerLive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mixer = FakeMixer({"/info": ("x",)}).start()
        self.mixer.meter_generator = lambda: meters.make_blob([-10.0, -0.2])
        self.app = App(root=ROOT, data=Path(self.tmp.name) / "data", addresses=["/info"])
        probe = serve(self.app, 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.app.stop_live()
        time.sleep(0.4)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.mixer.stop()
        self.tmp.cleanup()

    def call(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        r.add_header("X-AISE-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_live_start_poll_block_stop(self):
        code, _ = self.call("/api/live/start", {"ip": "127.0.0.1", "port": self.mixer.port})
        self.assertEqual(code, 202)
        time.sleep(0.8)
        code, snap = self.call("/api/live?alerts=1")
        self.assertTrue(snap["running"])
        self.assertEqual(snap["levels"], [-10.0, -0.2])
        self.assertIn("clip", [a["kind"] for a in snap["alerts"]])
        code, s = self.call("/api/live?alerts=0")
        self.assertIsNone(s["alerts"])
        # selama live, pekerjaan lain ke mixer ditolak
        code, err = self.call("/api/probe", {"ip": "127.0.0.1", "port": self.mixer.port})
        self.assertEqual(code, 409)
        self.assertTrue(self.call("/api/status")[1]["live"])
        self.call("/api/live/stop", {})
        for _ in range(30):
            if not self.call("/api/live?alerts=0")[1]["running"]:
                break
            time.sleep(0.1)
        self.assertFalse(self.call("/api/live?alerts=0")[1]["running"])
        code, _ = self.call("/api/probe", {"ip": "127.0.0.1", "port": self.mixer.port})
        self.assertEqual(code, 200)
        self.assertEqual(self.call("/api/status")[1]["port"], self.mixer.port)
        self.assertEqual(self.mixer.write_attempts, [])

    def test_bad_live_path_rejected(self):
        code, _ = self.call("/api/live/start", {"ip": "127.0.0.1", "port": self.mixer.port, "path": "/ch/01/mix/fader"})
        self.assertEqual(code, 400)
        self.assertEqual(self.mixer.meter_requests, [])


if __name__ == "__main__":
    unittest.main()
