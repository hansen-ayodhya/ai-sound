"""Jalur TULIS ke mixer (terpisah dari klien read-only; hanya dipakai bila operator menyalakan Mode kontrol).

Tujuan rancangan: perubahan harus halus dan bisa dibatalkan, tidak membuat mixer "glitch".
- Hanya parameter di daftar putih (SPECS); selain itu ditolak SEBELUM menyentuh jaringan.
  TIDAK PERNAH: 48V, mute, routing, fader Main/bus, scene, reset, preferensi.
- Nilai selalu berada di antara titik awal dan target rekomendasi (tidak ada ekstrapolasi).
- Perubahan dikirim paling sering 10x/detik, dengan batas langkah per kiriman (slew) supaya tidak
  ada loncatan/zipper noise. Nilai terbaru menggantikan yang antre; tidak ada tumpukan pesan.
- Bila operator mengubah parameter yang sama di M-AIR, kontrol dilepas (tidak berebut).
- Setelah selesai bergerak, nilai dibaca balik dari mixer untuk verifikasi.
- Mode mati sendiri bila tidak ada aktivitas; nilai terakhir dibiarkan (tidak dipulihkan paksa).
"""

from __future__ import annotations

import json
import math
import re
import socket
import threading
import time
from collections import deque
from pathlib import Path

from .client import disable_udp_connreset
from .osc import OscError, decode, encode

TICK_S = 0.1              # jeda kerja pengirim: maks 10 kiriman/detik per parameter
MAX_SENDS_PER_TICK = 3    # batas total kiriman per tick (semua parameter)
IDLE_S = 600.0            # mode mati sendiri setelah tidak ada aktivitas
MAX_ARMED = 6
MIN_DELTA = 1e-4          # selisih terlalu kecil tidak dikirim
ECHO_TOL = 0.02           # toleransi pembulatan mixer saat membaca balik/kabar perubahan (nilai mentah)
RECENT_S = 3.0            # kabar perubahan yang sama dengan nilai yang baru kita kirim dianggap gema
VERIFY_AFTER_S = 0.5      # diam sekian detik setelah nilai terakhir, lalu baca balik
MAX_GAIN_SWING_DB = 20.0  # batas perubahan gain dari titik awal

_CH = r"(?:0[1-9]|1[0-6])"
# (pola alamat, jenis, langkah maks per kiriman (nilai mentah), label)
SPECS = [
    (re.compile(rf"^/headamp/{_CH}/gain$"), "float", 1.0 / 72.0, "Gain"),
    (re.compile(rf"^/ch/{_CH}/mix/fader$"), "float", 0.02, "Fader"),
    (re.compile(rf"^/ch/{_CH}/preamp/hpf$"), "float", 0.02, "Frekuensi low cut"),
    (re.compile(rf"^/ch/{_CH}/preamp/hpon$"), "toggle", 1.0, "Low cut"),
    (re.compile(rf"^/ch/{_CH}/eq/on$"), "toggle", 1.0, "EQ"),
    (re.compile(rf"^/ch/{_CH}/eq/[1-4]/g$"), "float", 1.0 / 30.0, "EQ gain"),
    (re.compile(rf"^/ch/{_CH}/eq/[1-4]/f$"), "float", 0.02, "EQ frekuensi"),
    (re.compile(rf"^/ch/{_CH}/eq/[1-4]/q$"), "float", 0.03, "EQ Q"),
    (re.compile(rf"^/ch/{_CH}/dyn/on$"), "toggle", 1.0, "Kompresor"),
    (re.compile(rf"^/ch/{_CH}/dyn/thr$"), "float", 0.05, "Threshold kompresor"),
    (re.compile(rf"^/ch/{_CH}/dyn/ratio$"), "enum", 1.0, "Rasio kompresor"),
]
ENUM_MAX = 11


class WriteRefused(Exception):
    """Perubahan ditolak oleh pengaman tulis."""


def spec_for(address: str):
    for pat, kind, step, label in SPECS:
        if pat.match(address):
            return kind, step, label
    return None


def check_write(address: str, args: tuple) -> str:
    """Periksa satu pesan tulis; kembalikan jenisnya ('float'|'toggle'|'enum') atau lempar WriteRefused."""
    s = spec_for(address)
    if s is None:
        raise WriteRefused(f"alamat tidak boleh diubah dari aplikasi: {address}")
    kind = s[0]
    if len(args) != 1:
        raise WriteRefused("tepat satu nilai per pesan")
    v = args[0]
    if isinstance(v, bool):
        raise WriteRefused("tipe nilai tidak valid")
    if kind == "float":
        if not isinstance(v, float) or not math.isfinite(v) or not 0.0 <= v <= 1.0:
            raise WriteRefused(f"nilai harus bilangan desimal 0..1: {v!r}")
    elif kind == "toggle":
        if not isinstance(v, int) or v not in (0, 1):
            raise WriteRefused(f"sakelar harus 0 atau 1: {v!r}")
    else:
        if not isinstance(v, int) or not 0 <= v <= ENUM_MAX:
            raise WriteRefused(f"indeks rasio di luar 0..{ENUM_MAX}: {v!r}")
    return kind


class WriteClient:
    """Soket UDP kirim-saja ke mixer. Setiap paket diperiksa dua kali (nilai dan byte yang dikirim)."""

    def __init__(self, host: str, port: int):
        self.addr = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disable_udp_connreset(self._sock)
        self.sent = 0

    def close(self) -> None:
        self._sock.close()

    def send(self, address: str, value) -> None:
        check_write(address, (value,))
        packet = encode(address, (value,))
        try:
            a2, args2 = decode(packet)
        except OscError as exc:
            raise WriteRefused(f"paket tidak bisa didekode: {exc}") from exc
        check_write(a2, args2)
        self._sock.sendto(packet, self.addr)
        self.sent += 1


class Item:
    def __init__(self, addr: str, kind: str, step: float, label: str, awal, target, now: float):
        self.addr, self.kind, self.step, self.label = addr, kind, step, label
        self.awal, self.target = awal, target
        self.cur = awal                  # nilai terakhir yang kita kirim (atau awal)
        self.want = None                 # nilai yang diinginkan (dari slider)
        self.p = 0.0
        self.sends = 0
        self.finalize = False            # hapus dari daftar setelah mencapai `want`
        self.state = "aktif"             # aktif | selesai | dilepas
        self.msg = ""
        self.recent: deque = deque(maxlen=64)   # (waktu, nilai) yang baru dikirim
        self.last_move = now
        self.verified: tuple | None = None      # (cocok, nilai_terbaca)
        self.armed_at = now

    def value_at(self, p: float):
        v = self.awal + (self.target - self.awal) * p
        if self.kind == "float":
            return float(min(1.0, max(0.0, v)))
        return int(round(v))

    def view(self) -> dict:
        return {"alamat": self.addr, "label": self.label, "jenis": self.kind, "awal": self.awal, "target": self.target,
                "nilai": self.cur, "p": round(self.p, 4), "state": self.state, "pesan": self.msg,
                "kirim": self.sends, "verifikasi": self.verified}


class Controller:
    """Mengatur Mode kontrol: daftar parameter yang sedang dikendalikan dan pengirim berlatar belakang."""

    def __init__(self, log_path: Path, read_value, clock=time.monotonic):
        self.log_path = Path(log_path)
        self._read_value = read_value           # (addr) -> nilai terbaca dari mixer, atau None
        self._clock = clock
        self._lock = threading.RLock()
        self._items: dict[str, Item] = {}
        self._client: WriteClient | None = None
        self._expires = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.enabled = False

    # ---------- catatan ----------
    def _log(self, event: str, **kw) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **kw}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------- hidup-mati ----------
    def enable(self, ip: str, port: int) -> None:
        with self._lock:
            self.disable("diganti")
            self._client = WriteClient(ip, port)
            self._stop = threading.Event()
            self.enabled = True
            self._expires = self._clock() + IDLE_S
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self._log("enable", ip=ip, port=port)

    def disable(self, reason: str = "dimatikan") -> None:
        with self._lock:
            if not self.enabled:
                return
            self.enabled = False
            self._stop.set()
            for it in self._items.values():
                self._log("lepas", alamat=it.addr, awal=it.awal, akhir=it.cur, kirim=it.sends, alasan=reason)
            self._items.clear()
            if self._client:
                self._client.close()
                self._client = None
            self._log("disable", alasan=reason)

    def _touch(self) -> None:
        self._expires = self._clock() + IDLE_S

    def _require(self) -> None:
        if not self.enabled:
            raise WriteRefused("Mode kontrol belum dinyalakan.")

    # ---------- operasi ----------
    def arm(self, addr: str, awal, target, confirmed: bool = False) -> dict:
        with self._lock:
            self._require()
            s = spec_for(addr)
            if s is None:
                raise WriteRefused(f"{addr} tidak boleh diubah dari aplikasi.")
            kind, step, label = s
            if addr in self._items and self._items[addr].state == "aktif":
                raise WriteRefused("Parameter ini sedang dikendalikan.")
            if sum(1 for i in self._items.values() if i.state == "aktif") >= MAX_ARMED:
                raise WriteRefused(f"Maksimal {MAX_ARMED} parameter sekaligus. Selesaikan atau kembalikan salah satu dulu.")
            if awal is None or target is None:
                raise WriteRefused("Nilai awal/target tidak terbaca.")
            if kind == "float":
                if isinstance(awal, bool) or isinstance(target, bool) or not isinstance(awal, (int, float)) or not isinstance(target, (int, float)):
                    raise WriteRefused("Nilai tidak dikenali.")
                awal, target = float(awal), float(target)
            else:
                if isinstance(awal, bool) or not isinstance(awal, int) or isinstance(target, bool) or not isinstance(target, (int, float)):
                    raise WriteRefused("Tipe nilai dari mixer tidak sesuai harapan; ubah manual di M-AIR.")
                target = int(round(target))
            check_write(addr, (awal,))
            check_write(addr, (target,))
            if awal == target:
                raise WriteRefused("Nilai sekarang sudah sama dengan rekomendasi.")
            if addr.startswith("/headamp/") and abs(target - awal) * 72.0 > MAX_GAIN_SWING_DB:
                raise WriteRefused(f"Perubahan gain lebih dari {MAX_GAIN_SWING_DB:.0f} dB ditolak.")
            if (kind == "toggle" or (addr.startswith("/headamp/") and target > awal)) and not confirmed:
                raise WriteRefused("Perlu konfirmasi operator.")
            it = Item(addr, kind, step, label, awal, target, self._clock())
            self._items[addr] = it
            self._touch()
            self._log("arm", alamat=addr, awal=awal, target=target)
            return it.view()

    def set_fraction(self, addr: str, p: float) -> dict:
        with self._lock:
            self._require()
            it = self._items.get(addr)
            if it is None or it.state != "aktif":
                raise WriteRefused("Parameter ini tidak sedang dikendalikan.")
            if not isinstance(p, (int, float)) or isinstance(p, bool) or not math.isfinite(p):
                raise WriteRefused("Posisi tidak valid.")
            it.p = min(1.0, max(0.0, float(p)))
            it.want = it.value_at(it.p)
            it.verified = None
            self._touch()
            return it.view()

    def release(self, addr: str, restore: bool = False) -> dict:
        with self._lock:
            self._require()
            it = self._items.get(addr)
            if it is None:
                return {"alamat": addr, "state": "selesai"}
            if it.state != "aktif":
                self._items.pop(addr, None)
                return it.view()
            if restore:
                it.p, it.want = 0.0, it.awal
            it.finalize = True
            self._touch()
            self._log("restore" if restore else "kunci", alamat=addr, awal=it.awal, target=it.target)
            return it.view()

    def restore_all(self) -> int:
        with self._lock:
            self._require()
            n = 0
            for it in self._items.values():
                if it.state == "aktif" and (it.cur != it.awal or (it.want is not None and it.want != it.awal)):
                    it.p, it.want, it.finalize = 0.0, it.awal, True
                    n += 1
                elif it.state == "aktif":
                    it.finalize = True
            self._touch()
            self._log("restore-all", jumlah=n)
            return n

    def on_param(self, addr: str, args) -> None:
        """Kabar perubahan dari mixer: bila bukan hasil kiriman kita, operator mengubahnya di M-AIR -> lepas kontrol."""
        with self._lock:
            it = self._items.get(addr)
            if not self.enabled or it is None or it.state != "aktif" or not args:
                return
            v = args[0]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return
            now = self._clock()
            known = [it.cur, it.awal] + ([it.want] if it.want is not None else []) + [x for t, x in it.recent if now - t <= RECENT_S]
            tol = 0.5 if it.kind != "float" else ECHO_TOL
            if any(abs(float(v) - float(k)) <= tol for k in known):
                return
            it.state, it.msg = "dilepas", "Diubah di M-AIR; kontrol dari aplikasi dilepas."
            it.want = None
            self._log("konflik", alamat=addr, nilai_mixer=v, terakhir_kita=it.cur)

    def status(self) -> dict:
        with self._lock:
            left = max(0.0, self._expires - self._clock()) if self.enabled else 0.0
            return {"aktif": self.enabled, "sisa_detik": int(left), "item": [i.view() for i in self._items.values()]}

    # ---------- pengirim ----------
    def _next_value(self, it: Item):
        cur, want = it.cur, it.want
        if it.kind == "float":
            d = want - cur
            if abs(d) <= it.step:
                return float(want)
            return float(cur + math.copysign(it.step, d))
        d = want - cur
        if it.kind == "toggle" or abs(d) <= 1:
            return int(want)
        return int(cur + (1 if d > 0 else -1))

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self._tick()
            except Exception as exc:  # pengirim tidak boleh mati diam-diam
                self._log("error", pesan=f"{type(exc).__name__}: {exc}")
                self.disable("kesalahan internal")
                return

    def _tick(self) -> None:
        verify: list[Item] = []
        with self._lock:
            if not self.enabled:
                return
            now = self._clock()
            if now >= self._expires:
                self.disable("tidak ada aktivitas")
                return
            budget = MAX_SENDS_PER_TICK
            for it in list(self._items.values()):
                if it.state != "aktif":
                    continue
                if it.want is not None and it.want != it.cur and budget > 0:
                    nxt = self._next_value(it)
                    if it.kind == "float" and abs(nxt - it.cur) < MIN_DELTA:
                        it.cur = nxt
                        continue
                    try:
                        self._client.send(it.addr, nxt)
                    except (WriteRefused, OSError) as exc:
                        it.state, it.msg, it.want = "dilepas", f"Gagal mengirim: {exc}", None
                        self._log("gagal", alamat=it.addr, pesan=str(exc))
                        continue
                    budget -= 1
                    it.cur = nxt
                    it.sends += 1
                    it.recent.append((now, nxt))
                    it.last_move = now
                elif it.want is not None and it.want == it.cur and it.verified is None and now - it.last_move >= VERIFY_AFTER_S:
                    verify.append(it)
                if it.finalize and (it.want is None or it.want == it.cur):
                    verify_first = it.verified is None and it.sends > 0
                    if not verify_first:
                        self._log("selesai", alamat=it.addr, awal=it.awal, akhir=it.cur, kirim=it.sends, verifikasi=it.verified)
                        self._items.pop(it.addr, None)
        for it in verify:                       # membaca balik di luar kunci (butuh jaringan)
            got = None
            try:
                got = self._read_value(it.addr)
            except Exception:
                pass
            with self._lock:
                if self._items.get(it.addr) is not it or it.state != "aktif":
                    continue
                tol = 0.5 if it.kind != "float" else ECHO_TOL
                ok = got is not None and abs(float(got) - float(it.cur)) <= tol
                it.verified = (ok, got)
                if not ok:
                    it.msg = ("Mixer tidak menjawab saat dicek." if got is None
                              else f"Mixer menunjukkan {got}, bukan {it.cur}. Cek di M-AIR.")
                    self._log("verifikasi-gagal", alamat=it.addr, dikirim=it.cur, terbaca=got)
