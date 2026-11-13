"""Pilihan: aplikasi berjalan sendiri saat pengguna Windows masuk (setelah komputer dinyalakan).

Memakai kunci Run milik PENGGUNA (HKCU), jadi tidak perlu hak administrator dan hanya berlaku untuk akun Windows ini.
Yang ditulis hanyalah satu nilai bernama NAME berisi perintah menjalankan aplikasi; mematikan pilihan menghapus nilai itu.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "AI-Sound-Engineer"
WINDOWS = os.name == "nt"


def command(frozen: bool, app_dir: Path, lan: bool, port: int, default_port: int, browser: bool) -> str:
    """Perintah yang dijalankan Windows saat masuk: exe (atau .bat untuk versi kode sumber) dengan mode yang sama seperti sekarang."""
    if frozen:
        parts = [f'"{sys.executable}"'] + ([] if lan else ["--lokal"])
        if port != default_port:
            parts += ["--port", str(int(port))]
        if not browser:
            parts.append("--no-browser")
        return " ".join(parts)
    bat = Path(app_dir) / ("AI-Sound-Engineer-Jaringan.bat" if lan else "AI-Sound-Engineer.bat")
    return f'"{bat}"'


def read(name: str = NAME) -> str | None:
    if not WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as k:
            v, kind = winreg.QueryValueEx(k, name)
            return v if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(v, str) else None
    except OSError:
        return None


def write(cmd: str | None, name: str = NAME) -> None:
    """Isi (cmd) atau hapus (None) nilai mulai-otomatis."""
    if not WINDOWS:
        raise ValueError("Mulai otomatis hanya tersedia di Windows.")
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if cmd is None:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)


def view(expected: str) -> dict:
    """aktif: nilai ada; cocok: menunjuk ke program yang sedang berjalan ini (exe yang dipindah folder perlu dinyalakan ulang)."""
    cur = read()
    here = expected.split('"')[1].lower() if '"' in expected else ""
    return {"didukung": WINDOWS, "aktif": cur is not None, "cocok": bool(cur) and here != "" and here in cur.lower(),
            "browser": bool(cur) and "--no-browser" not in cur, "perintah": cur or ""}
