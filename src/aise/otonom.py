"""Mode kerja aplikasi terhadap SEMUA temuan: manual | semi | otonom.

Sumber temuan: mesin realtime (panas, feedback, balance, kompresor, struktur gain), aturan konfigurasi (low cut, EQ boomy/harsh/muddy,
kompresor, siaran), analisis EQ, hasil Cek Balance, dan saran Gemini yang belum selesai.

manual  - seperti sebelumnya: operator menggeser slider Terapkan sendiri.
semi    - tiap temuan menjadi usulan; operator menekan Setujui, lalu aplikasi menerapkannya perlahan sampai selesai.
otonom  - aplikasi menerapkan SENDIRI temuan program (bukan Gemini, bukan satu kejadian clipping sesaat) yang lolos kebijakan batas
          di bawah; selebihnya menunggu persetujuan seperti mode semi.

Semua perubahan tetap lewat Controller (daftar putih: gain, fader, low cut, EQ, kompresor pada channel 01-16; perubahan bertahap; baca balik).
TIDAK PERNAH: 48V, mute, routing, gate, kiriman, Main/bus, scene. Pengaman tambahan di sini:
- kebijakan batas per jenis parameter, dihitung dari nilai awal sesi (lihat `batas_otonom`); sakelar hanya boleh dinyalakan, tidak dimatikan;
- satu tindakan pada satu waktu, jeda per channel, batas jumlah per jam;
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
from .ignored import key_for
from .writer import WriteRefused, spec_for

MODES = ("manual", "semi", "otonom")
TICK_S = 0.5
NO_AUTO_PREFIX = ("live-gain-",)   # satu kejadian clipping sesaat (mis. mic terbentur) tidak cukup untuk bertindak sendiri; mesin-panas-* menggantikannya
MAX_SHOWN = 15
COOL_S = 20.0                # setelah satu tindakan, channel itu diamati dulu
OPERATOR_HOLD_S = 60.0       # channel yang baru disentuh operator tidak diganggu
REJECT_S = 600.0             # usulan program yang ditolak tidak muncul lagi selama ini
REJECT_GEMINI_S = 12 * 3600  # saran Gemini yang ditolak: Gemini mengulang saran yang sama tiap analisis, jadi ditahan lebih lama
AUTO_PER_HOUR = 30
ACTION_TIMEOUT_S = 20.0
OWN_ECHO_S = 3.0
_CH_RE = re.compile(r"^/(?:headamp|ch)/(\d\d)/")
_EQ_RE = re.compile(r"^/ch/\d\d/eq/[1-4]/([gfq])$")

# Batas otonom (jarak dari NILAI AWAL SESI). Yang menjauhi 0 dB pada EQ dibatasi lebih ketat daripada yang mengecilkan efek EQ.
LIMITS = {
    "gain": "turun maks 9 dB, naik maks 3 dB dari nilai awal",
    "fader": "turun maks 6 dB, naik maks 3 dB (tidak melewati +5 dB, tidak sampai mati)",
    "hpf": "frekuensi 40-250 Hz",
    "sakelar": "hanya menyalakan Low cut, EQ, atau Kompresor (tidak pernah mematikan)",
    "eq": "gain maks 6 dB dari nilai awal (boost sampai +4, potongan sampai -9); mengecilkan efek EQ tidak dibatasi; frekuensi dan Q maks 2x",
    "kompresor": "threshold -40 sampai -5 dB; rasio sampai 5:1",
}


def _ch_of(addr: str) -> int | None:
    m = _CH_RE.match(addr)
    return int(m.group(1)) if m else None


def writable(addr) -> bool:
    return isinstance(addr, str) and spec_for(addr) is not None


def batas_otonom(addr: str, base, cur, tgt) -> str:
    """"" bila perubahan cur -> tgt boleh dijalankan tanpa persetujuan; selain itu alasannya. `base` = nilai awal sesi."""
    try:
        if not writable(addr):
            return "bukan parameter yang boleh diubah dari aplikasi"
        if base is None:
            base = cur
        if addr.startswith("/headamp/") and addr.endswith("/gain"):
            d = convert.gain_db(tgt) - convert.gain_db(base)
            return "" if -9.05 <= d <= 3.05 else f"batas gain otonom ({LIMITS['gain']})"
        if addr.endswith("/mix/fader"):
            t, b = convert.fader_db(tgt), convert.fader_db(base)
            if float("-inf") in (t, b):
                return "fader mati/tidak terbaca: perlu keputusan operator"
            return "" if -6.05 <= t - b <= 3.05 and t <= 5.05 else f"batas fader otonom ({LIMITS['fader']})"
        if addr.endswith("/preamp/hpf"):
            return "" if 40.0 <= convert.hpf_hz(tgt) <= 250.0 else f"batas low cut otonom ({LIMITS['hpf']})"
        if addr.endswith(("/hpon", "/eq/on", "/dyn/on")):
            return "" if int(tgt) == 1 else "mematikan fitur perlu keputusan operator"
        m = _EQ_RE.match(addr)
        if m and m.group(1) == "g":
            t, c, b = convert.eq_gain_db(tgt), convert.eq_gain_db(cur), convert.eq_gain_db(base)
            if abs(t) <= abs(c) + 0.05:
                return ""                                  # hanya mengecilkan efek EQ yang sudah ada
            return "" if abs(t - b) <= 6.05 and -9.05 <= t <= 4.05 else f"batas EQ otonom ({LIMITS['eq']})"
        if m and m.group(1) == "f":
            r = convert.eq_hz(tgt) / convert.eq_hz(base)
            return "" if 0.5 <= r <= 2.0 else f"batas EQ otonom ({LIMITS['eq']})"
        if m:
            q, qb = convert.eq_q(tgt), convert.eq_q(base)
            return "" if 0.5 <= q <= 6.0 and 0.5 <= q / qb <= 2.0 else f"batas EQ otonom ({LIMITS['eq']})"
        if addr.endswith("/dyn/thr"):
            return "" if -40.05 <= convert.comp_thr_db(tgt) <= -4.95 else f"batas kompresor otonom ({LIMITS['kompresor']})"
        if addr.endswith("/dyn/ratio"):
            ti = convert.ratio_index(tgt)
            return "" if ti is not None and ti <= 7 else f"batas kompresor otonom ({LIMITS['kompresor']})"
    except (TypeError, ValueError):
        return "nilai tidak terbaca"
    return "bukan parameter yang boleh diubah dari aplikasi"


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
            if not self.usul[fid].get("bisa"):
                raise ValueError("Tidak ada langkah yang bisa diterapkan dari aplikasi. Ubah manual di M-AIR.")
            self._approved.add(fid)
            self.usul[fid]["status"] = "antre"
            return self.view()

    def reject(self, fid: str) -> dict:
        with self._lock:
            u = self.usul.pop(fid, None)
            if u:                                      # ditahan berdasarkan channel + parameter, bukan id: saran Gemini mendapat id baru tiap analisis
                self._rejected[u["kunci"]] = self._clock() + (REJECT_GEMINI_S if u.get("sumber") == "gemini" else REJECT_S)
            self._approved.discard(fid)
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
                    "batas": {**LIMITS, "per_jam": AUTO_PER_HOUR}}

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
        fid = str(f.get("id", ""))
        if f.get("sumber") == "gemini":
            return False, "saran Gemini selalu menunggu persetujuan Anda"
        if fid.startswith(NO_AUTO_PREFIX):
            return False, "baru satu kejadian sesaat; menunggu terbukti bertahan atau persetujuan Anda"
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
            a = u.get("alamat", "")
            if not writable(a):
                return False, "ada langkah yang harus Anda ubah manual di M-AIR"
            why = batas_otonom(a, self.baseline.get(a), u.get("sekarang"), u.get("usulan"))
            if why:
                return False, why + "; perlu persetujuan Anda"
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
        fnd.sort(key=lambda f: (f.get("severity") != "perhatian", f.get("sumber") == "gemini"))
        todo = None
        with self._lock:
            self._rejected = {k: t for k, t in self._rejected.items() if t > now}
            seen = {}
            for f in fnd:
                fid, kunci = f["id"], key_for(f)
                if kunci in self._rejected or len(seen) >= MAX_SHOWN or any(v["kunci"] == kunci for v in seen.values()):
                    continue
                ok = [u for u in f["usulan"] if writable(u.get("alamat"))]
                manual = [u.get("teks", "") for u in f["usulan"] if not writable(u.get("alamat"))]
                auto, why = self._auto_ok(f, now) if self.mode == "otonom" else (False, "menunggu persetujuan")
                old = self.usul.get(fid, {})
                seen[fid] = {"id": fid, "kunci": kunci, "ch": f.get("ch"), "masalah": f.get("masalah", ""), "severity": f.get("severity", "info"),
                             "sumber": f.get("sumber", "program"), "teks": [u.get("teks", "") for u in ok], "manual": manual,
                             "bisa": bool(ok), "otomatis": auto, "catatan": "" if auto else why,
                             "status": "antre" if fid in self._approved else "usul", "pesan": old.get("pesan", "")}
                if todo is None and ok and (auto or fid in self._approved):
                    todo = (f, auto, ok, manual)
            self._approved &= set(seen)
            self.usul = seen
        if todo:
            f, auto, ok, manual = todo
            self._start({"id": f["id"], "kunci": key_for(f), "ch": f.get("ch"), "masalah": f.get("masalah", ""), "manual": manual,
                         "items": [{"alamat": u["alamat"], "target": u["usulan"], "teks": u.get("teks", "")} for u in ok]},
                        auto=auto, confirmed=True)   # persetujuan operator atau kebijakan batas_otonom sudah menjadi izinnya

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
                if auto and batas_otonom(a, self.baseline.get(a), cur, it["target"]):
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
                    self._rejected[act.get("kunci", act["id"])] = now + 30.0      # jangan diulang terus-menerus
                    self._log("gagal", id=act["id"], pesan=err)
                return err
            if auto:
                self._auto_times.append(now)
            self._job = {"id": act["id"], "ch": act["ch"], "masalah": act["masalah"], "auto": auto, "restore": restore, "items": armed,
                         "addrs": {i["alamat"] for i in armed}, "t0": now, "pesan": err, "manual": act.get("manual", [])}
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
                     "teks": [i["teks"] for i in job["items"]], "otomatis": job["auto"], "pulih": job["restore"], "manual": job["manual"], "ok": not bad, "pesan": bad}
            self.riwayat.append(entry)
            self._job = None
            self._log("selesai" if not bad else "bermasalah", **{k: entry[k] for k in ("id", "ch", "otomatis", "pesan", "teks")})
