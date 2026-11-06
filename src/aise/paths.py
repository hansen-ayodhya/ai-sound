"""Lokasi file, baik saat dijalankan dari kode sumber maupun dari file .exe.

- Kode sumber: semuanya di folder proyek (root repo ai-sound).
- .exe (PyInstaller): halaman viewer ikut terbungkus di dalam exe, sedangkan
  folder `data` (hasil audit, IP, PIN) disimpan di samping file exe supaya
  tidak hilang saat exe ditutup atau diganti versi baru.
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else BUNDLE
DATA = APP_DIR / "data"
BUILD_VERSION_FILE = (BUNDLE / "aise" if FROZEN else Path(__file__).resolve().parent) / "_build_version.txt"
