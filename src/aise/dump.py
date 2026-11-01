"""Dump konfigurasi mixer READ-ONLY ke JSON (dengan timestamp dan hash).

Pemakaian:
    python -m aise.dump --host 192.168.x.x --out data/dump1.json
    python -m aise.dump --diff data/dump1.json data/dump2.json
    python -m aise.dump --host 192.168.x.x --only /info /ch/01/config/name   # uji kecil
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from .addresses import all_addresses
from .client import DEFAULT_PORT, ReadOnlyClient
from .guard import ReadOnlyViolation, check


def _hash(values: dict) -> str:
    blob = json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def run_dump(client: ReadOnlyClient, addresses: list[str]) -> dict:
    values: dict[str, list | None] = {}
    for addr in addresses:
        reply = client.query(addr)
        values[addr] = list(reply) if reply is not None else None
    answered = sum(v is not None for v in values.values())
    return {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "queried": len(addresses),
        "answered": answered,
        "sha256": _hash(values),
        "values": values,
    }


def diff(a: dict, b: dict) -> list[str]:
    out = []
    for key in sorted(set(a["values"]) | set(b["values"])):
        va, vb = a["values"].get(key), b["values"].get(key)
        if va != vb:
            out.append(f"{key}: {va!r} -> {vb!r}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Dump konfigurasi mixer (READ-ONLY)")
    p.add_argument("--host")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--out")
    p.add_argument("--only", nargs="+", metavar="ALAMAT", help="uji kecil: hanya query alamat ini")
    p.add_argument("--log", default="data/osc_log.jsonl")
    p.add_argument("--diff", nargs=2, metavar=("A", "B"))
    args = p.parse_args(argv)

    if args.diff:
        a, b = (json.loads(Path(f).read_text(encoding="utf-8")) for f in args.diff)
        changes = diff(a, b)
        print("IDENTIK" if not changes else f"{len(changes)} perbedaan:")
        for line in changes:
            print(" ", line)
        return 0 if not changes else 1

    if not args.host:
        p.error("--host wajib")
    if not args.only and not args.out:
        p.error("--out wajib untuk dump penuh (atau pakai --only untuk uji kecil)")
    addresses = args.only or all_addresses()
    try:
        for a in addresses:
            check(a, ())  # tolak alamat terlarang sebelum membuka koneksi
    except ReadOnlyViolation as exc:
        print(f"DITOLAK (read-only): {exc}", file=sys.stderr)
        return 2
    with ReadOnlyClient(args.host, args.port, timeout=0.5, retries=1, log_path=args.log) as client:
        result = run_dump(client, addresses)
    if args.only and not args.out:
        for addr, val in result["values"].items():
            print(f"{addr} = {val if val is not None else '(tidak dijawab)'}")
        print(f"{result['answered']}/{result['queried']} dijawab")
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{result['answered']}/{result['queried']} alamat dijawab -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
