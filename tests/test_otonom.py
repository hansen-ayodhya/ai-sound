"""Mode semi-otonom dan otonom: lewat Controller sungguhan ke mixer tiruan."""
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

from aise import convert, meters
from aise.audit import run_audit
from aise.fake_mixer import FakeMixer
from aise.server import ROOT, App, serve
from aise.otonom import MAX_GAIN_CUT_DB, Autopilot
from aise.writer import Controller

GAIN, FADER, HPON = "/headamp/03/gain", "/ch/03/mix/fader", "/ch/03/preamp/hpon"


def finding(fid, addr, cur, tgt, ch=3):
    return {"id": fid, "ch": ch, "masalah": "uji", "severity": "info",
            "usulan": [{"alamat": addr, "sekarang": cur, "usulan": tgt, "teks": "uji"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mixer = FakeMixer({GAIN: (convert.gain_raw(30),), FADER: (convert.fader_raw(0),), HPON: (0,),
                                "/headamp/05/gain": (convert.gain_raw(30),)}).start()
        self.ctl = Controller(Path(self.tmp.name) / "changes.jsonl", lambda a: self.mixer.state[a][0])
        self.ctl.enable("127.0.0.1", self.mixer.port)
        self.findings, self.ignored, self.alive = [], set(), True
        self.ap = Autopilot(self.ctl, lambda a: self.mixer.state[a][0], lambda: list(self.findings),
                            lambda f: f["id"] in self.ignored, Path(self.tmp.name) / "otonom.jsonl", alive=lambda: self.alive)

    def tearDown(self):
        self.ap.set_mode("manual")
        self.ctl.disable()
        self.mixer.stop()
        self.tmp.cleanup()

    def gain(self, addr=GAIN):
        return convert.gain_db(self.mixer.state[addr][0])

    def wait(self, cond, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def done(self):
        return self.wait(lambda: self.ap.view()["riwayat"] and not self.ap.view()["jalan"])


class Modes(Base):
    def test_requirements(self):
        with self.assertRaises(ValueError):
            self.ap.set_mode("ngawur")
        with self.assertRaises(ValueError):                 # otonom perlu dua konfirmasi
            self.ap.set_mode("otonom", peta_ok=True)
        self.alive = False
        with self.assertRaises(ValueError):                 # tanpa pantau live
            self.ap.set_mode("semi")
        self.alive = True
        self.ctl.disable()
        with self.assertRaises(ValueError):                 # tanpa Mode kontrol
            self.ap.set_mode("semi")
        self.assertEqual(self.ap.mode, "manual")

    def test_manual_does_nothing(self):
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(26))]
        self.ap.tick()
        time.sleep(0.4)
        self.assertEqual(self.mixer.write_attempts, [])
        self.assertEqual(self.ap.view()["usul"], [])

    def test_falls_back_when_control_or_live_stops(self):
        self.ap.set_mode("otonom", True, True)
        self.alive = False
        self.assertTrue(self.wait(lambda: self.ap.mode == "manual"))
        self.assertIn("pantau live", self.ap.view()["pesan"])
        self.alive = True
        self.ap.set_mode("semi")
        self.ctl.disable()
        self.assertTrue(self.wait(lambda: self.ap.mode == "manual"))


class Semi(Base):
    def test_waits_for_approval_then_applies_gradually(self):
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(26))]
        self.ap.set_mode("semi")
        self.assertTrue(self.wait(lambda: self.ap.view()["usul"]))
        time.sleep(0.8)
        self.assertEqual(self.mixer.write_attempts, [])     # belum disetujui: tidak ada yang ditulis
        self.assertFalse(self.ap.view()["usul"][0]["otomatis"])
        self.ap.approve("mesin-panas-ch3")
        self.assertTrue(self.done())
        self.assertAlmostEqual(self.gain(), 26.0, places=1)
        h = self.ap.view()["riwayat"][0]
        self.assertTrue(h["ok"])
        self.assertFalse(h["otomatis"])
        steps = [a[0] for _, addr, a in self.mixer.write_log if addr == GAIN]
        self.assertGreaterEqual(len(steps), 4)              # bertahap, bukan satu loncatan
        self.assertEqual(self.ctl.status()["item"], [])     # dilepas setelah selesai

    def test_approval_covers_increase_and_toggle(self):
        self.findings = [{"id": "mesin-struktur-ch3", "ch": 3, "masalah": "uji", "usulan": [
            {"alamat": GAIN, "sekarang": convert.gain_raw(30), "usulan": convert.gain_raw(34), "teks": "gain"},
            {"alamat": FADER, "sekarang": convert.fader_raw(0), "usulan": convert.fader_raw(-4), "teks": "fader"}]}]
        self.ap.set_mode("semi")
        self.assertTrue(self.wait(lambda: self.ap.view()["usul"]))
        self.ap.approve("mesin-struktur-ch3")
        self.assertTrue(self.done())
        self.assertAlmostEqual(self.gain(), 34.0, places=1)
        self.assertAlmostEqual(convert.fader_db(self.mixer.state[FADER][0]), -4.0, places=1)

    def test_reject_and_ignore_hide_the_proposal(self):
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(26)),
                         finding("mesin-panas-ch5", "/headamp/05/gain", convert.gain_raw(30), convert.gain_raw(26), ch=5)]
        self.ignored.add("mesin-panas-ch5")
        self.ap.set_mode("semi")
        self.assertTrue(self.wait(lambda: [u["id"] for u in self.ap.view()["usul"]] == ["mesin-panas-ch3"]))
        self.ap.reject("mesin-panas-ch3")
        time.sleep(1.2)
        self.assertEqual(self.ap.view()["usul"], [])
        with self.assertRaises(ValueError):
            self.ap.approve("mesin-panas-ch3")
        self.assertEqual(self.mixer.write_attempts, [])


class Otonom(Base):
    def test_applies_sustained_reduction_by_itself(self):
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(26))]
        self.ap.set_mode("otonom", True, True)
        self.assertTrue(self.done())
        self.assertAlmostEqual(self.gain(), 26.0, places=1)
        self.assertTrue(self.ap.view()["riwayat"][0]["otomatis"])
        self.assertTrue(self.ap.view()["bisa_pulih"])

    def test_never_raises_or_toggles_by_itself(self):
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(34)),      # label penyelamatan tapi arahnya naik
                         finding("mesin-feedback-ch3", HPON, 0, 1),                                        # sakelar
                         finding("live-gain-ch5", "/headamp/05/gain", convert.gain_raw(30), convert.gain_raw(26), ch=5),   # satu kejadian sesaat
                         finding("eq-boomy-ch3-b1", "/ch/03/eq/1/g", 0.7, 0.5)]
        self.ap.set_mode("otonom", True, True)
        self.assertTrue(self.wait(lambda: len(self.ap.view()["usul"]) == 4))
        time.sleep(1.5)
        self.assertEqual(self.mixer.write_attempts, [])
        self.assertTrue(all(not u["otomatis"] and u["catatan"] for u in self.ap.view()["usul"]))

    def test_cap_from_session_baseline(self):
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(30 - MAX_GAIN_CUT_DB - 2))]
        self.ap.set_mode("otonom", True, True)
        self.assertTrue(self.wait(lambda: self.ap.view()["usul"]))
        time.sleep(1.2)
        self.assertEqual(self.mixer.write_attempts, [])
        self.assertIn("batas otonom", self.ap.view()["usul"][0]["catatan"])

    def test_leaves_channel_the_operator_just_touched(self):
        self.ap.set_mode("otonom", True, True)
        self.ap.on_param("/ch/03/eq/2/g", (0.4,))           # operator menggeser EQ channel 3 di M-AIR
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(26))]
        self.assertTrue(self.wait(lambda: self.ap.view()["usul"]))
        time.sleep(1.2)
        self.assertEqual(self.mixer.write_attempts, [])
        self.assertEqual(self.ap.view()["ditahan"], [3])
        self.assertIn("operator", self.ap.view()["usul"][0]["catatan"])

    def test_cooldown_then_restore_to_baseline(self):
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(27))]
        self.ap.set_mode("otonom", True, True)
        self.assertTrue(self.done())
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(27), convert.gain_raw(24))]
        time.sleep(1.5)
        self.assertAlmostEqual(self.gain(), 27.0, places=1)                 # channel sedang diamati: belum ada tindakan kedua
        self.assertIn("menunggu hasil", self.ap.view()["usul"][0]["catatan"])
        self.findings = []
        self.ap.restore()
        self.assertEqual(self.ap.mode, "manual")
        self.assertTrue(self.wait(lambda: abs(self.gain() - 30.0) < 0.1 and not self.ap.view()["jalan"]))
        self.assertFalse(self.ap.view()["bisa_pulih"])

    def test_stale_reading_cancels_auto_action(self):
        self.mixer.state[GAIN] = (convert.gain_raw(20),)                    # operator sudah menurunkan lebih jauh
        self.findings = [finding("mesin-panas-ch3", GAIN, convert.gain_raw(30), convert.gain_raw(26))]
        self.ap.set_mode("otonom", True, True)
        time.sleep(1.5)
        self.assertEqual(self.mixer.write_attempts, [])
        self.assertAlmostEqual(self.gain(), 20.0, places=1)


class ServerFlow(unittest.TestCase):
    """Dari meter sampai mixer: mesin menemukan sinyal panas, mode otonom menurunkan gain, operator memulihkan."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        addrs = ["/info", "/ch/03/config/name", GAIN, "/ch/03/mix/fader", "/ch/03/mix/on"]
        self.mixer = FakeMixer({"/info": ("x",), "/ch/03/config/name": ("Mic",), GAIN: (convert.gain_raw(30),),
                                "/ch/03/mix/fader": (convert.fader_raw(0),), "/ch/03/mix/on": (1,)}).start()
        n = {"i": 0}
        pattern = [-7.0, -15.0, -25.0, -60.0, -12.0, -30.0]

        def blob():
            n["i"] += 1
            return meters.make_blob([-90.0, -90.0, pattern[n["i"] % len(pattern)]])
        self.mixer.meter_generator = blob
        self.mixer.stream_seconds = 60.0
        self.mixer.echo_writes = True
        self.app = App(root=ROOT, data=self.data, addresses=addrs)
        run_audit("127.0.0.1", self.mixer.port, base=self.data, addresses=addrs, timeout=0.1)
        (self.data / "mixer-ip.txt").write_text("127.0.0.1")
        self.app.save_port(self.mixer.port)
        probe = serve(App(root=ROOT, data=self.data), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.app.control.disable()
        self.app.stop_live()
        time.sleep(0.3)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.mixer.stop()
        self.tmp.cleanup()

    def call(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        r.add_header("X-AISE-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait(self, cond, timeout=30.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.1)
        return False

    def test_meter_to_mixer(self):
        self.assertEqual(self.call("/api/otonom/mode", {"mode": "semi"})[0], 400)                 # Mode kontrol belum menyala
        self.assertEqual(self.call("/api/prefs", {"kepekaan": "gila"})[0], 400)
        self.assertEqual(self.call("/api/status")[1]["kepekaan"], "tinggi")
        self.app.start_live("127.0.0.1", self.mixer.port, "/meters/1")
        self.assertEqual(self.call("/api/mesin/acuan", {})[0], 400)                               # belum ada perkiraan balance
        self.assertTrue(self.wait(lambda: any(f["id"] == "mesin-panas-ch3" for f in self.call("/api/live")[1]["findings"])))
        live = self.call("/api/live")[1]
        self.assertEqual(live["otonom"]["mode"], "manual")
        self.assertEqual(live["mesin"]["kepekaan"], "tinggi")
        self.assertEqual(self.mixer.write_attempts, [])                                           # manual: hanya temuan
        self.assertEqual(self.call("/api/control/enable", {"scene_saved": True, "paham": True})[0], 200)
        self.assertEqual(self.call("/api/otonom/mode", {"mode": "otonom"})[0], 400)               # tanpa dua konfirmasi
        self.assertEqual(self.mixer.write_attempts, [])
        code, v = self.call("/api/otonom/mode", {"mode": "otonom", "peta_ok": True, "hadir": True})
        self.assertEqual((code, v["mode"]), (200, "otonom"))
        self.assertTrue(self.wait(lambda: convert.gain_db(self.mixer.state[GAIN][0]) < 29.0))
        self.assertTrue(self.wait(lambda: self.call("/api/otonom")[1]["riwayat"]))
        v = self.call("/api/otonom")[1]
        self.assertTrue(v["riwayat"][0]["ok"], v)
        self.assertTrue(v["riwayat"][0]["otomatis"])
        self.assertEqual(v["ditahan"], [])                                                        # gema tulisan sendiri bukan "operator"
        self.assertAlmostEqual(convert.gain_db(self.mixer.state[GAIN][0]), 27.0, places=1)
        self.assertEqual({a for a, _ in self.mixer.write_attempts}, {GAIN})
        code, v = self.call("/api/otonom/pulihkan", {})
        self.assertEqual((code, v["mode"]), (200, "manual"))
        self.assertTrue(self.wait(lambda: abs(convert.gain_db(self.mixer.state[GAIN][0]) - 30.0) < 0.1))
        self.assertTrue((self.data / "otonom.jsonl").exists())
        self.app.stop_live()
        self.assertEqual(self.app.otonom.mode, "manual")


if __name__ == "__main__":
    unittest.main()
