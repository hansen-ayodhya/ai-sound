"""Aplikasi web lokal: semua fungsi lewat browser, tanpa mengetik perintah.

    python -m aise.server            (dijalankan oleh AI-Sound-Engineer.bat)

Keamanan:
- Hanya mendengarkan di 127.0.0.1 (tidak bisa dibuka dari perangkat lain).
- Header Host harus localhost/127.0.0.1 (mencegah DNS rebinding).
- Setiap panggilan API wajib membawa token acak yang hanya tertanam di halaman
  yang disajikan server ini (website lain tidak bisa membacanya).
- Semua akses mixer tetap lewat klien READ-ONLY dan penjaganya.
- Satu pekerjaan mixer pada satu waktu.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import meters
from .audit import PROBE_ADDRESSES, MixerNotAnswering, run_audit
from .client import DEFAULT_PORT, ReadOnlyClient
from .dump import run_dump
from .guard import ReadOnlyViolation, check
from .live import LiveMonitor, labels_from_dump
from .menu import load_ip, save_ip, valid_ip

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HTTP_PORT = 8765
MAX_BODY = 10_000


class Busy(RuntimeError):
    pass


class App:
    def __init__(self, root: Path = ROOT, data: Path | None = None, addresses: list[str] | None = None):
        self.root = root
        self.data = data or root / "data"
        self.addresses = addresses
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self.job: dict = {"state": "idle"}
        self.shutdown_cb = None
        self.live: LiveMonitor | None = None

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

        self.start_job("audit", lambda: run_audit(ip, port, base=self.data,
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
        self.live = LiveMonitor(ip, port, path, labels_from_dump(dump), log, on_stop=on_stop)
        try:
            self.live.start()
        except Exception:
            on_stop()
            raise

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
            return self.headers.get("Host") in allowed_hosts

        def _token_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-AISE-Token", ""), app.token)

        def _static(self, path: str) -> None:
            for prefix, base in static_roots.items():
                if path.startswith(prefix):
                    target = (base / path[len(prefix):]).resolve()
                    if not target.is_relative_to(base.resolve()) or not target.is_file():
                        break
                    data = target.read_bytes()
                    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    if target.suffix in (".md", ".jsonl", ".txt"):
                        ctype = "text/plain"
                    if target.suffix == ".html":
                        meta = f'<meta name="aise-token" content="{app.token}">'
                        data = data.replace(b"</head>", meta.encode() + b"</head>", 1)
                    if ctype.startswith(("text/", "application/json", "application/javascript")):
                        ctype += "; charset=utf-8"
                    return self._send(200, data, ctype)
            self._send(404, b"Tidak ditemukan", "text/plain; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
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
            if path in ("/", "/viewer", "/viewer/"):
                self.send_response(302)
                self.send_header("Location", "/viewer/home.html")
                self.end_headers()
                return
            if path.startswith("/api/"):
                if not self._token_ok():
                    return self._json(403, {"error": "Token tidak valid. Buka lewat AI-Sound-Engineer.bat."})
                if path == "/api/status":
                    return self._json(200, {"ip": load_ip(app.data / "mixer-ip.txt"), "default_port": DEFAULT_PORT,
                                            "port": app.load_port(), "job": app.job, "latest": app.latest(),
                                            "live": bool(app.live and app.live.running)})
                if path == "/api/live":
                    if not app.live:
                        return self._json(200, {"running": False, "frames": 0, "alerts": []})
                    want = parse_qs(urlparse(self.path).query).get("alerts", ["1"])[0] == "1"
                    return self._json(200, app.live.snapshot(include_alerts=want))
                if path == "/api/job":
                    return self._json(200, app.job)
                return self._json(404, {"error": "Tidak dikenal."})
            return self._static(path)

        # ---------- POST ----------
        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "Host tidak diizinkan."})
            if not self._token_ok():
                return self._json(403, {"error": "Token tidak valid. Buka lewat AI-Sound-Engineer.bat."})
            path = urlparse(self.path).path
            try:
                body = self._body()
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
                if path == "/api/live/stop":
                    app.stop_live()
                    return self._json(200, {"ok": True})
                if path == "/api/update":
                    return self._json(200, app.run_cmd(["git", "pull"], 90))
                if path == "/api/tests":
                    return self._json(200, app.run_cmd([sys.executable, "-m", "unittest", "discover", "-s", "tests"], 180))
                if path == "/api/shutdown":
                    self._json(200, {"ok": True})
                    if app.shutdown_cb:
                        threading.Thread(target=app.shutdown_cb, daemon=True).start()
                    return
                return self._json(404, {"error": "Tidak dikenal."})
            except Busy as exc:
                return self._json(409, {"error": str(exc)})
            except (ValueError, ReadOnlyViolation) as exc:
                return self._json(400, {"error": str(exc)})

    return Handler


def serve(app: App, http_port: int = DEFAULT_HTTP_PORT) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", http_port), make_handler(app, http_port))
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
    args = p.parse_args(argv)
    url = f"http://localhost:{args.port}/viewer/home.html"
    if _in_use(args.port):
        print("Aplikasi sudah berjalan. Membuka browser...")
        webbrowser.open(url)
        return 0
    httpd = serve(App(), args.port)
    print("=" * 56)
    print("  AI SOUND ENGINEER berjalan  (hanya MEMBACA mixer)")
    print(f"  Buka di browser: {url}")
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
