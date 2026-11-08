import base64
import io
import json
import math
import struct
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_ai
from test_ai import KEY, FakeGemini
from aise import ai
from aise.server import _clean_audio


def wav_b64(seconds=2.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(seconds * rate))))
    return base64.b64encode(buf.getvalue()).decode()


SPEC_ROOM = [round(i * 0.5, 1) for i in range(24)]
SPEC_MIX = [0.0] * 24


class Clean(unittest.TestCase):
    def item(self, peran="ruangan_hp", **kw):
        return {"peran": peran, "wav_b64": wav_b64(), "spektrum": SPEC_ROOM, **kw}

    def test_valid_and_limits(self):
        out = _clean_audio([self.item(), self.item("mixer", spektrum=SPEC_MIX)], True)
        self.assertEqual([a["peran"] for a in out], ["ruangan_hp", "mixer"])
        self.assertEqual(out[0]["spektrum"], SPEC_ROOM)
        self.assertIsNone(_clean_audio(None, None))
        self.assertIsNone(_clean_audio([], None))
        self.assertIsNone(_clean_audio([self.item(spektrum=[1, 2, 3])], True)[0]["spektrum"])      # panjang salah -> dibuang, bukan gagal

    def test_rejections(self):
        with self.assertRaises(ValueError):
            _clean_audio([self.item()], None)                                    # tanpa izin
        with self.assertRaises(ValueError):
            _clean_audio([self.item(), self.item(), self.item("mixer")], True)   # lebih dari 2
        with self.assertRaises(ValueError):
            _clean_audio([self.item(), self.item()], True)                       # peran ganda
        with self.assertRaises(ValueError):
            _clean_audio([self.item("lainnya")], True)
        with self.assertRaises(ValueError):
            _clean_audio([self.item(wav_b64="@@@")], True)
        with self.assertRaises(ValueError):
            _clean_audio([self.item(wav_b64=base64.b64encode(b"bukan wav" * 20).decode())], True)
        with self.assertRaises(ValueError):
            _clean_audio([self.item(wav_b64=wav_b64(0.2))], True)                # terlalu pendek


class Prompt(unittest.TestCase):
    def test_compare_data_and_levels(self):
        audio = [{"peran": "ruangan_hp", "detik": 2.0, "spektrum": SPEC_ROOM, "wav": b""},
                 {"peran": "mixer", "detik": 2.0, "spektrum": SPEC_MIX, "wav": b""}]
        live = {"running": True, "alerts": [], "levels": [-20.0, -30.0], "labels": {"0": "CH 01 Vokal"}}
        text = ai.build_prompt({"values": {}}, None, [], live, "umum", "", None, None, audio)
        self.assertIn('"selisih_ruangan_minus_mixer_db": [', text)
        self.assertIn("REKAMAN", text)
        self.assertIn("CH 01 Vokal", text)
        self.assertNotIn('"selisih_ruangan_minus_mixer_db"', ai.build_prompt({"values": {}}, None, [], None, "umum", "", None, None, audio[:1]))
        self.assertNotIn("REKAMAN", ai.build_prompt({"values": {}}, None, [], None, "umum", ""))


class Flow(test_ai.ServerAI):
    test_flow_and_key_never_leaves_laptop = None

    def test_audio_reaches_gemini_with_mixer_data(self):
        self.call("/api/ai/config", {"key": KEY})
        audio = [{"peran": "ruangan_hp", "wav_b64": wav_b64(), "spektrum": SPEC_ROOM},
                 {"peran": "mixer", "wav_b64": wav_b64(), "spektrum": SPEC_MIX}]
        code, raw = self.call("/api/ai/analyze", {"situasi": "khotbah", "audio": audio})
        self.assertEqual(code, 400)                                              # izin belum dicentang
        code, raw = self.call("/api/ai/analyze", {"situasi": "khotbah", "audio": audio, "izin": True})
        self.assertEqual(code, 200, raw)
        res = json.loads(raw)
        self.assertEqual([a["peran"] for a in res["audio"]], ["ruangan_hp", "mixer"])
        body = json.loads(FakeGemini.calls[-1][3])
        parts = body["contents"][0]["parts"]
        self.assertEqual(sum(1 for p in parts if "inline_data" in p or "inlineData" in p), 2)
        self.assertIn("selisih_ruangan_minus_mixer_db", parts[0]["text"])
        self.assertNotIn(KEY, json.dumps(body))
        code, raw = self.call("/api/ai/analyze", {"situasi": "khotbah"})         # tanpa rekaman tetap jalan seperti biasa
        self.assertEqual(code, 200)
        self.assertNotIn("audio", json.loads(raw))


if __name__ == "__main__":
    unittest.main()
