"""Cek balance terpandu: bandingkan level vokal dan musik di mic ruangan (meter live), tanpa kalibrasi mutlak.

Mic dipakai sebagai alat banding: tahap vokal saja dan musik saja memakai mic, gain, dan posisi yang sama, jadi selisihnya
(vokal - musik, dB) tidak bergantung pada kepekaan mic. Yang diukur hanya LEVEL meter (bukan frekuensi); mic vokal meredam bass,
jadi angka bukan ukuran mutlak. Penilaian telinga operator ("pas") dicatat sebagai acuan pribadi ruangan.
"""

from __future__ import annotations

import math
import statistics
import threading
import time

from . import channels, convert

MUSIC_TYPES = {"bass", "gitar_listrik", "keyboard", "drum_elektrik", "playback"}
VOICE_TYPES = {"vokal_utama", "vokal_singer", "khotbah"}
ORDER = ["hening", "vokal", "musik", "bersama"]
DUR = {"peta": 8.0, "hening": 6.0, "vokal": 12.0, "musik": 12.0, "bersama": 10.0}
LEAD = 3.0                 # detik bersiap sebelum merekam
HZ = 20.0                  # pembacaan meter per detik
MIN_SNR = 6.0
MAP_MIN_RISE, MAP_MIN_MARGIN = 10.0, 6.0
MAX_CUT_DB = 9.0
DEFAULT_CUT_DB = 3.0
VERDICTS = ("musik_keras", "pas", "vokal_keras")


def _pw(db: float) -> float:
    return 10 ** (max(-120.0, db) / 10)


def _db(p: float) -> float:
    return 10 * math.log10(max(p, 1e-12))


def active_db(frames: list[float]) -> float | None:
    """Level aktif: rata-rata daya dari 50% frame tertinggi (mengabaikan jeda di antara kata atau not)."""
    if not frames:
        return None
    top = sorted((_pw(v) for v in frames), reverse=True)[: max(1, len(frames) // 2)]
    return _db(sum(top) / len(top))


def noise_db(frames: list[float]) -> float | None:
    return _db(statistics.median(_pw(v) for v in frames)) if frames else None


def stats(frames: list[float]) -> dict | None:
    if not frames:
        return None
    return {"aktif": round(active_db(frames), 1), "noise": round(noise_db(frames), 1), "puncak": round(max(frames), 1), "n": len(frames)}


def detect_channel(by_ch: dict[int, list[float]]) -> dict:
    """Channel yang paling naik saat mic diketuk: (puncak ke-95 - median), harus jelas lebih tinggi dari channel lain."""
    rises = {}
    for ch, fr in by_ch.items():
        if len(fr) >= 10:
            s = sorted(fr)
            rises[ch] = s[int(0.95 * (len(s) - 1))] - statistics.median(fr)
    if not rises:
        return {"ch": None, "alasan": "tidak ada data meter"}
    best = max(rises, key=rises.get)
    others = sorted((v for c, v in rises.items() if c != best), reverse=True)
    margin = rises[best] - (others[0] if others else 0.0)
    if rises[best] < MAP_MIN_RISE:
        return {"ch": None, "alasan": f"tidak ada channel yang naik jelas (maks {rises[best]:.0f} dB). Ketuk mic lebih keras."}
    if margin < MAP_MIN_MARGIN:
        return {"ch": None, "alasan": "beberapa channel naik bersamaan; ulangi dan ketuk hanya mic itu."}
    return {"ch": best, "naik_db": round(rises[best], 1)}


def groups(profile: dict | None) -> tuple[list[int], list[int]]:
    voc, mus = [], []
    for s in (profile or {}).get("sources", []):
        (voc if s.get("jenis") in VOICE_TYPES else mus if s.get("jenis") in MUSIC_TYPES else []).extend(s.get("ch", []))
    return sorted(set(c for c in voc if 1 <= c <= 16)), sorted(set(c for c in mus if 1 <= c <= 16))


def evaluate(ph: dict, notes: list[str]) -> dict:
    """ph: statistik mic per tahap {'hening','vokal','musik','bersama'}; notes: peringatan dari pemeriksaan lain."""
    out = {"peringatan": list(notes)}
    h, v, m, b = ph.get("hening"), ph.get("vokal"), ph.get("musik"), ph.get("bersama")
    if not (h and v and m):
        out["peringatan"].append("Data belum lengkap.")
        return out
    out.update(noise=h["noise"], vokal=v["aktif"], musik=m["aktif"], bersama=b["aktif"] if b else None,
               selisih=round(v["aktif"] - m["aktif"], 1), snr_vokal=round(v["aktif"] - h["noise"], 1), snr_musik=round(m["aktif"] - h["noise"], 1))
    if any(x and x["puncak"] >= -1.0 for x in (h, v, m, b)):
        out["peringatan"].append("Mic sempat clipping: turunkan gain channel mic ruangan lalu ulangi.")
    if out["snr_vokal"] < MIN_SNR:
        out["peringatan"].append(f"Vokal hanya {out['snr_vokal']:.0f} dB di atas bising ruangan di mic: terlalu pelan untuk dinilai. Ulangi lebih keras atau naikkan gain mic.")
    if out["snr_musik"] < MIN_SNR:
        out["peringatan"].append(f"Musik hanya {out['snr_musik']:.0f} dB di atas bising ruangan di mic: terlalu pelan untuk dinilai.")
    if b and b["aktif"] < max(v["aktif"], m["aktif"]) - 2.0:
        out["peringatan"].append("Tahap bersama lebih pelan dari tahap tunggal: kemungkinan fader diubah atau pemain tidak bermain seperti tadi.")
    return out


def recommend(selisih: float, verdict: str, target_pas: float | None) -> dict:
    """Berapa dB musik diturunkan (atau vokal diturunkan) menurut penilaian telinga operator."""
    if verdict == "musik_keras":
        d = (target_pas - selisih) if target_pas is not None and target_pas > selisih else DEFAULT_CUT_DB
        d = max(1.0, min(MAX_CUT_DB, round(d * 2) / 2))
        return {"turunkan": "musik", "db": d, "dasar": "target dari penilaian 'pas' sebelumnya" if target_pas is not None and target_pas > selisih else "langkah awal umum"}
    if verdict == "vokal_keras":
        d = (selisih - target_pas) if target_pas is not None and selisih > target_pas else DEFAULT_CUT_DB
        d = max(1.0, min(MAX_CUT_DB, round(d * 2) / 2))
        return {"turunkan": "vokal", "db": d, "dasar": "target dari penilaian 'pas' sebelumnya" if target_pas is not None and selisih > target_pas else "langkah awal umum"}
    return {"turunkan": None, "db": 0.0, "dasar": "balance sudah pas menurut telinga Anda"}


def findings(result: dict, rec: dict, profile: dict | None, values: dict) -> list[dict]:
    """Usulan penurunan fader channel musik (atau vokal) sebesar rec['db']."""
    if not rec.get("turunkan"):
        return []
    voc, mus = groups(profile)
    chs = mus if rec["turunkan"] == "musik" else voc
    out = []
    for n in chs:
        fa, on = values.get(f"/ch/{n:02d}/mix/fader"), values.get(f"/ch/{n:02d}/mix/on")
        if not fa or fa[0] is None or (on and on[0] == 0):
            continue
        cur = convert.fader_db(fa[0])
        if cur == float("-inf"):
            continue
        tgt = max(-60.0, round(cur - rec["db"], 1))
        src = channels.source_for(profile, n) or {}
        nm = src.get("nama") or f"CH {n:02d}"
        out.append({
            "id": f"balance-{rec['turunkan']}-ch{n}", "ch": n, "sumber": "program", "severity": "perhatian", "channel": f"CH {n:02d}",
            "masalah": f"{nm}: {'musik' if rec['turunkan'] == 'musik' else 'vokal'} terlalu keras di posisi mic ruangan",
            "penyebab": "Penilaian telinga operator di posisi mic ruangan menyatakan "
                        + ("musik terlalu keras dibanding vokal." if rec["turunkan"] == "musik" else "vokal terlalu keras dibanding musik."),
            "data": f"Selisih vokal - musik di mic: {result.get('selisih')} dB; bising ruangan {result.get('noise')} dB. Dasar angka: {rec['dasar']}. "
                    "Level dibaca dari meter (bukan frekuensi); mic vokal meredam bass.",
            "uji": "Putar musik dan vokal bersama seperti saat ibadah, lalu dengar di posisi mic. Ulangi cek balance setelah fader diturunkan.",
            "rekomendasi": f"Turunkan fader {nm} sekitar {rec['db']:g} dB (dari {cur:+.1f} ke {tgt:+.1f} dB); semua sumber di kelompok ini diturunkan sama supaya perbandingan antar-musik tetap.",
            "risiko": "Rendah. Bila terlalu pelan, naikkan kembali sebagian.",
            "usulan": [{"alamat": f"/ch/{n:02d}/mix/fader", "sekarang": fa[0], "usulan": round(convert.fader_raw(tgt), 4), "teks": f"Fader: {cur:+.1f} → {tgt:+.1f} dB"}],
        })
    return out


class Session:
    """Satu sesi cek balance. Pembaca meter dan nilai setting disuntikkan supaya mudah diuji."""

    def __init__(self, get_levels, get_values, profile: dict | None, mic_ch: int, check_map: bool = True, scale: float = 1.0, sleep=time.sleep):
        self.get_levels, self.get_values, self.profile, self.mic_ch = get_levels, get_values, profile, mic_ch
        self.scale, self._sleep = scale, sleep
        self.voc, self.mus = groups(profile)
        self.queue = (["peta"] if check_map else []) + ORDER
        self.fase = self.queue[0]
        self.status = "menunggu"            # menunggu | hitung_mundur | merekam | selesai | batal | galat
        self.sisa = 0.0
        self.ph: dict[str, dict] = {}
        self.notes: list[str] = []
        self.peta: dict | None = None
        self.hasil: dict | None = None
        self.verdict: str | None = None
        self.rekomendasi: dict | None = None
        self.pesan = ""
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def mulai(self) -> None:
        with self._lock:
            if self.status not in ("menunggu",):
                raise ValueError("Tahap ini sedang berjalan atau sesi sudah selesai.")
            self.status, self.pesan = "hitung_mundur", ""
            self._thread = threading.Thread(target=self._run_phase, daemon=True)
            self._thread.start()

    def batal(self) -> None:
        self._abort.set()
        self.status = "batal"

    def _snap(self) -> dict:
        v = self.get_values() or {}
        keys = [f"/headamp/{self.mic_ch:02d}/gain", f"/ch/{self.mic_ch:02d}/mix/fader"] + [f"/ch/{c:02d}/mix/fader" for c in self.voc + self.mus]
        return {k: (v.get(k) or [None])[0] for k in keys}

    def _run_phase(self) -> None:
        fase, dur = self.fase, DUR[self.fase] * self.scale
        try:
            steps = max(1, int(LEAD * 10))
            for i in range(steps):
                if self._abort.is_set():
                    return
                self.sisa = LEAD * self.scale * (1 - i / steps)
                self._sleep(LEAD * self.scale / steps)
            if self._abort.is_set():
                return
            self.status, start = "merekam", self._snap()
            mic, per_ch = [], {c: [] for c in range(1, 17)}
            n = max(12, int(dur * HZ))
            for i in range(n):
                if self._abort.is_set():
                    return
                self.sisa = dur * (1 - i / n)
                lv = self.get_levels() or []
                if len(lv) >= self.mic_ch:
                    mic.append(lv[self.mic_ch - 1])
                for c in per_ch:
                    if len(lv) >= c:
                        per_ch[c].append(lv[c - 1])
                self._sleep(dur / n)
            end = self._snap()
            changed = [k for k in start if start[k] is not None and end.get(k) is not None and abs(start[k] - end[k]) > 0.01]
            if changed:
                self.notes.append(f"Fader atau gain berubah selama tahap '{fase}': hasil kurang adil. Ulangi bila perlu.")
            if fase == "peta":
                self.peta = detect_channel({c: f for c, f in per_ch.items()})
                if self.peta["ch"] != self.mic_ch:
                    self.pesan = (f"Yang naik saat diketuk adalah CH {self.peta['ch']:02d}, bukan CH {self.mic_ch:02d}." if self.peta.get("ch")
                                  else self.peta.get("alasan", "Pemetaan meter belum jelas."))
                    self.status = "galat"
                    return
            else:
                if not mic:
                    self.pesan, self.status = "Meter live tidak memberi data. Pastikan pantau live berjalan.", "galat"
                    return
                self.ph[fase] = stats(mic)
                self._cross_check(fase, per_ch)
            self.queue.pop(0)
            if self.queue:
                self.fase, self.status = self.queue[0], "menunggu"
            else:
                self.hasil = evaluate(self.ph, self.notes)
                self.status = "selesai"
            self.sisa = 0.0
        except Exception as exc:  # sesi tidak boleh mati diam-diam
            self.status, self.pesan = "galat", f"{type(exc).__name__}: {exc}"

    def _cross_check(self, fase: str, per_ch: dict[int, list[float]]) -> None:
        def act(chs):
            return max((active_db(per_ch[c]) for c in chs if per_ch.get(c)), default=None)
        if fase == "vokal" and self.mus and self.voc:
            m, v = act(self.mus), act(self.voc)
            if m is not None and v is not None and m > v - 15 and m > -50:
                self.notes.append("Musik masih terdengar saat tahap vokal saja (meter channel musik masih bergerak). Hentikan musik lalu ulangi tahap ini.")
        if fase == "musik" and self.mus and self.voc:
            m, v = act(self.mus), act(self.voc)
            if m is not None and v is not None and v > m - 15 and v > -50:
                self.notes.append("Vokal masih terdengar saat tahap musik saja (meter channel vokal masih bergerak). Diamkan vokal lalu ulangi tahap ini.")

    def ulangi_tahap(self) -> None:
        with self._lock:
            if self.status not in ("menunggu", "galat"):
                raise ValueError("Tidak bisa mengulang sekarang.")
            self.status, self.pesan = "menunggu", ""

    def nilai(self, verdict: str, target_pas: float | None) -> dict:
        if self.status != "selesai" or not self.hasil or "selisih" not in self.hasil:
            raise ValueError("Selesaikan semua tahap dulu.")
        if verdict not in VERDICTS:
            raise ValueError("Penilaian tidak dikenal.")
        self.verdict = verdict
        self.rekomendasi = recommend(self.hasil["selisih"], verdict, target_pas)
        return self.rekomendasi

    def view(self) -> dict:
        return {"fase": self.fase, "status": self.status, "sisa": round(self.sisa, 1), "mic_ch": self.mic_ch, "pesan": self.pesan,
                "tahap_selesai": [k for k in ORDER if k in self.ph], "peta": self.peta, "hasil": self.hasil, "penilaian": self.verdict,
                "rekomendasi": self.rekomendasi, "durasi": DUR, "ada_peta": "peta" in self.queue or self.peta is not None}
