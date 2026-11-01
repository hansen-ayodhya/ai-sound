"""Penjaga READ-ONLY.

Aturan Tahap 1: hanya pesan *query* (alamat OSC tanpa argumen) ke alamat yang
ada di allowlist. Semua selain itu ditolak SEBELUM menyentuh jaringan.

PENTING: allowlist ini belum diverifikasi terhadap MR18 yang sebenarnya.
Perluas hanya setelah diverifikasi dan dengan keputusan eksplisit.
"""

from __future__ import annotations

import re

from .osc import OscError, decode


class ReadOnlyViolation(Exception):
    """Dilempar bila ada upaya mengirim sesuatu selain query yang diizinkan."""


# Prefix alamat yang boleh di-query. Sengaja TIDAK termasuk:
#   /-action, /-snap, /-prefs  (aksi/snapshot/preferensi)
#   /xremote, /renew, /meters  (langganan/stream; butuh argumen atau keputusan terpisah)
ALLOWED_PREFIXES = (
    "/info",
    "/xinfo",
    "/status",
    "/ch/",
    "/rtn/",
    "/bus/",
    "/fxsend/",
    "/lr/",
    "/dca/",
    "/fx/",
    "/headamp/",
    "/config/",
    "/outputs/",
    "/routing/",
)

_ADDRESS_RE = re.compile(r"^/[A-Za-z0-9_\-/]+$")


def check(address: str, args: tuple) -> None:
    if args:
        raise ReadOnlyViolation(
            f"pesan dengan argumen ditolak (dianggap tulis): {address} {args!r}"
        )
    if not _ADDRESS_RE.match(address):
        raise ReadOnlyViolation(f"alamat tidak valid: {address!r}")
    if not any(address == p or address.startswith(p) for p in ALLOWED_PREFIXES):
        raise ReadOnlyViolation(f"alamat di luar allowlist: {address}")


def check_bytes(packet: bytes) -> None:
    """Pemeriksaan kedua pada byte yang benar-benar akan dikirim."""
    try:
        address, args = decode(packet)
    except OscError as exc:
        raise ReadOnlyViolation(f"paket tidak bisa didekode: {exc}") from exc
    check(address, args)
