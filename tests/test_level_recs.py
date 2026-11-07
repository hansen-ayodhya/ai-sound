import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import ai, convert, live, recs
from aise.audit import run_audit
from aise.fake_mixer import FakeMixer
from aise.live import Alert, LiveMonitor, make_gain_findings
from aise.server import ROOT, App, serve

GAIN = "/headamp/03/gain"


def dump(db):
    return {"values": {GAIN: [convert.gain_raw(db)], "/ch/03/config/name": ["Mic"]}}


class Pure(unittest.TestCase):
    def test_gain_roundtrip_matches_verified_range(self):
        self.assertAlmostEqual(convert.gain_db(0.0), -12.0)
        self.assertAlmostEqual(convert.gain_db(1.0), 60.0)
        self.assertAlmostEqual(convert.gain_db(convert.gain_raw(23.0)), 23.0)
        self.assertEqual(convert.gain_raw(99), 1.0)

    def test_gemini_gain_validated(self):
        u = ai.to_usulan(3, "gain_db", 20, dump(30))
        self.assertEqual(u["alamat"], GAIN)
        self.assertAlmostEqual(convert.gain_db(u["usulan"]), 20, places=1)
        self.assertIsNone(ai.to_usulan(3, "gain_db", 90, dump(30)))
        self.assertIsNone(ai.to_usulan(3, "gain_db", "20", dump(30)))
        self.assertIn("gain_db", ai.PARAMS)
        self.assertNotIn("phantom", " ".join(ai.PARAMS))

    def test_fmt_gain(self):
        self.assertEqual(recs.fmt_value(GAIN, convert.gain_raw(26)), "+26 dB")

    def test_clip_proposes_lower_gain(self):
        f = make_gain_findings({2: ("clip", -0.4)}, dump(30), {2: "CH 03 Mic"})[0]
        self.assertEqual(f["id"], "live-gain-ch3")
        self.assertEqual(f["ch"], 3)
        self.assertEqual(f["sumber"], "program")
        self.assertAlmostEqual(convert.gain_db(f["usulan"][0]["usulan"]), 26, places=1)
        self.assertEqual(f["usulan"][0]["alamat"], GAIN)
        self.assertIn("Mic", f["masalah"])

    def test_hot_is_smaller_step_and_floor(self):
        f = make_gain_findings({2: ("hot", -2.0)}, dump(30))[0]
        self.assertAlmostEqual(convert.gain_db(f["usulan"][0]["usulan"]), 27, places=1)
        self.assertEqual(make_gain_findings({2: ("clip", 0.0)}, dump(-12)), [])           # sudah paling bawah
        self.assertEqual(make_gain_findings({2: ("clip", 0.0)}, {"values": {}}), [])      # gain tidak terbaca
        self.assertEqual(make_gain_findings({40: ("clip", 0.0)}, dump(30)), [])           # bukan channel input

    def test_monitor_remembers_recent_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = LiveMonitor("127.0.0.1", 1, "/meters/1", {}, Path(tmp) / "live.jsonl")
            m._record([Alert(1.0, "t", "hot", "warn", 2, "CH 03", -2.0, "x")])
            self.assertEqual(m.recent_events()[2][0], "hot")
            m._record([Alert(2.0, "t", "clip", "bad", 2, "CH 03", -0.4, "x")])
            self.assertEqual(m.recent_events()[2][0], "clip")
            m._record([Alert(3.0, "t", "weak", "info", 5, "CH 06", -45.0, "x")])
            self.assertNotIn(5, m.recent_events())
            self.assertEqual(m.recent_events(within_s=-1), {})


class Server(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        addrs = ["/info", "/ch/03/config/name", GAIN]
        self.mixer = FakeMixer({"/info": ("x",), "/ch/03/config/name": ("Mic",), GAIN: (convert.gain_raw(30),)}).start()
        self.app = App(root=ROOT, data=self.data, addresses=addrs)
        run_audit("127.0.0.1", self.mixer.port, base=self.data, addresses=addrs, timeout=0.1)
        probe = serve(App(root=ROOT, data=self.data), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.mixer.stop()
        self.tmp.cleanup()

    def get(self, path):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        r.add_header("X-AISE-Token", self.app.token)
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read())

    def test_live_gain_finding_flow(self):
        self.assertEqual(self.get("/api/recs")["recs"], {})
        self.app.live = LiveMonitor("127.0.0.1", 1, "/meters/1", {2: "CH 03 Mic"}, self.data / "live.jsonl")
        self.assertEqual(self.get("/api/live?alerts=1")["findings"], [])
        self.app.live._record([Alert(time.monotonic(), "t", "clip", "bad", 2, "CH 03 Mic", -0.4, "x")])
        fnd = self.get("/api/live?alerts=1")["findings"]
        self.assertEqual([f["id"] for f in fnd], ["live-gain-ch3"])
        self.assertIsNone(self.get("/api/live?alerts=0")["findings"])
        v = self.get("/api/recs")
        self.assertEqual(v["recs"]["live-gain-ch3"]["status"], "belum")
        self.assertEqual(v["recs"]["live-gain-ch3"]["items"][0]["awal_teks"], "+30 dB")
        self.assertEqual(v["recs"]["live-gain-ch3"]["items"][0]["target_teks"], "+26 dB")
        self.app.data.mkdir(parents=True, exist_ok=True)
        (self.data / "mixer-ip.txt").write_text("127.0.0.1")
        self.app.save_port(self.mixer.port)
        self.mixer.state[GAIN] = (convert.gain_raw(26.5),)               # operator menurunkan gain di M-AIR
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/settings/refresh", data=b"{}", method="POST")
        r.add_header("X-AISE-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        urllib.request.urlopen(r, timeout=20).read()
        v = self.get("/api/recs")
        self.assertEqual(v["recs"]["live-gain-ch3"]["status"], "selesai", v)
        self.assertEqual(v["selesai"][0]["id"], "live-gain-ch3")
        self.assertEqual(self.mixer.write_attempts, [])


if __name__ == "__main__":
    unittest.main()
