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


class LrEq(test_memori.MemoriServer):
    test_flow_records_rates_and_clears = None

    def test_lr_eq_endpoint_reads_six_bands(self):
        from aise import convert as cv
        v = self.mixer.state
        v["/lr/eq/on"] = (1,)
        for b, (t, hz, db) in enumerate([(0, 80, 0), (2, 200, 3), (2, 1000, 0), (2, 3000, -2), (2, 6000, 0), (4, 10000, 2)], 1):
            v[f"/lr/eq/{b}/type"], v[f"/lr/eq/{b}/f"] = (t,), (cv.eq_raw_hz(hz),)
            v[f"/lr/eq/{b}/g"], v[f"/lr/eq/{b}/q"] = (cv.eq_gain_raw(db),), (0.5,)
        self.assertFalse(self.call("/api/lr-eq")[1]["tersedia"])                   # belum terbaca
        self.app.addresses = [a for a in addresses.main_addresses()]
        self.app._read_settings("127.0.0.1", self.mixer.port, None)
        r = self.call("/api/lr-eq")[1]
        self.assertTrue(r["tersedia"])
        self.assertEqual(len(r["band"]), 6)
        self.assertEqual(r["on"], 1)
        self.assertEqual((r["band"][1]["hz"], r["band"][1]["db"], r["band"][0]["type"]), (200, 3.0, 0))
        self.assertIn("/lr/eq/6/g", addresses.main_addresses())
        self.assertNotIn("/lr/eq/7/g", addresses.main_addresses())


class MicCheck(test_memori.MemoriServer):
    test_flow_records_rates_and_clears = None

    def test_mic_check_reads_channel_setup_and_features_listed(self):
        from aise import convert as cv
        from aise.live import LiveMonitor
        self.app._values = {"/ch/12/config/name": ["md_27"], "/headamp/12/gain": [cv.gain_raw(24)], "/headamp/12/phantom": [1],
                            "/ch/12/mix/fader": [cv.fader_raw(-6)], "/ch/12/mix/on": [1], "/ch/12/mix/lr": [1], "/ch/12/eq/on": [1],
                            "/ch/12/preamp/hpon": [0], "/ch/12/mix/03/level": [cv.fader_raw(-5)], "/ch/12/mix/01/level": [0.0]}
        self.app.live = LiveMonitor("127.0.0.1", 1, "/meters/1", {}, self.data / "live.jsonl")
        self.app.live.running = True
        self.app.live.levels = [-90.0] * 11 + [-28.0] + [-90.0] * 4
        self.app.live.peaks = [-90.0] * 11 + [-12.0] + [-90.0] * 4
        code, m = self.call("/api/mic-check?ch=12")
        self.assertEqual(code, 200)
        self.assertEqual((m["nama"], m["phantom"], m["lr"], m["eq_on"], m["hpf_on"], m["ada_data"]), ("md", 1, 1, 1, 0, True))
        self.assertAlmostEqual(m["gain_db"], 24, delta=0.2)
        self.assertEqual([b["bus"] for b in m["bus"]], [3])                             # kiriman -∞ ke Bus 1 diabaikan
        self.assertEqual((m["level_db"], m["puncak_db"], m["live"]), (-28.0, -12.0, True))
        self.assertFalse(self.call("/api/mic-check?ch=5")[1]["ada_data"])               # channel tanpa data terbaca
        self.assertEqual(self.call("/api/mic-check?ch=0")[0], 400)
        self.assertEqual(self.call("/api/mic-check?ch=abc")[0], 400)
        fitur = self.call("/api/status")[1]["fitur"]
        for k in ("ukur_realtime", "tampilan_bar", "pra_cek_mic", "kesiapan", "cek_balance"):
            self.assertIn(k, fitur)
