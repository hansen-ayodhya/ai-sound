"""Memori belajar ruangan (lokal): saran, perubahan, masalah, hasil ukur, dan penilaian operator.

Disimpan di data/memori/catatan.jsonl, tidak berisi API key, dan tidak pernah dikirim sendiri.
Hanya ringkasan kecil ("profil") yang ikut ke Gemini saat analisis, sebagai riwayat dengan bukti terbatas.
Operator bisa melihat dan menghapusnya (halaman Memori AI).
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path

MAX_LINES = 2000
JENIS = ("saran", "selesai", "perubahan", "masalah", "ukur", "nilai")
MIN_RATED = 5            # kurang dari ini: data belum cukup untuk dianggap pola
PROFILE_ITEMS = 20


def _s(x, n=120) -> str:
    return str(x if x is not None else "")[:n]


class Memori:
    def __init__(self, folder: Path):
        self.path = Path(folder) / "catatan.jsonl"
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("jenis") in JENIS:
                out.append(d)
        return out

    def _write(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(self.path)

    def catat(self, jenis: str, unik_id: str | None = None, **kw) -> bool:
        """Tambah satu catatan. Dengan unik_id: lewati bila id yang sama untuk jenis ini sudah ada."""
        if jenis not in JENIS:
            raise ValueError(f"jenis catatan tidak dikenal: {jenis}")
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "jenis": jenis, **kw}
        if unik_id is not None:
            row["uid"] = unik_id
        with self._lock:
            if unik_id is not None and any(r["jenis"] == jenis and r.get("uid") == unik_id for r in self._read()):
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows = self._read()
            if len(rows) > MAX_LINES + 300:
                self._write(rows[-MAX_LINES:])
        return True

    def catat_ukur(self, body: dict) -> dict:
        bands = []
        for b in (body.get("band") or [])[:8]:
            try:
                hz, db, q = float(b["hz"]), float(b["db"]), float(b["q"])
            except (KeyError, TypeError, ValueError):
                continue
            if 20 <= hz <= 20000 and -20 <= db <= 20 and 0.1 <= q <= 20:
                bands.append({"hz": round(hz), "db": round(db, 1), "q": round(q, 1), "jenis": _s(b.get("jenis"), 20)})
        titik = body.get("titik")
        if not isinstance(titik, int) or isinstance(titik, bool) or not 1 <= titik <= 50:
            raise ValueError("Jumlah titik ukur tidak valid.")
        self.catat("ukur", mic=_s(body.get("mic"), 20), target=_s(body.get("target"), 20), titik=titik, band=bands)
        return {"disimpan": True, "band": len(bands)}

    def semua(self, jenis: str | None = None, n: int | None = None) -> list[dict]:
        with self._lock:
            rows = [r for r in self._read() if jenis is None or r["jenis"] == jenis]
        return rows[-n:] if n else rows

    def hapus(self, jenis: str | None = None) -> int:
        with self._lock:
            rows = self._read()
            keep = [] if jenis is None else [r for r in rows if r["jenis"] != jenis]
            if jenis is not None and jenis not in JENIS:
                raise ValueError("jenis tidak dikenal")
            self._write(keep)
            return len(rows) - len(keep)

    def profil(self) -> dict:
        """Ringkasan kecil untuk Gemini dan halaman Memori. Apa adanya: tidak menyimpulkan di luar data."""
        rows = self.semua()
        jumlah = Counter(r["jenis"] for r in rows)
        nilai: dict[str, str] = {}
        for r in rows:
            if r["jenis"] == "nilai":
                nilai[r.get("id", "")] = r.get("nilai", "")
        masalah = Counter((r.get("ch"), r.get("jenis_masalah")) for r in rows if r["jenis"] == "masalah")
        dinilai = []
        for r in [r for r in rows if r["jenis"] == "selesai"][-PROFILE_ITEMS:]:
            dinilai.append({"ch": r.get("ch"), "masalah": _s(r.get("masalah"), 90), "perubahan": r.get("items", [])[:4],
                            "nilai": nilai.get(r.get("id", "")) or "belum dinilai"})
        ukur = next((r for r in reversed(rows) if r["jenis"] == "ukur"), None)
        n_bagus = sum(1 for v in nilai.values() if v == "bagus")
        n_kurang = sum(1 for v in nilai.values() if v == "kurang")
        return {
            "jumlah_catatan": dict(jumlah),
            "masalah_berulang": [{"ch": c, "jenis": j, "kali": k} for (c, j), k in masalah.most_common(8) if k >= 2],
            "perubahan_dinilai": dinilai,
            "penilaian": {"bagus": n_bagus, "kurang": n_kurang},
            "ukur_ruangan_terakhir": {k: ukur.get(k) for k in ("ts", "mic", "target", "titik", "band")} if ukur else None,
            "cukup_data": n_bagus + n_kurang >= MIN_RATED,
            "catatan": ("Data penilaian belum cukup untuk menyimpulkan pola; perlakukan hanya sebagai petunjuk."
                        if n_bagus + n_kurang < MIN_RATED else
                        "Riwayat ini bukti terbatas dari satu ruangan; bukan kepastian."),
        }
