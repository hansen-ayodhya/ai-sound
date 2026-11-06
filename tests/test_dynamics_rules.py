import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import channels, convert, report


def ids(dump, profile=None):
    return {f.id: f for f in report.analyze({"values": dump}, profile)}


class Dynamics(unittest.TestCase):
    def test_comp_off_on_voice_has_start_values(self):
        f = ids({"/ch/01/config/name": ["Vokal 1"], "/ch/01/dyn/on": [0], "/ch/01/dyn/thr": [0.9], "/ch/01/dyn/ratio": [2]})["tanpa-komp-ch01"]
        u = {x["alamat"]: x["usulan"] for x in f.usulan}
        self.assertEqual(u["/ch/01/dyn/on"], 1)
        self.assertEqual(convert.ratio_value(u["/ch/01/dyn/ratio"]), report.COMP_START_RATIO)
        self.assertAlmostEqual(convert.comp_thr_db(u["/ch/01/dyn/thr"]), report.COMP_START_THR_DB, delta=0.01)

    def test_extreme_ratio(self):
        d = {"/ch/02/config/name": ["Singer"], "/ch/02/dyn/on": [1], "/ch/02/dyn/ratio": [convert.ratio_index_for(20)]}
        f = ids(d)["rasio-tinggi-ch02"]
        self.assertEqual(convert.ratio_value(f.usulan[0]["usulan"]), report.COMP_TARGET_RATIO)
        d["/ch/02/dyn/ratio"] = [convert.ratio_index_for(4)]
        self.assertNotIn("rasio-tinggi-ch02", ids(d))

    def test_tight_gate_on_speech(self):
        d = {"/ch/04/config/name": ["Khotbah"], "/ch/04/gate/on": [1], "/ch/04/gate/thr": [convert.gate_thr_raw(-20)]}
        f = ids(d)["gate-ketat-ch04"]
        self.assertIn("terputus", f.penyebab)
        self.assertAlmostEqual(convert.gate_thr_db(f.usulan[0]["usulan"]), report.GATE_VOICE_TARGET_DB, delta=0.01)
        d["/ch/04/gate/thr"] = [convert.gate_thr_raw(-60)]
        self.assertNotIn("gate-ketat-ch04", ids(d))

    def test_gate_on_line_source_via_profile(self):
        p = channels.validate({"sources": [{"nama": "Keyboard utama", "jenis": "keyboard", "ch": [7]}]})
        f = ids({"/ch/07/config/name": ["Ch 7"], "/ch/07/gate/on": [1]}, p)["gate-line-ch07"]
        self.assertEqual(f.usulan[0]["usulan"], 0)

    def test_fx_send_on_speech(self):
        d = {"/ch/09/config/name": ["Pdt"], "/ch/09/mix/07/level": [convert.fader_raw(-5)],
             "/ch/09/mix/08/level": [convert.fader_raw(-40)]}
        got = ids(d)
        self.assertIn("fx1-khotbah-ch09", got)
        self.assertNotIn("fx2-khotbah-ch09", got)
        self.assertEqual(got["fx1-khotbah-ch09"].usulan[0]["usulan"], 0.0)
        self.assertEqual(got["fx1-khotbah-ch09"].ch, 9)

    def test_fx_rule_not_for_singers(self):
        self.assertFalse([k for k in ids({"/ch/01/config/name": ["Vokal 1"], "/ch/01/mix/07/level": [0.9]}) if k.startswith("fx")])


class ConvertDyn(unittest.TestCase):
    def test_fader_roundtrip_and_ratio(self):
        for db in (-80, -45, -25, -10, 0, 8):
            self.assertAlmostEqual(convert.fader_db(convert.fader_raw(db)), db, places=6)
        self.assertEqual(convert.fader_raw(float("-inf")), 0.0)
        self.assertEqual(convert.ratio_value(6), 4.0)
        self.assertEqual(convert.ratio_value(0.5), 4.0)
        self.assertIsNone(convert.ratio_value(None))


if __name__ == "__main__":
    unittest.main()
