import base64
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

from test_ai import KEY, Base, FakeGemini
from aise import ai
from aise import dsp as d
from aise.server import ROOT, App, serve


def data(*outs):
    base = d.blank()
    for o in outs:
        base["output"][o["no"] - 1].update(o)
    return d.validate(base)


class Validate(unittest.TestCase):
    def test_ranges_and_roles(self):
        d.validate(d.blank())
        for bad in ({"no": 1, "peran": "speaker"}, {"no": 1, "hpf_hz": 5}, {"no": 1, "delay_ms": -1}, {"no": 1, "gain_db": "nol"}):
            with self.assertRaises(d.DSPError):
                b = d.blank(); b["output"][0].update(bad); d.validate(b)
        v = data({"no": 1, "peran": "sub", "hpf_tipe": "<b>LR24</b>", "peq": [{"hz": 100, "db": 3}, {"hz": None, "db": 1}]})
        self.assertEqual(v["output"][0]["hpf_tipe"], "bLR24/b")
        self.assertEqual(len(v["output"][0]["peq"]), 1)

    def test_inspect(self):
        self.assertEqual(d.inspect(b"<?xml version='1.0'?><preset><hpf>80</hpf></preset>")["jenis"], "xml")
        self.assertEqual(d.inspect(b'{"a": 1}')["jenis"], "json")
        self.assertEqual(d.inspect(b"[Output1]\nHPF=80\n")["jenis"], "ini")
        b = d.inspect(bytes(range(256)) * 4)
        self.assertEqual(b["jenis"], "biner")
        self.assertFalse(b["teks"])


class Rules(unittest.TestCase):
    def ids(self, *outs):
        return {f["id"]: f for f in d.analyze(data(*outs))}

    def test_overlap_gap_and_missing_hpf(self):
        f = self.ids({"no": 1, "peran": "utama_kiri", "hpf_hz": 80}, {"no": 3, "peran": "sub", "lpf_hz": 150})
        ov = f["dsp-xo-overlap-o1-o3"]
        self.assertEqual(ov["usulan"][0]["usulan"], 80)
        f = self.ids({"no": 1, "peran": "utama_kiri", "hpf_hz": 160}, {"no": 3, "peran": "sub", "lpf_hz": 80})
        self.assertIn("dsp-xo-lubang-o1-o3", f)
        f = self.ids({"no": 1, "peran": "utama_kiri"}, {"no": 3, "peran": "sub", "lpf_hz": 90})
        self.assertEqual(f["dsp-utama-tanpa-hpf-o1"]["usulan"][0]["usulan"], 90)
        f = self.ids({"no": 1, "peran": "utama_kiri", "hpf_hz": 90}, {"no": 3, "peran": "sub", "lpf_hz": 95})
        self.assertFalse([k for k in f if k.startswith("dsp-xo")])  # cukup sejajar

    def test_limiter_boost_lr_polarity_delay(self):
        f = self.ids({"no": 1, "peran": "utama_kiri", "hpf_hz": 90, "gain_db": 0, "peq": [{"hz": 3000, "db": 8, "q": 1.4}]},
                     {"no": 2, "peran": "utama_kanan", "hpf_hz": 90, "gain_db": 3, "polaritas_terbalik": True},
                     {"no": 3, "peran": "sub", "lpf_hz": 90, "limiter_thr_db": 0})
        self.assertIn("dsp-limiter-o1", f)
        self.assertNotIn("dsp-limiter-o3", f)
        self.assertEqual(f["dsp-boost-o1-b1"]["usulan"][0]["usulan"], 3.0)
        self.assertIn("gain 0 vs 3", f["dsp-kiri-kanan"]["data"])
        self.assertTrue([k for k in f if k.startswith("dsp-polaritas")])
        self.assertIn("dsp-delay", f)

    def test_empty_roles(self):
        self.assertEqual([x["id"] for x in d.analyze(d.validate(d.blank()))], ["dsp-peran-kosong"])
        self.assertEqual(d.analyze(None), [])


class ServerDSP(Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.app = App(root=ROOT, data=self.data, ai_base=self.base)
        probe = serve(App(root=ROOT, data=self.data), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
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

    def test_save_upload_and_read(self):
        code, v = self.call("/api/dsp")
        self.assertFalse(v["saved"])
        self.assertEqual(len(v["data"]["output"]), 8)
        b = d.blank()
        b["output"][0].update(peran="utama_kiri", hpf_hz=80)
        b["output"][2].update(peran="sub", lpf_hz=150)
        code, r = self.call("/api/dsp", {"data": b})
        self.assertEqual(code, 200, r)
        self.assertIn("dsp-xo-overlap-o1-o3", [f["id"] for f in r["findings"]])
        b["output"][0]["hpf_hz"] = 5
        self.assertEqual(self.call("/api/dsp", {"data": b})[0], 400)
        # preset teks & biner
        code, rec = self.call("/api/dsp/preset", {"b64": base64.b64encode(b"[Out1]\nHPF=80\nLPF=0\n").decode(), "nama": "../Ibadah.prs"})
        self.assertEqual(code, 200, rec)
        self.assertEqual(rec["nama_file"], "Ibadah.prs")
        self.assertTrue(rec["info"]["teks"])
        code, binrec = self.call("/api/dsp/preset", {"b64": base64.b64encode(bytes(range(256))).decode(), "nama": "x.bin"})
        self.assertFalse(binrec["info"]["teks"])
        code, lst = self.call("/api/dsp")
        self.assertEqual(len(lst["presets"]), 2)
        self.assertNotIn("cuplikan", lst["presets"][0]["info"])
        self.assertEqual(self.call("/api/dsp/preset?id=" + rec["id"])[1]["info"]["jenis"], "ini")
        self.assertEqual(self.call("/api/dsp/preset?id=../../gemini")[0], 400)
        # baca dengan Gemini
        self.assertEqual(self.call("/api/dsp/ai-read", {"preset_id": rec["id"]})[0], 400)            # tanpa izin
        self.assertEqual(self.call("/api/ai/config", {"key": KEY})[0], 200)
        code, r = self.call("/api/dsp/ai-read", {"izin": True, "preset_id": binrec["id"]})
        self.assertEqual(code, 400)
        self.assertIn("biner", r["error"])
        code, r = self.call("/api/dsp/ai-read", {"izin": True, "preset_id": rec["id"]})
        self.assertEqual(code, 200, r)
        o = r["draf"]["output"]
        self.assertEqual(o[0]["peran"], "utama_kiri")
        self.assertEqual(o[0]["peq"], [{"hz": 3000.0, "db": 8.0, "q": 1.4}])   # band 99999 Hz dibuang
        self.assertIsNone(o[2]["delay_ms"])                                    # 5000 ms di luar rentang
        self.assertIn("Output 3 delay_ms", r["dibuang"])
        self.assertEqual(r["tidak_terbaca"], ["limiter semua output"])
        sent = json.loads(FakeGemini.calls[-1][3])
        self.assertIn("HPF=80", sent["contents"][0]["parts"][1]["text"])
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()
        code, r = self.call("/api/dsp/ai-read", {"izin": True, "image_b64": png, "mime": "image/png"})
        self.assertEqual(code, 200, r)
        self.assertEqual(json.loads(FakeGemini.calls[-1][3])["contents"][0]["parts"][1]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(self.call("/api/dsp/ai-read", {"izin": True, "image_b64": png, "mime": "image/gif"})[0], 400)

    def test_dsp_included_in_ai_analysis(self):
        b = d.blank()
        b["output"][0].update(peran="utama_kiri", hpf_hz=80, nama="Mickey L")
        self.call("/api/dsp", {"data": b})
        self.call("/api/ai/config", {"key": KEY})
        code, _ = self.call("/api/ai/analyze", {"situasi": "umum"})
        self.assertEqual(code, 200)
        prompt = json.loads(FakeGemini.calls[-1][3])["contents"][0]["parts"][0]["text"]
        self.assertIn("Mickey L", prompt)
        self.assertIn("temuan_dsp", prompt)


if __name__ == "__main__":
    unittest.main()
