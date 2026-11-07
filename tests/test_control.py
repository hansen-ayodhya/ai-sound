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

from aise import convert, guard, meters, writer
from aise.audit import run_audit
from aise.fake_mixer import FakeMixer
from aise.live import Alert
from aise.server import ROOT, App, serve
from aise.writer import Controller, WriteRefused, check_write

GAIN = "/headamp/03/gain"
FADER = "/ch/03/mix/fader"


def wait_for(cond, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class CheckWrite(unittest.TestCase):
    def test_allowed(self):
        self.assertEqual(check_write(GAIN, (0.5,)), "float")
        self.assertEqual(check_write(FADER, (0.0,)), "float")
        self.assertEqual(check_write("/ch/01/preamp/hpon", (1,)), "toggle")
        self.assertEqual(check_write("/ch/16/eq/4/g", (0.3,)), "float")
        self.assertEqual(check_write("/ch/01/dyn/ratio", (5,)), "enum")
        self.assertEqual(check_write("/ch/01/dyn/thr", (0.4,)), "float")

    def test_never_allowed(self):
        for addr, v in [("/headamp/03/phantom", 1), ("/ch/03/mix/on", 0), ("/ch/03/mix/lr", 0), ("/lr/mix/fader", 0.5),
                        ("/bus/1/mix/fader", 0.5), ("/-snap/load", 1), ("/-action/setscene", 1), ("/ch/17/mix/fader", 0.5),
                        ("/ch/03/config/name", "x"), ("/ch/03/mix/01/level", 0.5), ("/ch/03/gate/thr", 0.5), ("/headamp/03/gain/x", 0.5)]:
            with self.assertRaises(WriteRefused, msg=addr):
                check_write(addr, (v,))

    def test_bad_values(self):
        for addr, args in [(GAIN, (1.5,)), (GAIN, (-0.1,)), (GAIN, (float("nan"),)), (GAIN, (1,)), (GAIN, (True,)), (GAIN, ()),
                           (GAIN, (0.1, 0.2)), (GAIN, ("0.1",)), ("/ch/01/preamp/hpon", (2,)), ("/ch/01/preamp/hpon", (0.5,)),
                           ("/ch/01/dyn/ratio", (12,)), ("/ch/01/dyn/ratio", (0.5,))]:
            with self.assertRaises(WriteRefused, msg=f"{addr} {args}"):
                check_write(addr, args)

    def test_readonly_guard_is_untouched(self):
        with self.assertRaises(guard.ReadOnlyViolation):
            guard.check(GAIN, (0.5,))
        with self.assertRaises(guard.ReadOnlyViolation):
            guard.check("/ch/01/mix/fader", (0.5,))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mixer = FakeMixer({GAIN: (convert.gain_raw(30),), FADER: (convert.fader_raw(-10),),
                                "/ch/03/preamp/hpon": (0,), "/ch/03/dyn/ratio": (2,), "/headamp/03/phantom": (0,)}).start()
        self.ctl = Controller(Path(self.tmp.name) / "changes.jsonl", lambda a: self.mixer.state[a][0])
        self.ctl.enable("127.0.0.1", self.mixer.port)

    def tearDown(self):
        self.ctl.disable()
        self.mixer.stop()
        self.tmp.cleanup()

    def val(self, addr=GAIN):
        return self.mixer.state[addr][0]

    def test_requires_enable_and_confirmation(self):
        self.ctl.disable()
        with self.assertRaises(WriteRefused):
            self.ctl.arm(GAIN, 0.5, 0.4)
        self.ctl.enable("127.0.0.1", self.mixer.port)
        with self.assertRaises(WriteRefused):                       # menaikkan gain perlu konfirmasi (risiko feedback)
            self.ctl.arm(GAIN, convert.gain_raw(30), convert.gain_raw(34))
        self.ctl.arm(GAIN, convert.gain_raw(30), convert.gain_raw(34), confirmed=True)
        with self.assertRaises(WriteRefused):                       # sakelar perlu konfirmasi
            self.ctl.arm("/ch/03/preamp/hpon", 0, 1)
        with self.assertRaises(WriteRefused):                       # 48V tidak pernah
            self.ctl.arm("/headamp/03/phantom", 0, 1, confirmed=True)
        with self.assertRaises(WriteRefused):                       # ayunan gain terlalu besar
            self.ctl.arm("/headamp/05/gain", convert.gain_raw(0), convert.gain_raw(30), confirmed=True)
        with self.assertRaises(WriteRefused):
            self.ctl.arm(FADER, 0.5, 0.5)                            # sudah sama
        with self.assertRaises(WriteRefused):
            self.ctl.set_fraction(FADER, 0.5)                        # belum di-arm
        self.assertEqual(self.mixer.write_attempts, [])             # menyalakan/arm tidak menulis apa pun

    def test_smooth_slew_and_exact_return(self):
        awal, target = convert.gain_raw(30), convert.gain_raw(10)
        self.ctl.arm(GAIN, awal, target)
        self.assertEqual(self.mixer.write_attempts, [])
        self.ctl.set_fraction(GAIN, 1.0)                             # loncat langsung ke ujung: harus tetap bertahap
        self.assertTrue(wait_for(lambda: abs(self.val() - target) < 1e-5))
        vals = [a[0] for _, ad, a in self.mixer.write_log if ad == GAIN]
        self.assertGreaterEqual(len(vals), 9)
        steps = [abs(b - a) for a, b in zip([awal] + vals, vals)]
        self.assertLessEqual(max(steps), writer.SPECS[0][2] + 1e-6)  # tidak ada loncatan melebihi batas langkah
        times = [t for t, ad, _ in self.mixer.write_log if ad == GAIN]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 0.06)                     # ~10 kiriman/detik, tidak membanjiri
        self.assertTrue(all(min(awal, target) - 1e-6 <= v <= max(awal, target) + 1e-6 for v in vals))   # tak pernah keluar rentang
        self.ctl.set_fraction(GAIN, 0.0)                             # kembali ke awal
        self.assertTrue(wait_for(lambda: abs(self.val() - awal) < 1e-5))
        self.assertEqual({ad for ad, _ in self.mixer.write_attempts}, {GAIN})
        self.assertTrue(wait_for(lambda: self.ctl.status()["item"][0]["verifikasi"] is not None))
        self.assertTrue(self.ctl.status()["item"][0]["verifikasi"][0])

    def test_midpoint_follows_slider(self):
        awal, target = convert.fader_raw(-10), convert.fader_raw(-4)
        self.ctl.arm(FADER, awal, target, confirmed=True)
        self.ctl.set_fraction(FADER, 0.5)
        self.assertTrue(wait_for(lambda: abs(self.val(FADER) - (awal + target) / 2) < 1e-4))

    def test_toggle_and_ratio_use_ints(self):
        self.ctl.arm("/ch/03/preamp/hpon", 0, 1, confirmed=True)
        self.ctl.set_fraction("/ch/03/preamp/hpon", 1.0)
        self.assertTrue(wait_for(lambda: self.val("/ch/03/preamp/hpon") == 1))
        self.assertIsInstance(self.val("/ch/03/preamp/hpon"), int)
        self.ctl.arm("/ch/03/dyn/ratio", 2, 6)
        self.ctl.set_fraction("/ch/03/dyn/ratio", 1.0)
        self.assertTrue(wait_for(lambda: self.val("/ch/03/dyn/ratio") == 6))
        seq = [a[0] for _, ad, a in self.mixer.write_log if ad == "/ch/03/dyn/ratio"]
        self.assertEqual(seq, [3, 4, 5, 6])                          # satu langkah per kiriman
        self.assertTrue(all(isinstance(x, int) for x in seq))

    def test_operator_change_in_miair_releases_control(self):
        awal, target = convert.gain_raw(30), convert.gain_raw(10)
        self.ctl.arm(GAIN, awal, target)
        self.ctl.set_fraction(GAIN, 0.5)
        self.assertTrue(wait_for(lambda: abs(self.val() - (awal + target) / 2) < 1e-4))
        self.ctl.on_param(GAIN, (self.val(),))                       # gema dari kiriman kita: diabaikan
        self.assertEqual(self.ctl.status()["item"][0]["state"], "aktif")
        self.ctl.on_param(GAIN, (convert.gain_raw(45),))             # operator memutar gain di M-AIR
        st = self.ctl.status()["item"][0]
        self.assertEqual(st["state"], "dilepas")
        n = len(self.mixer.write_log)
        with self.assertRaises(WriteRefused):
            self.ctl.set_fraction(GAIN, 1.0)
        time.sleep(0.4)
        self.assertEqual(len(self.mixer.write_log), n)               # tidak ada kiriman lagi: tidak berebut

    def test_expires_when_idle_and_keeps_last_value(self):
        t = [1000.0]
        ctl = Controller(Path(self.tmp.name) / "c2.jsonl", lambda a: None, clock=lambda: t[0])
        ctl.enable("127.0.0.1", self.mixer.port)
        ctl.arm(FADER, convert.fader_raw(-10), convert.fader_raw(-4), confirmed=True)
        ctl.set_fraction(FADER, 1.0)
        self.assertTrue(wait_for(lambda: abs(self.val(FADER) - convert.fader_raw(-4)) < 1e-4))
        t[0] += writer.IDLE_S + 1
        self.assertTrue(wait_for(lambda: not ctl.enabled))
        self.assertAlmostEqual(self.val(FADER), convert.fader_raw(-4), places=4)   # nilai dibiarkan, tidak dipulihkan paksa

    def test_restore_all(self):
        a1, t1 = convert.gain_raw(30), convert.gain_raw(24)
        a2, t2 = convert.fader_raw(-10), convert.fader_raw(-4)
        self.ctl.arm(GAIN, a1, t1)
        self.ctl.arm(FADER, a2, t2, confirmed=True)
        self.ctl.set_fraction(GAIN, 1.0)
        self.ctl.set_fraction(FADER, 1.0)
        self.assertTrue(wait_for(lambda: abs(self.val() - t1) < 1e-4 and abs(self.val(FADER) - t2) < 1e-4))
        self.assertEqual(self.ctl.restore_all(), 2)
        self.assertTrue(wait_for(lambda: abs(self.val() - a1) < 1e-4 and abs(self.val(FADER) - a2) < 1e-4))
        self.assertTrue(wait_for(lambda: self.ctl.status()["item"] == []))
        log = (Path(self.tmp.name) / "changes.jsonl").read_text().splitlines()
        self.assertTrue(any(json.loads(l)["event"] == "restore-all" for l in log))


class ServerFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        addrs = ["/info", "/ch/03/config/name", GAIN]
        self.mixer = FakeMixer({"/info": ("x",), "/ch/03/config/name": ("Mic",), GAIN: (convert.gain_raw(30),)}).start()
        self.mixer.meter_generator = lambda: meters.make_blob([-20.0, -20.0, -20.0])
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

    def test_full_flow(self):
        code, st = self.call("/api/control")
        self.assertEqual((code, st["aktif"]), (200, False))
        code, _ = self.call("/api/control/arm", {"alamat": GAIN})
        self.assertEqual(code, 400)                                           # belum dinyalakan
        code, _ = self.call("/api/control/enable", {"scene_saved": True, "paham": True})
        self.assertEqual(code, 400)                                           # live belum berjalan
        self.app.start_live("127.0.0.1", self.mixer.port, "/meters/1")
        self.assertTrue(wait_for(lambda: self.app.live.frames > 3))
        code, _ = self.call("/api/control/enable", {"scene_saved": True})
        self.assertEqual(code, 400)                                           # harus dicentang dua-duanya
        self.app.live._record([Alert(time.monotonic(), "t", "clip", "bad", 2, "CH 03 Mic", -0.4, "x")])
        code, _ = self.call("/api/control/enable", {"scene_saved": True, "paham": True})
        self.assertEqual(code, 200)
        code, e = self.call("/api/control/arm", {"alamat": "/headamp/03/phantom"})
        self.assertEqual(code, 400)                                           # 48V tidak pernah
        self.call("/api/recs")                                                # mendaftarkan usulan gain dari clipping
        code, it = self.call("/api/control/arm", {"alamat": GAIN})
        self.assertEqual(code, 200, it)
        awal = it["item"]["awal"]
        target = it["item"]["target"]
        self.assertAlmostEqual(convert.gain_db(awal), 30, places=1)
        self.assertAlmostEqual(convert.gain_db(target), 26, places=1)
        self.assertEqual(self.mixer.write_attempts, [])
        code, _ = self.call("/api/control/set", {"alamat": GAIN, "p": 1})
        self.assertEqual(code, 200)
        self.assertTrue(wait_for(lambda: abs(self.mixer.state[GAIN][0] - target) < 1e-4))
        time.sleep(0.4)
        code, st = self.call("/api/control")
        self.assertEqual(st["item"][0]["state"], "aktif")                     # gema dari mixer tidak dianggap konflik
        code, _ = self.call("/api/control/release", {"alamat": GAIN, "kembali": True})
        self.assertTrue(wait_for(lambda: abs(self.mixer.state[GAIN][0] - awal) < 1e-4))
        self.assertTrue(wait_for(lambda: self.call("/api/control")[1]["item"] == []))
        self.assertEqual({ad for ad, _ in self.mixer.write_attempts}, {GAIN})
        self.assertTrue((self.data / "changes.jsonl").exists())
        self.assertTrue(list(self.data.glob("control-snapshot-*.json")))


if __name__ == "__main__":
    unittest.main()
