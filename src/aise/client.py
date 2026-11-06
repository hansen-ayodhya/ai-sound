"""Klien OSC READ-ONLY untuk mixer. Satu-satunya jalur kirim ada di _send()."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from .guard import ReadOnlyViolation, check, check_bytes
from .osc import OscError, decode, encode

DEFAULT_PORT = 10024  # BELUM diverifikasi pada MR18; konfirmasi saat audit.
MIN_INTERVAL_S = 0.02  # maksimal ~50 pesan/detik


def disable_udp_connreset(sock: socket.socket) -> None:
    """Windows: matikan laporan 'connection reset' pada UDP (bila tersedia)."""
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        try:
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)  # type: ignore[attr-defined]
        except OSError:
            pass


class ReadOnlyClient:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = 1.0,
        retries: int = 2,
        log_path: str | Path | None = None,
    ):
        self.addr = (host, port)
        self.timeout = timeout
        self.retries = retries
        self.log_path = Path(log_path) if log_path else None
        self._last_send = 0.0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disable_udp_connreset(self._sock)
        self._sock.settimeout(timeout)

    def close(self) -> None:
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _log(self, **entry) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _send(self, packet: bytes) -> None:
        check_bytes(packet)  # lapisan kedua, pada byte yang akan dikirim
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last_send)
        if wait > 0:
            time.sleep(wait)
        self._sock.sendto(packet, self.addr)
        self._last_send = time.monotonic()

    def query(self, address: str) -> tuple | None:
        """Minta nilai satu parameter. Mengembalikan tuple argumen, atau None bila tak ada respons."""
        try:
            check(address, ())
        except ReadOnlyViolation as exc:
            self._log(event="blocked", address=address, reason=str(exc))
            raise
        packet = encode(address)
        for attempt in range(self.retries + 1):
            self._send(packet)
            self._log(event="query", address=address, attempt=attempt)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    data, _ = self._sock.recvfrom(4096)
                except socket.timeout:
                    break
                except ConnectionResetError:
                    # Windows: balasan "port tidak terjangkau" muncul sebagai error; anggap tidak dijawab.
                    break
                try:
                    reply_addr, args = decode(data)
                except OscError:
                    continue
                if reply_addr == address:
                    return args
        self._log(event="no_reply", address=address)
        return None

    def stream_meters(self, path: str, on_blob, should_stop, renew_every: float = 8.0) -> int:
        """Alirkan meter terus-menerus sampai `should_stop()` bernilai True.

        `on_blob(bytes)` dipanggil untuk setiap frame. Permintaan /meters tetap
        diperiksa penjaga (lewat _send) dan diulang tiap `renew_every` detik
        karena langganan di mixer kedaluwarsa sendiri. Mengembalikan jumlah frame.
        """
        packet = encode("/meters", (path,))
        frames = 0
        next_renew = time.monotonic()
        self._log(event="meters_start", path=path)
        old_timeout = self._sock.gettimeout()
        self._sock.settimeout(0.2)
        try:
            while not should_stop():
                if time.monotonic() >= next_renew:
                    self._send(packet)
                    next_renew = time.monotonic() + renew_every
                try:
                    data, _ = self._sock.recvfrom(65535)
                except (socket.timeout, ConnectionResetError):
                    continue
                try:
                    addr, args = decode(data)
                except OscError:
                    continue
                if addr == path and args and isinstance(args[0], (bytes, bytearray)):
                    frames += 1
                    on_blob(bytes(args[0]))
        finally:
            self._sock.settimeout(old_timeout)
            self._log(event="meters_end", path=path, frames=frames)
        return frames

    def subscribe_meters(self, path: str, seconds: float, renew_every: float = 8.0) -> list[bytes]:
        """Minta stream meter selama `seconds` detik; kembalikan daftar blob mentah."""
        blobs: list[bytes] = []
        end = time.monotonic() + seconds
        self.stream_meters(path, blobs.append, lambda: time.monotonic() >= end, renew_every)
        return blobs
