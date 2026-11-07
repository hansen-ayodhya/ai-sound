"""Mixer tiruan (UDP, localhost) untuk menguji klien tanpa perangkat nyata.

Mencatat setiap upaya tulis di `write_attempts` supaya tes bisa membuktikan
bahwa klien read-only tidak pernah mengirim tulis.
"""

from __future__ import annotations

import socket
import threading
import time

from .client import disable_udp_connreset
from .osc import OscError, decode, encode


class FakeMixer:
    def __init__(self, state: dict[str, tuple]):
        self.state = dict(state)
        self.write_attempts: list[tuple[str, tuple]] = []
        self.queries: list[str] = []
        self.meter_requests: list[tuple] = []
        self.subscribers: set = set()        # pelanggan /xremote
        self.xremote_requests = 0
        self.meter_frames: dict[str, list[bytes]] = {}
        # Bila diisi: fungsi tanpa argumen yang mengembalikan blob meter; mixer tiruan
        # lalu mengalirkan frame ~20x/detik selama `stream_seconds` setelah tiap permintaan.
        self.meter_generator = None
        self.stream_seconds = 10.0
        self._stream_until: dict[str, float] = {}
        self._streams: dict[str, threading.Thread] = {}
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disable_udp_connreset(self._sock)
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
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                return
            try:
                address, args = decode(data)
            except OscError:
                continue
            if address == "/xremote" and not args:
                self.xremote_requests += 1
                self.subscribers.add(peer)
                continue
            if address == "/meters" and args and isinstance(args[0], str):
                self.meter_requests.append(args)
                for blob in self.meter_frames.get(args[0], []):
                    self._sock.sendto(encode(args[0], (blob,)), peer)
                if self.meter_generator:
                    self._start_stream(args[0], peer)
                continue
            if args:
                self.write_attempts.append((address, args))
                self.state[address] = args
                continue
            self.queries.append(address)
            if address in self.state:
                self._sock.sendto(encode(address, self.state[address]), peer)

    def push(self, address: str, args: tuple) -> None:
        """Meniru operator mengubah setting di M-AIR: simpan lalu kabarkan ke pelanggan /xremote."""
        self.state[address] = args
        for peer in list(self.subscribers):
            try:
                self._sock.sendto(encode(address, args), peer)
            except OSError:
                pass

    def _start_stream(self, path: str, peer) -> None:
        self._stream_until[path] = time.monotonic() + self.stream_seconds
        t = self._streams.get(path)
        if t and t.is_alive():
            return

        def run():
            while not self._stop.is_set() and time.monotonic() < self._stream_until[path]:
                try:
                    self._sock.sendto(encode(path, (self.meter_generator(),)), peer)
                except OSError:
                    return
                time.sleep(0.05)

        t = threading.Thread(target=run, daemon=True)
        self._streams[path] = t
        t.start()
