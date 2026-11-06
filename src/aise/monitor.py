"""Rekam meter mixer (READ-ONLY) dan tampilkan ringkasan puncak/clipping.

    python -m aise.monitor --host 192.168.x.x --path /meters/1 --seconds 30 --out data/meter1.json

Belum diverifikasi pada MR18: path meter, format blob, pemetaan indeks ke channel.
"""

from __future__ import annotations

import argparse
import sys

from .client import DEFAULT_PORT, ReadOnlyClient
from .guard import ReadOnlyViolation, check
from .meters import CLIP_DB_DEFAULT, format_table, record, save


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rekam meter mixer (READ-ONLY)")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--path", default="/meters/1")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--clip-db", type=float, default=CLIP_DB_DEFAULT)
    p.add_argument("--out")
    p.add_argument("--log", default="data/osc_log.jsonl")
    args = p.parse_args(argv)
    if not 1 <= args.seconds <= 600:
        p.error("--seconds harus 1..600")
    try:
        check("/meters", (args.path,))
    except ReadOnlyViolation as exc:
        print(f"DITOLAK (read-only): {exc}", file=sys.stderr)
        return 2
    with ReadOnlyClient(args.host, args.port, log_path=args.log) as client:
        result = record(client, args.path, args.seconds, args.clip_db)
    print(format_table(result))
    if args.out:
        save(result, args.out)
        print(f"disimpan -> {args.out}")
    if result["frames"] == 0:
        print("Tidak ada frame meter diterima: path/format mungkin berbeda dari dugaan.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
