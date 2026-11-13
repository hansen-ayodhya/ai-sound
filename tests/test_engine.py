"""Mesin analisa realtime: tiap aturan diuji dengan meter buatan dan jam buatan (tanpa jaringan)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import convert
from aise.engine import HOLD_S, Engine

PROFILE = {"sources": [{"nama": "Vokal", "jenis": "vokal_utama", "ch": [1]}, {"nama": "Keyboard", "jenis": "keyboard", "ch": [5]}]}
SPEECH = [-7.0, -15.0, -25.0, -60.0, -12.0, -30.0]      # naik-turun seperti orang bicara


def settings(gain_db=30.0, fader_db=0.0):
    v = {}
    for ch in range(1, 17):
        v[f"/headamp/{ch:02d}/gain"] = [convert.gain_raw(gain_db)]
        v[f"/ch/{ch:02d}/mix/fader"] = [convert.fader_raw(fader_db)]
        v[f"/ch/{ch:02d}/mix/on"] = [1]
        v[f"/ch/{ch:02d}/dyn/on"] = [0]
    return v


class Rig:
    def __init__(self, sens="tinggi", profile=PROFILE, **kw):
        self.t = 1000.0
        self.values = settings(**kw)
        self.eng = Engine(lambda: self.values, lambda: profile, sens=sens, clock=lambda: self.t)
        self.n = 0

    def play(self, seconds, **per_ch):
        """per_ch: ch3=[pola level] atau ch3=-20.0; channel lain hening (-90)."""
        for _ in range(int(seconds * 20)):
            lv = [-90.0] * 16
            for k, pat in per_ch.items():
                lv[int(k[2:]) - 1] = pat[self.n % len(pat)] if isinstance(pat, list) else pat
            self.eng.feed(lv, self.t)
            self.n += 1
            self.t += 0.05

    def ids(self):
        return [f["id"] for f in self.eng.findings()]

    def get(self, fid):
        return next(f for f in self.eng.findings() if f["id"] == fid)


class Panas(unittest.TestCase):
    def test_hot_before_clipping_suggests_lower_gain(self):
        r = Rig()
        r.play(6, ch3=SPEECH)
        f = r.get("mesin-panas-ch3")
        u = f["usulan"][0]
        self.assertEqual(u["alamat"], "/headamp/03/gain")
        self.assertAlmostEqual(convert.gain_db(u["usulan"]), 27.0, places=1)     # puncak -7 -> menuju -10: turun 3 dB
        self.assertLess(u["usulan"], u["sekarang"])
        self.assertIn("perkiraan", f["data"])

    def test_needs_to_persist_and_respects_sensitivity(self):
        r = Rig()
        r.play(2.2, ch3=SPEECH)
        self.assertEqual(r.ids(), [])                       # belum bertahan cukup lama
        low = Rig(sens="rendah")
        low.play(10, ch3=SPEECH)
        self.assertEqual(low.ids(), [])                     # -7 dB belum dianggap panas pada kepekaan rendah

    def test_stale_meter_gives_nothing(self):
        r = Rig()
        r.play(6, ch3=SPEECH)
        self.assertTrue(r.ids())
        r.t += 3.0
        self.assertEqual(r.ids(), [])

    def test_target_is_frozen_then_dropped_when_gain_changes(self):
        r = Rig()
        r.play(6, ch3=SPEECH)
        first = r.get("mesin-panas-ch3")["usulan"][0]["usulan"]
        r.play(3, ch3=[-5.0, -15.0, -60.0])                 # makin panas, usulan tidak berubah-ubah
        self.assertEqual(r.get("mesin-panas-ch3")["usulan"][0]["usulan"], first)
        r.values["/headamp/03/gain"] = [first]              # operator/aplikasi menurunkan gain
        r.play(1, ch3=[-10.0, -18.0, -60.0])
        self.assertEqual(r.ids(), [])                       # diamati dari awal dengan gain baru

    def test_finding_lingers_briefly_after_condition_clears(self):
        r = Rig()
        r.play(6, ch3=SPEECH)
        r.play(HOLD_S - 2, ch3=[-20.0, -30.0, -60.0])
        self.assertIn("mesin-panas-ch3", r.ids())
        r.play(12, ch3=[-20.0, -30.0, -60.0])
        self.assertEqual(r.ids(), [])


class Struktur(unittest.TestCase):
    def test_low_gain_with_pushed_fader(self):
        r = Rig(gain_db=10.0, fader_db=8.0)
        r.play(30, ch4=[-30.0, -38.0, -45.0, -80.0])
        f = r.get("mesin-struktur-ch4")
        g, fd = f["usulan"]
        up = convert.gain_db(g["usulan"]) - 10.0
        self.assertGreaterEqual(up, 3.0)
        self.assertAlmostEqual(convert.fader_db(fd["usulan"]), 8.0 - up, places=1)      # volume di speaker tetap

    def test_fader_near_zero_is_fine(self):
        r = Rig(gain_db=10.0, fader_db=0.0)
        r.play(30, ch4=[-30.0, -38.0, -45.0, -80.0])
        self.assertEqual(r.ids(), [])


class Kompresor(unittest.TestCase):
    def test_threshold_far_above_signal(self):
        r = Rig()
        r.values["/ch/04/dyn/on"] = [1]
        r.values["/ch/04/dyn/thr"] = [convert.comp_thr_raw(-5.0)]
        r.play(30, ch4=[-30.0, -38.0, -45.0, -80.0])
        f = r.get("mesin-kompresor-ch4")
        self.assertLess(convert.comp_thr_db(f["usulan"][0]["usulan"]), -25.0)
        r2 = Rig()
        r2.values["/ch/04/dyn/on"] = [1]
        r2.values["/ch/04/dyn/thr"] = [convert.comp_thr_raw(-32.0)]
        r2.play(30, ch4=[-30.0, -38.0, -45.0, -80.0])
        self.assertEqual(r2.ids(), [])


class Feedback(unittest.TestCase):
    def test_level_rises_then_stays_flat_on_vocal_mic(self):
        r = Rig()
        r.play(6, ch1=[-25.0, -35.0, -60.0, -28.0])
        r.play(2.0, ch1=[-12.0, -12.3])
        f = r.get("mesin-feedback-ch1")
        u = f["usulan"][0]
        self.assertEqual(u["alamat"], "/ch/01/mix/fader")
        self.assertAlmostEqual(convert.fader_db(u["usulan"]), -3.0, places=1)

    def test_not_for_instruments_or_steady_sound_from_the_start(self):
        r = Rig()
        r.play(6, ch5=[-25.0, -35.0, -60.0, -28.0])
        r.play(2.0, ch5=[-12.0, -12.3])                     # keyboard menahan nada: wajar
        self.assertNotIn("mesin-feedback-ch5", r.ids())
        r2 = Rig()
        r2.play(10, ch1=[-12.0, -12.3])                     # rata sejak awal (mis. pink noise): bukan pola feedback
        self.assertNotIn("mesin-feedback-ch1", r2.ids())

    def test_normal_speech_is_not_feedback(self):
        r = Rig()
        r.play(12, ch1=[-12.0, -20.0, -35.0, -60.0, -15.0])
        self.assertNotIn("mesin-feedback-ch1", r.ids())


class Tenggelam(unittest.TestCase):
    VOC = [-25.0, -27.0, -30.0, -26.0]
    MUS = [-13.0, -14.0, -15.0, -13.5]

    def test_music_over_vocal_suggests_lowering_music(self):
        r = Rig()
        r.play(12, ch1=self.VOC, ch5=self.MUS)
        f = r.get("mesin-tenggelam-ch5")
        self.assertAlmostEqual(convert.fader_db(f["usulan"][0]["usulan"]), -2.0, places=1)
        b = r.eng.brief()["balance"]
        self.assertLess(b["selisih"], -6.0)
        self.assertNotIn("mesin-tenggelam-ch1", r.ids())

    def test_operator_reference_replaces_generic_threshold(self):
        r = Rig()
        r.play(3, ch1=self.VOC, ch5=self.MUS)
        r.eng.findings()
        r.eng.acuan_db = r.eng.brief()["balance"]["selisih"]         # operator: "balance sekarang pas"
        r.play(12, ch1=self.VOC, ch5=self.MUS)
        self.assertEqual(r.ids(), [])
        r.play(12, ch1=self.VOC, ch5=[v + 4.0 for v in self.MUS])    # musik naik 4 dB dari acuan
        self.assertIn("mesin-tenggelam-ch5", r.ids())

    def test_muted_or_silent_music_is_ignored(self):
        r = Rig()
        r.values["/ch/05/mix/on"] = [0]
        r.play(12, ch1=self.VOC, ch5=self.MUS)
        self.assertEqual(r.ids(), [])
        self.assertIsNone(r.eng.brief()["balance"])

    def test_no_profile_no_balance(self):
        r = Rig(profile=None)
        r.play(12, ch1=self.VOC, ch5=self.MUS)
        self.assertEqual(r.ids(), [])


class Misc(unittest.TestCase):
    def test_bad_sensitivity_rejected(self):
        with self.assertRaises(ValueError):
            Rig().eng.set_sens("gila")

    def test_engine_never_raises_on_odd_values(self):
        r = Rig()
        r.values["/headamp/03/gain"] = ["x"]
        r.values["/ch/03/mix/fader"] = [None]
        r.play(6, ch3=SPEECH)
        self.assertEqual(r.ids(), [])


if __name__ == "__main__":
    unittest.main()
