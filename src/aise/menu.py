"""Menu tanpa mengetik perintah. Dijalankan dengan klik dua kali AI-Sound-Engineer.bat.

Semua pilihan hanya MEMBACA mixer. Tidak ada pilihan yang mengubah setting.
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from . import dump, monitor
from .audit import MixerNotAnswering, run_audit

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
IP_FILE = DATA / "mixer-ip.txt"
VIEW_PORT = 8765
_server: subprocess.Popen | None = None

MENU = """
==============================================
  AI SOUND ENGINEER  -  hanya MEMBACA mixer
==============================================
  1. Buka penampil (browser)
  2. Uji kecil ke mixer (2 pertanyaan saja)
  3. Audit lengkap (baca semua setting 2x + laporan)
  4. Rekam meter 30 detik
  5. Buka laporan audit terakhir
  6. Update program
  7. Cek program (jalankan tes)
  0. Keluar
"""


def load_ip(path: Path = IP_FILE) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_ip(ip: str, path: Path = IP_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ip, encoding="utf-8")


def valid_ip(text: str) -> bool:
    try:
        ipaddress.IPv4Address(text)
        return True
    except ValueError:
        return False


def ask_ip() -> str | None:
    saved = load_ip()
    prompt = (f"IP MR18 (Enter = {saved}): " if saved
              else "IP MR18 (lihat di halaman router, contoh 192.168.1.50): ")
    text = input(prompt).strip() or saved
    if not valid_ip(text):
        print("IP tidak valid. Contoh yang benar: 192.168.1.50")
        return None
    save_ip(text)
    return text


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_viewer() -> None:
    global _server
    if not _port_in_use(VIEW_PORT):
        _server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(VIEW_PORT),
             "--bind", "127.0.0.1", "--directory", str(ROOT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(_server.terminate)
        time.sleep(1.0)
    url = f"http://localhost:{VIEW_PORT}/viewer/"
    webbrowser.open(url)
    print(f"Penampil dibuka di browser: {url}")
    print("Biarkan jendela menu ini terbuka selama memakai penampil.")


def open_file(path: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(path)  # type: ignore[attr-defined]  # Windows
    else:
        print(f"Buka file ini: {path}")


def _progress(i: int, n: int) -> None:
    print(f"\r  membaca {i}/{n} ...", end="", flush=True)
    if i == n:
        print()


def do_probe() -> None:
    ip = ask_ip()
    if ip:
        dump.main(["--host", ip, "--only", "/info", "/ch/01/config/name"])


def do_audit() -> None:
    print("\nSebelum mulai, pastikan:")
    print("  - TIDAK sedang ibadah")
    print("  - scene sudah disimpan lewat aplikasi mixer (hanya SAVE)")
    if input("Sudah semua? (y/n): ").strip().lower() != "y":
        print("Dibatalkan. Tidak ada yang dikirim ke mixer.")
        return
    ip = ask_ip()
    if not ip:
        return
    print("Membaca setting mixer dua kali. Bisa memakan beberapa menit...")
    try:
        res = run_audit(ip, base=DATA, progress=_progress)
    except MixerNotAnswering as exc:
        print(f"\n{exc}")
        return
    print(f"\nSelesai. {res['answered']}/{res['queried']} alamat dijawab.")
    print("Dua kali baca IDENTIK (program tidak mengubah apa pun)." if res["identical"]
          else f"PERHATIAN: {len(res['changes'])} perbedaan antar pembacaan. Kirim ke Claude sebelum lanjut.")
    print(f"Temuan awal di laporan: {res['findings']}")
    print(f"Disimpan di: {res['folder']}")
    if input("Buka penampil sekarang? (y/n): ").strip().lower() == "y":
        open_viewer()


def do_meters() -> None:
    ip = ask_ip()
    if ip:
        print("Merekam meter 30 detik...")
        monitor.main(["--host", ip, "--seconds", "30", "--log", str(DATA / "osc_log.jsonl")])


def do_open_report() -> None:
    try:
        latest = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Belum ada audit. Jalankan pilihan 3 dulu.")
        return
    open_file(ROOT / latest["report"])


def do_update() -> None:
    try:
        subprocess.run(["git", "pull"], cwd=ROOT, check=False)
        print("Selesai. Tutup lalu buka lagi menu ini supaya update terpakai.")
    except FileNotFoundError:
        print("Git tidak ditemukan di laptop ini.")


def do_tests() -> None:
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT)
    print("PROGRAM OK" if r.returncode == 0 else "ADA TES YANG GAGAL - kirim teksnya ke Claude.")


ACTIONS = {"1": open_viewer, "2": do_probe, "3": do_audit, "4": do_meters,
           "5": do_open_report, "6": do_update, "7": do_tests}


def main() -> int:
    os.chdir(ROOT)
    while True:
        print(MENU)
        choice = input("Pilih nomor lalu Enter: ").strip()
        if choice == "0":
            return 0
        action = ACTIONS.get(choice)
        if not action:
            print("Pilihan tidak dikenal.")
            continue
        try:
            action()
        except KeyboardInterrupt:
            print("\nDibatalkan.")
        except SystemExit:
            pass  # argparse/CLI selesai; tetap di menu
        except Exception as exc:  # jangan sampai menu tertutup tiba-tiba
            print(f"Terjadi kesalahan: {exc}")


if __name__ == "__main__":
    sys.exit(main())
