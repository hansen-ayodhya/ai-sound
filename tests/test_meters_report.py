import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import guard, meters, monitor, report
from aise.client import ReadOnlyClient
from aise.fake_mixer import FakeMixer
from aise.guard import ReadOnlyViolation
from aise.osc import encode


class MeterGuard(unittest.TestCase):
    def test_allows_valid_meter_request(self):
        guard.check("/meters", ("/meters/1",))
        guard.check("/meters", ("/meters/1", 0, 0, 10))
        guard.check_bytes(encode("/meters", ("/meters/1",)))

    def test_blocks_bad_meter_requests(self):
        for args in [(), ("/meters/abc",), ("/ch/01/mix/fader",), (1,), ("/meters/1", "x"),
                     ("/meters/1", -1), ("/meters/1", 99999), ("/meters/1", 1, 1, 1, 1)]:
            with self.assertRaises(ReadOnlyViolation, msg=str(args)):
                guard.check("/meters", args)

    def test_other_writes_still_blocked(self):
        with self.assertRaises(ReadOnlyViolation):
            guard.check("/ch/01/mix/fader", (0.5,))
        with self.assertRaises(ReadOnlyViolation):
            guard.check("/meters/1", ())


class MeterParsing(unittest.TestCase):
    def test_roundtrip(self):
        vals = [-30.0, -12.5, -0.5, -90.0]
        self.assertEqual(meters.parse_blob(meters.make_blob(vals)), vals)

    def test_bad_blob(self):
        with self.assertRaises(meters.MeterError):
            meters.parse_blob(b"\x01")
        with self.assertRaises(meters.MeterError):
            meters.parse_blob(b"\x05\x00\x00\x00\x00\x00")

    def test_summarize_clip(self):
        s = meters.summarize([[-20.0, -0.5], [-18.0, -0.2]], clip_db=-1.0)
        self.assertFalse(s["indices"]["0"]["clip"])
        self.assertTrue(s["indices"]["1"]["clip"])


class MeterEndToEnd(unittest.TestCase):
    def test_record_from_fake_with_zero_writes(self):
        frames = [meters.make_blob([-20.0, -0.3, -50.0]) for _ in range(3)]
        with FakeMixer({}) as mixer, tempfile.TemporaryDirectory() as tmp:
            mixer.meter_frames["/meters/1"] = frames
            with ReadOnlyClient("127.0.0.1", mixer.port, log_path=Path(tmp) / "l.jsonl") as c:
                result = meters.record(c, "/meters/1", 1.0)
            self.assertEqual(result["frames"], 3)
            self.assertTrue(result["indices"]["1"]["clip"])
            self.assertEqual(mixer.write_attempts, [])
            self.assertEqual(mixer.meter_requests[0], ("/meters/1",))

    def test_monitor_cli_rejects_bad_path_without_traffic(self):
        with FakeMixer({}) as mixer, tempfile.TemporaryDirectory() as tmp:
            rc = monitor.main(["--host", "127.0.0.1", "--port", str(mixer.port),
                               "--path", "/ch/01/mix/fader", "--seconds", "1",
                               "--log", str(Path(tmp) / "l.jsonl")])
            self.assertEqual(rc, 2)
            self.assertEqual(mixer.meter_requests, [])


def _dump(**extra):
    v = {
        "/ch/01/config/name": ["Vokal 1"], "/ch/01/preamp/hpon": [0], "/ch/01/dyn/on": [0],
        "/ch/01/mix/on": [1], "/headamp/01/phantom": [0],
        "/ch/02/config/name": ["Laptop L"], "/headamp/02/phantom": [1], "/ch/02/preamp/hpon": [0],
        "/ch/03/config/name": ["Kick"], "/ch/03/preamp/hpon": [0],
    }
    v.update(extra)
    return {"created": "t", "sha256": "abc", "values": v}


class Report(unittest.TestCase):
    def test_findings(self):
        ids = {f.id for f in report.analyze(_dump())}
        self.assertIn("hpf-mati-ch01", ids)
        self.assertIn("tanpa-komp-ch01", ids)
        self.assertIn("phantom-ch02", ids)
        self.assertNotIn("hpf-mati-ch03", ids)  # kick bukan channel suara
        self.assertNotIn("hpf-mati-ch02", ids)

    def test_unanswered_not_flagged(self):
        d = _dump(**{"/ch/01/preamp/hpon": None, "/ch/01/dyn/on": None})
        ids = {f.id for f in report.analyze(d)}
        self.assertNotIn("hpf-mati-ch01", ids)

    def test_empty_dump_flagged(self):
        d = {"values": {f"/ch/{i:02d}/config/name": None for i in range(1, 6)}}
        self.assertIn("dump-kosong", {f.id for f in report.analyze(d)})

    def test_render_has_six_elements(self):
        text = report.render(_dump(), "x.json")
        for key in ("Masalah", "Kemungkinan penyebab", "Data pembuktian", "Langkah uji",
                    "Rekomendasi", "Risiko perubahan"):
            self.assertIn(key, text)
        self.assertIn("Belum bisa dinilai", text)


if __name__ == "__main__":
    unittest.main()
