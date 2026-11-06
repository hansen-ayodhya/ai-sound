import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import ai, convert
from aise.server import ROOT, App, serve

KEY = "AIzaFAKEKEY_1234567890abcdef"
REPLY = {
    "ringkasan": "Prioritas: khotbah dulu.",
    "jawaban": "Vokal tenggelam karena ...",
    "prioritas": [
        {"judul": "Low cut khotbah", "ch": 4, "masalah": "m", "penyebab": "p", "data": "d", "uji": "u",
         "rekomendasi": "r", "risiko": "k", "keyakinan": "sedang",
         "usulan": [{"parameter": "hpf_on", "nilai": 1}, {"parameter": "hpf_hz", "nilai": 120},
                    {"parameter": "comp_ratio", "nilai": 4}, {"parameter": "eq2_db", "nilai": 40},
                    {"parameter": "master_limiter", "nilai": 1}]},
        {"judul": "Umum", "ch": 99, "masalah": "m", "penyebab": "p", "data": "d", "uji": "u",
         "rekomendasi": "r", "risiko": "k", "keyakinan": "yakin sekali", "usulan": [{"parameter": "fader_db", "nilai": -5}]},
    ],
    "butuh_data": ["Pengukuran RTA di kursi tengah"],
    "penilaian": [{"aspek": "boomy", "nilai": "kurang", "alasan": "Nada rendah dominan."},
                  {"aspek": "feedback", "nilai": "cukup", "alasan": "Ada nada konstan tipis."}],
}


class FakeGemini(BaseHTTPRequestHandler):
    calls: list = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        FakeGemini.calls.append(("GET", self.path, self.headers.get("x-goog-api-key"), b""))
        if self.headers.get("x-goog-api-key") != KEY:
            return self._send(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        self._send(200, {"models": [
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.8-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.9-flash-preview-11-2026", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]},
        ]})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakeGemini.calls.append(("POST", self.path, self.headers.get("x-goog-api-key"), body))
        if FakeGemini.mode == "429":
            return self._send(429, {"error": {"message": "Resource exhausted"}})
        self._send(200, {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(REPLY) + "\n```"}]}}]})


class Base(unittest.TestCase):
    def setUp(self):
        FakeGemini.calls, FakeGemini.mode = [], "ok"
        self.g = ThreadingHTTPServer(("127.0.0.1", 0), FakeGemini)
        threading.Thread(target=self.g.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.g.server_address[1]}"

    def tearDown(self):
        self.g.shutdown()
        self.g.server_close()


class Pure(unittest.TestCase):
    def test_pick_model(self):
        self.assertEqual(ai.pick_model(["gemini-2.5-pro", "gemini-3.8-flash-lite", "gemini-3.8-flash",
                                        "gemini-3.9-flash-preview-x", "gemini-3.8-flash-tts"]), "gemini-3.8-flash")
        self.assertEqual(ai.pick_model(["gemini-3.5-flash-lite"]), "gemini-3.5-flash-lite")
        self.assertIsNone(ai.pick_model([]))

    def test_to_usulan_validation(self):
        dump = {"values": {"/ch/04/dyn/ratio": [2]}}
        u = ai.to_usulan(4, "comp_ratio", 4, dump)
        self.assertEqual(convert.ratio_value(u["usulan"]), 4.0)
        self.assertEqual(u["sekarang"], 2)
        self.assertIsNone(ai.to_usulan(4, "eq2_db", 40, dump))       # di luar rentang
        self.assertIsNone(ai.to_usulan(4, "master_limiter", 1, dump))  # parameter terlarang
        self.assertIsNone(ai.to_usulan(4, "hpf_hz", "100", dump))     # bukan angka
        self.assertEqual(ai.to_usulan(4, "send_fx1_db", -90, dump)["usulan"], 0.0)

    def test_sanitize(self):
        out = ai.sanitize(REPLY, {"values": {}})
        p1, p2 = out["prioritas"]
        self.assertEqual(p1["ch"], 4)
        self.assertEqual([u["alamat"] for u in p1["usulan"]], ["/ch/04/preamp/hpon", "/ch/04/preamp/hpf", "/ch/04/dyn/ratio"])
        self.assertIsNone(p2["ch"])            # ch 99 tidak valid
        self.assertEqual(p2["keyakinan"], "rendah")
        self.assertEqual(out["usulan_dibuang"], 3)  # eq2_db 40, master_limiter, fader tanpa channel
        self.assertEqual(p1["sumber"], "gemini")

    def test_prompt_has_no_secrets(self):
        dump = {"created": "t", "values": {"/ch/01/config/name": ["WL"], "/ch/01/mix/fader": [0.75]}}
        text = ai.build_prompt(dump, None, [], None, "khotbah", "kenapa?")
        self.assertIn('"situasi": "khotbah"', text)
        self.assertIn("fader_db", text)
        self.assertNotIn(KEY, text)


class Calls(Base):
    def test_list_and_analyze(self):
        self.assertIn("gemini-3.8-flash", ai.list_models(KEY, base=self.base))
        res = ai.analyze({"key": KEY, "model": "gemini-3.8-flash"}, {"values": {}}, None, [], None, base=self.base)
        self.assertEqual(res["model"], "gemini-3.8-flash")
        self.assertEqual(len(res["prioritas"]), 2)
        method, path, hdr, body = FakeGemini.calls[-1]
        self.assertEqual(path, "/models/gemini-3.8-flash:generateContent")
        self.assertEqual(hdr, KEY)
        self.assertNotIn(KEY.encode(), body)  # kunci hanya di header, tidak di isi pesan

    def test_errors(self):
        with self.assertRaises(ai.AIError) as e:
            ai.list_models("AIzaSALAH_000000000000000", base=self.base)
        self.assertIn("ditolak", str(e.exception))
        FakeGemini.mode = "429"
        with self.assertRaises(ai.AIError) as e:
            ai.analyze({"key": KEY, "model": "gemini-3.8-flash"}, None, None, [], None, base=self.base)
        self.assertIn("Batas pemakaian", str(e.exception))
        with self.assertRaises(ai.AIError):
            ai.analyze({}, None, None, [], None, base=self.base)


class ServerAI(Base):
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
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_flow_and_key_never_leaves_laptop(self):
        code, raw = self.call("/api/ai/analyze", {"situasi": "umum"})
        self.assertEqual(code, 502)  # belum diatur
        code, raw = self.call("/api/ai/config", {"key": "pendek"})
        self.assertEqual(code, 400)
        code, raw = self.call("/api/ai/config", {"key": KEY})
        self.assertEqual(code, 200, raw)
        st = json.loads(raw)
        self.assertEqual(st["model"], "gemini-3.8-flash")
        self.assertNotIn(KEY.encode(), raw)
        code, raw = self.call("/api/ai")
        self.assertNotIn(KEY.encode(), raw)
        self.assertTrue(json.loads(raw)["status"]["configured"])
        code, raw = self.call("/data/gemini.json")
        self.assertEqual(code, 404)
        code, raw = self.call("/api/ai/config", {"model": "gemini-tidak-ada"})
        self.assertEqual(code, 400)
        code, raw = self.call("/api/ai/analyze", {"situasi": "khotbah", "pertanyaan": "Kenapa khotbah kurang jelas?"})
        self.assertEqual(code, 200, raw)
        res = json.loads(raw)
        self.assertEqual(res["situasi"], "khotbah")
        self.assertTrue((self.data / "ai-last.json").exists())
        code, raw = self.call("/api/ai")
        self.assertEqual(json.loads(raw)["last"]["ringkasan"], "Prioritas: khotbah dulu.")
        code, _ = self.call("/api/ai/analyze", {"situasi": "konser"})
        self.assertEqual(code, 400)
        code, raw = self.call("/api/ai/config", {"hapus": True})
        self.assertFalse(json.loads(raw)["configured"])
        self.assertFalse((self.data / "gemini.json").exists())


if __name__ == "__main__":
    unittest.main()
