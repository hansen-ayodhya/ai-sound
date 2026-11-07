"""Pemantauan LIVE (READ-ONLY): stream meter terus-menerus + aturan cepat lokal.

Aturan di sini berjalan di laptop tanpa internet dan tanpa AI cloud, supaya
peringatan muncul dalam sepersekian detik.

BELUM DIVERIFIKASI pada MR18:
- path dan format meter (lihat meters.py),
- pemetaan indeks meter ke channel (di sini PERKIRAAN: indeks 0..15 = CH 01..16),
- apakah meter input diambil sebelum atau sesudah fader.
Karena itu keseimbangan musik vs vokal BELUM dinilai: itu butuh meter yang
pasti sesudah fader/bus.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

from . import convert
from .client import ReadOnlyClient
from .meters import MeterError, parse_blob

CLIP_DB = -1.0          # di atas ini dianggap clipping
HOT_DB = -3.0           # di atas ini dianggap "panas"
HOT_FRAMES = 3          # berapa kali panas ...
HOT_WINDOW = 5.0        # ... dalam berapa detik
WEAK_MAX = -40.0        # puncak di bawah ini = sinyal lemah ...
WEAK_FLOOR = -60.0      # ... tapi di atas ini (berarti memang ada sinyal)
WEAK_WINDOW = 30.0      # dinilai selama berapa detik
STALE_S = 3.0           # tanpa frame selama ini = koneksi terputus
COOLDOWN = {"clip": 10.0, "hot": 20.0, "weak": 120.0, "stale": 30.0}
N_MAPPED_CHANNELS = 16  # PERKIRAAN pemetaan indeks -> channel
MAX_RUN_S = 4 * 3600    # berhenti otomatis setelah 4 jam


@dataclass
class Alert:
    t: float
    time: str
    kind: str       # clip | hot | weak | stale
    severity: str   # bad | warn | info
    index: int
    label: str
    level_db: float
    message: str


def channel_for_index(i: int) -> int | None:
    """PERKIRAAN: indeks meter 0..15 dianggap CH 01..16."""
    return i + 1 if 0 <= i < N_MAPPED_CHANNELS else None


def labels_from_dump(dump: dict | None, profile: dict | None = None) -> dict[int, str]:
    from .channels import source_for
    labels: dict[int, str] = {}
    values = (dump or {}).get("values", {})
    for i in range(N_MAPPED_CHANNELS):
        ch = channel_for_index(i)
        name = values.get(f"/ch/{ch:02d}/config/name")
        name = str(name[0]).strip() if name and name[0] not in (None, "") else ""
        src = source_for(profile, ch)
        labels[i] = f"CH {ch:02d}" + (f" {name}" if name else "") + (f" ({src['nama']})" if src else "")
    return labels


def inactive_from_dump(dump: dict | None) -> set[int]:
    """Indeks meter channel yang sedang di-mute di mixer (tidak perlu peringatan 'sinyal kecil')."""
    values = (dump or {}).get("values", {})
    out = set()
    for i in range(N_MAPPED_CHANNELS):
        v = values.get(f"/ch/{channel_for_index(i):02d}/mix/on")
        if v and v[0] == 0:
            out.add(i)
    return out


def analyze_eq_problems(dump: dict | None, profile: dict | None = None) -> list[dict]:
    """Analisis setting EQ per channel dan deteksi masalah: boomy, harsh, kurang jelas, over-bright.

    Mengembalikan list of findings dalam format recommendation (id, masalah, penyebab, rekomendasi, usulan).
    """
    findings = []
    values = (dump or {}).get("values", {})

    for i in range(N_MAPPED_CHANNELS):
        ch = channel_for_index(i)
        if not ch:
            continue

        # Baca 4 band EQ untuk channel ini
        eq_bands = []
        for band in range(1, 5):
            f_raw = values.get(f"/ch/{ch:02d}/eq/{band}/f")
            g_raw = values.get(f"/ch/{ch:02d}/eq/{band}/g")
            if f_raw and g_raw:
                freq_hz = convert.eq_hz(f_raw[0])
                gain_db = convert.eq_gain_db(g_raw[0])
                eq_bands.append({"band": band, "hz": freq_hz, "db": gain_db, "raw_f": f_raw[0], "raw_g": g_raw[0]})

        if not eq_bands:
            continue

        # Cek masalah per band
        for band in eq_bands:
            freq = band["hz"]
            gain = band["db"]

            # Boomy: boost 50-150 Hz dengan gain > +4dB
            if 50 <= freq <= 150 and gain > 4.0:
                findings.append({
                    "id": f"eq-boomy-ch{ch}",
                    "ch": ch,
                    "masalah": "Suara boomy (bass berlebihan)",
                    "penyebab": f"Band {band['band']} dipukul ke {freq:.0f} Hz +{gain:.1f} dB. Frekuensi ini bikin suara berat/menggelegar.",
                    "data": f"EQ Band {band['band']}: {freq:.0f} Hz, +{gain:.1f} dB",
                    "uji": "Dengar frekuensi bass – terasa dalam/berlapis-lapis? Kurangi gain di EQ atau naikkan frequency ke atas 150 Hz.",
                    "rekomendasi": "Kurangi boost di 50–150 Hz atau naik frekuensinya ke 150–250 Hz (warmth, lebih seimbang).",
                    "risiko": "Rendah.",
                    "keyakinan": "yakin sekali",
                    "sumber": "program",
                    "usulan": [{"alamat": f"/ch/{ch:02d}/eq/{band['band']}/g", "usulan": convert.eq_gain_raw(2.0), "teks": "Turunkan gain ke +2 dB"}]
                })

            # Harsh: boost 3–5 kHz dengan gain > +4dB
            elif 3000 <= freq <= 5000 and gain > 4.0:
                findings.append({
                    "id": f"eq-harsh-ch{ch}",
                    "ch": ch,
                    "masalah": "Suara kasar / harsh (vokal/instrumen terasa tajam)",
                    "penyebab": f"Band {band['band']} dipukul ke {freq:.0f} Hz +{gain:.1f} dB. Range ini bikin detail tapi bisa berlebihan jadi kasar.",
                    "data": f"EQ Band {band['band']}: {freq:.0f} Hz, +{gain:.1f} dB",
                    "uji": "Dengar vokal/gitar – terasa 'garpu' atau 'garau'? Kurangi gain sedikit di EQ.",
                    "rekomendasi": "Kurangi gain ke +2 dB atau pindah ke 2 kHz untuk presence tanpa harsh.",
                    "risiko": "Rendah.",
                    "keyakinan": "yakin sekali",
                    "sumber": "program",
                    "usulan": [{"alamat": f"/ch/{ch:02d}/eq/{band['band']}/g", "usulan": convert.eq_gain_raw(2.0), "teks": "Turunkan gain ke +2 dB"}]
                })

            # Over-bright: boost 8–12 kHz dengan gain > +6dB
            elif 8000 <= freq <= 12000 and gain > 6.0:
                findings.append({
                    "id": f"eq-bright-ch{ch}",
                    "ch": ch,
                    "masalah": "Suara terlalu cerah / sibilant (terlalu terang)",
                    "penyebab": f"Band {band['band']} dipukul ke {freq:.0f} Hz +{gain:.1f} dB. Boost berlebih di range brightness bisa bikin telinga sakit.",
                    "data": f"EQ Band {band['band']}: {freq:.0f} Hz, +{gain:.1f} dB",
                    "uji": "Dengar vokal – apakah 's' terasa menggigit? Kurangi gain di EQ.",
                    "rekomendasi": "Kurangi gain ke +3–4 dB saja, atau shift ke 5 kHz untuk clarity lebih seimbang.",
                    "risiko": "Rendah.",
                    "keyakinan": "yakin sekali",
                    "sumber": "program",
                    "usulan": [{"alamat": f"/ch/{ch:02d}/eq/{band['band']}/g", "usulan": convert.eq_gain_raw(4.0), "teks": "Turunkan gain ke +4 dB"}]
                })

    # Deteksi kurang jelas: jika tidak ada frequency di 2–5 kHz yang dipukul
    for i in range(N_MAPPED_CHANNELS):
        ch = channel_for_index(i)
        if not ch:
            continue

        eq_bands = []
        for band in range(1, 5):
            f_raw = values.get(f"/ch/{ch:02d}/eq/{band}/f")
            g_raw = values.get(f"/ch/{ch:02d}/eq/{band}/g")
            if f_raw and g_raw:
                freq_hz = convert.eq_hz(f_raw[0])
                gain_db = convert.eq_gain_db(g_raw[0])
                eq_bands.append({"band": band, "hz": freq_hz, "db": gain_db})

        # Check apakah ada band di 2–5 kHz dengan boost minimal
        presence_bands = [b for b in eq_bands if 2000 <= b["hz"] <= 5000 and b["db"] > 1.0]
        if not presence_bands:
            # Tidak ada presence. Ini INFO saja, bukan warning
            ch_name = values.get(f"/ch/{ch:02d}/config/name")
            ch_name = str(ch_name[0]).strip() if ch_name and ch_name[0] not in (None, "") else f"CH {ch:02d}"

            # Jangan flag jika channel suara vokal/khotbah yang biasanya perlu presence
            # Ini hanya untuk awareness, operator bisa keputusan sendiri
            pass

    return findings


class LiveRules:
    def __init__(self, labels: dict[int, str] | None = None, inactive: set[int] | None = None):
        self.inactive = inactive or set()
        self.labels = labels or {}
        self._hot: dict[int, deque] = {}
        self._hist: dict[int, deque] = {}
        self._last: dict[tuple[str, int], float] = {}
        self._last_weak_check = 0.0

    def label(self, i: int) -> str:
        return self.labels.get(i, f"Meter {i}")

    def _emit(self, out: list, kind: str, sev: str, i: int, lvl: float, t: float, msg: str) -> None:
        key = (kind, i)
        if t - self._last.get(key, -1e9) < COOLDOWN[kind]:
            return
        self._last[key] = t
        out.append(Alert(t, time.strftime("%H:%M:%S"), kind, sev, i, self.label(i), round(lvl, 1), msg))

    def feed(self, levels: list[float], t: float) -> list[Alert]:
        out: list[Alert] = []
        check_weak = t - self._last_weak_check >= 1.0
        if check_weak:
            self._last_weak_check = t
        for i, lvl in enumerate(levels):
            if i >= N_MAPPED_CHANNELS:
                break                     # meter selain channel input (bus/main/FX) belum dipetakan: tidak dinilai
            if lvl >= CLIP_DB:
                self._emit(out, "clip", "bad", i, lvl, t,
                           f"Clipping ({lvl:.1f} dB). Turunkan GAIN (bukan fader) di M-AIR.")
            elif lvl >= HOT_DB:
                q = self._hot.setdefault(i, deque())
                q.append(t)
                while q and t - q[0] > HOT_WINDOW:
                    q.popleft()
                if len(q) >= HOT_FRAMES:
                    self._emit(out, "hot", "warn", i, lvl, t,
                               f"Mendekati clipping berulang kali (di atas {HOT_DB:.0f} dB). Turunkan gain 2–3 dB.")
            h = self._hist.setdefault(i, deque())
            h.append((t, lvl))
            while h and t - h[0][0] > WEAK_WINDOW:
                h.popleft()
            if check_weak and i not in self.inactive and h and t - h[0][0] >= WEAK_WINDOW * 0.9:
                peak = max(v for _, v in h)
                if WEAK_FLOOR < peak < WEAK_MAX:
                    self._emit(out, "weak", "info", i, peak, t,
                               f"Sinyal sangat kecil (puncak {peak:.0f} dB selama {WEAK_WINDOW:.0f} dtk). "
                               "Naikkan gain jika mic ini dipakai; abaikan jika tidak.")
        return out

    def stale(self, t: float, since: float) -> list[Alert]:
        out: list[Alert] = []
        self._emit(out, "stale", "warn", -1, 0.0, t,
                   f"Data meter berhenti {since:.0f} dtk. Cek kabel LAN/Wi-Fi ke mixer.")
        return out


class LiveMonitor:
    def __init__(self, ip: str, port: int, path: str, labels: dict[int, str],
                 log_path: Path, on_stop=None, on_param=None, inactive: set[int] | None = None):
        self.ip, self.port, self.path = ip, port, path
        self.on_param = on_param          # dipanggil (alamat, argumen) untuk perubahan dari M-AIR
        self.rules = LiveRules(labels, inactive)
        self.labels = labels
        self.log_path = log_path
        self.on_stop = on_stop
        self._stop = threading.Event()
        self.levels: list[float] = []
        self.peaks: list[float] = []
        self.frames = 0
        self.bad_frames = 0
        self.alerts: deque[Alert] = deque(maxlen=200)
        self.started = time.monotonic()
        self.started_clock = time.strftime("%H:%M:%S")
        self.last_frame: float | None = None
        self.running = False
        self.error: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _should_stop(self) -> bool:
        return self._stop.is_set() or time.monotonic() - self.started > MAX_RUN_S

    def _run(self) -> None:
        try:
            with ReadOnlyClient(self.ip, self.port, log_path=self.log_path.with_name("osc_log.jsonl")) as c:
                c.stream_meters(self.path, self._on_blob, self._should_stop, on_param=self.on_param)
        except Exception as exc:  # tampilkan ke pengguna
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.running = False
            if self.on_stop:
                self.on_stop()

    def _record(self, alerts: list[Alert]) -> None:
        if not alerts:
            return
        self.alerts.extend(alerts)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            for a in alerts:
                fh.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")

    def _on_blob(self, blob: bytes) -> None:
        try:
            levels = parse_blob(blob)
        except MeterError:
            self.bad_frames += 1
            return
        t = time.monotonic()
        with self._lock:
            if len(self.peaks) != len(levels):
                self.peaks = list(levels)
            self.peaks = [max(l, p - 0.4) for l, p in zip(levels, self.peaks)]
            self.levels = levels
            self.frames += 1
            self.last_frame = t
            self._record(self.rules.feed(levels, t))

    def snapshot(self, include_alerts: bool = True) -> dict:
        now = time.monotonic()
        with self._lock:
            if self.running and self.last_frame is not None and now - self.last_frame > STALE_S:
                self._record(self.rules.stale(now, now - self.last_frame))
            elif self.running and self.last_frame is None and now - self.started > STALE_S:
                self._record(self.rules.stale(now, now - self.started))
            alerts = [asdict(a) for a in self.alerts]
            elapsed = max(now - self.started, 1e-6)
            return {
                "running": self.running,
                "error": self.error,
                "started": self.started_clock,
                "frames": self.frames,
                "bad_frames": self.bad_frames,
                "fps": round(self.frames / elapsed, 1),
                "age": None if self.last_frame is None else round(now - self.last_frame, 2),
                "levels": [round(v, 1) for v in self.levels],
                "peaks": [round(v, 1) for v in self.peaks],
                "labels": {str(k): v for k, v in self.labels.items()},
                "mapping": "perkiraan: indeks 0..15 = CH 01..16",
                "alerts": alerts if include_alerts else None,
                "alert_count": len(alerts),
            }
