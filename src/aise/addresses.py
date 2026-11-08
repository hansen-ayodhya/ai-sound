"""Daftar alamat OSC kandidat untuk dump konfigurasi.

STATUS: BELUM DIVERIFIKASI terhadap MR18/firmware Anda. Disusun dari
pengetahuan umum protokol keluarga X Air. Alamat yang tidak dijawab mixer
akan tercatat sebagai None di dump; itu informasi, bukan error. Koreksi daftar
ini setelah audit pertama.
"""

from __future__ import annotations

N_CHANNELS = 16
N_BUSES = 6
N_FX = 4


def global_addresses() -> list[str]:
    return ["/info", "/xinfo", "/status"]


def channel_addresses(n: int) -> list[str]:
    c = f"/ch/{n:02d}"
    addrs = [
        f"{c}/config/name",
        f"{c}/config/color",
        f"{c}/config/insrc",
        f"{c}/preamp/rtnsw",
        f"{c}/preamp/hpon",
        f"{c}/preamp/hpf",
        f"{c}/gate/on",
        f"{c}/gate/thr",
        f"{c}/gate/range",      # dugaan alamat; belum diverifikasi
        f"{c}/dyn/on",
        f"{c}/dyn/thr",
        f"{c}/dyn/ratio",
        f"{c}/dyn/attack",      # dugaan alamat; belum diverifikasi
        f"{c}/dyn/release",     # dugaan alamat; belum diverifikasi
        f"{c}/eq/on",
        f"{c}/mix/on",
        f"{c}/mix/fader",
        f"{c}/mix/lr",
        f"/headamp/{n:02d}/gain",
        f"/headamp/{n:02d}/phantom",
    ]
    for band in range(1, 5):
        for p in ("type", "f", "g", "q"):
            addrs.append(f"{c}/eq/{band}/{p}")
    for b in range(1, N_BUSES + 1):
        addrs.append(f"{c}/mix/{b:02d}/level")
    # Kiriman ke FX 1..4: DUGAAN bahwa MR18 memakai mix/07..10 untuk FX send. Belum diverifikasi.
    for fx in range(1, N_FX + 1):
        addrs.append(f"{c}/mix/{N_BUSES + fx:02d}/level")
    return addrs


def bus_addresses(n: int) -> list[str]:
    b = f"/bus/{n}"
    return [f"{b}/config/name", f"{b}/mix/on", f"{b}/mix/fader", f"{b}/eq/on", f"{b}/dyn/on"]


def main_addresses() -> list[str]:
    return ["/lr/mix/on", "/lr/mix/fader", "/lr/eq/on", "/lr/dyn/on", "/lr/config/name"]


def fx_addresses(n: int) -> list[str]:
    return [f"/fx/{n}/type"]


def all_addresses(stream_bus: int | None = None) -> list[str]:
    out = global_addresses()
    for n in range(1, N_CHANNELS + 1):
        out += channel_addresses(n)
    for n in range(1, N_BUSES + 1):
        out += bus_addresses(n)
    if stream_bus:
        from .siaran import bus_detail_addresses
        out += bus_detail_addresses(stream_bus)
    out += main_addresses()
    for n in range(1, N_FX + 1):
        out += fx_addresses(n)
    return out
