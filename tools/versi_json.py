"""Dipakai build: tulis versi.json di samping exe.

python tools/versi_json.py <exe> <file versi> <keluaran> [catatan]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise.pembaruan import make_meta

if __name__ == "__main__":
    exe, version_file, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    meta = make_meta(exe, version_file.read_text(encoding="utf-8"), sys.argv[4] if len(sys.argv) > 4 else "")
    out.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
