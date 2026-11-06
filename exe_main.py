"""Titik masuk untuk file .exe (PyInstaller).

Bawaan exe: MODE JARINGAN (HP/tablet/komputer lain bisa masuk dengan PIN), karena exe
ditujukan untuk berjalan di komputer EasyWorship. Tambahkan `--lokal` di shortcut untuk
membatasi hanya ke komputer itu sendiri.
"""

import sys
import traceback

from aise.server import main

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(line_buffering=True)  # pesan langsung tampil di jendela
    except (AttributeError, ValueError):
        pass
    args = sys.argv[1:]
    if "--lokal" in args:
        args.remove("--lokal")
    elif "--lan" not in args:
        args.append("--lan")
    try:
        rc = main(args)
    except Exception:
        traceback.print_exc()
        rc = 1
    if rc:
        input("\nTekan Enter untuk menutup jendela ini...")
    sys.exit(rc)
