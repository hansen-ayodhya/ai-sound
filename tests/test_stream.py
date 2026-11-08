import json
import math
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_ai
from test_ai import KEY, FakeGemini
from aise import stream


def wav(samples, rate=48000, unknown_size=False):
    data = b"".join(struct.pack("<h", max(-32768, min(32767, int(v * 32767)))) for v in samples)
    size = 0xFFFFFFFF if unknown_size else len(data)
    return b"RIFF" + struct.pack("<I", size if unknown_size else 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", size) + data


def sine(amp, secs=5.0, f=997.0):
    return [amp * math.sin(2 * math.pi * f * i / 48000) for i in range(int(48000 * secs))]


class Url(unittest.TestCase):
    def test_accepts_youtube_only(self):
        for u in ("https://www.youtube.com/watch?v=abc123", "https://youtu.be/abc123", "https://www.youtube.com/live/abc123", "https://m.youtube.com/watch?v=x"):
            self.assertEqual(stream.valid_url(u), u)
        for u in ("http://www.youtube.com/watch?v=a", "https://evil.com/watch?v=a", "https://www.youtube.com.evil.com/x", "file:///etc/passwd",
                  "https://www.youtube.com/watch?v=a b", "https://www.youtube.com/watch?v=a\n--exec", "https://user:pw@www.youtube.com/x", "", None,
                  "https://www.youtube.com/" + "a" * 400, "-o /tmp/x"):
            with self.assertRaises(ValueError, msg=str(u)):
                stream.valid_url(u)


class Analyze(unittest.TestCase):
    def test_normal_level(self):
        r = stream.analyze_wav(wav(sine(0.1)))
        self.assertEqual(r["status"], "baik" if r["angka"]["lufs"] >= -24 else "perhatian")
        self.assertAlmostEqual(r["angka"]["lufs"], -23.0, delta=0.5)          # sinus -20 dBFS = -23 LUFS (BS.1770)
        self.assertAlmostEqual(r["angka"]["puncak_db"], -20.0, delta=0.2)
        self.assertEqual(r["angka"]["sampel_clip"], 0)
        self.assertEqual(len(r["spektrum"]), 24)

    def test_good_loudness(self):
        r = stream.analyze_wav(wav(sine(0.2)))
        self.assertAlmostEqual(r["angka"]["lufs"], -17.0, delta=0.5)
        self.assertEqual(r["status"], "baik")

    def test_clipping_and_loud(self):
        r = stream.analyze_wav(wav([max(-1.0, min(1.0, 3 * v)) for v in sine(0.8)]))
        self.assertEqual(r["status"], "bermasalah")
        self.assertIn("Clipping jelas", " ".join(r["pesan"]))
        self.assertGreater(r["angka"]["clip_persen"], 1)
        loud = stream.analyze_wav(wav(sine(0.7)))
        self.assertEqual(loud["status"], "bermasalah")                        # lebih dari -9 LUFS
        self.assertIn("Terlalu keras", " ".join(loud["pesan"]))

    def test_quiet_and_silent(self):
        q = stream.analyze_wav(wav(sine(0.01)))
        self.assertEqual(q["status"], "perhatian")
        self.assertIn("Terlalu pelan", " ".join(q["pesan"]))
        s = stream.analyze_wav(wav([0.0] * 48000 * 4))
        self.assertEqual(s["status"], "senyap")

    def test_pipe_header_with_unknown_size_and_bad_input(self):
        r = stream.analyze_wav(wav(sine(0.1, 3.0), unknown_size=True))
        self.assertAlmostEqual(r["angka"]["lufs"], -23.0, delta=0.6)
        with self.assertRaises(stream.StreamError):
            stream.analyze_wav(b"bukan wav" * 20)
        with self.assertRaises(stream.StreamError):
            stream.analyze_wav(wav(sine(0.1, 1.0)))                           # terlalu pendek
        with self.assertRaises(stream.StreamError):
            stream.analyze_wav(wav(sine(0.1, 3.0), rate=16000))


class Grab(unittest.TestCase):
    def fake(self, calls, ytdlp_out=b"https://rr1.googlevideo.com/live.m3u8\n", ytdlp_rc=0):
        def run(cmd, timeout, stdin=None):
            calls.append(cmd)
            if "yt-dlp" in cmd[0]:
                return mock.Mock(returncode=ytdlp_rc, stdout=ytdlp_out, stderr=b"ERROR: tidak live\n")
            return mock.Mock(returncode=0, stdout=wav(sine(0.1, 3.0), unknown_size=True), stderr=b"")
        return run

    def test_commands_are_safe_and_result_is_wav(self):
        calls = []
        with mock.patch.object(stream, "find_tool", lambda n: "/x/" + n), mock.patch.object(stream, "_run", self.fake(calls)):
            out = stream.grab_audio("https://www.youtube.com/watch?v=abc")
        self.assertEqual(calls[0][-2:], ["--", "https://www.youtube.com/watch?v=abc"])      # URL setelah '--': tidak bisa jadi opsi
        self.assertTrue(all(isinstance(c, list) for c in calls))                          # tanpa shell
        self.assertIn("-t", calls[1])
        self.assertEqual(stream.pcm(out)[1], 48000)

    def test_errors_are_explained(self):
        with mock.patch.object(stream, "find_tool", lambda n: None):
            with self.assertRaises(stream.StreamError) as e:
                stream.grab_audio("https://youtu.be/abc")
            self.assertIn("Pasang-Alat-Siaran.bat", str(e.exception))
        with mock.patch.object(stream, "find_tool", lambda n: "/x/" + n), mock.patch.object(stream, "_run", self.fake([], ytdlp_rc=1)):
            with self.assertRaises(stream.StreamError) as e:
                stream.grab_audio("https://youtu.be/abc")
            self.assertIn("yt-dlp gagal", str(e.exception))
        with mock.patch.object(stream, "find_tool", lambda n: "/x/" + n), mock.patch.object(stream, "_run", self.fake([], ytdlp_out=b"file:///etc/passwd\n")):
            with self.assertRaises(stream.StreamError):
                stream.grab_audio("https://youtu.be/abc")
        with self.assertRaises(ValueError):
            stream.grab_audio("https://evil.com/x")


class CheckerTests(unittest.TestCase):
    def test_records_history_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ["https://youtu.be/abc", 10]
            c = stream.Checker(Path(tmp) / "h.jsonl", lambda: tuple(cfg))
            with mock.patch.object(stream, "grab_audio", lambda u: wav(sine(0.1, 3.0))):
                r = c.run_check()
            self.assertEqual(r["status"], "baik" if r["angka"]["lufs"] >= -24 else "perhatian")
            with mock.patch.object(stream, "grab_audio", mock.Mock(side_effect=stream.StreamError("siaran belum live"))):
                r2 = c.run_check()
            self.assertEqual(r2["status"], "error")
            h = c.history()
            self.assertEqual([x["status"] for x in h][0], "error")
            self.assertNotIn("spektrum", h[0])
            self.assertEqual(c.brief()["status"], "error")
            cfg[0] = ""
            self.assertIsNone(c.run_check())                                   # tanpa link: tidak melakukan apa-apa


class ServerStream(test_ai.ServerAI):
    test_flow_and_key_never_leaves_laptop = None

    def test_config_check_and_gemini(self):
        self.assertEqual(self.call("/api/prefs", {"youtube_url": "http://x.com/a"})[0], 400)
        self.assertEqual(self.call("/api/prefs", {"stream_check_min": 7})[0], 400)
        self.assertEqual(self.call("/api/prefs", {"youtube_url": "https://youtu.be/abc", "stream_check_min": 0})[0], 200)
        code, raw = self.call("/api/stream")
        v = json.loads(raw)
        self.assertEqual((v["url"], v["menit"]), ("https://youtu.be/abc", 0))
        self.assertEqual(set(v["alat"]), {"yt-dlp", "ffmpeg"})
        with mock.patch.object(stream, "grab_audio", lambda u: wav(sine(0.1, 3.0))), mock.patch.object(stream, "to_16k", lambda w: wav(sine(0.1, 3.0), rate=16000)):
            self.assertEqual(self.call("/api/stream/cek", {})[0], 202)
            end = time.monotonic() + 15
            while time.monotonic() < end and not json.loads(self.call("/api/stream")[1])["terakhir"]:
                time.sleep(0.1)
            self.assertIn(json.loads(self.call("/api/stream")[1])["terakhir"]["status"], ("baik", "perhatian"))
            self.assertEqual(self.call("/api/stream/gemini", {})[0], 502)                 # Gemini belum diatur
            self.call("/api/ai/config", {"key": KEY})
            code, raw = self.call("/api/stream/gemini", {})
            self.assertEqual(code, 200, raw)
            res = json.loads(raw)
            self.assertEqual(res["audio"][0]["peran"], "youtube")
            body = json.loads(FakeGemini.calls[-1][3])
            parts = body["contents"][0]["parts"]
            self.assertEqual(sum(1 for p in parts if "inline_data" in p or "inlineData" in p), 1)
            self.assertIn('"analisis_lokal"', parts[0]["text"])
            self.assertIn("siaran YouTube", json.dumps(parts))
            self.assertNotIn(KEY, json.dumps(body))
        self.call("/api/prefs", {"youtube_url": ""})
        self.assertEqual(self.call("/api/stream/gemini", {})[0], 400)                    # tanpa link


if __name__ == "__main__":
    unittest.main()


class ToolDiscovery(unittest.TestCase):
    def test_finds_tools_folder_and_winget_locations_without_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, local = Path(tmp) / "app", Path(tmp) / "local"
            (app / "tools").mkdir(parents=True)
            (app / "tools" / "yt-dlp.exe").write_bytes(b"x")
            wg = local / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_x" / "ffmpeg-7" / "bin"
            wg.mkdir(parents=True)
            (wg / "ffmpeg.exe").write_bytes(b"x")
            with mock.patch.object(stream, "APP_DIR", app), mock.patch.object(stream, "_exe_name", lambda n: n + ".exe"), \
                    mock.patch.dict(stream.os.environ, {"LOCALAPPDATA": str(local)}), mock.patch.object(stream.shutil, "which", lambda n: None):
                self.assertEqual(stream.find_tool("yt-dlp"), str(app / "tools" / "yt-dlp.exe"))
                self.assertEqual(stream.find_tool("ffmpeg"), str(wg / "ffmpeg.exe"))
                self.assertEqual(stream.tools(), {"yt-dlp": True, "ffmpeg": True})
            with mock.patch.object(stream, "APP_DIR", Path(tmp) / "kosong"), mock.patch.object(stream.shutil, "which", lambda n: None), \
                    mock.patch.dict(stream.os.environ, {"LOCALAPPDATA": ""}):
                self.assertEqual(stream.tools(), {"yt-dlp": False, "ffmpeg": False})
                with self.assertRaises(stream.StreamError) as e:
                    stream.grab_audio("https://youtu.be/abc")
                self.assertIn("folder:", str(e.exception))
                self.assertIn("tools", str(e.exception))
