"""Pengecekan siaran YouTube: ambil cuplikan audio dari link live, analisis di laptop, simpan riwayat.

Butuh program luar `yt-dlp` dan `ffmpeg` (dipasang operator, lihat Pasang-Alat-Siaran.bat). Hanya MEMBACA siaran publik/unlisted
milik operator; tidak menyentuh mixer. Yang dinilai: clipping, kekerasan (perkiraan LUFS), puncak, senyap. Bentuk spektrum
dihitung untuk Gemini/perbandingan tetapi tidak dinilai otomatis (tanpa kalibrasi, mudah salah).
"""

from __future__ import annotations

import array
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .paths import APP_DIR

SECONDS = 15
RATE = 48000
HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
CLIP_FS = 0.99                  # sampel di atas ini dianggap clipping
BANDS = [63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500]
FFT_N = 16384
MAX_HISTORY = 200


class StreamError(Exception):
    pass


def valid_url(url: str) -> str:
    u = str(url or "").strip()
    p = urlparse(u)
    if (len(u) > 300 or any(c.isspace() or ord(c) < 32 for c in u) or p.scheme != "https" or p.hostname not in HOSTS
            or len(p.path) < 2 or p.username or p.password):
        raise ValueError("Link harus alamat YouTube (https://www.youtube.com/... atau https://youtu.be/...).")
    return u


def find_tool(name: str) -> str | None:
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (APP_DIR / "tools", APP_DIR, APP_DIR / "data" / "tools"):
        p = d / exe
        if p.is_file():
            return str(p)
    return shutil.which(name)


def _run(cmd: list[str], timeout: float, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise StreamError("Waktu habis saat mengambil audio siaran.") from exc
    except OSError as exc:
        raise StreamError(f"Program tidak bisa dijalankan: {exc}") from exc


def tools() -> dict:
    return {"yt-dlp": bool(find_tool("yt-dlp")), "ffmpeg": bool(find_tool("ffmpeg"))}


def grab_audio(url: str, seconds: int = SECONDS) -> bytes:
    """Ambil `seconds` detik audio dari siaran (WAV mono 48 kHz)."""
    url = valid_url(url)
    ytdlp, ff = find_tool("yt-dlp"), find_tool("ffmpeg")
    if not ytdlp or not ff:
        raise StreamError("yt-dlp dan ffmpeg belum terpasang. Jalankan Pasang-Alat-Siaran.bat lalu buka ulang aplikasi.")
    r = _run([ytdlp, "-g", "-f", "bestaudio/best", "--no-playlist", "--no-warnings", "--", url], 60)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    if r.returncode != 0 or not out:
        err = (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["tidak ada keterangan"])[-1][:200]
        raise StreamError(f"yt-dlp gagal membuka siaran ({err}). Pastikan siaran sedang live dan berstatus publik atau unlisted; "
                          "bila baru-baru ini YouTube berubah, perbarui yt-dlp (winget upgrade yt-dlp.yt-dlp).")
    direct = out[0].strip()
    if not direct.startswith("https://"):
        raise StreamError("yt-dlp tidak memberi alamat audio yang bisa dibaca.")
    r2 = _run([ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", direct, "-t", str(seconds), "-vn", "-ac", "1",
               "-ar", str(RATE), "-f", "wav", "-"], seconds + 60)
    if r2.returncode != 0 or len(r2.stdout) < RATE * 2 * 2:
        raise StreamError("ffmpeg tidak mendapat audio dari siaran (siaran belum berjalan atau tertunda).")
    return r2.stdout


def to_16k(wav: bytes) -> bytes:
    ff = find_tool("ffmpeg")
    if not ff:
        raise StreamError("ffmpeg belum terpasang.")
    r = _run([ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "wav", "-"], 60, stdin=wav)
    if r.returncode != 0 or not r.stdout:
        raise StreamError("ffmpeg gagal mengecilkan rekaman.")
    return r.stdout


def pcm(wav: bytes) -> tuple[array.array, int]:
    """PCM16 mono dari WAV (juga yang header ukurannya tidak lengkap, seperti keluaran ffmpeg lewat pipa)."""
    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise StreamError("Audio bukan WAV yang valid.")
    pos, rate, size = 12, RATE, None
    while pos + 8 <= len(wav):
        cid, clen = wav[pos:pos + 4], int.from_bytes(wav[pos + 4:pos + 8], "little")
        if cid == b"fmt ":
            ch, rate = int.from_bytes(wav[pos + 10:pos + 12], "little"), int.from_bytes(wav[pos + 12:pos + 16], "little")
            bits = int.from_bytes(wav[pos + 22:pos + 24], "little")
            if ch != 1 or bits != 16:
                raise StreamError("WAV harus mono 16-bit.")
        elif cid == b"data":
            size = min(clen, len(wav) - pos - 8)
            start = pos + 8
            break
        pos += 8 + clen + (clen & 1)
    if size is None:
        raise StreamError("WAV tidak lengkap.")
    a = array.array("h")
    a.frombytes(wav[start:start + size - (size % 2)])
    if sys.byteorder == "big":
        a.byteswap()
    return a, rate


_S1B, _S1A = (1.53512485958697, -2.69169618940638, 1.19839281085285), (-1.69065929318241, 0.73248077421585)
_S2B, _S2A = (1.0, -2.0, 1.0), (-1.99004745483398, 0.99007225036621)


def _biquad(x: list[float], b: tuple, a: tuple) -> list[float]:
    b0, b1, b2 = b
    a1, a2 = a
    z1 = z2 = 0.0
    out = []
    ap = out.append
    for v in x:
        y = b0 * v + z1
        z1 = b1 * v - a1 * y + z2
        z2 = b2 * v - a2 * y
        ap(y)
    return out


def lufs(samples: list[float]) -> float | None:
    """Kekerasan terintegrasi (BS.1770, mono, 48 kHz); None bila terlalu pendek atau senyap."""
    k = _biquad(_biquad(samples, _S1B, _S1A), _S2B, _S2A)
    blk, hop = int(0.4 * RATE), int(0.1 * RATE)
    if len(k) < blk:
        return None
    sq = [v * v for v in k]
    pref = [0.0]
    for v in sq:
        pref.append(pref[-1] + v)
    z = [(pref[i + blk] - pref[i]) / blk for i in range(0, len(k) - blk + 1, hop)]
    z = [v for v in z if v > 0 and -0.691 + 10 * math.log10(v) > -70]
    if not z:
        return None
    rel = -0.691 + 10 * math.log10(sum(z) / len(z)) - 10
    z2 = [v for v in z if -0.691 + 10 * math.log10(v) > rel]
    return -0.691 + 10 * math.log10(sum(z2) / len(z2)) if z2 else None


def _fft(re: list[float], im: list[float]) -> None:
    n = len(re)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]
    ln = 2
    while ln <= n:
        ang = -2 * math.pi / ln
        wr, wi, half = math.cos(ang), math.sin(ang), ln >> 1
        for i in range(0, n, ln):
            cr, ci = 1.0, 0.0
            for k in range(half):
                a, b = i + k, i + k + half
                xr, xi = re[b] * cr - im[b] * ci, re[b] * ci + im[b] * cr
                re[b], im[b] = re[a] - xr, im[a] - xi
                re[a], im[a] = re[a] + xr, im[a] + xi
                cr, ci = cr * wr - ci * wi, cr * wi + ci * wr
        ln <<= 1


def spectrum(x: list[float], frames: int = 6) -> list[float] | None:
    """Level relatif per pita 1/3 oktaf (dB, dinormalkan ke rata-rata 250 Hz - 4 kHz); None bila terlalu pendek."""
    if len(x) < FFT_N:
        return None
    hann = [0.5 - 0.5 * math.cos(2 * math.pi * i / (FFT_N - 1)) for i in range(FFT_N)]
    power = [0.0] * (FFT_N // 2)
    starts = [round(i * (len(x) - FFT_N) / max(1, frames - 1)) for i in range(frames)]
    for s0 in starts:
        re = [x[s0 + i] * hann[i] for i in range(FFT_N)]
        im = [0.0] * FFT_N
        _fft(re, im)
        for k in range(FFT_N // 2):
            power[k] += re[k] * re[k] + im[k] * im[k]
    bin_hz, out = RATE / FFT_N, []
    for i in range(len(BANDS)):
        fc = 1000 * 2 ** ((i - 12) / 3)
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        s = sum(power[k] for k in range(math.ceil(lo / bin_hz), min(len(power), math.ceil(hi / bin_hz))))
        out.append(10 * math.log10(s / len(starts) + 1e-30))
    ref = [out[i] for i, f in enumerate(BANDS) if 250 <= f <= 4000]
    m = sum(ref) / len(ref)
    return [round(v - m, 1) for v in out]


def analyze_wav(wav: bytes) -> dict:
    a, rate = pcm(wav)
    if rate != RATE:
        raise StreamError(f"Sample rate {rate} Hz tidak didukung (harus {RATE}).")
    n = len(a)
    if n < RATE * 2:
        raise StreamError("Cuplikan terlalu pendek.")
    x = [v / 32768.0 for v in a]
    peak = max(abs(v) for v in x)
    ms = sum(v * v for v in x) / n
    rms_db = 10 * math.log10(ms + 1e-20)
    clipped = sum(1 for v in x if abs(v) >= CLIP_FS)
    clip_pct = 100.0 * clipped / n
    peak_db = 20 * math.log10(peak + 1e-10)
    loud = lufs(x)
    angka = {"puncak_db": round(peak_db, 1), "rms_db": round(rms_db, 1), "lufs": None if loud is None else round(loud, 1),
             "clip_persen": round(clip_pct, 3), "sampel_clip": clipped}
    msgs, status = [], "baik"

    def bump(level):
        nonlocal status
        order = {"baik": 0, "perhatian": 1, "bermasalah": 2}
        if order[level] > order[status]:
            status = level

    if rms_db < -60 or peak_db < -50:
        return {"status": "senyap", "ringkas": "Siaran senyap atau sangat pelan",
                "pesan": ["Hampir tidak ada suara di siaran. Cek apakah Bus siaran di-mute, USB terhubung, atau siaran sedang jeda."],
                "angka": angka, "spektrum": None, "detik": round(n / rate, 1)}
    if clip_pct >= 0.5 or clipped >= 200:
        msgs.append(f"Clipping jelas ({clip_pct:.2f}% sampel menyentuh batas). Suara akan terdengar pecah. Turunkan level ke siaran.")
        bump("bermasalah")
    elif clipped >= 10 or clip_pct >= 0.05:
        msgs.append(f"Ada clipping ringan ({clipped} sampel). Turunkan level sedikit.")
        bump("perhatian")
    if loud is not None and loud > -9:
        msgs.append(f"Terlalu keras ({loud:.1f} LUFS). Target siaran YouTube sekitar -14 LUFS; terlalu keras memicu distorsi dan diturunkan YouTube.")
        bump("bermasalah")
    elif loud is not None and loud > -11.5:
        msgs.append(f"Agak keras ({loud:.1f} LUFS; target sekitar -14).")
        bump("perhatian")
    elif loud is not None and loud < -24:
        msgs.append(f"Terlalu pelan ({loud:.1f} LUFS; target sekitar -14). Naikkan level di software streaming.")
        bump("perhatian")
    if peak_db > -1.0 and not any("Clipping" in m or "clipping" in m for m in msgs):
        msgs.append(f"Puncak sangat tinggi ({peak_db:.1f} dBFS); beri ruang minimal 1 dB.")
        bump("perhatian")
    if not msgs:
        msgs.append("Level siaran wajar: tidak ada clipping, kekerasan dalam rentang yang baik.")
    ringkas = {"baik": "Siaran baik", "perhatian": "Siaran perlu perhatian", "bermasalah": "Siaran bermasalah"}[status]
    return {"status": status, "ringkas": ringkas, "pesan": msgs, "angka": angka, "spektrum": spectrum(x), "detik": round(n / rate, 1)}


def sample_for_ai(url: str) -> dict:
    """Cuplikan segar untuk Gemini: WAV 16 kHz mono, spektrum, dan angka analisis lokal."""
    wav = grab_audio(url)
    res = analyze_wav(wav)
    return {"wav": to_16k(wav), "detik": res["detik"], "spektrum": res["spektrum"], "angka": res["angka"]}


class Checker:
    """Pengecekan berkala di latar belakang + riwayat."""

    def __init__(self, log_path: Path, get_cfg):
        self.log_path = Path(log_path)
        self._get_cfg = get_cfg                  # () -> (url, menit)
        self._lock = threading.Lock()
        self._busy = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last: dict | None = None
        self._next = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def reschedule(self) -> None:
        self._next = 0.0

    def _loop(self) -> None:
        while not self._stop.wait(5.0):
            url, minutes = self._get_cfg()
            if url and minutes and time.monotonic() >= self._next and not self._busy.is_set():
                self._next = time.monotonic() + minutes * 60
                self.run_check()

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def run_check(self) -> dict | None:
        url, _ = self._get_cfg()
        if not url or not self._lock.acquire(blocking=False):
            return None
        self._busy.set()
        try:
            try:
                res = analyze_wav(grab_audio(url))
            except StreamError as exc:
                res = {"status": "error", "ringkas": "Tidak bisa memeriksa siaran", "pesan": [str(exc)], "angka": None, "spektrum": None}
            res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **res}
            self.last = res
            self._append(res)
            return res
        finally:
            self._busy.clear()
            self._lock.release()

    def _append(self, res: dict) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: v for k, v in res.items() if k != "spektrum"}, ensure_ascii=False) + "\n")
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_HISTORY + 50:
                self.log_path.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n", encoding="utf-8")
        except OSError:
            pass

    def history(self, n: int = 12) -> list[dict]:
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()[-n:]
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out[::-1]

    def brief(self) -> dict:
        return {"status": self.last["status"] if self.last else None, "ts": self.last["ts"] if self.last else None, "sedang": self.busy}
