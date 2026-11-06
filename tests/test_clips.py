import base64
import json
import math
import struct
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
from aise.clips import ClipError, ClipStore, wav_info
from aise.server import ROOT, App, serve


def wav(seconds=10.0, rate=16000, hz=440.0):
    n = int(seconds * rate)
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate))) for i in range(n))
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


class Wav(unittest.TestCase):
    def test_info_and_limits(self):
        self.assertEqual(wav_info(wav(10))["seconds"], 10.0)
        for bad in (b"bukan wav", wav(0.2), wav(40)):
            with self.assertRaises(ClipError):
                wav_info(bad)

    def test_store_cleans_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = ClipStore(Path(tmp))
            rec = s.save(base64.b64encode(wav(2)).decode(), {"posisi": "baris 5", "situasi": "konser"},
                         {"rms_db": -20, "bands": [{"hz": 100, "db": 6}, {"hz": "x", "db": 1}], "jahat": "<script>"})
            self.assertEqual(rec["meta"]["situasi"], "umum")         # nilai tak dikenal -> umum
            self.assertEqual(len(rec["analisis"]["bands"]), 1)        # baris rusak dibuang
            self.assertNotIn("jahat", rec["analisis"])
            got, data = s.get(rec["id"])
            self.assertEqual(got["meta"]["posisi"], "baris 5")
            with self.assertRaises(ClipError):
                s.get("../../gemini")


class ServerClips(Base):
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

    def test_save_list_serve_and_ai_with_consent(self):
        code, raw = self.call("/api/clips", {"wav_b64": base64.b64encode(wav(10)).decode(),
                                             "meta": {"posisi": "baris 5", "situasi": "lagu"},
                                             "analisis": {"rms_db": -22.5, "peaks": [{"hz": 2500, "tonjolan_db": 14, "persisten": 0.9}]}})
        self.assertEqual(code, 200, raw)
        cid = json.loads(raw)["id"]
        code, raw = self.call("/api/clips")
        self.assertEqual(json.loads(raw)["clips"][0]["id"], cid)
        code, raw = self.call(f"/data/clips/{cid}.wav")
        self.assertEqual(code, 200)
        self.assertEqual(raw[:4], b"RIFF")
        code, raw = self.call("/api/clips/ai", {"id": cid})                 # tanpa izin
        self.assertEqual(code, 400)
        code, raw = self.call("/api/clips/ai", {"id": cid, "izin": True})   # belum ada kunci
        self.assertEqual(code, 502)
        self.assertEqual(self.call("/api/ai/config", {"key": KEY})[0], 200)
        code, raw = self.call("/api/clips/ai", {"id": cid, "izin": True})
        self.assertEqual(code, 200, raw)
        res = json.loads(raw)
        self.assertEqual(res["klip"], cid)
        self.assertTrue(res["prioritas"][0]["id"].startswith("gemini-klip-"))
        body = json.loads(FakeGemini.calls[-1][3])
        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "audio/wav")
        self.assertEqual(base64.b64decode(parts[1]["inline_data"]["data"])[:4], b"RIFF")
        self.assertIn("2500", parts[0]["text"])                              # angka analisis ikut terkirim
        self.assertNotIn(KEY, json.dumps(body))
        code, raw = self.call("/api/clips")
        self.assertIsNotNone(json.loads(raw)["clips"][0]["ai"])

    def test_bad_clip(self):
        code, _ = self.call("/api/clips", {"wav_b64": base64.b64encode(b"RIFFxxxxWAVEsalah").decode()})
        self.assertEqual(code, 400)
        code, _ = self.call("/api/clips/ai", {"id": "../gemini", "izin": True})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
