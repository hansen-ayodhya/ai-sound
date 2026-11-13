"""Mode kerja aplikasi terhadap temuan realtime: manual | semi | otonom.

manual  - seperti sebelumnya: operator menggeser slider Terapkan sendiri.
semi    - tiap temuan realtime menjadi usulan; operator menekan Setujui, lalu aplikasi menerapkannya perlahan sampai selesai.
otonom  - aplikasi menerapkan SENDIRI hanya tindakan penyelamatan yang sifatnya MENGURANGI (gain atau fader turun),
          dalam batas ketat; selain itu tetap menunggu persetujuan seperti mode semi.

Semua perubahan tetap lewat Controller (daftar putih, perubahan bertahap, baca balik). Pengaman tambahan di sini:
- otonom tidak pernah menaikkan apa pun, tidak pernah menyalakan/mematikan sakelar;
- batas jarak dari nilai awal sesi (gain 9 dB, fader 6 dB), satu tindakan pada satu waktu, jeda per channel, batas jumlah per jam;
- channel yang disentuh operator di M-AIR ditinggalkan sementara (tidak berebut);
- berhenti sendiri ke manual bila Mode kontrol mati atau pantau live berhenti.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from pathlib import Path

from . import convert, recs
from .writer import WriteRefused

MODES = ("manual", "semi", "otonom")
TICK_S = 0.5
# Hanya temuan mesin yang kondisinya terbukti bertahan beberapa detik; satu lonjakan sesaat (mis. mic terbentur) tidak cukup.
AUTO_PREFIX = ("mesin-panas-", "mesin-feedback-", "mesin-tenggelam-")
MAX_GAIN_CUT_DB = 9.0        # otonom: total penurunan gain dari nilai awal sesi
MAX_FADER_CUT_DB = 6.0       # otonom: total penurunan fader dari nilai awal sesi
COOL_S = 20.0                # setelah satu tindakan, channel itu diamati dulu
OPERATOR_HOLD_S = 60.0       # channel yang baru disentuh operator tidak diganggu
REJECT_S = 600.0             # usulan yang ditolak tidak muncul lagi selama ini
AUTO_PER_HOUR = 30
ACTION_TIMEOUT_S = 20.0
OWN_ECHO_S = 3.0
_CH_RE = re.compile(r"^/(?:headamp|ch)/(\d\d)/")


def _ch_of(addr: str) -> int | None:
    m = _CH_RE.match(addr)
    return int(m.group(1)) if m else None


def _cut_db(addr: str, a, b) -> float | None:
    """Berapa dB nilai turun dari a ke b (positif = turun); None bila alamat bukan gain/fader."""
    try:
        if addr.startswith("/headamp/") and addr.endswith("/gain"):
            return convert.gain_db(a) - convert.gain_db(b)
        if addr.endswith("/mix/fader"):
            da, db = convert.fader_db(a), convert.fader_db(b)
            return None if float("-inf") in (da, db) else da - db
    except (TypeError, ValueError):
        pass
    return None


class Autopilot:
    def __init__(self, control, read_value, get_findings, is_ignored, log_path: Path, alive=lambda: True, clock=time.monotonic):
        self.control, self._read, self._get_findings, self._is_ignored = control, read_value, get_findings, is_ignored
        self.log_path, self._alive, self._clock = Path(log_path), alive, clock
        self._lock = threading.RLock()
        self.mode = "manual"
        self.pesan = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.usul: dict[str, dict] = {}            # id temuan -> usulan yang sedang ditampilkan
        self._approved: set[str] = set()
        self._rejected: dict[str, float] = {}
        self._job: dict | None = None              # tindakan yang sedang berjalan
        self.baseline: dict[str, float] = {}       # alamat -> nilai sebelum disentuh mode ini (untuk batas dan pemulihan)
        self._cool: dict[int, float] = {}
        self._hold: dict[int, float] = {}
        self._own: dict[str, float] = {}           # alamat yang baru kita ubah (gema /xremote diabaikan)
        self._auto_times: deque = deque()
        self.riwayat: deque = deque(maxlen=40)

    # ---------- catatan ----------
    def _log(self, event: str, **kw) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "mode": self.mode, **kw}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------- mode ----------
    def set_mode(self, mode: str, peta_ok: bool = False, hadir: bool = False) -> dict:
        if mode not in MODES:
            raise ValueError("Mode harus manual, semi, atau otonom.")
        with self._lock:
            if mode != "manual":
                if not self.control.enabled:
                    raise ValueError("Nyalakan Mode kontrol dulu (scene harus sudah disimpan di M-AIR).")
                if not self._alive():
                    raise ValueError("Pantau live harus berjalan dulu.")
            if mode == "otonom" and not (peta_ok and hadir):
                raise ValueError("Mode otonom perlu dua konfirmasi: meter sudah dicocokkan dengan M-AIR, dan operator tetap di dekat mixer.")
            if mode == self.mode:
                return self.view()
            prev, self.mode, self.pesan = self.mode, mode, ""
            self._approved.clear()
            self._log("mode", dari=prev, ke=mode)
            if mode == "manual":
                self._stop.set()
                self.usul.clear()
                self._drop_job()
            elif not (self._thread and self._thread.is_alive()):
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            return self.view()

    def _fallback(self, why: str) -> None:
        with self._lock:
            if self.mode == "manual":
                return
            self._log("mode", dari=self.mode, ke="manual", alasan=why)
            self.mode, self.pesan = "manual", f"Kembali ke manual: {why}."
            self._stop.set()
            self.usul.clear()
            self._approved.clear()
            self._drop_job()

    def _drop_job(self) -> None:
        """Lepas tindakan yang sedang berjalan: parameter dikunci di nilai tujuannya oleh Controller, lalu tidak dipegang lagi."""
        job, self._job = self._job, None
        for it in (job or {}).get("items", []):
            try:
                self.control.release(it["alamat"])
            except WriteRefused:
                pass

    # ---------- operator ----------
    def approve(self, fid: str) -> dict:
        with self._lock:
            if self.mode == "manual":
                raise ValueError("Mode masih manual.")
            if fid not in self.usul:
                raise ValueError("Usulan itu sudah tidak berlaku.")
            self._approved.add(fid)
            self.usul[fid]["status"] = "antre"
            return self.view()

    def reject(self, fid: str) -> dict:
        with self._lock:
            self._rejected[fid] = self._clock() + REJECT_S
            self._approved.discard(fid)
            self.usul.pop(fid, None)
            self._log("tolak", id=fid)
            return self.view()

    def restore(self) -> dict:
        """Kembalikan semua yang diubah mode semi/otonom ke nilai awal sesi (atas perintah operator)."""
        with self._lock:
            if not self.control.enabled:
                raise ValueError("Mode kontrol sudah mati. Muat scene cadangan di M-AIR, atau nyalakan Mode kontrol lalu ulangi.")
            if self._job:
                raise ValueError("Masih ada tindakan berjalan. Coba lagi beberapa detik lagi.")
            if self.mode != "manual":
                self.set_mode("manual")
            items = []
            for a, v in self.baseline.items():
                cur = self._read(a)
                if cur is None:
                    raise ValueError("Mixer tidak menjawab. Tidak ada yang diubah; coba lagi atau muat scene cadangan di M-AIR.")
                if abs(float(cur) - float(v)) > 0.004:                 # yang sudah di nilai awal tidak perlu disentuh
                    items.append({"alamat": a, "target": v, "teks": f"{recs.fmt_value(a, cur)} → {recs.fmt_value(a, v)} (nilai awal)"})
            if not items:
                if not self.baseline:
                    raise ValueError("Belum ada perubahan dari mode semi/otonom.")
                self.baseline.clear()
                return self.view()
        err = self._start({"id": "pulihkan", "ch": None, "masalah": "Mengembalikan ke nilai awal sesi", "items": items}, auto=False, confirmed=True, restore=True)
        with self._lock:
            if not self._job:
                raise ValueError(f"Pemulihan tidak bisa dimulai: {err or 'tidak ada parameter yang bisa digerakkan'}. Coba lagi, atau muat scene cadangan di M-AIR.")
            if err:
                self._job["pesan"] = f"sebagian tidak dipulihkan: {err}"
            self._stop = threading.Event()
            threading.Thread(target=self._run, args=(True,), daemon=True).start()
            return self.view()

    def on_param(self, addr: str, args) -> None:
        """Operator mengubah sesuatu di M-AIR pada channel ini: tinggalkan channel itu sementara."""
        ch = _ch_of(addr)
        if ch is None or self.mode == "manual":
            return
        now = self._clock()
        with self._lock:
            if now - self._own.get(addr, -1e9) <= OWN_ECHO_S or (self._job and addr in self._job["addrs"]):
                return
            self._hold[ch] = now + OPERATOR_HOLD_S

    # ---------- tampilan ----------
    def view(self) -> dict:
        now = self._clock()
        with self._lock:
            return {"mode": self.mode, "pesan": self.pesan, "usul": list(self.usul.values()),
                    "jalan": None if not self._job else {"id": self._job["id"], "masalah": self._job["masalah"], "otomatis": self._job["auto"]},
                    "riwayat": list(self.riwayat)[::-1], "bisa_pulih": bool(self.baseline),
                    "ditahan": sorted(ch for ch, t in self._hold.items() if t > now),
                    "batas": {"gain_db": MAX_GAIN_CUT_DB, "fader_db": MAX_FADER_CUT_DB, "per_jam": AUTO_PER_HOUR}}

    # ---------- kerja ----------
    def _run(self, until_job_done: bool = False) -> None:
        stop = self._stop
        while not stop.wait(TICK_S):
            try:
                self.tick()
            except Exception as exc:               # tidak boleh mati diam-diam sambil tetap berstatus otonom
                self._log("error", pesan=f"{type(exc).__name__}: {exc}")
                self._fallback("kesalahan internal")
                return
            if until_job_done and self._job is None:
                return

    def _auto_ok(self, f: dict, now: float) -> tuple[bool, str]:
        """Bolehkah temuan ini dijalankan tanpa persetujuan (hanya di mode otonom)?"""
        if not str(f.get("id", "")).startswith(AUTO_PREFIX):
            return False, "perlu persetujuan (bukan tindakan penyelamatan)"
        ch = f.get("ch")
        if self._hold.get(ch, 0) > now:
            return False, "operator baru mengubah channel ini di M-AIR"
        if self._cool.get(ch, 0) > now:
            return False, "menunggu hasil tindakan sebelumnya"
        while self._auto_times and now - self._auto_times[0] > 3600:
            self._auto_times.popleft()
        if len(self._auto_times) >= AUTO_PER_HOUR:
            return False, f"batas {AUTO_PER_HOUR} tindakan otomatis per jam tercapai"
        for u in f.get("usulan") or []:
            a, cur, tgt = u.get("alamat", ""), u.get("sekarang"), u.get("usulan")
            cut = _cut_db(a, cur, tgt)
            if cut is None or cut <= 0:
                return False, "perlu persetujuan (bukan penurunan gain/fader)"
            total = _cut_db(a, self.baseline.get(a, cur), tgt)
            cap = MAX_GAIN_CUT_DB if a.startswith("/headamp/") else MAX_FADER_CUT_DB
            if total is None or total > cap + 0.05:
                return False, f"batas otonom tercapai (maks {cap:.0f} dB dari nilai awal); perlu keputusan operator"
        return True, ""

    def tick(self) -> None:
        if self.mode == "manual" and not self._job:
            return
        if not self.control.enabled:
            return self._fallback("Mode kontrol mati")
        if not self._alive():
            return self._fallback("pantau live berhenti")
        self.control.keep_alive()
        if self._job:
            return self._progress()
        if self.mode == "manual":
            return
        now = self._clock()
        fnd = [f for f in self._get_findings() if f.get("usulan") and f.get("id") and not self._is_ignored(f)]
        todo = None
        with self._lock:
            self._rejected = {k: t for k, t in self._rejected.items() if t > now}
            seen = {}
            for f in fnd:
                fid = f["id"]
                if fid in self._rejected:
                    continue
                auto, why = self._auto_ok(f, now) if self.mode == "otonom" else (False, "menunggu persetujuan")
                old = self.usul.get(fid, {})
                seen[fid] = {"id": fid, "ch": f.get("ch"), "masalah": f.get("masalah", ""), "severity": f.get("severity", "info"),
                             "teks": [u.get("teks", "") for u in f["usulan"]], "otomatis": auto, "catatan": "" if auto else why,
                             "status": "antre" if fid in self._approved else "usul", "pesan": old.get("pesan", "")}
                if todo is None and (auto or fid in self._approved):
                    todo = (f, auto)
            self._approved &= set(seen)
            self.usul = seen
        if todo:
            self._start({"id": todo[0]["id"], "ch": todo[0].get("ch"), "masalah": todo[0].get("masalah", ""),
                         "items": [{"alamat": u["alamat"], "target": u["usulan"], "teks": u.get("teks", "")} for u in todo[0]["usulan"]]},
                        auto=todo[1], confirmed=not todo[1])

    def _start(self, act: dict, auto: bool, confirmed: bool, restore: bool = False) -> str:
        """Baca nilai sekarang dari mixer, lalu mulai menggerakkan tiap parameter ke targetnya (bertahap oleh Controller).

        Mengembalikan pesan kesalahan ("" bila semua parameter mulai bergerak)."""
        now = self._clock()
        armed, err = [], ""
        for it in act["items"]:
            a = it["alamat"]
            try:
                cur = self._read(a)
                if cur is None:
                    raise WriteRefused("Mixer tidak menjawab saat membaca nilai sekarang.")
                if auto and (_cut_db(a, cur, it["target"]) or 0) <= 0:
                    raise WriteRefused("Nilai di mixer sudah berubah; tindakan otomatis dibatalkan.")
                self.control.arm(a, cur, it["target"], confirmed)
                self.control.set_fraction(a, 1.0)
                with self._lock:
                    if not restore:
                        self.baseline.setdefault(a, cur)
                    self._own[a] = now
                armed.append({"alamat": a, "awal": cur, "target": it["target"], "teks": it["teks"]})
            except (WriteRefused, ValueError, OSError) as exc:
                err = str(exc)
                break
        with self._lock:
            self._approved.discard(act["id"])
            if not armed:
                if "sama dengan rekomendasi" not in err:
                    if act["id"] in self.usul:
                        self.usul[act["id"]]["pesan"] = err
                    self._rejected[act["id"]] = now + 30.0      # jangan diulang terus-menerus
                    self._log("gagal", id=act["id"], pesan=err)
                return err
            if auto:
                self._auto_times.append(now)
            self._job = {"id": act["id"], "ch": act["ch"], "masalah": act["masalah"], "auto": auto, "restore": restore, "items": armed,
                         "addrs": {i["alamat"] for i in armed}, "t0": now, "pesan": err}
            self.usul.pop(act["id"], None)
            self._log("mulai", id=act["id"], otomatis=auto, items=[{k: i[k] for k in ("alamat", "awal", "target")} for i in armed])
        return err

    def _progress(self) -> None:
        job, now = self._job, self._clock()
        items = {i["alamat"]: i for i in self.control.status()["item"]}
        late = now - job["t0"] >= ACTION_TIMEOUT_S
        pending = False
        for it in job["items"]:
            s = items.get(it["alamat"])
            if s is None:
                continue                                   # sudah dilepas Controller
            if it.get("lepas"):
                pending = pending or not late              # tunggu sampai Controller benar-benar melepasnya
                continue
            if s["state"] == "dilepas":
                job["pesan"] = job["pesan"] or s["pesan"] or "dilepas"
                if job["ch"] is not None:
                    self._hold[job["ch"]] = now + OPERATOR_HOLD_S
            elif abs(float(s["nilai"]) - float(s["target"])) > 1e-6 or s["verifikasi"] is None:
                if not late:
                    pending = True
                    continue
                job["pesan"] = job["pesan"] or "terlalu lama; dihentikan"
            elif not s["verifikasi"][0]:
                job["pesan"] = job["pesan"] or s["pesan"]
            self.control.release(it["alamat"])
            self._own[it["alamat"]] = now
            it["lepas"] = True
            pending = pending or s["state"] != "dilepas"
        if pending:
            return
        bad = job["pesan"]
        with self._lock:
            if job["restore"] and not bad:
                self.baseline.clear()
            if job["ch"] is not None:
                self._cool[job["ch"]] = now + COOL_S
            entry = {"waktu": time.strftime("%H:%M:%S"), "id": job["id"], "ch": job["ch"], "masalah": job["masalah"],
                     "teks": [i["teks"] for i in job["items"]], "otomatis": job["auto"], "pulih": job["restore"], "ok": not bad, "pesan": bad}
            self.riwayat.append(entry)
            self._job = None
            self._log("selesai" if not bad else "bermasalah", **{k: entry[k] for k in ("id", "ch", "otomatis", "pesan", "teks")})
