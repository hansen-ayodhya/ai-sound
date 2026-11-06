"""Audit sekali jalan (READ-ONLY): cek koneksi, dump 2x, bandingkan, buat laporan.

Hasil disimpan di folder bertanggal `data/audit-YYYYmmdd-HHMMSS/` sehingga
baseline lama tidak pernah tertimpa. `data/latest.json` menunjuk ke audit
terakhir supaya penampil bisa memuatnya otomatis.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .addresses import all_addresses
from .client import DEFAULT_PORT, ReadOnlyClient
from .dump import diff, run_dump
from .report import analyze, findings_json, render

PROBE_ADDRESSES = ["/info", "/ch/01/config/name"]


class MixerNotAnswering(RuntimeError):
    pass


def run_audit(host: str, port: int = DEFAULT_PORT, base: str | Path = "data",
              addresses: list[str] | None = None, timeout: float = 0.5,
              progress=None) -> dict:
    base = Path(base)
    addresses = addresses or all_addresses()
    folder = base / f"audit-{time.strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    with ReadOnlyClient(host, port, timeout=timeout, retries=1,
                        log_path=folder / "osc_log.jsonl") as client:
        # Cek cepat dulu supaya tidak menunggu lama bila IP/port salah.
        if all(client.query(a) is None for a in PROBE_ADDRESSES):
            raise MixerNotAnswering(
                f"Mixer di {host}:{port} tidak menjawab. Cek IP, kabel LAN, dan bahwa laptop "
                "ada di jaringan yang sama. (Port juga masih dugaan.)")
        d1 = run_dump(client, addresses, progress=progress)
        d2 = run_dump(client, addresses, progress=progress)

    p1, p2, rep = folder / "dump1.json", folder / "dump2.json", folder / "report.md"
    p1.write_text(json.dumps(d1, indent=2, ensure_ascii=False), encoding="utf-8")
    p2.write_text(json.dumps(d2, indent=2, ensure_ascii=False), encoding="utf-8")
    rep.write_text(render(d1, p1.as_posix()), encoding="utf-8")
    fnd = folder / "findings.json"
    fnd.write_text(json.dumps(findings_json(d1), indent=2, ensure_ascii=False), encoding="utf-8")
    changes = diff(d1, d2)

    def rel(p: Path) -> str:
        # Jalur relatif terhadap folder proyek (yang disajikan ke penampil).
        return (p.relative_to(base.parent) if base.is_absolute() else p).as_posix()

    latest = {"dump": rel(p1), "compare": rel(p2), "report": rel(rep), "findings": rel(fnd), "created": d1["created"]}
    (base / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")

    return {
        "folder": str(folder),
        "answered": d1["answered"],
        "queried": d1["queried"],
        "identical": not changes,
        "changes": changes,
        "findings": len(analyze(d1)),
        "report": str(rep),
    }
