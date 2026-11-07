import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import convert
from aise.live import analyze_eq_problems
from aise.server import ROOT, App, serve


def dump_with(ch, hz, db):
    c = f"/ch/{ch:02d}/eq"
    v = {f"/ch/{ch:02d}/config/name": ["Mic"]}
    for b in range(1, 5):
        v[f"{c}/{b}/f"] = [convert.eq_raw_hz(1000)]
        v[f"{c}/{b}/g"] = [convert.eq_gain_raw(0)]
    v[f"{c}/2/f"], v[f"{c}/2/g"] = [convert.eq_raw_hz(hz)], [convert.eq_gain_raw(db)]
    return {"values": v}


class EqProblems(unittest.TestCase):
    def test_rules(self):
        self.assertIn("boomy", analyze_eq_problems(dump_with(1, 100, 6))[0]["id"])
        self.assertIn("harsh", analyze_eq_problems(dump_with(4, 3500, 5))[0]["id"])
        self.assertIn("bright", analyze_eq_problems(dump_with(2, 10000, 7))[0]["id"])
        self.assertIn("muddy", analyze_eq_problems(dump_with(3, 200, 5))[0]["id"])
        self.assertIn("unclear", analyze_eq_problems(dump_with(5, 2500, -4))[0]["id"])
        self.assertEqual(analyze_eq_problems(dump_with(1, 100, 1)), [])
        self.assertEqual(analyze_eq_problems(dump_with(1, 2500, -1)), [])

    def test_screenshot_ch12(self):
        v = {}
        for b, (hz, db) in enumerate([(22, -13), (500, -3.5), (43, -13), (11000, 1.5)], 1):
            v[f"/ch/12/eq/{b}/f"] = [convert.eq_raw_hz(hz)]
            v[f"/ch/12/eq/{b}/g"] = [convert.eq_gain_raw(db)]
        ids = [f["id"] for f in analyze_eq_problems({"values": v})]
        self.assertIn("eq-subcut-ch12-b1", ids)
        self.assertIn("eq-subcut-ch12-b3", ids)
        self.assertEqual(len(ids), 2)

    def test_extreme_and_overlap(self):
        v = {}
        for b, (hz, db) in enumerate([(1000, 10), (300, -4), (400, -5), (8000, 0)], 1):
            v[f"/ch/01/eq/{b}/f"] = [convert.eq_raw_hz(hz)]
            v[f"/ch/01/eq/{b}/g"] = [convert.eq_gain_raw(db)]
        ids = [f["id"] for f in analyze_eq_problems({"values": v})]
        self.assertIn("eq-extreme-ch1-b1", ids)
        self.assertIn("eq-overlap-ch1-b3", ids)

    def test_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            app = App(root=ROOT, data=data)
            app.latest_dump = lambda: ({}, dump_with(4, 3500, 5))
            probe = serve(App(root=ROOT, data=data), 0)
            port = probe.server_address[1]
            probe.server_close()
            httpd = serve(app, port)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                r = urllib.request.Request(f"http://127.0.0.1:{port}/api/live/eq-problems")
                r.add_header("X-AISE-Token", app.token)
                with urllib.request.urlopen(r, timeout=10) as resp:
                    out = json.loads(resp.read())
                self.assertEqual(out["findings"][0]["ch"], 4)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
