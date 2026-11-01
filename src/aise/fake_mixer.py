"""Mixer tiruan (UDP, localhost) untuk menguji klien tanpa perangkat nyata.

Mencatat setiap upaya tulis di `write_attempts` supaya tes bisa membuktikan
bahwa klien read-only tidak pernah mengirim tulis.
"""

from __future__ import annotations

import socket
import threading

from .osc import OscError, decode, encode


class FakeMixer:
    def __init__(self, state: dict[str, tuple]):
        self.state = dict(state)
        self.write_attempts: list[tuple[str, tuple]] = []
        self.queries: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.1)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FakeMixer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._sock.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, peer = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                address, args = decode(data)
            except OscError:
                continue
            if args:
                self.write_attempts.append((address, args))
                self.state[address] = args
                continue
            self.queries.append(address)
            if address in self.state:
                self._sock.sendto(encode(address, self.state[address]), peer)
