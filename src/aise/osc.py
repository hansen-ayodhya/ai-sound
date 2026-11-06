"""Encoder/decoder OSC minimal (stdlib saja). Tipe argumen: i, f, s, b."""

from __future__ import annotations

import struct


class OscError(ValueError):
    pass


def _pad(data: bytes) -> bytes:
    data += b"\x00"
    return data + b"\x00" * (-len(data) % 4)


def _read_string(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.find(b"\x00", pos)
    if end < 0:
        raise OscError("string tanpa terminator")
    text = buf[pos:end].decode("utf-8", errors="replace")
    return text, pos + ((end - pos) // 4 + 1) * 4


def encode(address: str, args: tuple = ()) -> bytes:
    tags = ","
    payload = b""
    for a in args:
        if isinstance(a, bool):
            raise OscError("bool tidak didukung")
        if isinstance(a, int):
            tags += "i"
            payload += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            payload += struct.pack(">f", a)
        elif isinstance(a, str):
            tags += "s"
            payload += _pad(a.encode("utf-8"))
        elif isinstance(a, (bytes, bytearray)):
            tags += "b"
            payload += struct.pack(">i", len(a)) + bytes(a) + b"\x00" * (-len(a) % 4)
        else:
            raise OscError(f"tipe argumen tidak didukung: {type(a)}")
    return _pad(address.encode("utf-8")) + _pad(tags.encode("ascii")) + payload


def decode(data: bytes) -> tuple[str, tuple]:
    if not data.startswith(b"/"):
        raise OscError("bukan pesan OSC (tidak diawali '/')")
    address, pos = _read_string(data, 0)
    if pos >= len(data):
        return address, ()
    tags, pos = _read_string(data, pos)
    if not tags.startswith(","):
        raise OscError("type tag tidak valid")
    args: list = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack_from(">i", data, pos)[0])
            pos += 4
        elif t == "f":
            args.append(struct.unpack_from(">f", data, pos)[0])
            pos += 4
        elif t == "s":
            s, pos = _read_string(data, pos)
            args.append(s)
        elif t == "b":
            n = struct.unpack_from(">i", data, pos)[0]
            pos += 4
            args.append(bytes(data[pos : pos + n]))
            pos += n + (-n % 4)
        else:
            raise OscError(f"type tag tidak didukung: {t!r}")
    return address, tuple(args)
