import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import balance, convert

PROFILE = {"sources": [{"nama": "Mic pengkhotbah", "jenis": "khotbah", "ch": [1]}, {"nama": "Drum elektrik", "jenis": "drum_elektrik", "ch": [2]},
                       {"nama": "Keyboard", "jenis": "keyboard", "ch": [3]}, {"nama": "Lainnya", "jenis": "lainnya", "ch": [4]}]}
MIC = 12


def values(**ov):
    v = {f"/ch/{n:02d}/mix/fader": [convert.fader_raw(-10)] for n in (1, 2, 3, MIC)}
    v.update({f"/ch/{n:02d}/mix/on": [1] for n in (1, 2, 3)})
    v[f"/headamp/{MIC:02d}/gain"] = [convert.gain_raw(20)]
    v.update(ov)
    return v


class Pure(unittest.TestCase):
    def test_active_level_ignores_pauses_and_noise_is_median(self):
        speech = [-20.0, -90.0, -20.0, -90.0, -20.0, -90.0, -20.0, -90.0]
        self.assertAlmostEqual(balance.active_db(speech), -20.0, delta=0.1)
        self.assertLess(balance.noise_db(speech + [-90.0] * 4), -60)
        self.assertIsNone(balance.active_db([]))

    def test_detect_channel(self):
        quiet = [-70.0] * 80
        tap = [-70.0] * 60 + [-20.0] * 20
        ok = balance.detect_channel({1: quiet, 2: quiet, 12: tap})
        self.assertEqual(ok["ch"], 12)
        self.assertIsNone(balance.detect_channel({1: quiet, 12: quiet})["ch"])                # tidak ada yang naik
        self.assertIsNone(balance.detect_channel({1: tap, 12: tap})["ch"])                     # dua channel naik bersamaan: ambigu
        self.assertIsNone(balance.detect_channel({})["ch"])

    def test_groups(self):
        self.assertEqual(balance.groups(PROFILE), ([1], [2, 3]))
        self.assertEqual(balance.groups(None), ([], []))

    def test_recommend_uses_learned_target_and_caps(self):
        self.assertEqual(balance.recommend(-4.0, "musik_keras", None)["db"], 3.0)
        r = balance.recommend(-4.0, "musik_keras", 2.0)                                       # target +2: perlu turun 6 dB
        self.assertEqual((r["turunkan"], r["db"]), ("musik", 6.0))
        self.assertEqual(balance.recommend(-30.0, "musik_keras", 10.0)["db"], balance.MAX_CUT_DB)
        self.assertEqual(balance.recommend(5.0, "vokal_keras", 2.0)["turunkan"], "vokal")
        self.assertIsNone(balance.recommend(0.0, "pas", None)["turunkan"])

    def test_findings_lower_music_faders_equally_skip_muted(self):
        res = {"selisih": -6.0, "noise": -60.0}
        v = values(**{"/ch/03/mix/on": [0]})
        f = balance.findings(res, balance.recommend(-6.0, "musik_keras", None), PROFILE, v)
        self.assertEqual([x["id"] for x in f], ["balance-musik-ch2"])                          # keyboard di-mute: dilewati
        u = f[0]["usulan"][0]
        self.assertEqual(u["alamat"], "/ch/02/mix/fader")
        self.assertAlmostEqual(convert.fader_db(u["usulan"]), -13.0, delta=0.3)
        self.assertEqual(balance.findings(res, balance.recommend(0, "pas", None), PROFILE, v), [])

    def test_evaluate_warnings(self):
        ph = {"hening": {"noise": -60.0, "aktif": -58.0, "puncak": -50.0}, "vokal": {"aktif": -30.0, "noise": -60.0, "puncak": -20.0},
              "musik": {"aktif": -24.0, "noise": -60.0, "puncak": -0.5}, "bersama": {"aktif": -28.0, "noise": -59.0, "puncak": -10.0}}
        r = balance.evaluate(ph, [])
        self.assertEqual(r["selisih"], -6.0)
        self.assertEqual(r["snr_vokal"], 30.0)
        self.assertTrue(any("clipping" in w for w in r["peringatan"]))
        self.assertTrue(any("lebih pelan dari tahap tunggal" in w for w in r["peringatan"]))
        self.assertTrue(any("terlalu pelan" in w for w in balance.evaluate({**ph, "vokal": {"aktif": -57.0, "noise": -60.0, "puncak": -40.0}}, [])["peringatan"]))
        self.assertIn("Data belum lengkap.", balance.evaluate({}, [])["peringatan"])


class SessionFlow(unittest.TestCase):
    def make(self, check_map=True, vals=None):
        self.cur = {"fase": "peta", "n": 0}
        def levels():
            f, lv = self.cur["fase"], [-90.0] * 16
            self.cur["n"] += 1
            if f == "peta":
                lv[MIC - 1] = -20.0 if self.cur["n"] % 10 < 2 else -70.0                      # ketukan: lonjakan singkat
            elif f == "hening":
                lv[MIC - 1] = -62.0
            elif f == "vokal":
                lv[0], lv[MIC - 1] = -15.0, -30.0
            elif f == "musik":
                lv[1], lv[2], lv[MIC - 1] = -12.0, -14.0, -24.0
            else:
                lv[0], lv[1], lv[2], lv[MIC - 1] = -15.0, -12.0, -14.0, -22.0
            return lv
        self.vals = vals or values()
        return balance.Session(levels, lambda: self.vals, PROFILE, MIC, check_map=check_map, scale=0.02, sleep=lambda s: time.sleep(min(s, 0.002)))

    def run_all(self, s):
        for _ in range(8):
            if s.status != "menunggu":
                break
            self.cur["fase"] = s.fase
            s.mulai()
            s._thread.join(5)

    def test_full_session_and_verdict(self):
        s = self.make(check_map=False)
        self.assertEqual([s.fase], ["hening"])
        for _ in range(4):
            self.assertEqual(s.status, "menunggu")
            self.cur["fase"] = s.fase
            s.mulai()
            with self.assertRaises(ValueError):
                s.mulai()                                                                     # tidak bisa dimulai dua kali
            s._thread.join(5)
        self.assertEqual(s.status, "selesai", s.pesan)
        h = s.hasil
        self.assertAlmostEqual(h["selisih"], -6.0, delta=0.6)                                 # vokal -30 vs musik -24
        self.assertAlmostEqual(h["noise"], -62.0, delta=0.6)
        self.assertEqual(h["peringatan"], [])
        rec = s.nilai("musik_keras", None)
        self.assertEqual((rec["turunkan"], rec["db"]), ("musik", 3.0))
        with self.assertRaises(ValueError):
            s.nilai("apa-saja", None)

    def test_map_step_detects_and_rejects_wrong_channel(self):
        s = self.make(check_map=True)
        self.cur["fase"] = "peta"
        s.mulai(); s._thread.join(5)
        self.assertEqual((s.status, s.fase), ("menunggu", "hening"))                           # pemetaan cocok: lanjut
        k = {"n": 0}
        def tap5():
            k["n"] += 1
            return [(-20.0 if k["n"] % 10 < 2 else -70.0) if i == 4 else -90.0 for i in range(16)]
        bad = balance.Session(tap5, lambda: values(), PROFILE, MIC, scale=0.02,
                              sleep=lambda s: time.sleep(min(s, 0.002)))
        bad.mulai(); bad._thread.join(5)
        self.assertEqual(bad.status, "galat")
        self.assertIn("CH 05", bad.pesan)
        bad.ulangi_tahap()
        self.assertEqual(bad.status, "menunggu")

    def test_warns_when_fader_changes_or_other_source_bleeds(self):
        s = self.make(check_map=False)
        def drift():
            self.vals["/ch/02/mix/fader"] = [convert.fader_raw(-5)]
            return self.vals
        s.get_values = lambda: drift() if s.fase == "musik" and s.status == "merekam" else self.vals
        # musik masih terdengar saat tahap vokal: channel musik bergerak
        orig = s.get_levels
        s.get_levels = lambda: [(-13.0 if i == 1 else v) for i, v in enumerate(orig())] if s.fase == "vokal" else orig()
        self.run_all(s)
        self.assertTrue(any("Musik masih terdengar" in n for n in s.notes), s.notes)

    def test_abort(self):
        s = self.make(check_map=False)
        self.cur["fase"] = s.fase
        s.mulai(); s.batal(); s._thread.join(5)
        self.assertEqual(s.status, "batal")


class ServerBalance(unittest.TestCase):
    def setUp(self):
        import json, tempfile, threading, urllib.error, urllib.request
        from aise.live import LiveMonitor
        from aise.server import ROOT, App, serve
        self.json, self.urlerr, self.urlreq = json, urllib.error, urllib.request
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.app = App(root=ROOT, data=self.data)
        self.app.balance_scale = 0.02
        self.app._values = values(**{"/ch/12/config/name": ["md_27"]})
        self.mon = LiveMonitor("127.0.0.1", 1, "/meters/1", {}, self.data / "live.jsonl")
        self.mon.running = True
        self.mon.levels = [-90.0] * 16
        probe = serve(App(root=ROOT, data=self.data), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.app.channels_path = None

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None):
        r = self.urlreq.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else self.json.dumps(body).encode(), method="GET" if body is None else "POST")
        r.add_header("X-AISE-Token", self.app.token); r.add_header("Content-Type", "application/json")
        try:
            with self.urlreq.urlopen(r, timeout=20) as resp:
                return resp.status, self.json.loads(resp.read())
        except self.urlerr.HTTPError as e:
            return e.code, self.json.loads(e.read())

    def test_full_flow_creates_appliable_findings_and_learns_target(self):
        import threading
        code, v = self.call("/api/balance")
        self.assertEqual((code, v["saran_mic"], v["profil_ok"], v["live"]), (200, 12, False, False))                  # "md_27" dikenali sebagai MD
        self.assertEqual(self.call("/api/balance/start", {"mic_ch": 12})[0], 400)                                    # live belum jalan
        self.app.live = self.mon
        self.assertEqual(self.call("/api/balance/start", {"mic_ch": 12})[0], 400)                                    # Daftar Channel kosong
        self.app.profile = lambda: PROFILE
        self.assertEqual(self.call("/api/balance/start", {"mic_ch": 99})[0], 400)
        self.assertEqual(self.call("/api/balance/start", {"mic_ch": 12, "lewati_peta": True})[0], 200)
        stop = threading.Event()
        def feed():
            while not stop.is_set():
                f = self.app.balance.fase if self.app.balance else ""
                lv = [-90.0] * 16
                if f == "hening":
                    lv[MIC - 1] = -62.0
                elif f == "vokal":
                    lv[0], lv[MIC - 1] = -15.0, -30.0
                elif f == "musik":
                    lv[1], lv[2], lv[MIC - 1] = -12.0, -14.0, -24.0
                else:
                    lv[0], lv[1], lv[MIC - 1] = -15.0, -12.0, -22.0
                self.mon.levels = lv
                time.sleep(0.002)
        threading.Thread(target=feed, daemon=True).start()
        try:
            for _ in range(4):
                self.assertEqual(self.call("/api/balance/mulai", {})[0], 200)
                end = time.monotonic() + 10
                while time.monotonic() < end and self.app.balance.status != "menunggu" and self.app.balance.status != "selesai":
                    time.sleep(0.02)
        finally:
            stop.set()
        s = self.call("/api/balance")[1]["sesi"]
        self.assertEqual(s["status"], "selesai", s)
        self.assertAlmostEqual(s["hasil"]["selisih"], -6.0, delta=0.8)
        self.assertEqual(self.call("/api/balance/nilai", {"penilaian": "x"})[0], 400)
        code, r = self.call("/api/balance/nilai", {"penilaian": "musik_keras"})
        self.assertEqual(code, 200)
        fnd = self.app.live_findings({})
        self.assertEqual([f["id"] for f in fnd], ["balance-musik-ch2", "balance-musik-ch3"])                          # fader musik, bukan vokal
        self.assertEqual(fnd[0]["usulan"][0]["alamat"], "/ch/02/mix/fader")
        self.app._values["/ch/02/mix/fader"] = [convert.fader_raw(-13)]                                              # operator sudah menurunkan
        self.assertEqual([f["id"] for f in self.app.live_findings({})], ["balance-musik-ch3"])                       # yang tercapai dilepas
        prof = self.app.memori.profil()["balance_vokal_vs_musik"]
        self.assertEqual((prof["jumlah"], prof["target_pas_db"]), (1, None))                                         # belum ada penilaian "pas"
        self.app.memori.catat("balance", selisih=3.0, noise=-60.0, verdict="pas", mic_ch=12)
        self.assertEqual(self.app.memori.profil()["balance_vokal_vs_musik"]["target_pas_db"], 3.0)


if __name__ == "__main__":
    unittest.main()
