import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import convert, report


class Convert(unittest.TestCase):
    def test_roundtrips(self):
        for hz in (40, 100, 120, 300):
            self.assertAlmostEqual(convert.hpf_hz(convert.hpf_raw(hz)), hz, places=6)
        for hz in (100, 1000, 3000):
            self.assertAlmostEqual(convert.eq_hz(convert.eq_raw_hz(hz)), hz, places=6)
        for db in (-12, 0, 3, 9):
            self.assertAlmostEqual(convert.eq_gain_db(convert.eq_gain_raw(db)), db, places=6)

    def test_clamped(self):
        self.assertEqual(convert.eq_gain_raw(40), 1.0)
        self.assertEqual(convert.hpf_raw(5), 0.0)
        self.assertFalse(convert.VERIFIED)


def dump(**v):
    return {"values": v}


class Usulan(unittest.TestCase):
    def test_hpf_vocal_100_speech_120(self):
        d = dump(**{"/ch/01/config/name": ["Vokal 1"], "/ch/01/preamp/hpon": [0], "/ch/01/preamp/hpf": [0.2],
                    "/ch/02/config/name": ["Khotbah"], "/ch/02/preamp/hpon": [0], "/ch/02/preamp/hpf": [0.2]})
        f = {x.id: x for x in report.analyze(d)}
        u1 = f["hpf-mati-ch01"].usulan
        self.assertEqual(u1[0]["usulan"], 1)
        self.assertAlmostEqual(convert.hpf_hz(u1[1]["usulan"]), 100, delta=1)
        u2 = f["hpf-mati-ch02"].usulan
        self.assertAlmostEqual(convert.hpf_hz(u2[1]["usulan"]), 120, delta=1)

    def test_big_boost_on_voice(self):
        d = dump(**{"/ch/03/config/name": ["Vokal 3"], "/ch/03/eq/on": [1],
                    "/ch/03/eq/2/g": [convert.eq_gain_raw(9.0)], "/ch/03/eq/2/f": [convert.eq_raw_hz(3000)],
                    "/ch/03/eq/3/g": [convert.eq_gain_raw(4.0)], "/ch/03/eq/3/f": [convert.eq_raw_hz(800)]})
        ids = [x.id for x in report.analyze(d)]
        self.assertIn("boost-b2-ch03", ids)
        self.assertNotIn("boost-b3-ch03", ids)  # +4 dB masih wajar
        f = next(x for x in report.analyze(d) if x.id == "boost-b2-ch03")
        self.assertEqual(f.ch, 3)
        self.assertAlmostEqual(convert.eq_gain_db(f.usulan[0]["usulan"]), report.BOOST_TARGET_DB, delta=0.01)
        self.assertIn("kHz", f.usulan[0]["teks"])

    def test_no_boost_rule_when_eq_off_or_not_voice(self):
        base = {"/ch/03/eq/2/g": [convert.eq_gain_raw(12.0)], "/ch/03/eq/2/f": [convert.eq_raw_hz(3000)]}
        d1 = dump(**{"/ch/03/config/name": ["Vokal 3"], "/ch/03/eq/on": [0], **base})
        d2 = dump(**{"/ch/03/config/name": ["Gitar"], "/ch/03/eq/on": [1], **base})
        for d in (d1, d2):
            self.assertFalse([x for x in report.analyze(d) if x.id.startswith("boost")])

    def test_report_lists_usulan(self):
        d = dump(**{"/ch/01/config/name": ["Vokal 1"], "/ch/01/preamp/hpon": [0], "/ch/01/preamp/hpf": [0.2]})
        self.assertIn("Usulan nilai", report.render(d))


if __name__ == "__main__":
    unittest.main()
