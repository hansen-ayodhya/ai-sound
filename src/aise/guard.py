"""Penjaga READ-ONLY.

Aturan: hanya pesan *query* (alamat OSC tanpa argumen) ke alamat di allowlist,
ditambah SATU bentuk khusus: permintaan meter `/meters <path> [int...]`
(membaca level; tidak mengubah setting). Semua selain itu ditolak SEBELUM
menyentuh jaringan.

PENTING: allowlist dan bentuk permintaan meter belum diverifikasi terhadap
MR18 yang sebenarnya. Perluas hanya setelah diverifikasi dan dengan keputusan
eksplisit.
"""

from __future__ import annotations

import re

from .osc import OscError, decode


class ReadOnlyViolation(Exception):
    """Dilempar bila ada upaya mengirim sesuatu selain query yang diizinkan."""


# Prefix alamat yang boleh di-query. Sengaja TIDAK termasuk:
#   /-action, /-snap, /-prefs  (aksi/snapshot/preferensi)
#   /renew                     (belum dibutuhkan)
# DIIZINKAN khusus: /xremote tanpa argumen = berlangganan kabar perubahan dari mixer (hanya MENERIMA).
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
_METER_PATH_RE = re.compile(r"^/meters/\d{1,2}$")
_MAX_METER_INT_ARGS = 3
_MAX_METER_INT_VALUE = 1000


def _check_meter_request(args: tuple) -> None:
    if not args or not isinstance(args[0], str) or not _METER_PATH_RE.match(args[0]):
        raise ReadOnlyViolation(f"permintaan /meters tidak valid: {args!r}")
    rest = args[1:]
    if len(rest) > _MAX_METER_INT_ARGS:
        raise ReadOnlyViolation("permintaan /meters: argumen terlalu banyak")
    for a in rest:
        if isinstance(a, bool) or not isinstance(a, int) or not 0 <= a <= _MAX_METER_INT_VALUE:
            raise ReadOnlyViolation(f"permintaan /meters: argumen tidak valid {a!r}")


def check(address: str, args: tuple) -> None:
    if address == "/meters":
        _check_meter_request(args)
        return
    if address == "/xremote":
        if args:
            raise ReadOnlyViolation(f"/xremote tidak boleh membawa argumen: {args!r}")
        return
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
