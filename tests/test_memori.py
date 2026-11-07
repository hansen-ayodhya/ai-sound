import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import ai, convert, memori
from aise.audit import run_audit
from aise.fake_mixer import FakeMixer
from aise.live import Alert, LiveMonitor
from aise.server import ROOT, App, serve


class MemoriUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = memori.Memori(Path(self.tmp.name) / "memori")

    def tearDown(self):
        self.tmp.cleanup()

    def test_unique_and_profile_flags_little_data(self):
        self.assertTrue(self.m.catat("saran", unik_id="a", id="a", ch=1, masalah="x"))
        self.assertFalse(self.m.catat("saran", unik_id="a", id="a", ch=1, masalah="x"))
        self.m.catat("selesai", unik_id="a@1", id="a", ch=1, masalah="x", items=[{"teks": "Gain", "awal": "+30 dB", "sekarang": "+26 dB"}])
        self.m.catat("nilai", id="a", nilai="bagus")
        p = self.m.profil()
        self.assertEqual(p["jumlah_catatan"]["saran"], 1)
        self.assertEqual(p["perubahan_dinilai"][0]["nilai"], "bagus")
        self.assertFalse(p["cukup_data"])
        self.assertIn("belum cukup", p["catatan"])

    def test_repeated_problems_and_enough_data(self):
        for _ in range(3):
            self.m.catat("masalah", ch=2, jenis_masalah="clip", level_db=-0.4)
        self.m.catat("masalah", ch=5, jenis_masalah="hot", level_db=-2)
        for i in range(5):
            self.m.catat("nilai", id=f"r{i}", nilai="bagus" if i % 2 else "kurang")
        p = self.m.profil()
        self.assertEqual(p["masalah_berulang"], [{"ch": 2, "jenis": "clip", "kali": 3}])   # satu kali saja bukan pola
        self.assertTrue(p["cukup_data"])

    def test_ukur_validation_and_delete(self):
        r = self.m.catat_ukur({"mic": "genggam", "target": "umum", "titik": 3, "band": [{"hz": 200, "db": -5, "q": 1.4, "jenis": "kurangi"}, {"hz": 5, "db": 99, "q": 1}]})
        self.assertEqual(r["band"], 1)
        with self.assertRaises(ValueError):
            self.m.catat_ukur({"titik": 0})
        self.assertEqual(self.m.hapus("ukur"), 1)
        self.m.catat("masalah", ch=1, jenis_masalah="clip", level_db=0)
        self.assertEqual(self.m.hapus(), 1)
        self.assertEqual(self.m.semua(), [])

    def test_prompt_includes_history_with_caveat(self):
        self.m.catat("masalah", ch=2, jenis_masalah="clip", level_db=-0.4)
        text = ai.build_prompt({"values": {}}, None, [], None, "umum", "", None, self.m.profil())
        self.assertIn("riwayat", text)
        self.assertIn("belum cukup", text)
        self.assertNotIn("riwayat", ai.build_prompt({"values": {}}, None, [], None, "umum", "", None, memori.Memori(Path(self.tmp.name) / "kosong").profil()))
        self.assertIn("bukti terbatas", ai.SYSTEM)


class MemoriServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        addrs = ["/info", "/ch/03/config/name", "/headamp/03/gain"]
        self.mixer = FakeMixer({"/info": ("x",), "/ch/03/config/name": ("Mic",), "/headamp/03/gain": (convert.gain_raw(30),)}).start()
        self.app = App(root=ROOT, data=self.data, addresses=addrs)
        run_audit("127.0.0.1", self.mixer.port, base=self.data, addresses=addrs, timeout=0.1)
        (self.data / "mixer-ip.txt").write_text("127.0.0.1")
        self.app.save_port(self.mixer.port)
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

    def call(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        r.add_header("X-AISE-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flow_records_rates_and_clears(self):
        self.app.live = LiveMonitor("127.0.0.1", 1, "/meters/1", {2: "CH 03 Mic"}, self.data / "live.jsonl", on_alert=self.app._memori_alert)
        self.app.live._record([Alert(time.monotonic(), "t", "clip", "bad", 2, "CH 03 Mic", -0.4, "x")])
        snap = self.app.live.snapshot()
        self.assertIn("ago", snap["alerts"][0])
        self.assertLess(snap["alerts"][0]["ago"], 5)
        code, _ = self.call("/api/recs")                                    # mendaftarkan usulan gain dari clipping live
        self.assertEqual(code, 200)
        code, v = self.call("/api/memori")
        jenis = [r["jenis"] for r in v["catatan"]]
        self.assertIn("saran", jenis)
        self.assertIn("masalah", jenis)
        self.call("/api/recs")
        self.assertEqual([r["jenis"] for r in self.call("/api/memori")[1]["catatan"]].count("saran"), jenis.count("saran"))   # tidak ganda
        rid = next(r["id"] for r in v["catatan"] if r["jenis"] == "saran")
        self.assertEqual(self.call("/api/memori/nilai", {"id": rid, "nilai": "bagus"})[0], 200)
        self.assertEqual(self.call("/api/memori/nilai", {"id": "tidak-ada", "nilai": "bagus"})[0], 400)
        self.assertEqual(self.call("/api/memori/nilai", {"id": rid, "nilai": "mantap"})[0], 400)
        self.assertEqual(self.call("/api/memori/ukur", {"titik": 2, "mic": "ukur", "band": []})[0], 200)
        self.assertEqual(self.call("/api/memori/ukur", {"titik": 0})[0], 400)
        self.mixer.state["/headamp/03/gain"] = (convert.gain_raw(26),)       # operator menurunkan gain
        (self.data / "mixer-ip.txt").write_text("127.0.0.1")
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/settings/refresh", data=b"{}", method="POST")
        r.add_header("X-AISE-Token", self.app.token); r.add_header("Content-Type", "application/json")
        urllib.request.urlopen(r, timeout=20).read()
        self.call("/api/recs")
        jenis2 = [r["jenis"] for r in self.call("/api/memori")[1]["catatan"]]
        self.assertIn("selesai", jenis2)                                      # verifikasi selesai ikut dicatat
        self.assertGreater(self.call("/api/memori")[1]["profil"]["jumlah_catatan"]["selesai"], 0)
        self.assertEqual(self.call("/api/memori/hapus", {})[0], 200)
        self.assertEqual(self.call("/api/memori")[1]["catatan"], [])


if __name__ == "__main__":
    unittest.main()
