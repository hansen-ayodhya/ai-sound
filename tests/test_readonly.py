import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import guard
from aise.addresses import all_addresses
from aise.client import ReadOnlyClient
from aise.dump import diff, run_dump
from aise.fake_mixer import FakeMixer
from aise.guard import ReadOnlyViolation
from aise.osc import decode, encode

STATE = {
    "/info": ("V1.0", "MR18", "1.23"),
    "/ch/01/config/name": ("Vokal 1",),
    "/ch/01/mix/fader": (0.75,),
    "/headamp/01/gain": (0.4,),
}


class OscCodec(unittest.TestCase):
    def test_roundtrip(self):
        for args in [(), (1,), (0.5,), ("abc",), (1, 2.5, "xy"), (b"\x01\x02\x03",)]:
            addr, got = decode(encode("/ch/01/x", args))
            self.assertEqual(addr, "/ch/01/x")
            self.assertEqual(len(got), len(args))

    def test_known_bytes(self):
        self.assertEqual(encode("/info"), b"/info\x00\x00\x00,\x00\x00\x00")


class Guard(unittest.TestCase):
    def test_allows_queries(self):
        guard.check("/ch/01/config/name", ())
        guard.check("/info", ())

    def test_blocks_writes_and_unknown(self):
        for addr, args in [
            ("/ch/01/mix/fader", (0.5,)),
            ("/ch/01/mix/on", (0,)),
            ("/-snap/load", ()),
            ("/-action/setscene", ()),
            ("/xremote", ()),
            ("/meters/1", ()),
            ("ch/01", ()),
            ("/ch/01/*", ()),
        ]:
            with self.assertRaises(ReadOnlyViolation, msg=addr):
                guard.check(addr, args)

    def test_check_bytes_blocks_write_packet(self):
        with self.assertRaises(ReadOnlyViolation):
            guard.check_bytes(encode("/ch/01/mix/fader", (0.9,)))


class ClientAgainstFake(unittest.TestCase):
    def test_query_and_no_writes(self):
        with FakeMixer(STATE) as mixer, tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.jsonl"
            with ReadOnlyClient("127.0.0.1", mixer.port, timeout=0.3, retries=0, log_path=log) as c:
                self.assertEqual(c.query("/ch/01/config/name"), ("Vokal 1",))
                self.assertIsNone(c.query("/ch/02/config/name"))
                with self.assertRaises(ReadOnlyViolation):
                    c.query("/-snap/load")
            self.assertEqual(mixer.write_attempts, [])
            events = [json.loads(l)["event"] for l in log.read_text().splitlines()]
            self.assertIn("blocked", events)

    def test_full_dump_twice_identical_and_zero_writes(self):
        with FakeMixer(STATE) as mixer:
            with ReadOnlyClient("127.0.0.1", mixer.port, timeout=0.05, retries=0) as c:
                d1 = run_dump(c, all_addresses()[:60])
                d2 = run_dump(c, all_addresses()[:60])
            self.assertEqual(d1["sha256"], d2["sha256"])
            self.assertEqual(diff(d1, d2), [])
            self.assertEqual(d1["answered"], len(STATE))
            self.assertEqual(mixer.write_attempts, [])

    def test_diff_detects_change(self):
        a = {"values": {"/x/y": [1]}}
        b = {"values": {"/x/y": [2]}}
        self.assertEqual(len(diff(a, b)), 1)


if __name__ == "__main__":
    unittest.main()
