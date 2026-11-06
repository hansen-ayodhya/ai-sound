import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import menu
from aise.audit import MixerNotAnswering, run_audit
from aise.fake_mixer import FakeMixer

STATE = {
    "/info": ("V1.0", "MR18", "1.23"),
    "/ch/01/config/name": ("Vokal 1",),
    "/ch/01/preamp/hpon": (0,),
}
ADDRS = list(STATE) + ["/ch/02/config/name"]


class Audit(unittest.TestCase):
    def test_full_audit_writes_files_and_no_writes_to_mixer(self):
        with FakeMixer(STATE) as mixer, tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "data"
            res = run_audit("127.0.0.1", mixer.port, base=base, addresses=ADDRS, timeout=0.1)
            self.assertTrue(res["identical"])
            self.assertEqual(res["answered"], 3)
            self.assertGreaterEqual(res["findings"], 1)  # HPF mati di Vokal 1
            folder = Path(res["folder"])
            for name in ("dump1.json", "dump2.json", "report.md", "osc_log.jsonl"):
                self.assertTrue((folder / name).exists(), name)
            latest = json.loads((base / "latest.json").read_text())
            self.assertTrue(latest["dump"].startswith("data/audit-"))
            self.assertTrue((Path(tmp) / latest["dump"]).exists())
            self.assertEqual(mixer.write_attempts, [])

    def test_aborts_quickly_when_mixer_silent(self):
        with FakeMixer({}) as mixer, tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MixerNotAnswering):
                run_audit("127.0.0.1", mixer.port, base=Path(tmp) / "data", addresses=ADDRS, timeout=0.1)
            self.assertEqual(mixer.write_attempts, [])
            self.assertEqual(sorted(set(mixer.queries)), ["/ch/01/config/name", "/info"])


class MenuHelpers(unittest.TestCase):
    def test_valid_ip(self):
        self.assertTrue(menu.valid_ip("192.168.1.50"))
        for bad in ("", "abc", "192.168.1", "300.1.1.1", "192.168.1.50; del"):
            self.assertFalse(menu.valid_ip(bad), bad)

    def test_ip_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x" / "ip.txt"
            self.assertEqual(menu.load_ip(p), "")
            menu.save_ip("10.0.0.5", p)
            self.assertEqual(menu.load_ip(p), "10.0.0.5")


if __name__ == "__main__":
    unittest.main()
