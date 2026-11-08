import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_memori
from aise import addresses, convert, siaran
from aise.fake_mixer import FakeMixer

PROFILE = {"sources": [{"nama": "Mic pengkhotbah", "jenis": "khotbah", "ch": [1]}, {"nama": "Drum elektrik", "jenis": "drum_elektrik", "ch": [2]}]}


def dump(**o):
    v = {"/ch/01/config/name": ["PDT_7"], "/ch/02/config/name": ["Drum_9"], "/ch/01/mix/on": [1], "/ch/02/mix/on": [1],
         "/bus/3/config/name": ["YouTube"], "/bus/3/mix/on": [1], "/bus/3/mix/fader": [convert.fader_raw(-3)],
         "/bus/3/eq/on": [0], "/bus/3/dyn/on": [1],
         "/ch/01/mix/03/level": [convert.fader_raw(-12)], "/ch/02/mix/03/level": [convert.fader_raw(-4)]}
    v.update(o)
    return {"values": v}


def ids(d, bus=3):
    return {f["id"] for f in siaran.findings(d, bus, PROFILE)}


class Rules(unittest.TestCase):
    def test_summary_and_clean_names(self):
        s = siaran.summary(dump(), 3, PROFILE)
        self.assertEqual(s["nama"], "YouTube")
        self.assertEqual([k["nama"] for k in s["kiriman"]], ["Drum", "PDT"])         # urut dari yang tertinggi, akhiran _N dibuang
        self.assertAlmostEqual(s["fader_db"], -3, delta=0.5)
        self.assertIsNone(siaran.summary(None, 3))
        self.assertIsNone(siaran.summary(dump(), 0))

    def test_clean_setup_has_balance_note_and_checklist_only(self):
        got = ids(dump(**{"/ch/02/mix/03/level": [convert.fader_raw(-10)]}))
        self.assertEqual(got, {"siaran-cek"})

    def test_each_problem(self):
        self.assertIn("siaran-fader", ids(dump(**{"/bus/3/mix/fader": [convert.fader_raw(5)]})))
        self.assertIn("siaran-mute", ids(dump(**{"/bus/3/mix/on": [0]})))
        self.assertIn("siaran-dyn", ids(dump(**{"/bus/3/dyn/on": [0]})))
        self.assertIn("siaran-kiriman", ids(dump(**{"/ch/02/mix/03/level": [convert.fader_raw(3)]})))
        eq = dump(**{"/bus/3/eq/on": [1], "/bus/3/eq/1/f": [convert.eq_raw_hz(100)], "/bus/3/eq/1/g": [convert.eq_gain_raw(6)]})
        self.assertIn("siaran-eq", ids(eq))
        self.assertIn("siaran-balance", ids(dump()))                                   # drum -4 dB vs khotbah -12 dB: selisih 8 dB

    def test_muted_channel_not_counted_and_bus_zero_means_none(self):
        self.assertNotIn("siaran-balance", ids(dump(**{"/ch/02/mix/on": [0]})))
        self.assertEqual(siaran.findings(dump(), 0, PROFILE), [])

    def test_stream_bus_detail_addresses_only_for_that_bus(self):
        self.assertIn("/bus/3/dyn/thr", addresses.all_addresses(3))
        self.assertNotIn("/bus/3/dyn/thr", addresses.all_addresses())
        self.assertNotIn("/bus/2/eq/1/g", addresses.all_addresses(3))


class Server(test_memori.MemoriServer):
    test_flow_records_rates_and_clears = None

    def test_prefs_view_and_routing_probe(self):
        self.assertEqual(self.call("/api/status")[1]["stream_bus"], 3)                  # bawaan sesuai keterangan operator
        self.assertEqual(self.call("/api/prefs", {"stream_bus": 9})[0], 400)
        self.assertEqual(self.call("/api/prefs", {"stream_bus": 2})[1]["stream_bus"], 2)
        self.assertEqual(self.call("/api/siaran")[1]["bus"], 2)
        self.call("/api/prefs", {"stream_bus": 3})
        self.mixer.state["/routing/usb/01"] = (3,)
        code, r = self.call("/api/siaran/routing", {})
        self.assertEqual(code, 200)
        self.assertEqual(r["dijawab"], {"/routing/usb/01": [3]})
        self.assertGreaterEqual(r["ditanya"], 10)
        self.assertEqual(self.mixer.write_attempts, [])                                  # hanya membaca


if __name__ == "__main__":
    unittest.main()
