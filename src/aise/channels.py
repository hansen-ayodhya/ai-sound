"""Daftar Channel: sumber suara apa ada di channel mana (diisi operator).

Dipakai aturan AI sebagai pengganti tebakan dari nama channel. Disimpan di
data/channels.json. Nomor channel TIDAK pernah dikarang: daftar bawaan hanya
berisi sumber yang disebutkan operator, dengan channel kosong.
"""

from __future__ import annotations


import json
import re
import time
from copy import deepcopy
from pathlib import Path


def clean_name(name) -> str:
    """Nama channel dari mixer tanpa akhiran nomor otomatis (mis. 'Lead Keyb_27' -> 'Lead Keyb')."""
    return re.sub(r"_\d{1,3}$", "", str(name if name is not None else "").strip()).strip()


# Jenis sumber dan sifatnya untuk aturan AI.
#   voice: channel suara manusia; speech: bicara (khotbah); hpf: usulan low cut (Hz);
#   line: sumber line-level yang tidak butuh 48V bila koneksinya belum diisi.
TYPES: dict[str, dict] = {
    "vokal_utama": {"label": "Vokal utama (WL)", "voice": True, "speech": False, "hpf": 100, "line": False},
    "vokal_singer": {"label": "Vokal singer", "voice": True, "speech": False, "hpf": 100, "line": False},
    "khotbah": {"label": "Bicara / khotbah", "voice": True, "speech": True, "hpf": 120, "line": False},
    "bass": {"label": "Bass", "voice": False, "speech": False, "hpf": None, "line": False},
    "gitar_listrik": {"label": "Gitar listrik", "voice": False, "speech": False, "hpf": None, "line": True},
    "keyboard": {"label": "Keyboard", "voice": False, "speech": False, "hpf": None, "line": True},
    "drum_elektrik": {"label": "Drum elektrik", "voice": False, "speech": False, "hpf": None, "line": True},
    "playback": {"label": "Playback komputer", "voice": False, "speech": False, "hpf": None, "line": True},
    "lainnya": {"label": "Lainnya", "voice": False, "speech": False, "hpf": None, "line": False},
}
KONEKSI = ["", "kabel", "wireless", "DI pasif", "DI aktif", "line langsung", "USB"]
# 1..16 = input mic/line MR18; 17 = Aux/USB return (bukan channel 1–16).
CH_MIN, CH_MAX, CH_AUX = 1, 17, 17
MAX_SOURCES = 40

ACOUSTIC_RE = re.compile(r"\b(ak|akustik|acoustic|aco|ag)\b", re.I)

# Petunjuk nama channel di mixer untuk "isi otomatis" dan cek kecocokan.
NAME_HINTS: dict[str, re.Pattern] = {
    "khotbah": re.compile(r"\b(khotbah|kotbah|pdt|pastor|pendeta|speech|mc|podium)", re.I),
    "vokal_utama": re.compile(r"\b(wl|worship|lead)", re.I),
    "vokal_singer": re.compile(r"\b(singer|sing|bv|backing|vok|vocal|vox)", re.I),
    "bass": re.compile(r"\bbass", re.I),
    "gitar_listrik": re.compile(r"\b(git|gtr|guitar|eg\b)", re.I),
    "keyboard": re.compile(r"\b(key|keys|kbd|piano|synth|filler|pad)", re.I),
    "drum_elektrik": re.compile(r"\b(drum|edrum|e-drum|kit)", re.I),
    "playback": re.compile(r"\b(usb|laptop|pc|komputer|playback|ew|easyworship|video)", re.I),
}


def _src(id_: str, nama: str, jenis: str, stereo: bool = False, koneksi: str = "") -> dict:
    return {"id": id_, "nama": nama, "jenis": jenis, "ch": [], "stereo": stereo,
            "koneksi": koneksi, "mic": "", "catatan": "", "otomatis": False}


# Daftar dari operator (2026-11-06). Channel sengaja kosong sampai diisi.
DEFAULT_PROFILE: dict = {
    "updated": None,
    "sources": [
        _src("bass", "Bass", "bass"),
        _src("gitar", "Gitar listrik", "gitar_listrik"),
        _src("key1", "Keyboard utama", "keyboard"),
        _src("key2", "Keyboard filler", "keyboard"),
        _src("drum", "Drum elektrik", "drum_elektrik", stereo=True),
        _src("wl", "Mic WL (worship leader)", "vokal_utama"),
        _src("singer1", "Mic singer 1", "vokal_singer"),
        _src("singer2", "Mic singer 2", "vokal_singer"),
        _src("khotbah1", "Mic pengkhotbah 1", "khotbah"),
        _src("khotbah2", "Mic pengkhotbah 2", "khotbah"),
        _src("usb", "USB komputer (EasyWorship)", "playback", stereo=True, koneksi="USB"),
    ],
}


class ProfileError(ValueError):
    pass


def default_profile() -> dict:
    p = deepcopy(DEFAULT_PROFILE)
    p["default"] = True
    return p


def validate(profile: dict) -> dict:
    """Periksa dan rapikan isian dari formulir. Melempar ProfileError bila salah."""
    if not isinstance(profile, dict) or not isinstance(profile.get("sources"), list):
        raise ProfileError("Format Daftar Channel salah.")
    srcs = profile["sources"]
    if len(srcs) > MAX_SOURCES:
        raise ProfileError(f"Maksimal {MAX_SOURCES} sumber.")
    out, used, ids = [], {}, set()
    for i, s in enumerate(srcs):
        if not isinstance(s, dict):
            raise ProfileError("Format baris salah.")
        nama = str(s.get("nama", "")).strip()[:40]
        if not nama:
            raise ProfileError(f"Baris {i + 1}: nama sumber kosong.")
        jenis = s.get("jenis")
        if jenis not in TYPES:
            raise ProfileError(f"{nama}: jenis tidak dikenal.")
        koneksi = s.get("koneksi", "")
        if koneksi not in KONEKSI:
            raise ProfileError(f"{nama}: koneksi tidak dikenal.")
        chs = s.get("ch", [])
        if not isinstance(chs, list) or len(chs) > 2:
            raise ProfileError(f"{nama}: maksimal 2 channel (kiri/kanan).")
        clean_ch = []
        for c in chs:
            if isinstance(c, bool) or not isinstance(c, int) or not CH_MIN <= c <= CH_MAX:
                raise ProfileError(f"{nama}: nomor channel tidak valid.")
            if c in clean_ch:
                continue
            if c != CH_AUX and c in used:
                raise ProfileError(f"Channel {c} dipakai dua kali: {used[c]} dan {nama}.")
            used[c] = nama
            clean_ch.append(c)
        id_ = re.sub(r"[^a-z0-9_-]", "", str(s.get("id", "")).lower())[:24] or f"src{i + 1}"
        while id_ in ids:
            id_ += "x"
        ids.add(id_)
        out.append({"id": id_, "nama": nama, "jenis": jenis, "ch": clean_ch,
                    "stereo": bool(s.get("stereo", False)), "koneksi": koneksi,
                    "mic": str(s.get("mic", "")).strip()[:60],
                    "catatan": str(s.get("catatan", "")).strip()[:120],
                    "otomatis": bool(s.get("otomatis", False))})
    return {"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sources": out}


def load(path: Path) -> dict | None:
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def save(path: Path, profile: dict) -> dict:
    clean = validate(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return clean


def source_for(profile: dict | None, ch: int) -> dict | None:
    if not profile:
        return None
    for s in profile.get("sources", []):
        if ch in s.get("ch", []):
            return s
    return None


def short_role(src: dict) -> str:
    return src["nama"]


def _names(dump: dict | None) -> dict[int, str]:
    out = {}
    for n in range(1, 17):
        v = (dump or {}).get("values", {}).get(f"/ch/{n:02d}/config/name")
        if v and v[0] not in (None, ""):
            out[n] = clean_name(v[0])
    return out


def suggest(profile: dict, dump: dict | None) -> dict:
    """Isi otomatis channel yang masih kosong dari nama channel di mixer.

    Hanya mengisi sumber yang belum punya channel, hanya ke channel yang belum
    dipakai, dan menandai hasilnya `otomatis` supaya operator memeriksanya.
    """
    p = deepcopy(profile)
    names = _names(dump)
    taken = {c for s in p["sources"] for c in s["ch"]}
    for s in p["sources"]:
        if s["ch"]:
            continue
        hint = NAME_HINTS.get(s["jenis"])
        if not hint:
            continue
        want = 2 if s.get("stereo") else 1
        found = []
        for n, name in sorted(names.items()):
            if n in taken or not hint.search(name):
                continue
            if s["jenis"] == "gitar_listrik" and ACOUSTIC_RE.search(name):
                continue  # gitar akustik bukan gitar listrik
            # Jangan tarik channel yang lebih cocok untuk jenis lain (mis. "Vokal WL" untuk singer).
            better = [j for j, h in NAME_HINTS.items() if j != s["jenis"] and h.search(name)
                      and j in ("khotbah", "vokal_utama") and s["jenis"] not in ("khotbah", "vokal_utama")]
            if better:
                continue
            found.append(n)
            if len(found) == want:
                break
        if found:
            s["ch"] = found
            s["otomatis"] = True
            taken.update(found)
    return p


def mismatch(src: dict, mixer_name: str) -> str | None:
    """Jenis lain yang lebih cocok dengan nama di mixer, bila jelas berbeda."""
    if not mixer_name:
        return None
    own = NAME_HINTS.get(src["jenis"])
    if own and own.search(mixer_name):
        return None
    for j, h in NAME_HINTS.items():
        if j != src["jenis"] and h.search(mixer_name):
            # Vokal utama vs singer sama-sama vokal: bukan salah pasang.
            if {j, src["jenis"]} <= {"vokal_utama", "vokal_singer"}:
                return None
            return j
    return None
