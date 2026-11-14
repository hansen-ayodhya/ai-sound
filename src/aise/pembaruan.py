"""Pembaruan aplikasi dari dalam aplikasi (semi-otomatis: operator menekan satu tombol).

Sumber: rilis GitHub bertanda `terbaru` di repo proyek, yang diisi otomatis oleh build (lihat
.github/workflows/build-exe.yml): `AI-Sound-Engineer.exe` dan `versi.json` (versi, sha256, ukuran).

Urutan memasang (hanya versi .exe di Windows):
  1. unduh exe baru ke samping exe lama (nama sementara), 2. cocokkan ukuran dan sha256 dengan versi.json,
  3. tulis skrip kecil yang menunggu aplikasi tertutup, menukar file, lalu membuka aplikasi lagi,
  4. aplikasi menutup diri. Exe lama disimpan sebagai `AI-Sound-Engineer.lama.exe`; folder `data` tidak disentuh.

Repo private: GitHub menolak unduhan tanpa token. Operator bisa mengisi token baca-saja (disimpan di folder
data komputer ini, tidak pernah ditampilkan lagi, hanya dikirim ke GitHub), atau menjadikan repo public.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from pathlib import Path

REPO = "hansen-ayodhya/ai-sound"
API_BASE = "https://api.github.com"
WEB_BASE = "https://github.com"           # unduhan langsung rilis public: tanpa api.github.com, tanpa batas permintaan API
TAG = "terbaru"
EXE_NAME = "AI-Sound-Engineer.exe"
NEW_NAME = "AI-Sound-Engineer.baru"
OLD_NAME = "AI-Sound-Engineer.lama.exe"
BAT_NAME = "pasang-pembaruan.bat"
RESULT_NAME = "pembaruan-hasil.txt"          # ditulis skrip ke folder data: ok | gagal
META_NAME = "versi.json"
MAX_EXE = 300_000_000
MAX_META = 20_000
CHECK_EVERY_S = 6 * 3600
TIMEOUT_S = 30
TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{20,255}$")
ARG_RE = re.compile(r"^[A-Za-z0-9_\-=.:]{1,40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class UpdateError(ValueError):
    """Pembaruan tidak bisa dilanjutkan (pesan untuk operator)."""


def why(exc: BaseException) -> str:
    """Penyebab gangguan koneksi dalam bahasa operator, plus teks teknisnya (supaya bisa dilaporkan)."""
    r = getattr(exc, "reason", exc)
    tech = f"{type(r).__name__}: {r}"[:160]
    if isinstance(r, ssl.SSLCertVerificationError):
        return f"sertifikat HTTPS GitHub ditolak (antivirus atau proxy yang memeriksa HTTPS? tanggal/jam komputer salah?) [{tech}]"
    if isinstance(r, ssl.SSLError):
        return f"sambungan HTTPS gagal dinegosiasikan [{tech}]"
    if isinstance(r, socket.gaierror):
        return f"nama github.com tidak bisa dicari (DNS/internet mati?) [{tech}]"
    if isinstance(r, (socket.timeout, TimeoutError)):
        return f"GitHub tidak menjawab dalam {TIMEOUT_S} detik (internet lambat atau diblokir firewall) [{tech}]"
    if isinstance(r, ConnectionError):
        return f"sambungan ditolak/terputus (firewall, antivirus, atau proxy?) [{tech}]"
    return f"[{tech}]"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def parse_version(text: str) -> tuple[str, datetime | None]:
    """'7f608d1 13-11-2026 13:40' -> (sha pendek, waktu build)."""
    parts = str(text or "").split()
    when = None
    if len(parts) >= 3:
        try:
            when = datetime.strptime(parts[1] + " " + parts[2], "%d-%m-%Y %H:%M")
        except ValueError:
            when = None
    return (parts[0] if parts else ""), when


def is_newer(local: str, remote: str) -> bool:
    """Rilis dianggap lebih baru bila commit-nya berbeda dan waktu build-nya tidak lebih tua dari yang terpasang."""
    (ls, lt), (rs, rt) = parse_version(local), parse_version(remote)
    if not rs or rs == ls:
        return False
    return rt >= lt if lt and rt else True


def make_meta(exe: Path, versi: str, catatan: str = "") -> dict:
    """Isi versi.json untuk sebuah exe (dipakai build lewat tools/versi_json.py)."""
    data = Path(exe).read_bytes()
    return {"versi": versi.strip(), "sha256": hashlib.sha256(data).hexdigest(), "ukuran": len(data), "catatan": catatan.strip()[:200]}


def make_bat(pid: int, args: list[str]) -> str:
    """Skrip penukar. Semua nama file tetap dan relatif terhadap folder skrip; argumen sudah disaring."""
    a = " ".join(x for x in args if ARG_RE.match(x))
    lines = [
        "@echo off",
        "rem Dibuat oleh AI Sound Engineer: memasang versi baru setelah aplikasi tertutup.",
        "setlocal",
        'cd /d "%~dp0"',
        "set N=0",
        ":tunggu_tutup",
        f'tasklist /fi "PID eq {int(pid)}" 2>nul | find " {int(pid)} " >nul',
        "if errorlevel 1 goto tukar",
        "set /a N+=1",
        "if %N% geq 60 goto gagal_diam",
        "ping -n 2 127.0.0.1 >nul",
        "goto tunggu_tutup",
        ":tukar",
        "set N=0",
        ":coba",
        f'if exist "{OLD_NAME}" del /f /q "{OLD_NAME}" >nul 2>&1',
        f'move /y "{EXE_NAME}" "{OLD_NAME}" >nul 2>&1',
        "if not errorlevel 1 goto pasang",
        "set /a N+=1",
        "if %N% geq 30 goto gagal",
        "ping -n 2 127.0.0.1 >nul",
        "goto coba",
        ":pasang",
        f'move /y "{NEW_NAME}" "{EXE_NAME}" >nul 2>&1',
        "if not errorlevel 1 goto berhasil",
        f'move /y "{OLD_NAME}" "{EXE_NAME}" >nul 2>&1',
        "goto gagal",
        ":berhasil",
        f'echo ok> "data\\{RESULT_NAME}"',
        f'start "AI Sound Engineer" "{EXE_NAME}" {a}'.rstrip(),
        "goto akhir",
        ":gagal",
        f'echo gagal> "data\\{RESULT_NAME}"',
        f'if exist "{EXE_NAME}" start "AI Sound Engineer" "{EXE_NAME}" {a}'.rstrip(),
        "goto akhir",
        ":gagal_diam",
        f'echo gagal> "data\\{RESULT_NAME}"',
        ":akhir",
        '(goto) 2>nul & del "%~f0"',
    ]
    return "\r\n".join(lines) + "\r\n"


def launch_bat(bat: Path) -> None:
    """Jalankan skrip terlepas dari aplikasi.

    Variabel lingkungan milik PyInstaller (_PYI_*, _MEIPASS*) tidak diwariskan: kalau ikut terbawa, exe baru mengira
    dirinya anak dari exe lama dan mencari folder sementara yang sudah dihapus."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_PYI_", "_MEIPASS"))}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(bat.parent), env=env, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Updater:
    def __init__(self, data: Path, app_dir: Path, version: str, frozen: bool, base: str = API_BASE, repo: str = REPO,
                 windows: bool = os.name == "nt", launch=launch_bat, clock=time.monotonic, web_base: str | None = None):
        self.web = (web_base or (WEB_BASE if base == API_BASE else base)).rstrip("/")
        self.data, self.app_dir, self.version, self.frozen = Path(data), Path(app_dir), version, frozen
        self.base, self.repo, self.windows, self._launch, self._clock = base.rstrip("/"), repo, windows, launch, clock
        self.token_path = self.data / "github-token.txt"
        self._lock = threading.Lock()
        self._info: dict | None = None
        self._error = ""
        self._checked: float | None = None
        self._checked_clock = ""
        self.state = {"tahap": "diam", "persen": None, "pesan": ""}
        self.last_result = self._take_result()

    # ---------- token (hanya untuk repo private) ----------
    def token(self) -> str | None:
        try:
            t = self.token_path.read_text(encoding="utf-8").strip()
            return t if TOKEN_RE.match(t) else None
        except OSError:
            return None

    def set_token(self, token: str | None) -> None:
        if not token:
            self.token_path.unlink(missing_ok=True)
        else:
            token = str(token).strip()
            if not TOKEN_RE.match(token):
                raise UpdateError("Token tidak dikenali. Tempel token GitHub apa adanya (biasanya diawali github_pat_ atau ghp_).")
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(token, encoding="utf-8")
        self._checked = None

    # ---------- jaringan ----------
    def _open(self, url: str, accept: str, auth: bool = True):
        """Buka URL. Token (bila ada dan auth=True) hanya dikirim ke alamat yang diminta; pengalihan diikuti TANPA token."""
        headers = {"Accept": accept, "User-Agent": "AI-Sound-Engineer", "X-GitHub-Api-Version": "2022-11-28"}
        tok = self.token() if auth else None
        if tok:
            headers["Authorization"] = "Bearer " + tok
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            return opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            loc = exc.headers.get("Location") if exc.code in (301, 302, 303, 307, 308) else None
            if loc:
                if not loc.startswith("https://") and self.base.startswith("https://"):
                    raise UpdateError("GitHub mengalihkan ke alamat yang tidak aman; unduhan dibatalkan.") from exc
                try:
                    return opener.open(urllib.request.Request(loc, headers={"User-Agent": "AI-Sound-Engineer"}), timeout=TIMEOUT_S)
                except urllib.error.HTTPError as exc2:
                    raise UpdateError(f"Gagal mengunduh file dari GitHub (kesalahan {exc2.code}).") from exc2
                except (urllib.error.URLError, OSError) as exc2:
                    raise UpdateError(f"Gagal mengunduh file dari GitHub: {why(exc2)}") from exc2
            if exc.code == 401:
                raise UpdateError("Token ditolak GitHub (salah ketik atau sudah kedaluwarsa). Buat token baru lalu isi ulang.") from exc
            if exc.code == 404:
                raise UpdateError("Rilis 'terbaru' tidak ditemukan: build terakhir belum selesai, atau repo ini private (isi token baca-saja, atau jadikan repo public)." if not tok else
                                  "Rilis 'terbaru' tidak ditemukan: build terakhir mungkin belum selesai, atau token tidak diberi akses ke repo ini.") from exc
            if exc.code in (403, 429):
                raise UpdateError("GitHub menolak sementara (batas permintaan atau izin token). Coba lagi beberapa menit lagi.") from exc
            raise UpdateError(f"GitHub menjawab kesalahan {exc.code}.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise UpdateError(f"Tidak bisa menghubungi GitHub: {why(exc)}") from exc

    def _read(self, url: str, limit: int, auth: bool = True) -> bytes:
        try:
            with self._open(url, "application/octet-stream", auth) as r:
                return r.read(limit)
        except (OSError, ValueError) as exc:               # terputus saat membaca isi
            raise UpdateError(f"Unduhan terputus: {why(exc)}") from exc

    def _release(self) -> dict:
        tok = self.token()
        if tok:                                            # repo private: lewat API dengan token
            try:
                with self._open(f"{self.base}/repos/{self.repo}/releases/tags/{TAG}", "application/vnd.github+json") as r:
                    rel = json.loads(r.read(2_000_000))
            except (OSError, ValueError) as exc:
                raise UpdateError(f"Daftar rilis tidak terbaca: {why(exc)}") from exc
            assets = {a.get("name"): a for a in rel.get("assets", []) if isinstance(a, dict)}
            if EXE_NAME not in assets or META_NAME not in assets:
                raise UpdateError("Rilis belum lengkap (build sedang berjalan). Coba lagi beberapa menit lagi.")
            raw, exe_url = self._read(assets[META_NAME]["url"], MAX_META + 1), assets[EXE_NAME]["url"]
        else:                                              # repo public: alamat unduhan langsung, tidak lewat api.github.com
            d = f"{self.web}/{self.repo}/releases/download/{TAG}"
            raw, exe_url = self._read(f"{d}/{META_NAME}", MAX_META + 1, auth=False), f"{d}/{EXE_NAME}"
        try:
            meta = json.loads(raw.decode("utf-8-sig"))
            versi, sha, size = str(meta["versi"]), str(meta["sha256"]).lower(), int(meta["ukuran"])
        except (ValueError, KeyError, TypeError) as exc:
            raise UpdateError("Keterangan versi di rilis tidak terbaca.") from exc
        if not SHA_RE.match(sha) or not 0 < size <= MAX_EXE:
            raise UpdateError("Keterangan versi di rilis tidak wajar; pembaruan dibatalkan.")
        return {"versi": versi, "sha256": sha, "ukuran": size, "catatan": str(meta.get("catatan", ""))[:200], "url": exe_url, "auth": bool(tok)}

    # ---------- cek ----------
    def check(self, force: bool = False) -> dict:
        with self._lock:
            now = self._clock()
            if force or self._checked is None or now - self._checked > CHECK_EVERY_S:
                self._checked, self._checked_clock = now, time.strftime("%H:%M")
                try:
                    self._info, self._error = self._release(), ""
                except UpdateError as exc:
                    self._info, self._error = None, str(exc)
        return self.view()

    def available(self) -> bool:
        return bool(self._info and is_newer(self.version, self._info["versi"]))

    def view(self) -> dict:
        i = self._info
        return {"sekarang": self.version, "frozen": self.frozen, "bisa_pasang": self.frozen and self.windows, "token_ada": self.token() is not None,
                "tersedia": self.available(), "rilis": None if not i else {"versi": i["versi"], "ukuran": i["ukuran"], "catatan": i["catatan"]},
                "galat": self._error, "diperiksa": self._checked_clock, "hasil_terakhir": self.last_result, **self.state}

    def _take_result(self) -> str:
        """Hasil pemasangan sebelumnya (ditulis skrip penukar); dibaca sekali lalu dihapus."""
        p = self.data / RESULT_NAME
        try:
            res = p.read_text(encoding="utf-8", errors="replace").strip().lower()
            p.unlink(missing_ok=True)
            return res if res in ("ok", "gagal") else ""
        except OSError:
            return ""

    # ---------- uji koneksi bertahap (untuk mencari tahu MENGAPA tidak bisa menghubungi GitHub) ----------
    def diagnose(self) -> dict:
        """DNS -> sambungan TCP -> sertifikat HTTPS -> unduh versi.json, masing-masing dengan hasil dan waktu. Hanya membaca."""
        steps: list[dict] = []

        def add(nama: str, ok: bool, detail: str) -> bool:
            steps.append({"langkah": nama, "ok": ok, "detail": detail})
            return ok

        u = urlparse(self.web)
        host, port = u.hostname or "github.com", u.port or (443 if u.scheme == "https" else 80)
        px = {k: urlparse(v).hostname or "?" for k, v in urllib.request.getproxies().items() if k in ("http", "https")}
        add("Proxy sistem", True, ("Dipakai: " + ", ".join(f"{k} → {v}" for k, v in px.items()) + ". Langkah 3-4 menyambung langsung dan mengabaikan proxy.") if px else "Tidak ada proxy.")
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            add(f"Mencari alamat {host} (DNS)", False, why(exc))
            return {"langkah": steps, "kesimpulan": "Komputer ini tidak bisa menemukan alamat GitHub: periksa sambungan internet atau pengaturan DNS."}
        by_fam: dict[str, tuple] = {}
        for fam, _, _, _, addr in infos:
            by_fam.setdefault("IPv6" if fam == socket.AF_INET6 else "IPv4", addr)
        add(f"Mencari alamat {host} (DNS)", True, ", ".join(f"{k} {v[0]}" for k, v in by_fam.items()))
        first_ok = None
        for fam, addr in by_fam.items():
            t0 = time.monotonic()
            try:
                sk = socket.create_connection(addr[:2], timeout=8)
                sk.close()
                add(f"Menyambung ke GitHub lewat {fam}", True, f"{int((time.monotonic() - t0) * 1000)} md")
                first_ok = first_ok or addr
            except OSError as exc:
                add(f"Menyambung ke GitHub lewat {fam}", False, why(exc))
        if not first_ok:
            return {"langkah": steps, "kesimpulan": "Alamat GitHub ketemu, tetapi sambungan ditolak atau tidak dijawab: kemungkinan firewall/antivirus memblokir program ini, atau internet terputus."}
        if u.scheme == "https":
            try:
                with socket.create_connection(first_ok[:2], timeout=8) as sk, ssl.create_default_context().wrap_socket(sk, server_hostname=host) as ss:
                    add("Sertifikat HTTPS", True, f"sah ({ss.version()})")
            except (OSError, ssl.SSLError) as exc:
                add("Sertifikat HTTPS", False, why(exc))
                return {"langkah": steps, "kesimpulan": "Sambungan ke GitHub ada, tetapi sertifikat HTTPS-nya ditolak: biasanya antivirus/proxy yang memeriksa HTTPS, atau jam dan tanggal komputer salah."}
        t0 = time.monotonic()
        try:
            with self._open(f"{self.web}/{self.repo}/releases/download/{TAG}/{META_NAME}", "application/octet-stream", auth=False) as r:
                body, date = r.read(MAX_META + 1), r.headers.get("Date")
            add("Mengunduh keterangan versi", True, f"{len(body)} byte, {int((time.monotonic() - t0) * 1000)} md")
            if date:
                skew = abs((parsedate_to_datetime(date) - datetime.now(timezone.utc)).total_seconds())
                add("Jam komputer", skew < 300, "cocok dengan GitHub" if skew < 300 else f"selisih {int(skew // 60)} menit dari GitHub: perbaiki tanggal/jam Windows")
        except (UpdateError, ValueError) as exc:
            add("Mengunduh keterangan versi", False, str(exc))
            return {"langkah": steps, "kesimpulan": "Sambungan dasar berhasil tetapi unduhan gagal: lihat pesan di langkah yang merah."}
        return {"langkah": steps, "kesimpulan": "Semua langkah berhasil: komputer ini bisa menjangkau GitHub." if all(x["ok"] for x in steps) else "Terhubung, tetapi ada catatan di langkah yang merah."}

    # ---------- unduh dan pasang ----------
    def download(self, info: dict) -> Path:
        """Unduh exe ke samping exe lama dan cocokkan dengan versi.json. File yang tidak cocok dihapus."""
        dest = self.app_dir / NEW_NAME
        h, got = hashlib.sha256(), 0
        try:
            with self._open(info["url"], "application/octet-stream", info.get("auth", True)) as r, dest.open("wb") as fh:
                while True:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > info["ukuran"]:
                        raise UpdateError("File yang diunduh lebih besar dari keterangan rilis; dibatalkan.")
                    h.update(chunk)
                    fh.write(chunk)
                    self.state.update(persen=int(100 * got / info["ukuran"]))
            with dest.open("rb") as fh:
                head = fh.read(2)
            if got != info["ukuran"] or h.hexdigest() != info["sha256"]:
                raise UpdateError("File yang diunduh tidak cocok dengan keterangan rilis (unduhan terputus, atau build baru sedang diunggah). Coba lagi.")
            if head != b"MZ":
                raise UpdateError("File yang diunduh bukan program Windows; dibatalkan.")
        except UpdateError:
            dest.unlink(missing_ok=True)
            raise
        except OSError as exc:
            dest.unlink(missing_ok=True)
            raise UpdateError(f"Unduhan gagal atau tidak bisa disimpan: {why(exc)}") from exc
        return dest

    def install(self, args: list[str], pid: int, on_ready) -> dict:
        """Mulai mengunduh lalu memasang di latar belakang. `on_ready()` dipanggil saat aplikasi harus menutup diri."""
        if not (self.frozen and self.windows):
            raise UpdateError("Pasang otomatis hanya untuk versi .exe di Windows. Untuk versi kode sumber pakai tombol Update program.")
        with self._lock:
            if self.state["tahap"] in ("unduh", "mulai_ulang"):
                raise UpdateError("Pembaruan sedang berjalan.")
            self.state = {"tahap": "unduh", "persen": 0, "pesan": ""}

        def run():
            try:
                self.check(force=True)
                if self._error:
                    raise UpdateError(self._error)
                if not self.available():
                    raise UpdateError("Tidak ada versi yang lebih baru.")
                self.download(self._info)
                (self.data).mkdir(parents=True, exist_ok=True)
                bat = self.app_dir / BAT_NAME
                bat.write_bytes(make_bat(pid, args).encode("ascii"))
                self._launch(bat)
                self.state = {"tahap": "mulai_ulang", "persen": 100, "pesan": "Versi baru terunduh. Aplikasi menutup lalu membuka lagi sendiri."}
                on_ready()
            except Exception as exc:
                self.state = {"tahap": "gagal", "persen": None, "pesan": str(exc) if isinstance(exc, UpdateError) else f"{type(exc).__name__}: {exc}"}

        threading.Thread(target=run, daemon=True).start()
        return self.view()
