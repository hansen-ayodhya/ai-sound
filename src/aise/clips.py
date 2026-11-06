"""Klip suara: rekaman pendek (mis. dari HP di kursi jemaat) untuk dinilai.

Browser yang membuka file rekaman (m4a/mp3/wav), memotong bagian yang dipilih,
menghitung spektrum, lalu mengirim WAV mono 16-bit (16 kHz) + angka analisisnya
ke sini. Klip disimpan di data/clips/ untuk perbandingan sebelum/sesudah.
Klip hanya dikirim ke Gemini bila operator menekan tombol dan mencentang izin.
"""

from __future__ import annotations

import base64
import json
import re
import struct
import time
from pathlib import Path

MAX_SECONDS = 31.0
MAX_CLIPS = 50
ALLOWED_DURATIONS = (5, 10, 20, 30)


class ClipError(ValueError):
    pass


def wav_info(data: bytes) -> dict:
    """Periksa WAV PCM 16-bit sederhana; kembalikan rate, kanal, durasi."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ClipError("File bukan WAV yang valid.")
    pos, fmt, size = 12, None, None
    while pos + 8 <= len(data):
        cid, clen = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", data, pos + 8)
        elif cid == b"data":
            size = min(clen, len(data) - pos - 8)
            break
        pos += 8 + clen + (clen & 1)
    if not fmt or size is None:
        raise ClipError("WAV tidak lengkap.")
    audio_fmt, ch, rate, _, _, bits = fmt
    if audio_fmt != 1 or bits != 16 or ch not in (1, 2) or not 8000 <= rate <= 48000:
        raise ClipError("WAV harus PCM 16-bit, mono/stereo, 8–48 kHz.")
    dur = size / (rate * ch * 2)
    if dur <= 0.5 or dur > MAX_SECONDS:
        raise ClipError("Durasi klip harus antara 0,5 dan 30 detik.")
    return {"rate": rate, "channels": ch, "seconds": round(dur, 2)}


def _clean_meta(meta) -> dict:
    meta = meta if isinstance(meta, dict) else {}
    situasi = meta.get("situasi") if meta.get("situasi") in ("umum", "lagu", "khotbah", "soundcheck") else "umum"
    return {"posisi": str(meta.get("posisi", "")).strip()[:60], "situasi": situasi,
            "catatan": str(meta.get("catatan", "")).strip()[:300],
            "nama_file": str(meta.get("nama_file", "")).strip()[:120],
            "mulai_detik": float(meta.get("mulai_detik") or 0)}


def _num(x, lo=-200.0, hi=200.0):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi


def _clean_analysis(a) -> dict:
    """Simpan hanya angka analisis yang dikenal dan masuk akal."""
    a = a if isinstance(a, dict) else {}
    out: dict = {}
    for k in ("rms_db", "peak_db"):
        if _num(a.get(k)):
            out[k] = round(float(a[k]), 1)
    bands = a.get("bands")
    if isinstance(bands, list):
        out["bands"] = [{"hz": float(b["hz"]), "db": round(float(b["db"]), 1)} for b in bands[:40]
                        if isinstance(b, dict) and _num(b.get("hz"), 10, 24000) and _num(b.get("db"))]
    groups = a.get("groups")
    if isinstance(groups, dict):
        out["groups"] = {str(k)[:20]: round(float(v), 1) for k, v in list(groups.items())[:10] if _num(v)}
    peaks = a.get("peaks")
    if isinstance(peaks, list):
        out["peaks"] = [{"hz": round(float(p["hz"])), "tonjolan_db": round(float(p["tonjolan_db"]), 1),
                         "persisten": round(float(p.get("persisten", 0)), 2)}
                        for p in peaks[:10] if isinstance(p, dict) and _num(p.get("hz"), 20, 20000)
                        and _num(p.get("tonjolan_db"), 0, 80)]
    ind = a.get("indikasi")
    if isinstance(ind, list):
        out["indikasi"] = [str(x)[:200] for x in ind[:10]]
    return out


class ClipStore:
    def __init__(self, data: Path):
        self.dir = data / "clips"

    def save(self, wav_b64: str, meta, analysis) -> dict:
        try:
            raw = base64.b64decode(wav_b64 or "", validate=True)
        except ValueError:
            raise ClipError("Data audio rusak.") from None
        info = wav_info(raw)
        self.dir.mkdir(parents=True, exist_ok=True)
        cid = time.strftime("klip-%Y%m%d-%H%M%S")
        n = 1
        while (self.dir / f"{cid}.wav").exists():
            n += 1
            cid = time.strftime("klip-%Y%m%d-%H%M%S") + f"-{n}"
        (self.dir / f"{cid}.wav").write_bytes(raw)
        rec = {"id": cid, "dibuat": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "audio": info,
               "meta": _clean_meta(meta), "analisis": _clean_analysis(analysis), "ai": None}
        self._write(rec)
        self._prune()
        return rec

    def _write(self, rec: dict) -> None:
        (self.dir / f"{rec['id']}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")

    def _prune(self) -> None:
        recs = sorted(self.dir.glob("klip-*.json"))
        for old in recs[:-MAX_CLIPS]:
            old.unlink(missing_ok=True)
            old.with_suffix(".wav").unlink(missing_ok=True)

    def get(self, cid: str) -> tuple[dict, bytes]:
        if not re.fullmatch(r"klip-\d{8}-\d{6}(-\d+)?", cid or ""):
            raise ClipError("Klip tidak dikenal.")
        try:
            rec = json.loads((self.dir / f"{cid}.json").read_text(encoding="utf-8"))
            return rec, (self.dir / f"{cid}.wav").read_bytes()
        except (OSError, ValueError):
            raise ClipError("Klip tidak ditemukan.") from None

    def set_ai(self, cid: str, result: dict) -> None:
        rec, _ = self.get(cid)
        rec["ai"] = result
        self._write(rec)

    def list(self) -> list[dict]:
        out = []
        for f in sorted(self.dir.glob("klip-*.json"), reverse=True):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out
