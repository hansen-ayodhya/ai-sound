"""Pelacakan rekomendasi: apakah usulan sudah dilakukan operator di mixer?

Program tidak mengubah mixer. Setiap kali setting mixer terbaca (live, tombol Baca setting, atau Audit),
nilai sekarang dibandingkan dengan nilai usulan. Hasilnya per rekomendasi:
  belum    - nilai belum berubah dari saat usulan dibuat
  sebagian - sudah bergerak ke arah usulan tetapi belum cukup dekat
  selesai  - nilai sudah sesuai usulan (terverifikasi dari pembacaan mixer)
Rekomendasi aturan lokal yang MASIH memicu aturannya tidak pernah dianggap selesai (aturan itu sendiri
adalah penentu akhir). Usulan DSP (alamat 'dsp:...') tidak dilacak karena DSP tidak terbaca.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import convert

TOL_RAW = 0.03          # selisih nilai mentah (0..1) yang dianggap "sudah sama"
DONE_PROGRESS = 0.7     # sudah menempuh 70% jarak dari nilai awal ke usulan = dianggap selesai
MIN_MOVE = 0.005        # perubahan lebih kecil dari ini dianggap tidak berubah
MAX_TRACKED = 200
MAX_DONE_SHOWN = 30
TOGGLE_SUFFIX = ("/on", "/hpon", "/phantom", "/lr")


def _first(values: dict, addr: str):
    v = values.get(addr)
    return v[0] if v else None


def _is_mixer_addr(a) -> bool:
    return isinstance(a, str) and a.startswith("/")


def load(path: Path) -> dict:
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) and isinstance(s.get("tracked"), dict) else {"tracked": {}}
    except (OSError, ValueError):
        return {"tracked": {}}


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def fmt_value(alamat: str, raw) -> str:
    """Nilai mixer dalam satuan manusia (konversi sama dengan tampilan)."""
    if raw is None:
        return "?"
    try:
        if alamat.endswith(TOGGLE_SUFFIX):
            return "ON" if int(raw) == 1 else "OFF"
        if alamat.startswith("/headamp/") and alamat.endswith("/gain"):
            return f"{convert.gain_db(raw):+.0f} dB"
        if alamat.endswith("/preamp/hpf"):
            return f"≈{convert.hpf_hz(raw):.0f} Hz"
        if "/eq/" in alamat and alamat.endswith("/f"):
            hz = convert.eq_hz(raw)
            return f"≈{hz / 1000:.1f} kHz" if hz >= 1000 else f"≈{hz:.0f} Hz"
        if "/eq/" in alamat and alamat.endswith("/g"):
            return f"≈{convert.eq_gain_db(raw):+.1f} dB"
        if "/eq/" in alamat and alamat.endswith("/q"):
            return f"≈Q {convert.eq_q(raw):.1f}"
        if alamat.endswith("/dyn/thr"):
            return f"≈{convert.comp_thr_db(raw):.0f} dB"
        if alamat.endswith("/dyn/ratio"):
            return f"≈{convert.ratio_value(raw):g}:1"
        if alamat.endswith("/gate/thr"):
            return f"≈{convert.gate_thr_db(raw):.0f} dB"
        if alamat.endswith("/mix/fader") or (alamat.count("/mix/") and alamat.endswith("/level")):
            d = convert.fader_db(raw)
            return "mati (−∞)" if d == float("-inf") else f"≈{d:.0f} dB"
    except (TypeError, ValueError):
        pass
    return f"{raw}"


def item_state(alamat: str, base, target, cur) -> str:
    """belum | sebagian | selesai | tidak_terbaca"""
    if cur is None or target is None:
        return "tidak_terbaca"
    try:
        if alamat.endswith(TOGGLE_SUFFIX):
            return "selesai" if int(cur) == int(target) else "belum"
        if alamat.endswith("/dyn/ratio"):
            ci, ti = convert.ratio_index(cur), convert.ratio_index(target)
            if abs(ci - ti) <= 1:
                return "selesai"
            bi = convert.ratio_index(base) if base is not None else None
            return "sebagian" if bi is not None and abs(ci - bi) >= 1 and abs(ci - ti) < abs(bi - ti) else "belum"
        cur, target = float(cur), float(target)
        if abs(cur - target) <= TOL_RAW:
            return "selesai"
        if base is None:
            return "belum"
        base = float(base)
        if abs(cur - base) < MIN_MOVE or abs(target - base) < 1e-9:
            return "belum"
        progress = (cur - base) / (target - base)
        if progress >= DONE_PROGRESS:
            return "selesai"
        return "sebagian" if progress > 0.05 else "belum"
    except (TypeError, ValueError):
        return "tidak_terbaca"


def register(state: dict, findings: list[dict], dump: dict | None, now: str) -> None:
    """Catat rekomendasi baru beserta nilai awalnya (dari pembacaan mixer saat itu)."""
    tracked = state.setdefault("tracked", {})
    vals = (dump or {}).get("values", {})
    for f in findings:
        items = [u for u in (f.get("usulan") or []) if _is_mixer_addr(u.get("alamat")) and u.get("usulan") is not None]
        if not items or not f.get("id"):
            continue
        fid = f["id"]
        cur = tracked.get(fid)
        if cur and not cur.get("done_at"):
            continue                                      # sudah dilacak; nilai awal dipertahankan
        if cur and f.get("sumber") == "gemini":
            continue                                      # id Gemini unik per analisis
        tracked[fid] = {
            "id": fid, "ch": f.get("ch"), "sumber": f.get("sumber") or "program", "masalah": f.get("masalah", ""),
            "dibuat": now, "done_at": None, "status": "belum", "kembali": bool(cur),
            "items": [{"alamat": u["alamat"], "teks": u.get("teks", ""), "base": _first(vals, u["alamat"]),
                       "target": u["usulan"]} for u in items]}
    if len(tracked) > MAX_TRACKED:
        for fid in sorted(tracked, key=lambda k: tracked[k].get("dibuat", ""))[: len(tracked) - MAX_TRACKED]:
            del tracked[fid]


def evaluate(state: dict, dump: dict | None, now: str, firing: set[str] | None = None) -> None:
    """Bandingkan nilai mixer sekarang dengan usulan; tandai selesai bila sudah sesuai."""
    if not dump:
        return
    vals, firing = dump.get("values", {}), firing or set()
    for fid, t in state.get("tracked", {}).items():
        states = []
        for it in t["items"]:
            cur = _first(vals, it["alamat"])
            it["sekarang"] = cur
            it["sekarang_teks"] = fmt_value(it["alamat"], cur)
            it["status"] = item_state(it["alamat"], it.get("base"), it.get("target"), cur)
            states.append(it["status"])
        if states and all(s == "selesai" for s in states):
            status = "selesai"
        elif any(s in ("selesai", "sebagian") for s in states):
            status = "sebagian"
        else:
            status = "belum"
        if status == "selesai" and t["sumber"] != "gemini" and fid in firing:
            status = "sebagian"                           # aturan lokal masih memicu: belum benar-benar beres
        t["status"] = status
        if status == "selesai":
            t["done_at"] = t.get("done_at") or now
        else:
            if t.get("done_at"):
                t["kembali"] = True          # sempat selesai, lalu dibatalkan operator
            t["done_at"] = None


def view(state: dict) -> dict:
    tracked = state.get("tracked", {})
    recs = {fid: {"status": t["status"], "done_at": t.get("done_at"), "kembali": t.get("kembali", False),
                  "items": [{"teks": i["teks"], "status": i.get("status", "belum"),
                             "awal_teks": fmt_value(i["alamat"], i.get("base")), "sekarang_teks": i.get("sekarang_teks", "?"),
                             "target_teks": fmt_value(i["alamat"], i.get("target"))} for i in t["items"]]}
            for fid, t in tracked.items()}
    done = sorted((t for t in tracked.values() if t.get("done_at")), key=lambda t: t["done_at"], reverse=True)[:MAX_DONE_SHOWN]
    return {"recs": recs,
            "selesai": [{"id": t["id"], "ch": t.get("ch"), "sumber": t["sumber"], "masalah": t["masalah"],
                         "done_at": t["done_at"], "kembali": t.get("kembali", False), "items": recs[t["id"]]["items"]} for t in done]}
