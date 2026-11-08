import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_memori
from aise import convert, ignored, live
from aise.live import Alert, LiveMonitor


def monitor(state, tmp):
    return LiveMonitor("127.0.0.1", 1, "/meters/1", {2: "CH 03 Mic"}, Path(tmp) / "l.jsonl", state_of=lambda i: state)


class AlertStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = {"gain": convert.gain_raw(30), "on": 1}
        self.m = monitor(self.state, self.tmp.name)
        self.now = time.monotonic()

    def tearDown(self):
        self.tmp.cleanup()

    def alert(self, kind="clip", index=2, age=30.0):
        self.m._record([Alert(self.now - age, "t", kind, "bad", index, "CH 03 Mic", -0.4, "x")])

    def status(self, i=0):
        return self.m.snapshot()["alerts"][i]

    def test_stays_active_while_condition_persists_then_resolves_when_quiet(self):
        self.alert()
        self.m._last_over[2] = time.monotonic() - 1
        self.assertFalse(self.status()["resolved"])                      # lama (30 dtk) tapi masih terjadi: tetap aktif
        self.m._last_over[2] = time.monotonic() - live.QUIET_S - 1
        s = self.status()
        self.assertTrue(s["resolved"])
        self.assertIn("tidak mendekati clipping", s["reason"])
        self.m._last_over[2] = time.monotonic()
        self.assertTrue(self.status()["resolved"])                       # sekali teratasi, tidak hidup lagi (kejadian baru = peringatan baru)

    def test_resolved_when_gain_lowered(self):
        self.alert()
        self.m._last_over[2] = time.monotonic()
        self.state["gain"] = convert.gain_raw(28)
        self.assertFalse(self.status()["resolved"])                      # baru turun 2 dB: belum cukup
        self.state["gain"] = convert.gain_raw(26)
        s = self.status()
        self.assertTrue(s["resolved"])
        self.assertIn("gain sudah diturunkan 4 dB", s["reason"])

    def test_resolved_when_muted_or_weak_fixed_or_data_back(self):
        self.alert(); self.m._last_over[2] = time.monotonic()
        self.state["on"] = 0
        self.assertIn("di-mute", self.status()["reason"])
        self.state["on"] = 1
        self.alert("weak", 3)
        self.assertFalse(self.m.snapshot()["alerts"][1]["resolved"])
        self.m._last_loud[3] = time.monotonic()
        self.assertTrue(self.m.snapshot()["alerts"][1]["resolved"])
        self.m._record([Alert(self.now, "t", "stale", "warn", -1, "", 0.0, "x")])
        self.assertFalse(self.m.snapshot()["alerts"][2]["resolved"])
        self.m.last_frame = time.monotonic()
        self.assertTrue(self.m.snapshot()["alerts"][2]["resolved"])


class IgnoredUnit(unittest.TestCase):
    def test_key_same_for_rule_and_gemini_and_restore(self):
        a = {"id": "x-1", "ch": 3, "usulan": [{"alamat": "/ch/03/mix/01/level"}, {"alamat": "/ch/03/mix/02/level"}]}
        b = {"id": "gemini-9-ch03-123456", "ch": 3, "usulan": [{"alamat": "/ch/03/mix/02/level"}, {"alamat": "/ch/03/mix/01/level"}]}
        self.assertEqual(ignored.key_for(a), ignored.key_for(b))
        self.assertEqual(ignored.key_for({"id": "umum-1", "ch": None, "usulan": []}), "id:umum-1")
        with tempfile.TemporaryDirectory() as tmp:
            ig = ignored.Ignored(Path(tmp) / "d.json")
            k = ig.add({**a, "masalah": "Kiriman bus"})
            self.assertIn(ignored.key_for(b), ig.keys())
            self.assertEqual(ig.listing()[0]["masalah"], "Kiriman bus")
            self.assertTrue(ig.restore(k))
            self.assertFalse(ig.restore(k))
            self.assertEqual(ig.keys(), set())


class IgnoreServer(test_memori.MemoriServer):
    test_flow_records_rates_and_clears = None

    def test_ignore_hides_and_untracks_and_teaches_ai(self):
        self.app.live = LiveMonitor("127.0.0.1", 1, "/meters/1", {2: "CH 03 Mic"}, self.data / "live.jsonl", on_alert=self.app._memori_alert)
        self.app.live._record([Alert(time.monotonic(), "t", "clip", "bad", 2, "CH 03 Mic", -0.4, "x")])
        code, v = self.call("/api/recs")
        self.assertIn("live-gain-ch3", v["recs"])
        self.assertEqual(v["diabaikan"], [])
        code, r = self.call("/api/ignore", {"id": "live-gain-ch3", "ch": 3, "masalah": "Gain CH 03", "alamat": ["/headamp/03/gain"]})
        self.assertEqual(code, 200)
        self.assertEqual(r["key"], "3|/headamp/03/gain")
        self.assertEqual(self.call("/api/ignore", {"id": "x", "alamat": ["bukan-alamat"]})[0], 400)
        self.assertEqual(self.call("/api/ignore", {"alamat": []})[0], 400)
        self.assertEqual(self.call("/api/ignore", {"id": "x", "ch": "3", "alamat": []})[0], 400)
        # rekomendasi yang diabaikan tidak didaftarkan lagi (id baru dengan usulan yang sama juga tidak)
        (self.data / "recs.json").unlink()
        code, v = self.call("/api/recs")
        self.assertNotIn("live-gain-ch3", v["recs"])
        self.assertEqual(v["diabaikan"][0]["key"], "3|/headamp/03/gain")
        prof = self.call("/api/memori")[1]["profil"]
        self.assertEqual(prof["diabaikan_operator"][0]["masalah"], "Gain CH 03")
        code, r = self.call("/api/ignore/restore", {"key": "3|/headamp/03/gain"})
        self.assertTrue(r["dikembalikan"])
        code, v = self.call("/api/recs")
        self.assertIn("live-gain-ch3", v["recs"])


if __name__ == "__main__":
    unittest.main()


class WeakAlertsOff(unittest.TestCase):
    def test_rules_skip_weak_when_disabled(self):
        r = live.LiveRules()
        r.weak_enabled = False
        kinds = []
        for i in range(0, 35):
            kinds += [x.kind for x in r.feed([-50.0], float(i))]
        self.assertNotIn("weak", kinds)
        r2 = live.LiveRules()
        kinds = []
        for i in range(0, 35):
            kinds += [x.kind for x in r2.feed([-50.0], float(i))]
        self.assertIn("weak", kinds)

    def test_existing_weak_alert_resolved_when_turned_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = LiveMonitor("127.0.0.1", 1, "/meters/1", {}, Path(tmp) / "l.jsonl")
            m._record([Alert(time.monotonic(), "t", "weak", "info", 2, "CH 03", -45.0, "x")])
            self.assertFalse(m.snapshot()["alerts"][0]["resolved"])
            m.rules.weak_enabled = False
            s = m.snapshot()["alerts"][0]
            self.assertTrue(s["resolved"])
            self.assertIn("dimatikan", s["reason"])


class WeakPrefServer(test_memori.MemoriServer):
    test_flow_records_rates_and_clears = None

    def test_default_off_persists_and_applies_to_live(self):
        self.assertFalse(self.call("/api/status")[1]["weak_alerts"])                  # default: mati
        self.app.live = LiveMonitor("127.0.0.1", 1, "/meters/1", {}, self.data / "live.jsonl")
        self.assertTrue(self.call("/api/prefs", {"weak_alerts": True})[1]["weak_alerts"])
        self.assertTrue(self.app.live.rules.weak_enabled)
        self.assertTrue(self.call("/api/status")[1]["weak_alerts"])
        self.assertEqual(self.call("/api/prefs", {"weak_alerts": "ya"})[0], 400)
        self.call("/api/prefs", {"weak_alerts": False})
        self.assertFalse(self.app.live.rules.weak_enabled)
        self.assertFalse(self.app.prefs()["weak_alerts"])
