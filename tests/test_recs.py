import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ai import KEY, Base
from aise import convert, recs
from aise.audit import run_audit
from aise.fake_mixer import FakeMixer
from aise.server import ROOT, App, serve


def finding(fid, ch, items, sumber=None):
    f = {"id": fid, "ch": ch, "masalah": "m " + fid, "usulan": [{"alamat": a, "usulan": t, "teks": f"teks {a}"} for a, t in items]}
    if sumber:
        f["sumber"] = sumber
    return f


def dump(**v):
    return {"values": {"/" + k.replace("__", "/"): [x] for k, x in v.items()}}


class ItemState(unittest.TestCase):
    def test_toggle(self):
        self.assertEqual(recs.item_state("/ch/01/preamp/hpon", 0, 1, 1), "selesai")
        self.assertEqual(recs.item_state("/ch/01/preamp/hpon", 0, 1, 0), "belum")
        self.assertEqual(recs.item_state("/ch/01/preamp/hpon", 0, 1, None), "tidak_terbaca")

    def test_numeric_progress(self):
        a = "/ch/01/eq/1/g"
        base, target = convert.eq_gain_raw(14.6), convert.eq_gain_raw(3.0)
        self.assertEqual(recs.item_state(a, base, target, base), "belum")
        self.assertEqual(recs.item_state(a, base, target, convert.eq_gain_raw(12.0)), "sebagian")
        self.assertEqual(recs.item_state(a, base, target, convert.eq_gain_raw(5.0)), "selesai")   # ≥70% jalan
        self.assertEqual(recs.item_state(a, base, target, convert.eq_gain_raw(3.1)), "selesai")
        self.assertEqual(recs.item_state(a, base, target, convert.eq_gain_raw(16.0)), "belum")    # salah arah

    def test_ratio_within_one_step(self):
        a = "/ch/01/dyn/ratio"
        self.assertEqual(recs.item_state(a, 10, 5, 6), "selesai")
        self.assertEqual(recs.item_state(a, 10, 5, 9), "sebagian")
        self.assertEqual(recs.item_state(a, 10, 5, 10), "belum")

    def test_fmt(self):
        self.assertEqual(recs.fmt_value("/ch/01/preamp/hpon", 1), "ON")
        self.assertEqual(recs.fmt_value("/ch/01/mix/07/level", 0.0), "mati (−∞)")
        self.assertEqual(recs.fmt_value("/ch/01/dyn/ratio", 5), "≈3:1")
        self.assertEqual(recs.fmt_value("/ch/01/eq/1/g", None), "?")


class Flow(unittest.TestCase):
    def test_rule_finding_done_when_applied_and_no_longer_firing(self):
        st = {"tracked": {}}
        f = finding("hpf-mati-ch01", 1, [("/ch/01/preamp/hpon", 1)])
        recs.register(st, [f], dump(ch__01__preamp__hpon=0), "t1")
        recs.evaluate(st, dump(ch__01__preamp__hpon=0), "t1", {"hpf-mati-ch01"})
        self.assertEqual(st["tracked"]["hpf-mati-ch01"]["status"], "belum")
        d2 = dump(ch__01__preamp__hpon=1)                # operator menyalakan HPF
        recs.evaluate(st, d2, "t2", set())              # aturan tidak memicu lagi
        v = recs.view(st)
        self.assertEqual(v["recs"]["hpf-mati-ch01"]["status"], "selesai")
        self.assertEqual(v["selesai"][0]["done_at"], "t2")
        self.assertEqual(v["selesai"][0]["items"][0]["awal_teks"], "OFF")
        self.assertEqual(v["selesai"][0]["items"][0]["sekarang_teks"], "ON")

    def test_rule_still_firing_is_never_done(self):
        st = {"tracked": {}}
        a = "/ch/04/eq/1/g"
        base, target = convert.eq_gain_raw(14.6), convert.eq_gain_raw(3.0)
        f = finding("boost-b1-ch04", 4, [(a, target)])
        recs.register(st, [f], {"values": {a: [base]}}, "t1")
        now = {"values": {a: [convert.eq_gain_raw(6.5)]}}   # ≥70% jalan, tetapi aturan (>6 dB) masih memicu
        recs.evaluate(st, now, "t2", {"boost-b1-ch04"})
        self.assertEqual(st["tracked"]["boost-b1-ch04"]["status"], "sebagian")
        self.assertIsNone(st["tracked"]["boost-b1-ch04"]["done_at"])

    def test_reverted_after_done(self):
        st = {"tracked": {}}
        f = finding("hpf-mati-ch01", 1, [("/ch/01/preamp/hpon", 1)])
        recs.register(st, [f], dump(ch__01__preamp__hpon=0), "t1")
        recs.evaluate(st, dump(ch__01__preamp__hpon=1), "t2", set())
        self.assertTrue(st["tracked"]["hpf-mati-ch01"]["done_at"])
        recs.evaluate(st, dump(ch__01__preamp__hpon=0), "t3", {"hpf-mati-ch01"})   # operator membatalkan
        self.assertIsNone(st["tracked"]["hpf-mati-ch01"]["done_at"])
        recs.register(st, [f], dump(ch__01__preamp__hpon=0), "t4")                 # aturan memicu lagi
        self.assertTrue(st["tracked"]["hpf-mati-ch01"]["kembali"])
        self.assertEqual(recs.view(st)["selesai"], [])

    def test_gemini_uses_progress_and_dsp_items_skipped(self):
        st = {"tracked": {}}
        a = "/ch/04/preamp/hpf"
        f = finding("gemini-1-ch04-101010", 4, [(a, convert.hpf_raw(120)), ("dsp:out1/hpf_hz", 80)], "gemini")
        recs.register(st, [f], {"values": {a: [convert.hpf_raw(25)]}}, "t1")
        self.assertEqual(len(st["tracked"][f["id"]]["items"]), 1)         # alamat dsp tidak dilacak
        recs.evaluate(st, {"values": {a: [convert.hpf_raw(110)]}}, "t2")
        self.assertEqual(st["tracked"][f["id"]]["status"], "selesai")

    def test_no_usulan_not_tracked(self):
        st = {"tracked": {}}
        recs.register(st, [{"id": "x", "ch": 1, "masalah": "m", "usulan": []}], {"values": {}}, "t")
        self.assertEqual(st["tracked"], {})


class ServerRecs(Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.mixer = FakeMixer({"/info": ("x",), "/ch/01/config/name": ("Vokal 1",), "/ch/01/preamp/hpon": (0,),
                                "/ch/01/preamp/hpf": (0.2,)}).start()
        self.app = App(root=ROOT, data=self.data, ai_base=self.base,
                       addresses=["/info", "/ch/01/config/name", "/ch/01/preamp/hpon", "/ch/01/preamp/hpf"])
        run_audit("127.0.0.1", self.mixer.port, base=self.data, addresses=self.app.addresses, timeout=0.1)
        probe = serve(App(root=ROOT, data=self.data), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.mixer.stop()
        self.tmp.cleanup()
        super().tearDown()

    def call(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        r.add_header("X-AISE-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_operator_applies_recommendation_and_it_is_verified(self):
        code, v = self.call("/api/recs")
        self.assertEqual(code, 200)
        self.assertEqual(v["recs"]["hpf-mati-ch01"]["status"], "belum")
        self.assertEqual(v["selesai"], [])
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "mixer-ip.txt").write_text("127.0.0.1")
        self.app.save_port(self.mixer.port)
        self.mixer.state["/ch/01/preamp/hpon"] = (1,)                  # operator menyalakan low cut di M-AIR
        self.mixer.state["/ch/01/preamp/hpf"] = (convert.hpf_raw(100),)
        code, _ = self.call("/api/settings/refresh", {})               # ↻ Baca setting
        self.assertEqual(code, 200)
        code, v = self.call("/api/recs")
        self.assertEqual(v["recs"]["hpf-mati-ch01"]["status"], "selesai", v)
        self.assertEqual(v["selesai"][0]["id"], "hpf-mati-ch01")
        self.assertEqual(v["selesai"][0]["items"][0]["sekarang_teks"], "ON")
        self.assertTrue((self.data / "recs.json").exists())
        self.assertEqual(self.mixer.write_attempts, [])

    def test_gemini_recommendation_tracked_with_unique_id(self):
        import re
        self.call("/api/ai/config", {"key": KEY})
        code, res = self.call("/api/ai/analyze", {"situasi": "umum"})
        self.assertEqual(code, 200, res)
        ids = [p["id"] for p in res["prioritas"]]
        self.assertTrue(all(re.search(r"-\d{6}$", i) for i in ids), ids)       # ada cap waktu di akhir id
        state = json.loads((self.data / "recs.json").read_text())
        first = res["prioritas"][0]
        self.assertIn(first["id"], state["tracked"])                           # rekomendasi Gemini yang punya usulan dilacak
        self.assertEqual(state["tracked"][first["id"]]["sumber"], "gemini")
        self.assertNotIn(res["prioritas"][1]["id"], state["tracked"])          # tanpa usulan tidak dilacak


if __name__ == "__main__":
    unittest.main()
