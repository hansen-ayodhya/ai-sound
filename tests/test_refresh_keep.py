import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise.fake_mixer import FakeMixer
from aise.server import ROOT, App

THR, RAT = "/ch/01/dyn/thr", "/ch/01/dyn/ratio"


class RefreshKeepsKnownValues(unittest.TestCase):
    def test_lost_reply_does_not_wipe_value_and_address_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp, FakeMixer({THR: (0.63,), RAT: (3,)}) as mixer:
            app = App(root=ROOT, data=Path(tmp) / "data", addresses=[THR, RAT])
            d1 = app._read_settings("127.0.0.1", mixer.port, None)
            self.assertAlmostEqual(d1["values"][THR][0], 0.63, places=5)
            gone = mixer.state.pop(THR)                                   # satu pembacaan: balasan threshold hilang
            d2 = app._read_settings("127.0.0.1", mixer.port, None)
            self.assertAlmostEqual(d2["values"][THR][0], 0.63, places=5)  # nilai lama dipertahankan, bukan kosong
            self.assertEqual(d2["values"][RAT], [3])
            mixer.state[THR] = (0.5,)                                     # pulih: nilai baru terbaca lagi (alamat tidak dibuang)
            d3 = app._read_settings("127.0.0.1", mixer.port, None)
            self.assertEqual(d3["values"][THR], [0.5])

    def test_address_that_never_answers_is_skipped_after_three_reads(self):
        with tempfile.TemporaryDirectory() as tmp, FakeMixer({RAT: (3,)}) as mixer:
            app = App(root=ROOT, data=Path(tmp) / "data", addresses=[THR, RAT])
            for _ in range(3):
                app._read_settings("127.0.0.1", mixer.port, None)
            self.assertEqual(app._fails[THR], 3)
            n = mixer.queries.count(THR)
            app._read_settings("127.0.0.1", mixer.port, None)
            self.assertEqual(mixer.queries.count(THR), n)                 # tidak ditanyakan lagi


if __name__ == "__main__":
    unittest.main()


class ManualRefreshRetriesSkipped(unittest.TestCase):
    def test_refresh_settings_clears_skipped_addresses(self):
        with tempfile.TemporaryDirectory() as tmp, FakeMixer({RAT: (3,)}) as mixer:
            app = App(root=ROOT, data=Path(tmp) / "data", addresses=[THR, RAT])
            (app.data).mkdir(parents=True, exist_ok=True)
            (app.data / "mixer-ip.txt").write_text("127.0.0.1")
            app.save_port(mixer.port)
            for _ in range(3):
                app._read_settings("127.0.0.1", mixer.port, None)
            self.assertEqual(app._fails[THR], 3)
            mixer.state[THR] = (0.4,)                                     # mixer akhirnya menjawab
            d = app.refresh_settings()
            self.assertEqual(d["mode"], "sekarang")
            self.assertAlmostEqual(app._values[THR][0], 0.4, places=5)
