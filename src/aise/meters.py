"""Pembacaan dan ringkasan meter (level) mixer. READ-ONLY.

Format blob meter di bawah ini (int32 jumlah little-endian, lalu int16
little-endian, nilai/256 = dB) adalah dugaan dari keluarga X Air/X32 dan
BELUM diverifikasi pada MR18 ini. Indeks meter juga belum dipetakan ke nama
channel; hasil memakai nomor indeks apa adanya.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

CLIP_DB_DEFAULT = -1.0


class MeterError(ValueError):
    pass


def parse_blob(blob: bytes) -> list[float]:
    if len(blob) < 4:
        raise MeterError("blob meter terlalu pendek")
    (count,) = struct.unpack_from("<i", blob, 0)
    if count < 0 or len(blob) < 4 + 2 * count:
        raise MeterError(f"blob meter tidak konsisten (count={count}, panjang={len(blob)})")
    raw = struct.unpack_from("<" + "h" * count, blob, 4)
    return [v / 256.0 for v in raw]


def make_blob(values_db: list[float]) -> bytes:
    """Untuk pengujian (mixer tiruan)."""
    ints = [max(-32768, min(32767, int(round(v * 256)))) for v in values_db]
    return struct.pack("<i", len(ints)) + struct.pack("<" + "h" * len(ints), *ints)


def summarize(frames: list[list[float]], clip_db: float = CLIP_DB_DEFAULT) -> dict:
    if not frames:
        return {"frames": 0, "indices": {}}
    width = min(len(f) for f in frames)
    indices = {}
    for i in range(width):
        col = [f[i] for f in frames]
        indices[str(i)] = {
            "peak_db": round(max(col), 2),
            "mean_db": round(sum(col) / len(col), 2),
            "min_db": round(min(col), 2),
            "clip": max(col) >= clip_db,
        }
    return {"frames": len(frames), "clip_threshold_db": clip_db, "indices": indices}


def record(client, path: str, seconds: float, clip_db: float = CLIP_DB_DEFAULT) -> dict:
    blobs = client.subscribe_meters(path, seconds)
    frames: list[list[float]] = []
    bad = 0
    for blob in blobs:
        try:
            frames.append(parse_blob(blob))
        except MeterError:
            bad += 1
    result = summarize(frames, clip_db)
    result.update({"created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": path,
                   "seconds": seconds, "bad_frames": bad})
    return result


def format_table(result: dict) -> str:
    lines = [f"{result['frames']} frame, {result.get('bad_frames', 0)} frame rusak"]
    lines.append("indeks   puncak(dB)  rata2(dB)  min(dB)  CLIP?")
    for idx, s in result["indices"].items():
        lines.append(f"{idx:>5}   {s['peak_db']:>9}  {s['mean_db']:>9}  {s['min_db']:>7}  {'YA' if s['clip'] else '-'}")
    return "\n".join(lines)


def save(result: dict, out: str | Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
