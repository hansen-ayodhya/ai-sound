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
        self.assertIn("Vokal 1", a[0].label)
        self.assertLess(len(a[0].message), 80)          # pesan ringkas
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


class LiveSettingsRefresh(unittest.TestCase):
    def test_settings_reread_during_live(self):
        from aise import server as srv
        tmp = tempfile.TemporaryDirectory()
        mixer = FakeMixer({"/info": ("x",), "/ch/01/config/name": ("Vokal 1",), "/ch/01/preamp/hpon": (0,)}).start()
        mixer.meter_generator = lambda: meters.make_blob([-20.0])
        app = App(root=ROOT, data=Path(tmp.name) / "data", addresses=["/info", "/ch/01/config/name", "/ch/01/preamp/hpon"])
        old = srv.LIVE_REFRESH_S
        srv.LIVE_REFRESH_S = 1
        try:
            app.start_live("127.0.0.1", mixer.port, "/meters/1")
            for _ in range(100):
                if app.live_settings_at:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(app.live_settings_at)
            latest = json.loads((app.data / "latest.json").read_text())
            self.assertEqual(latest["dump"], "data/live-setting/dump1.json")
            self.assertTrue(latest["live"])
            mixer.state["/ch/01/config/name"] = ("WL",)     # operator mengganti nama di mixer
            first = app.live_settings_at
            d = {"values": {}}
            for _ in range(60):
                try:
                    d = json.loads((app.data / "live-setting" / "dump1.json").read_text())
                except (OSError, ValueError):                 # Windows bisa menahan file sesaat saat diganti
                    time.sleep(0.1)
                    continue
                if d["values"].get("/ch/01/config/name") == ["WL"]:
                    break
                time.sleep(0.1)
            self.assertEqual(d["values"]["/ch/01/config/name"], ["WL"])
            fnd = json.loads((app.data / "live-setting" / "findings.json").read_text())
            self.assertIsInstance(fnd, list)
            self.assertEqual(mixer.write_attempts, [])
        finally:
            srv.LIVE_REFRESH_S = old
            app.stop_live()
            time.sleep(0.5)
            mixer.stop()
            tmp.cleanup()


class ManualRefresh(unittest.TestCase):
    def test_refresh_without_live_and_during_live(self):
        from aise import server as srv
        tmp = tempfile.TemporaryDirectory()
        mixer = FakeMixer({"/info": ("x",), "/ch/01/config/name": ("Vokal 1",)}).start()
        mixer.meter_generator = lambda: meters.make_blob([-20.0])
        app = App(root=ROOT, data=Path(tmp.name) / "data", addresses=["/info", "/ch/01/config/name"])
        try:
            with self.assertRaises(ValueError):
                app.refresh_settings()  # IP belum diisi
            app.data.mkdir(parents=True, exist_ok=True)
            (app.data / "mixer-ip.txt").write_text("127.0.0.1")
            app.save_port(mixer.port)
            r = app.refresh_settings()
            self.assertEqual((r["mode"], r["answered"]), ("sekarang", 2))
            self.assertEqual(json.loads((app.data / "latest.json").read_text())["dump"], "data/live-setting/dump1.json")
            old = srv.LIVE_REFRESH_S
            srv.LIVE_REFRESH_S = 3600  # jadwal jauh: hanya tombol yang memicu
            try:
                app.start_live("127.0.0.1", mixer.port, "/meters/1")
                time.sleep(6)                       # putaran pertama (5 dtk) selesai
                first = app.live_settings_at
                mixer.state["/ch/01/config/name"] = ("WL",)
                time.sleep(1.1)
                self.assertEqual(app.refresh_settings(), {"mode": "live"})
                for _ in range(40):
                    d = json.loads((app.data / "live-setting" / "dump1.json").read_text())
                    if d["values"]["/ch/01/config/name"] == ["WL"]:
                        break
                    time.sleep(0.1)
                self.assertEqual(d["values"]["/ch/01/config/name"], ["WL"])
                self.assertNotEqual(app.live_settings_at, first)
            finally:
                srv.LIVE_REFRESH_S = old
            self.assertEqual(mixer.write_attempts, [])
        finally:
            app.stop_live()
            time.sleep(0.6)
            mixer.stop()
            tmp.cleanup()


class RulesScope(unittest.TestCase):
    def test_unmapped_meters_ignored(self):
        r = LiveRules({0: "CH 01"})
        levels = [-30.0] * 25 + [-0.2] + [-50.0] * 5          # indeks 25 clipping tetapi bukan channel input
        self.assertEqual(r.feed(levels, 100.0), [])

    def test_muted_channel_no_weak_alert(self):
        r = LiveRules({0: "CH 01", 1: "CH 02"}, inactive={0})
        got = []
        for i in range(0, 32):
            got += r.feed([-50.0, -50.0], float(i))
        self.assertEqual({a.index for a in got if a.kind == "weak"}, {1})   # CH 01 di-mute: tidak ada peringatan
        r2 = LiveRules({0: "CH 01"}, inactive={0})
        self.assertEqual([a.kind for a in r2.feed([-0.5], 200.0)], ["clip"])  # clipping tetap diperingatkan

    def test_inactive_from_dump(self):
        d = {"values": {"/ch/01/mix/on": [0], "/ch/02/mix/on": [1]}}
        self.assertEqual(live.inactive_from_dump(d), {0})


class RealtimeParams(unittest.TestCase):
    def test_changes_from_mixer_reach_app_and_settings(self):
        from aise import guard, server as srv
        from aise.audit import run_audit
        guard.check("/xremote", ())                      # langganan kabar perubahan diizinkan
        with self.assertRaises(guard.ReadOnlyViolation):
            guard.check("/xremote", (1,))
        tmp = tempfile.TemporaryDirectory()
        names = ["/info", "/ch/01/config/name", "/ch/01/eq/1/g", "/ch/01/preamp/hpon"]
        mixer = FakeMixer({"/info": ("x",), "/ch/01/config/name": ("Vokal 1",), "/ch/01/eq/1/g": (0.5,),
                           "/ch/01/preamp/hpon": (0,)}).start()
        mixer.meter_generator = lambda: meters.make_blob([-20.0])
        data = Path(tmp.name) / "data"
        run_audit("127.0.0.1", mixer.port, base=data, addresses=names, timeout=0.1)
        app = App(root=ROOT, data=data, addresses=names)
        old = srv.PARAM_FLUSH_S
        srv.PARAM_FLUSH_S = 0.2
        try:
            app.start_live("127.0.0.1", mixer.port, "/meters/1")
            for _ in range(100):
                if mixer.xremote_requests:
                    break
                time.sleep(0.05)
            self.assertGreaterEqual(mixer.xremote_requests, 1)
            self.assertEqual(app.params_since(None), {"param_seq": 0, "params": []})
            mixer.push("/ch/01/eq/1/g", (0.9,))             # operator mengubah EQ di M-AIR
            mixer.push("/ch/01/eq/1/g", (0.95,))            # digeser lagi: hanya nilai terakhir yang dikirim ke tampilan
            mixer.push("/-prefs/x", (1,))                   # di luar daftar: diabaikan
            mixer.push("/ch/01/preamp/hpon", (1,))
            for _ in range(100):
                if app.param_seq >= 3:
                    break
                time.sleep(0.05)
            r = app.params_since(0)
            self.assertEqual(r["param_seq"], 3)
            got = {p["a"]: p["v"] for p in r["params"]}
            self.assertEqual(set(got), {"/ch/01/eq/1/g", "/ch/01/preamp/hpon"})
            self.assertAlmostEqual(got["/ch/01/eq/1/g"][0], 0.95, places=5)   # OSC memakai float 32-bit
            self.assertEqual(got["/ch/01/preamp/hpon"], [1])
            self.assertEqual(app.params_since(r["param_seq"])["params"], [])
            for _ in range(100):                            # digabung ke setting terbaru setelah jeda
                d = json.loads((app.data / "live-setting" / "dump1.json").read_text()) if (app.data / "live-setting" / "dump1.json").exists() else {"values": {}}
                if abs((d["values"].get("/ch/01/eq/1/g") or [0])[0] - 0.95) < 1e-5:
                    break
                time.sleep(0.1)
            self.assertAlmostEqual(d["values"]["/ch/01/eq/1/g"][0], 0.95, places=5)
            self.assertEqual(d["values"]["/ch/01/preamp/hpon"], [1])
            self.assertEqual(mixer.write_attempts, [])
        finally:
            srv.PARAM_FLUSH_S = old
            app.stop_live()
            time.sleep(0.6)
            mixer.stop()
            tmp.cleanup()
