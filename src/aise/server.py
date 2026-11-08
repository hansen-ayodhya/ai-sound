"""Aplikasi web lokal: semua fungsi lewat browser, tanpa mengetik perintah.

    python -m aise.server            (dijalankan oleh AI-Sound-Engineer.bat)

Keamanan:
- Bawaan: hanya mendengarkan di 127.0.0.1 (tidak bisa dibuka dari perangkat lain).
- Mode jaringan (`--lan`, lewat AI-Sound-Engineer-Jaringan.bat): perangkat lain di jaringan
  gereja bisa membuka aplikasi setelah memasukkan PIN. Percobaan PIN salah dibatasi.
  Update/cek program/matikan hanya boleh dari laptop itu sendiri. Lalu lintas tidak
  terenkripsi (HTTP), jadi pakai hanya di jaringan gereja yang berpassword.
- Header Host harus localhost/127.0.0.1 (mencegah DNS rebinding).
- Setiap panggilan API wajib membawa token acak yang hanya tertanam di halaman
  yang disajikan server ini (website lain tidak bisa membacanya).
- Semua akses mixer tetap lewat klien READ-ONLY dan penjaganya.
- Satu pekerjaan mixer pada satu waktu.
"""

from __future__ import annotations

import argparse
import base64
import html
import ipaddress
import json
import mimetypes
import secrets
import socket
import subprocess
import sys
import threading
from collections import deque
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlparse

from . import ai, channels, meters, recs
from . import dsp as dspmod
from .clips import ClipError, ClipStore, wav_info
from .addresses import all_addresses
from .audit import PROBE_ADDRESSES, MixerNotAnswering, run_audit
from .client import DEFAULT_PORT, ReadOnlyClient
from .dump import run_dump
from .guard import ReadOnlyViolation, check
from .guard import ALLOWED_PREFIXES
from .live import LiveMonitor, analyze_eq_problems, inactive_from_dump, labels_from_dump, make_gain_findings
from . import convert
from . import siaran as siaranmod
from . import stream as streammod
from .writer import spec_for
from .report import findings_json, render
from .menu import load_ip, save_ip, valid_ip
from .ignored import Ignored, key_for
from .memori import Memori
from .writer import Controller, WriteRefused
from .paths import BUILD_VERSION_FILE, BUNDLE, FROZEN
from .paths import DATA as DATA_DIR

ROOT = BUNDLE
CONTENT_TYPES = {".html": "text/html", ".json": "application/json", ".md": "text/plain",
                 ".js": "application/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml"}
DEFAULT_HTTP_PORT = 8765
MAX_BODY = 60_000
MAX_CLIP_BODY = 3_000_000  # klip ≤30 dtk WAV 16 kHz mono ≈ 1 MB, base64 ≈ 1,3 MB
MAX_DSP_BODY = 10_000_000  # preset ≤5 MB atau tangkapan layar, dalam base64
BIG_BODY = {"/api/ai/analyze": 4_000_000, "/api/clips": MAX_CLIP_BODY, "/api/dsp/preset": MAX_DSP_BODY, "/api/dsp/ai-read": MAX_DSP_BODY}
IMAGE_MIME = ("image/png", "image/jpeg", "image/webp")
LIVE_REFRESH_S = 60  # baca ulang setting mixer selama live
PARAM_FLUSH_S = 1.5  # jeda sebelum kabar perubahan digabung ke setting terbaru
MAX_PIN_FAILS = 5
PIN_LOCK_S = 300
# Kunci Gemini boleh DITULIS dari perangkat yang sudah masuk (PIN); kunci tidak pernah dikirim balik.
LOCAL_ONLY = {"/api/update", "/api/tests", "/api/shutdown"}

LOGIN_PAGE = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Masuk</title>
<style>:root{{--bg:#0f1216;--panel:#171c22;--line:#2c3540;--text:#e6eaf0;--muted:#8c97a6;--accent:#5aa2ff;--bad:#ff6b6b}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:16px}}
form{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px;width:min(360px,100%)}}
h1{{font-size:18px;margin:0 0 4px}}p{{color:var(--muted);margin:0 0 14px;font-size:13px}}
input{{width:100%;font:inherit;font-size:22px;letter-spacing:.3em;text-align:center;padding:10px;border-radius:8px;
border:1px solid var(--line);background:var(--bg);color:var(--text)}}
button{{width:100%;margin-top:12px;padding:10px;font:inherit;font-weight:700;border-radius:8px;border:0;background:var(--accent);color:#0b1a2e}}
.err{{color:var(--bad);margin-top:10px;font-size:13px}}</style></head><body>
<form method="post" action="/login"><h1>AI Sound Engineer</h1>
<p>Masukkan PIN yang tampil di laptop (jendela hitam aplikasi, atau Beranda di laptop).</p>
<input name="pin" inputmode="numeric" autocomplete="one-time-code" maxlength="12" autofocus aria-label="PIN">
<button type="submit">Masuk</button>{err}</form></body></html>"""


class Busy(RuntimeError):
    pass


class App:
    def __init__(self, root: Path = ROOT, data: Path | None = None, addresses: list[str] | None = None,
                 lan: bool = False, pin: str | None = None, trust_loopback: bool = True,
                 ai_base: str = ai.API_BASE):
        self.ai_base = ai_base
        self._ai_lock = threading.Lock()
        self.lan = lan
        self.pin = pin
        self.trust_loopback = trust_loopback  # False hanya untuk tes (meniru perangkat lain)
        self.sessions: set[str] = set()
        self.pin_fails: dict[str, tuple[int, float]] = {}
        self.lan_url: str | None = None
        self.root = root
        self.version = _version(root)
        self.frozen = FROZEN
        self.data = data or (DATA_DIR if root == ROOT else root / "data")
        self.addresses = addresses
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self.job: dict = {"state": "idle"}
        self.shutdown_cb = None
        self.live: LiveMonitor | None = None
        self.live_settings_at: str | None = None
        self._refresh_now = threading.Event()
        self._recs_lock = threading.Lock()
        # Perubahan dari M-AIR yang diterima lewat /xremote (hanya menerima)
        self.param_seq = 0
        self.param_log: deque = deque(maxlen=800)
        self._overlay: dict[str, list] = {}
        self._param_lock = threading.Lock()
        self._flush_pending = False
        # Mode kontrol (jalur tulis terpisah; mati sampai operator menyalakannya)
        self.memori = Memori(self.data / "memori")
        self.ignored = Ignored(self.data / "diabaikan.json")
        self.prefs_path = self.data / "prefs.json"
        self.stream = streammod.Checker(self.data / "stream-checks.jsonl", self.stream_cfg)
        self._fails: dict[str, int] = {}   # berapa kali berturut-turut alamat tidak dijawab (dilewati setelah 3x)
        self._values: dict = {}           # nilai setting terbaru (untuk menilai peringatan live secara realtime)
        self.control = Controller(self.data / "changes.jsonl", self._read_one, on_event=self._memori_event)

    # ---------- pekerjaan ----------
    def _acquire(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise Busy("Masih ada pekerjaan berjalan. Tunggu sampai selesai.")

    def start_job(self, kind: str, fn) -> None:
        self._acquire()
        self.job = {"state": "running", "kind": kind, "started": time.strftime("%H:%M:%S"),
                    "progress": None, "phase": ""}

        def run():
            try:
                self.job.update(state="done", result=fn())
            except MixerNotAnswering as exc:
                self.job.update(state="error", error=str(exc))
            except Exception as exc:  # tampilkan ke pengguna, jangan matikan server
                self.job.update(state="error", error=f"{type(exc).__name__}: {exc}")
            finally:
                self._lock.release()

        threading.Thread(target=run, daemon=True).start()

    # ---------- aksi ----------
    def probe(self, ip: str, port: int) -> dict:
        self._acquire()
        try:
            with ReadOnlyClient(ip, port, timeout=0.5, retries=1,
                                log_path=self.data / "osc_log.jsonl") as c:
                d = run_dump(c, PROBE_ADDRESSES)
        finally:
            self._lock.release()
        return {"answered": d["answered"], "queried": d["queried"], "values": d["values"]}

    def audit(self, ip: str, port: int) -> None:
        passes = {"n": 0}

        def progress(i: int, n: int) -> None:
            self.job["progress"] = [i, n]
            self.job["phase"] = f"Pembacaan {passes['n'] + 1} dari 2"
            if i == n:
                passes["n"] += 1

        self.start_job("audit", lambda: run_audit(ip, port, base=self.data, profile=self.profile(),
                                                  addresses=self.addresses, progress=progress))

    def record_meters(self, ip: str, port: int, path: str, seconds: float) -> None:
        def fn():
            with ReadOnlyClient(ip, port, log_path=self.data / "osc_log.jsonl") as c:
                self.job["phase"] = f"Merekam {seconds:.0f} detik"
                res = meters.record(c, path, seconds)
            out = self.data / f"meters-{time.strftime('%Y%m%d-%H%M%S')}.json"
            meters.save(res, out)
            res["file"] = out.name
            return res

        self.start_job("meters", fn)

    def start_live(self, ip: str, port: int, path: str) -> None:
        self._acquire()  # dilepas saat pemantauan berhenti
        self.job = {"state": "live", "kind": "live", "started": time.strftime("%H:%M:%S")}
        dump = None
        latest = self.latest()
        if latest:
            try:
                dump = json.loads((self.data.parent / latest["dump"]).read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError):
                dump = None

        def on_stop():
            self.job = {"state": "idle"}
            self._lock.release()

        log = self.data / f"live-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.live = LiveMonitor(ip, port, path, labels_from_dump(dump, self.profile()), log, on_stop=on_stop,
                                on_param=self._on_param, inactive=inactive_from_dump(dump), on_alert=self._memori_alert, state_of=self._state_of,
                                weak_alerts=self.prefs().get("weak_alerts", False))
        try:
            self.live.start()
        except Exception:
            on_stop()
            raise
        # Selama live, setting mixer dibaca ulang berkala (hanya query) supaya saran mengikuti situasi terbaru.
        if dump:
            self._values = dict(dump.get("values", {}))
        threading.Thread(target=self._refresh_settings_loop, args=(ip, port, self.live, None), daemon=True).start()

    def _read_settings(self, ip: str, port: int, addresses: list[str] | None) -> dict:
        """Baca setting sekali (hanya query), simpan sebagai 'setting terbaru' dan jadikan acuan tampilan."""
        todo = [a for a in (addresses or self.addresses or all_addresses(self.stream_bus())) if self._fails.get(a, 0) < 3]
        with ReadOnlyClient(ip, port, timeout=0.3, retries=1, log_path=self.data / "osc_log.jsonl") as c:
            d = run_dump(c, todo)
        if not d["answered"]:
            raise MixerNotAnswering(f"Mixer di {ip}:{port} tidak menjawab saat membaca setting.")
        prev = self._values
        for a, v in list(d["values"].items()):
            self._fails[a] = self._fails.get(a, 0) + 1 if v is None else 0
            if v is None and prev.get(a) is not None:
                d["values"][a] = prev[a]          # balasan yang hilang tidak boleh menghapus nilai yang sudah diketahui
        self._save_dump(d)
        with self._param_lock:
            self._overlay.clear()          # pembacaan penuh lebih baru daripada kabar perubahan sebelumnya
        return d

    def _save_dump(self, d: dict, mark: bool = True) -> None:
        folder = self.data / "live-setting"
        folder.mkdir(parents=True, exist_ok=True)
        profile = self.profile()
        (folder / "dump1.json").write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "findings.json").write_text(json.dumps(self.rule_findings(d, profile), indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "report.md").write_text(render(d, "live-setting/dump1.json", profile), encoding="utf-8")
        latest = {"dump": "data/live-setting/dump1.json", "findings": "data/live-setting/findings.json",
                  "report": "data/live-setting/report.md", "created": d["created"], "live": True}
        (self.data / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")
        self._values = d.get("values", {})
        if mark:
            self.live_settings_at = d["created"]

    # ---------- kabar perubahan dari mixer (realtime) ----------
    def _on_param(self, addr: str, args) -> None:
        self.control.on_param(addr, args)
        if not addr.startswith(ALLOWED_PREFIXES) or addr.startswith("/meters"):
            return
        val = [a for a in args if isinstance(a, (int, float, str)) and not isinstance(a, bool)]
        if not val:
            return
        self._values[addr] = val
        with self._param_lock:
            self.param_seq += 1
            self.param_log.append((self.param_seq, addr, val))
            self._overlay[addr] = val
            if not self._flush_pending:
                self._flush_pending = True
                threading.Timer(PARAM_FLUSH_S, self._flush_params).start()

    def _flush_params(self) -> None:
        """Gabungkan kabar perubahan ke 'setting terbaru' (agar aturan, saran, dan verifikasi ikut terbarui)."""
        with self._param_lock:
            self._flush_pending = False
            overlay = dict(self._overlay)
        if not overlay:
            return
        try:
            _, d = self.latest_dump()
            if not d:
                return
            d = json.loads(json.dumps(d))
            d["values"].update(overlay)
            d["created"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._save_dump(d, mark=False)
        except Exception:
            pass

    def params_since(self, since: int | None) -> dict:
        with self._param_lock:
            seq = self.param_seq
            if since is None:
                return {"param_seq": seq, "params": []}
            last: dict[str, tuple] = {}
            for n, a, v in self.param_log:
                if n > since:
                    last[a] = (n, v)
            return {"param_seq": seq, "params": [{"a": a, "v": v} for a, (n, v) in sorted(last.items(), key=lambda kv: kv[1][0])][-400:]}

    def _refresh_settings_loop(self, ip: str, port: int, mon, addresses: list[str] | None) -> None:
        wait = 5.0
        while True:
            waited = 0.0
            while waited < wait:          # tunggu jadwal, kecuali operator menekan "Baca setting"
                if mon._stop.wait(0.5):
                    return
                if self._refresh_now.is_set():
                    break
                waited += 0.5
            self._refresh_now.clear()
            wait = LIVE_REFRESH_S
            if not mon.running:
                return
            try:
                self._read_settings(ip, port, addresses)
            except Exception:  # pembaruan setting tidak boleh mematikan pantau meter
                continue

    def refresh_settings(self) -> dict:
        """Tombol '↻ Baca setting': saat live -> percepat jadwal; bila tidak live -> baca sekali sekarang."""
        self._fails.clear()               # permintaan operator: tanyakan lagi semua alamat, termasuk yang sempat dilewati
        if self.live and self.live.running:
            self._refresh_now.set()
            return {"mode": "live"}
        ip, port = load_ip(self.data / "mixer-ip.txt"), self.load_port()
        if not valid_ip(ip):
            raise ValueError("IP mixer belum diisi. Isi di Beranda (Uji kecil) dulu.")
        _, dump = self.latest_dump()
        if dump and not self._values:
            self._values = dict(dump.get("values", {}))
        self._acquire()
        try:
            d = self._read_settings(ip, port, None)
        finally:
            self._lock.release()
        return {"mode": "sekarang", "answered": d["answered"], "queried": d["queried"], "created": d["created"]}

    # ---------- Daftar Channel ----------
    def profile(self) -> dict | None:
        return channels.load(self.data / "channels.json")

    def latest_dump(self) -> tuple[dict | None, dict | None]:
        latest = self.latest()
        if not latest:
            return None, None
        try:
            return latest, json.loads((self.data.parent / latest["dump"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            return latest, None

    def channels_view(self) -> dict:
        saved = self.profile()
        base = saved or channels.default_profile()
        _, dump = self.latest_dump()
        names = {}
        if dump:
            for n in range(1, 17):
                v = dump["values"].get(f"/ch/{n:02d}/config/name")
                if v and v[0] not in (None, ""):
                    names[n] = channels.clean_name(v[0])
        return {"profile": base, "saved": saved is not None, "mixer_names": names,
                "suggested": channels.suggest(base, dump) if dump else None,
                "types": {k: v["label"] for k, v in channels.TYPES.items()},
                "koneksi": channels.KONEKSI}

    def save_channels(self, profile: dict) -> dict:
        clean = channels.save(self.data / "channels.json", profile)
        # Hitung ulang saran AI audit terakhir dengan daftar baru.
        latest, dump = self.latest_dump()
        count = None
        if latest and dump:
            fnd = findings_json(dump, clean)
            (self.data.parent / latest["findings"]).write_text(json.dumps(fnd, indent=2, ensure_ascii=False), encoding="utf-8")
            (self.data.parent / latest["report"]).write_text(render(dump, latest["dump"], clean), encoding="utf-8")
            count = len(fnd)
        return {"ok": True, "findings": count, "profile": clean}

    def disk_version(self) -> str:
        """Versi kode di folder sekarang (bisa lebih baru dari yang sedang berjalan setelah 'Update program')."""
        now = time.monotonic()
        if now - getattr(self, "_disk_v_t", -1e9) > 10:
            self._disk_v = self.version if self.frozen else _version(self.root)
            self._disk_v_t = now
        return self._disk_v

    def own_ips(self) -> set[str]:
        """Alamat milik komputer ini (localhost + IP jaringannya). Disimpan sebentar."""
        now = time.monotonic()
        if now - getattr(self, "_own_ips_t", -1e9) > 30:
            ips = {"127.0.0.1", "::1"}
            try:
                ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
            except OSError:
                pass
            ip = lan_ip()
            if ip:
                ips.add(ip)
            self._own_ips, self._own_ips_t = ips, now
        return self._own_ips

    # ---------- Gemini ----------
    @property
    def ai_cfg_path(self) -> Path:
        return self.data / "gemini.json"

    def ai_view(self) -> dict:
        try:
            last = json.loads((self.data / "ai-last.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = None
        return {"status": ai.public_status(ai.load_config(self.ai_cfg_path)), "last": last}

    def ai_config(self, body: dict) -> dict:
        cfg = ai.load_config(self.ai_cfg_path)
        if body.get("hapus") is True:
            self.ai_cfg_path.unlink(missing_ok=True)
            return ai.public_status({})
        key = ai.clean_key(body.get("key") or "")
        if key or body.get("key"):
            problem = ai.key_problem(key)
            if problem:
                raise ValueError(problem)
            models = ai.list_models(key, base=self.ai_base)
            model = ai.pick_model(models)
            if not model:
                raise ValueError("Kunci diterima, tapi tidak ada model Gemini yang bisa dipakai untuk kunci ini.")
            cfg = {"key": key, "model": model, "models": models}
        elif body.get("model"):
            if not cfg.get("key"):
                raise ValueError("Isi API key dulu.")
            if body["model"] not in (cfg.get("models") or []):
                raise ValueError("Model tidak ada di daftar untuk kunci ini.")
            cfg["model"] = body["model"]
        else:
            raise ValueError("Tidak ada yang disimpan.")
        ai.save_config(self.ai_cfg_path, cfg)
        return ai.public_status(cfg)

    @property
    def clips(self) -> ClipStore:
        return ClipStore(self.data)

    def clip_ai(self, cid: str) -> dict:
        if not self._ai_lock.acquire(blocking=False):
            raise Busy("Gemini masih memproses permintaan sebelumnya. Tunggu sebentar.")
        try:
            rec, wav = self.clips.get(cid)
            _, dump = self.latest_dump()
            profile = self.profile()
            findings = self.rule_findings(dump, profile) if dump else []
            res = ai.analyze_clip(ai.load_config(self.ai_cfg_path), rec, wav, dump, profile, findings, base=self.ai_base,
                                  dsp=self.dsp_data())
            self._track_gemini(res, dump)
            self.clips.set_ai(cid, res)
            # Hasil klip juga menjadi "analisis AI terakhir" supaya bayangannya tampil di Tampilan Mixer.
            (self.data / "ai-last.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
            return res
        finally:
            self._ai_lock.release()

    # ---------- pelacakan rekomendasi ----------
    @property
    def recs_path(self) -> Path:
        return self.data / "recs.json"

    def _track_gemini(self, res: dict, dump: dict | None) -> None:
        stamp = time.strftime("%H%M%S")
        for p in res.get("prioritas", []):
            p["id"] = f"{p['id']}-{stamp}"   # unik per analisis, supaya tiap analisis dilacak sendiri
        if not dump:
            return
        with self._recs_lock:
            state = recs.load(self.recs_path)
            now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            recs.register(state, res.get("prioritas", []), dump, now)
            recs.evaluate(state, dump, now)
            recs.save(self.recs_path, state)

    def recs_view(self) -> dict:
        """Daftarkan rekomendasi aturan terbaru, bandingkan dengan setting mixer sekarang, kembalikan statusnya."""
        with self._recs_lock:
            state = recs.load(self.recs_path)
            latest, dump = self.latest_dump()
            if dump:
                try:
                    fnd = json.loads((self.data.parent / latest["findings"]).read_text(encoding="utf-8"))
                except (OSError, ValueError, KeyError):
                    fnd = []
                now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                eqp = analyze_eq_problems(dump)
                before = {k: bool(t.get("done_at")) for k, t in state.get("tracked", {}).items()}
                ign = self.ignored.keys()
                recs.register(state, [f for f in fnd + eqp + self.live_findings(dump) if key_for(f) not in ign], dump, now)
                recs.evaluate(state, dump, now, {f["id"] for f in fnd + eqp})
                self._memori_recs(state, before)
                recs.save(self.recs_path, state)
            return {**recs.view(state), "diabaikan": self.ignored.listing()}

    def _memori_recs(self, state: dict, before: dict) -> None:
        """Catat saran baru dan saran yang baru terverifikasi selesai ke memori belajar."""
        try:
            view = recs.view(state)["recs"]
            for fid, t in state.get("tracked", {}).items():
                items = [i.get("teks", "")[:80] for i in t.get("items", [])][:4]
                if fid not in before:
                    self.memori.catat("saran", unik_id=fid, id=fid, ch=t.get("ch"), sumber=t.get("sumber"), masalah=t.get("masalah", "")[:120], usulan=items)
                if t.get("done_at") and not before.get(fid):
                    ch = [{"teks": i["teks"][:80], "awal": i["awal_teks"], "sekarang": i["sekarang_teks"]} for i in view[fid]["items"]][:4]
                    self.memori.catat("selesai", unik_id=fid + "@" + str(t["done_at"]), id=fid, ch=t.get("ch"), masalah=t.get("masalah", "")[:120], items=ch)
        except Exception:
            pass

    def ignore(self, body: dict) -> dict:
        us = body.get("alamat") if isinstance(body.get("alamat"), list) else []
        if len(us) > 12 or not all(isinstance(a, str) and a.startswith("/") and len(a) < 80 for a in us):
            raise ValueError("Data rekomendasi tidak valid.")
        ch = body.get("ch")
        if ch is not None and (not isinstance(ch, int) or isinstance(ch, bool)):
            raise ValueError("Channel tidak valid.")
        f = {"id": str(body.get("id", ""))[:80], "ch": ch, "masalah": str(body.get("masalah", ""))[:160], "usulan": [{"alamat": a} for a in us]}
        if not f["id"]:
            raise ValueError("Rekomendasi tidak dikenal.")
        key = self.ignored.add(f)
        self.memori.catat("abaikan", unik_id=key, ch=ch, masalah=f["masalah"])
        return {"key": key}

    def memori_nilai(self, rid: str, nilai: str) -> dict:
        if nilai not in ("bagus", "kurang"):
            raise ValueError("Penilaian harus 'bagus' atau 'kurang'.")
        if rid not in recs.load(self.recs_path).get("tracked", {}):
            raise ValueError("Rekomendasi itu tidak dikenal.")
        self.memori.catat("nilai", id=rid, nilai=nilai)
        return {"ok": True}

    # ---------- Mode kontrol (menerapkan rekomendasi) ----------
    def _memori_event(self, event: str, kw: dict) -> None:
        if event == "selesai":
            self.memori.catat("perubahan", alamat=kw.get("alamat"), aksi="dikembalikan" if kw.get("akhir") == kw.get("awal") else "diterapkan",
                              awal=recs.fmt_value(kw.get("alamat", ""), kw.get("awal")), akhir=recs.fmt_value(kw.get("alamat", ""), kw.get("akhir")))
        elif event == "konflik":
            self.memori.catat("perubahan", alamat=kw.get("alamat"), aksi="diubah_operator", akhir=recs.fmt_value(kw.get("alamat", ""), kw.get("nilai_mixer")))

    def stream_bus(self) -> int:
        """Bus untuk siaran (mis. YouTube lewat USB); 0 = tidak ada. Bawaan Bus 3 sesuai keterangan operator."""
        b = self.prefs().get("stream_bus", 3)
        return b if isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= siaranmod.N_BUSES else 3

    def rule_findings(self, dump: dict, profile: dict | None) -> list[dict]:
        out = findings_json(dump, profile)
        b = self.stream_bus()
        return out + (siaranmod.findings(dump, b, profile) if b else [])

    def stream_cfg(self) -> tuple[str, int]:
        p = self.prefs()
        url = p.get("youtube_url") if isinstance(p.get("youtube_url"), str) else ""
        m = p.get("stream_check_min", 10)
        return url, m if m in (0, 5, 10, 15, 30) else 10

    def stream_view(self) -> dict:
        url, m = self.stream_cfg()
        return {"url": url, "menit": m, "alat": streammod.tools(), "folder_alat": str(streammod.tools_dir()), "sedang": self.stream.busy, "terakhir": self.stream.last,
                "riwayat": self.stream.history(10)}

    def stream_gemini(self) -> dict:
        url, _ = self.stream_cfg()
        if not url:
            raise ValueError("Isi link YouTube dulu.")
        try:
            s = streammod.sample_for_ai(url)
        except streammod.StreamError as exc:
            raise ValueError(str(exc)) from exc
        return self.ai_analyze("umum", "Analisis siaran YouTube saya: apa yang terdengar oleh penonton (clipping, kekerasan, keseimbangan), "
                               "bandingkan dengan Bus siaran di mixer, dan apa penyebabnya.",
                               audio=[{"peran": "youtube", "wav": s["wav"], "detik": s["detik"], "spektrum": s["spektrum"], "angka": s["angka"]}])

    def lr_eq(self) -> dict:
        """EQ Main L/R (6 band) dari setting mixer terbaru, dalam satuan manusia (perkiraan)."""
        _, dump = self.latest_dump()
        v = (dump or {}).get("values", {})
        def first(a):
            x = v.get(a)
            return x[0] if x else None
        bands = []
        for b in range(1, 7):
            f, g = first(f"/lr/eq/{b}/f"), first(f"/lr/eq/{b}/g")
            if f is None or g is None:
                continue
            q, t = first(f"/lr/eq/{b}/q"), first(f"/lr/eq/{b}/type")
            bands.append({"band": b, "type": t if isinstance(t, int) and not isinstance(t, bool) else None, "hz": round(convert.eq_hz(f)),
                          "db": round(convert.eq_gain_db(g), 1), "q": round(convert.eq_q(q), 2) if q is not None else 1.0})
        return {"tersedia": bool(bands), "on": first("/lr/eq/on"), "band": bands}

    def siaran_view(self) -> dict:
        b = self.stream_bus()
        _, dump = self.latest_dump()
        profile = self.profile()
        return {"bus": b, "ringkasan": siaranmod.summary(dump, b, profile) if b and dump else None,
                "temuan": siaranmod.findings(dump, b, profile) if b and dump else []}

    def siaran_routing(self) -> dict:
        """Tanyakan alamat kandidat routing USB (hanya membaca). Kebanyakan mungkin tidak dijawab: itu informasi."""
        ip, port = load_ip(self.data / "mixer-ip.txt"), self.load_port()
        if not valid_ip(ip):
            raise ValueError("IP mixer belum diisi. Isi di Beranda dulu.")
        probes = siaranmod.probe_addresses()
        got = {}
        with ReadOnlyClient(ip, port, timeout=0.25, retries=0, log_path=self.data / "osc_log.jsonl") as c:
            for a in probes:
                r = c.query(a)
                if r is not None:
                    got[a] = list(r)
        return {"ditanya": len(probes), "dijawab": got}

    def prefs(self) -> dict:
        try:
            d = json.loads(self.prefs_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def set_prefs(self, body: dict) -> dict:
        p = self.prefs()
        if "stream_bus" in body:
            sb = body["stream_bus"]
            if not isinstance(sb, int) or isinstance(sb, bool) or not 0 <= sb <= siaranmod.N_BUSES:
                raise ValueError("Bus siaran harus 0 (tidak ada) sampai 6.")
            p["stream_bus"] = sb
        if "youtube_url" in body:
            u = body["youtube_url"]
            p["youtube_url"] = "" if u in (None, "") else streammod.valid_url(u)
        if "stream_check_min" in body:
            m = body["stream_check_min"]
            if m not in (0, 5, 10, 15, 30) or isinstance(m, bool):
                raise ValueError("Jeda pengecekan harus 0 (mati), 5, 10, 15, atau 30 menit.")
            p["stream_check_min"] = m
        if "weak_alerts" in body:
            if not isinstance(body["weak_alerts"], bool):
                raise ValueError("Pilihan tidak valid.")
            p["weak_alerts"] = body["weak_alerts"]
            if self.live:
                self.live.rules.weak_enabled = p["weak_alerts"]
        self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
        self.prefs_path.write_text(json.dumps(p), encoding="utf-8")
        if "youtube_url" in body or "stream_check_min" in body:
            self.stream.reschedule()
            self.stream.start()
        return {"weak_alerts": p.get("weak_alerts", False), "stream_bus": self.stream_bus()}

    def _state_of(self, index: int) -> dict:
        ch = index + 1
        def first(a):
            v = self._values.get(a)
            return v[0] if v else None
        return {"gain": first(f"/headamp/{ch:02d}/gain"), "on": first(f"/ch/{ch:02d}/mix/on")}

    def _memori_alert(self, a) -> None:
        self.memori.catat("masalah", ch=a.index + 1, jenis_masalah=a.kind, level_db=a.level_db)

    def _read_one(self, addr: str):
        """Baca satu nilai langsung dari mixer (query read-only); None bila tidak menjawab."""
        ip, port = load_ip(self.data / "mixer-ip.txt"), self.load_port()
        if not valid_ip(ip):
            return None
        with ReadOnlyClient(ip, port, timeout=0.4, retries=2, log_path=self.data / "osc_log.jsonl") as c:
            r = c.query(addr)
        return r[0] if r else None

    def control_status(self) -> dict:
        return {**self.control.status(), "live": bool(self.live and self.live.running)}

    def control_enable(self, body: dict) -> dict:
        if body.get("scene_saved") is not True or body.get("paham") is not True:
            raise ValueError("Centang dulu: scene sudah disimpan di M-AIR, dan Anda paham perubahan langsung terdengar.")
        if not (self.live and self.live.running):
            raise ValueError("Pemantauan live harus berjalan dulu (untuk melihat hasil dan mendeteksi perubahan dari M-AIR).")
        ip, port = load_ip(self.data / "mixer-ip.txt"), self.load_port()
        if not valid_ip(ip):
            raise ValueError("IP mixer belum diisi.")
        _, dump = self.latest_dump()
        if dump:
            snap = {a: v for a, v in dump.get("values", {}).items() if v is not None and spec_for(a)}
            (self.data / f"control-snapshot-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(
                json.dumps({"created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "values": snap}, ensure_ascii=False), encoding="utf-8")
        self.control.enable(ip, port)
        return self.control_status()

    def control_arm(self, addr: str, konfirmasi: bool, hint=None) -> dict:
        if spec_for(addr) is None:
            raise WriteRefused(f"{addr} tidak boleh diubah dari aplikasi.")
        tracked = recs.load(self.recs_path).get("tracked", {})
        cands = [(t.get("dibuat", ""), it) for t in tracked.values() if not t.get("done_at") for it in t["items"] if it["alamat"] == addr]
        if not cands:
            raise WriteRefused("Rekomendasi untuk parameter ini belum tercatat atau sudah selesai. Muat ulang halaman.")
        if isinstance(hint, (int, float)) and not isinstance(hint, bool):       # target dari kartu yang diklik operator
            cands = [c for c in cands if abs(float(c[1]["target"]) - float(hint)) <= 0.002]
            if not cands:
                raise WriteRefused("Rekomendasi itu sudah berubah. Muat ulang halaman.")
        target = max(cands, key=lambda c: c[0])[1]["target"]
        awal = self._read_one(addr)
        if awal is None:
            raise ValueError("Mixer tidak menjawab saat membaca nilai sekarang. Perubahan dibatalkan.")
        return self.control.arm(addr, awal, target, konfirmasi)

    def live_findings(self, dump: dict | None = None) -> list[dict]:
        """Usulan gain dari clipping/hampir clipping yang baru terjadi di meter live (kosong bila tidak live)."""
        if not self.live:
            return []
        events = self.live.recent_events()
        if not events:
            return []
        if dump is None:
            _, dump = self.latest_dump()
        return make_gain_findings(events, dump, self.live.labels)

    def eq_problems(self) -> list[dict]:
        """Analisis setting EQ mixer dan deteksi masalah: boomy, harsh, dll."""
        _, dump = self.latest_dump()
        if not dump:
            return []
        return analyze_eq_problems(dump)

    # ---------- DSP ----------
    @property
    def dsp_path(self) -> Path:
        return self.data / "dsp" / "dsp.json"

    def dsp_data(self) -> dict | None:
        return dspmod.load(self.dsp_path)

    def dsp_view(self) -> dict:
        data = self.dsp_data()
        return {"data": data or dspmod.blank(), "saved": data is not None, "peran": dspmod.PERAN,
                "presets": dspmod.PresetStore(self.data).list(), "findings": dspmod.analyze(data)}

    def dsp_ai_read(self, body: dict) -> dict:
        if body.get("izin") is not True:
            raise ValueError("Centang izin dulu sebelum data dikirim ke Google.")
        if body.get("preset_id"):
            rec, raw = dspmod.PresetStore(self.data).get(str(body["preset_id"]))
            if not rec["info"].get("teks"):
                raise ValueError("File preset ini biner (bukan teks), jadi tidak bisa dibaca Gemini. Pakai tangkapan layar software Protea.")
            text = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", errors="replace")[:200_000]
            parts = [{"text": "Isi file preset '" + rec["nama_file"] + "':\n" + text}]
        else:
            mime = str(body.get("mime", ""))
            if mime not in IMAGE_MIME:
                raise ValueError("Gambar harus PNG, JPG, atau WEBP.")
            try:
                img = base64.b64decode(body.get("image_b64") or "", validate=True)
            except ValueError:
                raise ValueError("Gambar rusak saat diunggah.") from None
            if not img or len(img) > 7_000_000:
                raise ValueError("Gambar kosong atau terlalu besar (maks 7 MB).")
            parts = [{"inline_data": {"mime_type": mime, "data": base64.b64encode(img).decode("ascii")}}]
        if not self._ai_lock.acquire(blocking=False):
            raise Busy("Gemini masih memproses permintaan sebelumnya. Tunggu sebentar.")
        try:
            return ai.read_dsp(ai.load_config(self.ai_cfg_path), parts, base=self.ai_base)
        finally:
            self._ai_lock.release()

    def ai_analyze(self, situasi: str, pertanyaan: str, audio: list[dict] | None = None) -> dict:
        if not self._ai_lock.acquire(blocking=False):
            raise Busy("Gemini masih memproses permintaan sebelumnya. Tunggu sebentar.")
        try:
            _, dump = self.latest_dump()
            profile = self.profile()
            findings = self.rule_findings(dump, profile) if dump else []
            live = self.live.snapshot() if self.live else None
            res = ai.analyze(ai.load_config(self.ai_cfg_path), dump, profile, findings, live,
                             situasi=situasi, pertanyaan=pertanyaan, base=self.ai_base, dsp=self.dsp_data(),
                             riwayat=self.memori.profil(), audio=audio,
                             siaran=siaranmod.summary(dump, self.stream_bus(), profile) if self.stream_bus() else None)
            if audio:
                res["audio"] = [{"peran": a["peran"], "detik": a["detik"]} for a in audio]
            self._track_gemini(res, dump)
            (self.data / "ai-last.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
            return res
        finally:
            self._ai_lock.release()

    def stop_live(self) -> None:
        if self.live:
            self.live.stop()

    def save_port(self, port: int) -> None:
        (self.data / "mixer-port.txt").write_text(str(port), encoding="utf-8")

    def load_port(self) -> int:
        try:
            return int((self.data / "mixer-port.txt").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return DEFAULT_PORT

    def latest(self) -> dict | None:
        try:
            return json.loads((self.data / "latest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def run_cmd(self, args: list[str], timeout: int) -> dict:
        try:
            r = subprocess.run(args, cwd=self.root, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return {"ok": False, "output": f"Program '{args[0]}' tidak ditemukan di laptop ini."}
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "Waktu habis."}
        out = (r.stdout + r.stderr).strip()
        return {"ok": r.returncode == 0, "output": out[-4000:]}


def _clean_audio(items, izin) -> list[dict] | None:
    """Periksa rekaman yang ikut dikirim ke Gemini: maks 2 (HP dan mixer), WAV valid, dan operator sudah memberi izin."""
    if not items:
        return None
    if not isinstance(items, list) or len(items) > 2:
        raise ValueError("Maksimal 2 rekaman (HP dan mixer).")
    if izin is not True:
        raise ValueError("Centang izin dulu sebelum rekaman dikirim ke Google.")
    out, seen = [], set()
    for it in items:
        peran = it.get("peran") if isinstance(it, dict) else None
        if peran not in ("ruangan_hp", "mixer") or peran in seen:
            raise ValueError("Peran rekaman harus 'ruangan_hp' atau 'mixer', masing-masing satu.")
        seen.add(peran)
        try:
            wav = base64.b64decode(str(it.get("wav_b64", "")), validate=True)
        except ValueError as exc:
            raise ValueError("Rekaman tidak valid.") from exc
        info = wav_info(wav)
        spec = it.get("spektrum")
        if isinstance(spec, list) and len(spec) == 24 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and abs(x) < 200 for x in spec):
            spec = [round(float(x), 1) for x in spec]
        else:
            spec = None
        out.append({"peran": peran, "wav": wav, "detik": info["seconds"], "spektrum": spec})
    return out


def _remember(app: "App", ip: str, port: int) -> None:
    save_ip(ip, app.data / "mixer-ip.txt")
    app.save_port(port)


def _parse_target(body: dict) -> tuple[str, int]:
    ip = str(body.get("ip", "")).strip()
    if not valid_ip(ip):
        raise ValueError("IP tidak valid. Contoh: 192.168.1.50")
    port = body.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Port tidak valid.")
    return ip, port


def _version(root: Path) -> str:
    try:
        text = BUILD_VERSION_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%d-%m-%Y %H:%M"],
                           cwd=root, capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def load_or_create_pin(path: Path) -> str:
    try:
        pin = path.read_text(encoding="utf-8").strip()
        if pin.isdigit() and len(pin) >= 6:
            return pin
    except OSError:
        pass
    pin = f"{secrets.randbelow(10**6):06d}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pin, encoding="utf-8")
    return pin


def lan_ip() -> str | None:
    """IP laptop di jaringan lokal (tidak mengirim paket; hanya memilih antarmuka)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return ip if ipaddress.IPv4Address(ip).is_private and not ip.startswith("127.") else None
    except OSError:
        return None


def make_handler(app: App, http_port: int):
    allowed_hosts = {f"localhost:{http_port}", f"127.0.0.1:{http_port}"}
    static_roots = {"/viewer/": app.root / "viewer", "/data/": app.data}

    class Handler(BaseHTTPRequestHandler):
        server_version = "AISE"

        def log_message(self, *args):  # tenang di konsol
            pass

        # ---------- pembantu ----------
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            if host in allowed_hosts:
                return True
            if not app.lan:
                return False
            # Mode jaringan: hanya alamat IP pribadi (bukan nama domain) dengan port kita.
            name, _, port = host.rpartition(":")
            try:
                return port == str(http_port) and ipaddress.IPv4Address(name).is_private
            except ValueError:
                return False

        def _is_local(self) -> bool:
            # Komputer yang menjalankan aplikasi = lokal, termasuk bila dibuka lewat IP jaringannya sendiri.
            return app.trust_loopback and self.client_address[0] in app.own_ips()

        def _session(self) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "aise_session" and v in app.sessions:
                    return v
            return None

        def _authed(self) -> bool:
            return self._is_local() or self._session() is not None

        def _login_page(self, code: int = 200, err: str = "") -> None:
            e = f'<div class="err">{html.escape(err)}</div>' if err else ""
            self._send(code, LOGIN_PAGE.format(err=e).encode("utf-8"), "text/html; charset=utf-8")

        def _redirect(self, where: str, cookie: str | None = None) -> None:
            self.send_response(302)
            self.send_header("Location", where)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _do_login(self) -> None:
            who = self.client_address[0]
            n, until = app.pin_fails.get(who, (0, 0.0))
            if until > time.time():
                return self._login_page(429, f"Terlalu banyak PIN salah. Coba lagi dalam {int(until - time.time()) // 60 + 1} menit.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1000:
                return self._login_page(400, "Permintaan tidak valid.")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            pin = ""
            for part in raw.split("&"):
                k, _, v = part.partition("=")
                if k == "pin":
                    pin = unquote_plus(v).strip()
            if app.pin and secrets.compare_digest(pin, app.pin):
                app.pin_fails.pop(who, None)
                sid = secrets.token_urlsafe(24)
                app.sessions.add(sid)
                return self._redirect("/viewer/home.html",
                                      f"aise_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
            n += 1
            app.pin_fails[who] = (n, time.time() + PIN_LOCK_S if n >= MAX_PIN_FAILS else 0.0)
            return self._login_page(401, "PIN salah.")

        def _token_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-AISE-Token", ""), app.token)

        def _static(self, path: str) -> None:
            for prefix, base in static_roots.items():
                if path.startswith(prefix):
                    target = (base / path[len(prefix):]).resolve()
                    if not target.is_relative_to(base.resolve()) or not target.is_file():
                        break
                    # Dari folder data hanya hasil audit/meter/AI yang boleh dibuka.
                    # TIDAK PERNAH: gemini.json (API key), pin.txt, mixer-ip.txt, channels.json, log.
                    if prefix == "/data/":
                        rel = target.relative_to(base.resolve())
                        ok_file = ((rel.parts[0].startswith("audit-") or rel.parts[0] == "live-setting") and target.suffix in (".json", ".md")) or \
                            rel.as_posix() in ("latest.json", "ai-last.json") or \
                            (len(rel.parts) == 1 and rel.name.startswith("meters-") and target.suffix == ".json") or \
                            (rel.parts[0] == "clips" and len(rel.parts) == 2 and target.suffix in (".wav", ".json"))
                        if not ok_file:
                            break
                    data = target.read_bytes()
                    ctype = CONTENT_TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    if target.suffix == ".wav":
                        ctype = "audio/wav"
                    if target.suffix in (".md", ".jsonl", ".txt"):
                        ctype = "text/plain"
                    if target.suffix == ".html":
                        meta = f'<meta name="aise-token" content="{app.token}">'
                        data = data.replace(b"</head>", meta.encode() + b"</head>", 1)
                    if ctype.startswith(("text/", "application/json", "application/javascript")):
                        ctype += "; charset=utf-8"
                    return self._send(200, data, ctype)
            self._send(404, b"Tidak ditemukan", "text/plain; charset=utf-8")

        def _reject(self, code: int, msg: str) -> None:
            """Tolak permintaan POST setelah membuang isinya; menjawab sebelum isi dibaca bisa memutus koneksi (terutama di Windows)."""
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 10_000_000)
                while n > 0:
                    chunk = self.rfile.read(min(n, 65536))
                    if not chunk:
                        break
                    n -= len(chunk)
            except (OSError, ValueError):
                pass
            self._json(code, {"error": msg})

        def _body(self, limit: int = MAX_BODY) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("Permintaan terlalu besar.")
            raw = self.rfile.read(n) if n else b"{}"
            obj = json.loads(raw or b"{}")
            if not isinstance(obj, dict):
                raise ValueError("Format permintaan salah.")
            return obj

        # ---------- GET ----------
        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "Host tidak diizinkan."})
            path = urlparse(self.path).path
            if path == "/login":
                return self._redirect("/viewer/home.html") if self._authed() else self._login_page()
            if not self._authed():
                if path.startswith("/api/"):
                    return self._json(401, {"error": "Perlu masuk dengan PIN."})
                return self._redirect("/login")
            if path in ("/", "/viewer", "/viewer/"):
                self.send_response(302)
                self.send_header("Location", "/viewer/home.html")
                self.end_headers()
                return
            if path.startswith("/api/"):
                if not self._token_ok():
                    return self._json(403, {"error": "Token tidak valid. Buka lewat AI-Sound-Engineer.bat."})
                if path == "/api/status":
                    local = self._is_local()
                    return self._json(200, {"ip": load_ip(app.data / "mixer-ip.txt"), "default_port": DEFAULT_PORT,
                                            "port": app.load_port(), "job": app.job, "latest": app.latest(),
                                            "live": bool(app.live and app.live.running), "weak_alerts": app.prefs().get("weak_alerts", False), "stream_bus": app.stream_bus(),
                                            "is_local": local, "lan": app.lan, "version": app.version, "frozen": app.frozen,
                                            "needs_restart": app.disk_version().split(" ")[0] != app.version.split(" ")[0],
                                            "lan_url": app.lan_url if local else None,
                                            "pin": app.pin if (local and app.lan) else None})
                if path == "/api/live":
                    if not app.live:
                        return self._json(200, {"running": False, "frames": 0, "alerts": []})
                    q = parse_qs(urlparse(self.path).query)
                    want = q.get("alerts", ["1"])[0] == "1"
                    try:
                        since = int(q["since"][0]) if "since" in q else None
                    except ValueError:
                        since = None
                    return self._json(200, {**app.live.snapshot(include_alerts=want), "settings_at": app.live_settings_at,
                                            **app.params_since(since), "findings": app.live_findings() if want else None,
                                            "stream": app.stream.brief() if want else None})
                if path == "/api/control":
                    return self._json(200, app.control_status())
                if path == "/api/siaran":
                    return self._json(200, app.siaran_view())
                if path == "/api/stream":
                    return self._json(200, app.stream_view())
                if path == "/api/lr-eq":
                    return self._json(200, app.lr_eq())
                if path == "/api/memori":
                    return self._json(200, {"profil": app.memori.profil(), "catatan": app.memori.semua(n=60)[::-1]})
                if path == "/api/live/eq-problems":
                    return self._json(200, {"findings": app.eq_problems()})
                if path == "/api/job":
                    return self._json(200, app.job)
                if path == "/api/channels":
                    return self._json(200, app.channels_view())
                if path == "/api/ai":
                    return self._json(200, {**app.ai_view(), "is_local": self._is_local()})
                if path == "/api/clips":
                    return self._json(200, {"clips": app.clips.list()})
                if path == "/api/recs":
                    return self._json(200, app.recs_view())
                if path == "/api/dsp":
                    return self._json(200, app.dsp_view())
                if path == "/api/dsp/preset":
                    pid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    try:
                        return self._json(200, dspmod.PresetStore(app.data).get(pid)[0])
                    except dspmod.DSPError as exc:
                        return self._json(400, {"error": str(exc)})
                return self._json(404, {"error": "Tidak dikenal."})
            return self._static(path)

        # ---------- POST ----------
        def do_POST(self):
            if not self._host_ok():
                return self._reject(403, "Host tidak diizinkan.")
            path = urlparse(self.path).path
            if path == "/login":
                return self._do_login()
            if not self._authed():
                return self._reject(401, "Perlu masuk dengan PIN.")
            if not self._token_ok():
                return self._reject(403, "Token tidak valid. Buka lewat AI-Sound-Engineer.bat.")
            if path in LOCAL_ONLY and not self._is_local():
                return self._reject(403, "Tombol ini hanya bisa dipakai dari laptop yang menjalankan aplikasi.")
            try:
                body = self._body(BIG_BODY.get(path, MAX_BODY))
                if path == "/api/ip":
                    ip, _ = _parse_target(body)
                    save_ip(ip, app.data / "mixer-ip.txt")
                    return self._json(200, {"ok": True})
                if path == "/api/probe":
                    ip, port = _parse_target(body)
                    _remember(app, ip, port)
                    return self._json(200, app.probe(ip, port))
                if path == "/api/audit":
                    if body.get("no_service") is not True or body.get("scene_saved") is not True:
                        raise ValueError("Centang dulu: tidak sedang ibadah, dan scene sudah disimpan.")
                    ip, port = _parse_target(body)
                    _remember(app, ip, port)
                    app.audit(ip, port)
                    return self._json(202, {"ok": True})
                if path == "/api/meters":
                    ip, port = _parse_target(body)
                    mpath = str(body.get("path", "/meters/1"))
                    check("/meters", (mpath,))
                    seconds = body.get("seconds", 30)
                    if not isinstance(seconds, (int, float)) or not 1 <= seconds <= 120:
                        raise ValueError("Durasi harus 1–120 detik.")
                    app.record_meters(ip, port, mpath, float(seconds))
                    return self._json(202, {"ok": True})
                if path == "/api/live/start":
                    ip, port = _parse_target(body)
                    mpath = str(body.get("path", "/meters/1"))
                    check("/meters", (mpath,))
                    _remember(app, ip, port)
                    app.start_live(ip, port, mpath)
                    return self._json(202, {"ok": True})
                if path == "/api/channels":
                    return self._json(200, app.save_channels(body.get("profile")))
                if path == "/api/ai/config":
                    return self._json(200, app.ai_config(body))
                if path == "/api/clips":
                    rec = app.clips.save(body.get("wav_b64", ""), body.get("meta"), body.get("analisis"))
                    return self._json(200, rec)
                if path == "/api/dsp":
                    clean = dspmod.save(app.dsp_path, body.get("data"))
                    return self._json(200, {"ok": True, "data": clean, "findings": dspmod.analyze(clean)})
                if path == "/api/dsp/preset":
                    return self._json(200, dspmod.PresetStore(app.data).save(body.get("b64", ""), body.get("nama", "")))
                if path == "/api/dsp/ai-read":
                    return self._json(200, app.dsp_ai_read(body))
                if path == "/api/clips/ai":
                    if body.get("izin") is not True:
                        raise ValueError("Centang izin dulu sebelum klip dikirim ke Google.")
                    return self._json(200, app.clip_ai(str(body.get("id", ""))))
                if path == "/api/ai/analyze":
                    situasi = str(body.get("situasi", "umum"))
                    if situasi not in ai.SITUASI:
                        raise ValueError("Situasi tidak dikenal.")
                    return self._json(200, app.ai_analyze(situasi, str(body.get("pertanyaan", "")), _clean_audio(body.get("audio"), body.get("izin"))))
                if path == "/api/stream/cek":
                    threading.Thread(target=app.stream.run_check, daemon=True).start()
                    return self._json(202, {"ok": True})
                if path == "/api/stream/gemini":
                    return self._json(200, app.stream_gemini())
                if path == "/api/siaran/routing":
                    return self._json(200, app.siaran_routing())
                if path == "/api/prefs":
                    return self._json(200, app.set_prefs(body))
                if path == "/api/ignore":
                    return self._json(200, app.ignore(body))
                if path == "/api/ignore/restore":
                    return self._json(200, {"dikembalikan": app.ignored.restore(str(body.get("key", "")))})
                if path == "/api/memori/nilai":
                    return self._json(200, app.memori_nilai(str(body.get("id", "")), str(body.get("nilai", ""))))
                if path == "/api/memori/ukur":
                    return self._json(200, app.memori.catat_ukur(body))
                if path == "/api/memori/hapus":
                    return self._json(200, {"dihapus": app.memori.hapus(body.get("jenis") or None)})
                if path == "/api/control/enable":
                    return self._json(200, app.control_enable(body))
                if path == "/api/control/disable":
                    app.control.disable("dimatikan operator")
                    return self._json(200, app.control_status())
                if path == "/api/control/arm":
                    return self._json(200, {"item": app.control_arm(str(body.get("alamat", "")), body.get("konfirmasi") is True, body.get("target"))})
                if path == "/api/control/set":
                    return self._json(200, {"item": app.control.set_fraction(str(body.get("alamat", "")), body.get("p"))})
                if path == "/api/control/release":
                    return self._json(200, {"item": app.control.release(str(body.get("alamat", "")), body.get("kembali") is True)})
                if path == "/api/control/restore-all":
                    return self._json(200, {"dipulihkan": app.control.restore_all()})
                if path == "/api/settings/refresh":
                    return self._json(200, app.refresh_settings())
                if path == "/api/live/stop":
                    app.stop_live()
                    return self._json(200, {"ok": True})
                if path in ("/api/update", "/api/tests") and app.frozen:
                    return self._json(200, {"ok": False, "output": "Ini versi .exe. Untuk update: unduh exe terbaru dari GitHub "
                                            "(tab Actions, build terakhir, bagian Artifacts), lalu ganti file exe saat tidak ibadah."})
                if path == "/api/update":
                    return self._json(200, app.run_cmd(["git", "pull"], 90))
                if path == "/api/tests":
                    return self._json(200, app.run_cmd([sys.executable, "-m", "unittest", "discover", "-s", "tests"], 180))
                if path == "/api/shutdown":
                    app.control.disable("aplikasi ditutup")
                    self._json(200, {"ok": True})
                    if app.shutdown_cb:
                        threading.Thread(target=app.shutdown_cb, daemon=True).start()
                    return
                return self._json(404, {"error": "Tidak dikenal."})
            except Busy as exc:
                return self._json(409, {"error": str(exc)})
            except ai.AIError as exc:
                return self._json(502, {"error": str(exc)})
            except (ValueError, ReadOnlyViolation, WriteRefused, channels.ProfileError, ClipError, dspmod.DSPError) as exc:
                return self._json(400, {"error": str(exc)})

    return Handler


def serve(app: App, http_port: int = DEFAULT_HTTP_PORT) -> ThreadingHTTPServer:
    bind = "0.0.0.0" if app.lan else "127.0.0.1"
    httpd = ThreadingHTTPServer((bind, http_port), make_handler(app, http_port))
    app.shutdown_cb = httpd.shutdown
    return httpd


def _in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Aplikasi web lokal AI Sound Engineer")
    p.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--lan", action="store_true", help="izinkan perangkat lain di jaringan gereja (dengan PIN)")
    args = p.parse_args(argv)
    url = f"http://localhost:{args.port}/viewer/home.html"
    if _in_use(args.port):
        if args.lan:
            print("=" * 56)
            print("  MODE JARINGAN BELUM AKTIF")
            print("  Aplikasi lama masih berjalan di jendela hitam lain.")
            print("  1. Tutup SEMUA jendela hitam AI Sound Engineer.")
            print("  2. Klik dua kali AI-Sound-Engineer-Jaringan.bat lagi.")
            print("=" * 56)
            return 1
        print("Aplikasi sudah berjalan. Membuka browser...")
        webbrowser.open(url)
        return 0
    app = App(lan=args.lan)
    if args.lan:
        app.pin = load_or_create_pin(app.data / "pin.txt")
        ip = lan_ip()
        app.lan_url = f"http://{ip}:{args.port}" if ip else None
    httpd = serve(app, args.port)
    app.stream.start()
    print("=" * 56)
    print("  AI SOUND ENGINEER berjalan  (hanya MEMBACA mixer)")
    print(f"  Buka di browser: {url}")
    if args.lan:
        print("  MODE JARINGAN AKTIF")
        print(f"  Dari HP/tablet/komputer lain: {app.lan_url or '(IP laptop tidak terdeteksi)'}")
        print(f"  PIN: {app.pin}")
        print("  Bila Windows bertanya soal firewall, izinkan untuk")
        print("  jaringan PRIVATE saja.")
    print("  JANGAN tutup jendela ini selama aplikasi dipakai.")
    print("  Untuk mematikan: tutup jendela ini, atau tombol")
    print("  'Matikan aplikasi' di halaman Beranda.")
    print("=" * 56)
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    print("Aplikasi dimatikan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
