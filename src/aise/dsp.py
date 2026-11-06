"""Data DSP (Ashly Protea 4.8SP): preset yang diunggah + data per output yang diperiksa operator.

Program TIDAK berkomunikasi dengan DSP. Sumber data:
- file preset yang diunggah (disimpan apa adanya; formatnya belum diketahui sehingga hanya diperiksa jenisnya),
- isian operator, atau draf hasil Gemini membaca tangkapan layar/preset teks (selalu diperiksa operator dulu).
Saran di sini adalah saran program (aturan lokal) untuk tingkat SISTEM.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from pathlib import Path

N_OUT, N_IN, MAX_PEQ = 8, 4, 10
MAX_PRESET_BYTES = 5_000_000
PERAN = {"utama_kiri": "Speaker utama kiri", "utama_kanan": "Speaker utama kanan", "sub": "Subwoofer",
         "monitor": "Monitor panggung", "lainnya": "Lainnya", "tidak_dipakai": "Tidak dipakai", "": "(belum diisi)"}

BOOST_MAX_DB = 6.0
XO_TOL = 1.3  # sub LPF > utama HPF x 1.3 = tumpang tindih; utama HPF > sub LPF x 1.3 = lubang


class DSPError(ValueError):
    pass


def _num(x, lo, hi):
    if x is None or x == "":
        return None
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise DSPError("Nilai bukan angka.")
    if not lo <= x <= hi:
        raise DSPError(f"Nilai {x} di luar rentang {lo}..{hi}.")
    return round(float(x), 3)


def _txt(x, n=40):
    return str(x or "").strip()[:n]


def _tipe(x) -> str:
    """Tipe filter, mis. 'LR24', 'BW12'. Dibatasi ke huruf/angka."""
    return re.sub(r"[^A-Za-z0-9 \-/]", "", str(x or ""))[:16]


def slope_of(tipe: str) -> float:
    m = re.search(r"(\d{1,2})", tipe or "")
    v = float(m.group(1)) if m else 24.0
    return v if v in (6, 12, 18, 24, 36, 48) else 24.0


def validate(data) -> dict:
    if not isinstance(data, dict):
        raise DSPError("Format data DSP salah.")
    outs = []
    for i, o in enumerate((data.get("output") or [])[:N_OUT]):
        if not isinstance(o, dict):
            raise DSPError("Format output salah.")
        no = o.get("no", i + 1)
        if not isinstance(no, int) or isinstance(no, bool) or not 1 <= no <= N_OUT:
            raise DSPError("Nomor output harus 1–8.")
        peran = o.get("peran", "")
        if peran not in PERAN:
            raise DSPError(f"Output {no}: peran tidak dikenal.")
        try:
            peq = []
            for b in (o.get("peq") or [])[:MAX_PEQ]:
                if not isinstance(b, dict):
                    continue
                hz, db, q = _num(b.get("hz"), 20, 20000), _num(b.get("db"), -30, 15), _num(b.get("q"), 0.1, 20)
                if hz is not None and db is not None:
                    peq.append({"hz": hz, "db": db, "q": q if q is not None else 1.0})
            outs.append({
                "no": no, "nama": _txt(o.get("nama")), "peran": peran, "sumber": _txt(o.get("sumber"), 30),
                "gain_db": _num(o.get("gain_db"), -60, 20), "mute": bool(o.get("mute")),
                "polaritas_terbalik": bool(o.get("polaritas_terbalik")), "delay_ms": _num(o.get("delay_ms"), 0, 1000),
                "hpf_hz": _num(o.get("hpf_hz"), 10, 20000), "hpf_tipe": _tipe(o.get("hpf_tipe")),
                "lpf_hz": _num(o.get("lpf_hz"), 10, 20000), "lpf_tipe": _tipe(o.get("lpf_tipe")),
                "limiter_thr_db": _num(o.get("limiter_thr_db"), -60, 24),
                "limiter_attack_ms": _num(o.get("limiter_attack_ms"), 0, 1000),
                "limiter_release_ms": _num(o.get("limiter_release_ms"), 0, 10000),
                "peq": peq, "catatan": _txt(o.get("catatan"), 120),
            })
        except DSPError as e:
            raise DSPError(f"Output {no}: {e}") from None
    ins = []
    for i, x in enumerate((data.get("input") or [])[:N_IN]):
        if not isinstance(x, dict):
            continue
        try:
            ins.append({"no": i + 1, "nama": _txt(x.get("nama")), "sumber": _txt(x.get("sumber"), 30),
                        "gain_db": _num(x.get("gain_db"), -60, 20)})
        except DSPError as e:
            raise DSPError(f"Input {i + 1}: {e}") from None
    if len({o["no"] for o in outs}) != len(outs):
        raise DSPError("Nomor output dobel.")
    return {"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "output": sorted(outs, key=lambda o: o["no"]),
            "input": ins, "catatan": _txt(data.get("catatan"), 500), "sumber_data": _txt(data.get("sumber_data"), 60)}


def blank() -> dict:
    return {"updated": None, "output": [{"no": n, "nama": "", "peran": "", "sumber": "", "gain_db": None, "mute": False,
                                         "polaritas_terbalik": False, "delay_ms": None, "hpf_hz": None, "hpf_tipe": "",
                                         "lpf_hz": None, "lpf_tipe": "", "limiter_thr_db": None, "limiter_attack_ms": None,
                                         "limiter_release_ms": None, "peq": [], "catatan": ""} for n in range(1, N_OUT + 1)],
            "input": [{"no": n, "nama": "", "sumber": "", "gain_db": None} for n in range(1, N_IN + 1)],
            "catatan": "", "sumber_data": ""}


# ---------- file preset ----------
def inspect(raw: bytes) -> dict:
    """Kenali jenis file tanpa mengklaim bisa membacanya."""
    info: dict = {"ukuran": len(raw), "sha256": hashlib.sha256(raw).hexdigest()[:16]}
    sample = raw[:65536]
    try:
        text = sample.decode("utf-8")
        printable = sum(c.isprintable() or c in "\r\n\t" for c in text) / max(1, len(text))
    except UnicodeDecodeError:
        text, printable = None, 0.0
    if text is None:
        try:
            text = sample.decode("utf-16")
            printable = sum(c.isprintable() or c in "\r\n\t" for c in text) / max(1, len(text))
        except UnicodeDecodeError:
            text = None
    if text is not None and printable > 0.95:
        s = text.lstrip()
        is_ini = bool(re.search(r"^\s*\[[^\]\n]+\]\s*$", s, re.M)) and bool(re.search(r"^\s*[^=\n\[]+=", s, re.M))
        jenis = "xml" if s.startswith("<") else "ini" if is_ini else "json" if s[:1] in "{[" else "teks"
        lines = text.splitlines()
        info.update(jenis=jenis, teks=True, cuplikan="\n".join(lines[:60])[:6000], baris=len(lines),
                    angka=len(re.findall(r"-?\d+(?:\.\d+)?", text)))
    else:
        info.update(jenis="biner", teks=False, cuplikan=" ".join(f"{b:02x}" for b in raw[:256]),
                    tanda_awal=raw[:8].hex())
    return info


class PresetStore:
    def __init__(self, data: Path):
        self.dir = data / "dsp" / "presets"

    def save(self, b64: str, name: str) -> dict:
        try:
            raw = base64.b64decode(b64 or "", validate=True)
        except ValueError:
            raise DSPError("File rusak saat diunggah.") from None
        if not raw:
            raise DSPError("File kosong.")
        if len(raw) > MAX_PRESET_BYTES:
            raise DSPError("File terlalu besar (maks 5 MB).")
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(str(name or "preset")).name)[:80] or "preset"
        pid = time.strftime("preset-%Y%m%d-%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        n = 1
        while (self.dir / f"{pid}.bin").exists():
            n += 1
            pid = time.strftime("preset-%Y%m%d-%H%M%S") + f"-{n}"
        (self.dir / f"{pid}.bin").write_bytes(raw)
        rec = {"id": pid, "nama_file": safe, "diunggah": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "info": inspect(raw)}
        (self.dir / f"{pid}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        return rec

    def get(self, pid: str) -> tuple[dict, bytes]:
        if not re.fullmatch(r"preset-\d{8}-\d{6}(-\d+)?", pid or ""):
            raise DSPError("Preset tidak dikenal.")
        try:
            return (json.loads((self.dir / f"{pid}.json").read_text(encoding="utf-8")),
                    (self.dir / f"{pid}.bin").read_bytes())
        except (OSError, ValueError):
            raise DSPError("Preset tidak ditemukan.") from None

    def list(self) -> list[dict]:
        out = []
        for f in sorted(self.dir.glob("preset-*.json"), reverse=True):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                rec["info"] = {k: v for k, v in rec["info"].items() if k != "cuplikan"}
                out.append(rec)
            except (OSError, ValueError):
                continue
        return out


def load(path: Path) -> dict | None:
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def save(path: Path, data: dict) -> dict:
    clean = validate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return clean


# ---------- saran program untuk sistem ----------
def _f(id_, sev, masalah, penyebab, data, uji, rekomendasi, risiko, usulan=None):
    return {"id": id_, "severity": sev, "channel": "DSP", "ch": None, "kategori": "dsp", "sumber": "program",
            "masalah": masalah, "penyebab": penyebab, "data": data, "uji": uji, "rekomendasi": rekomendasi,
            "risiko": risiko, "usulan": usulan or []}


def _name(o):
    return f"Output {o['no']}" + (f" ({o['nama']})" if o["nama"] else f" ({PERAN[o['peran']]})")


def analyze(dsp: dict | None) -> list[dict]:
    if not dsp:
        return []
    outs = [o for o in dsp["output"] if o["peran"] not in ("", "tidak_dipakai")]
    mains = [o for o in outs if o["peran"] in ("utama_kiri", "utama_kanan")]
    subs = [o for o in outs if o["peran"] == "sub"]
    out: list[dict] = []
    if not outs:
        return [_f("dsp-peran-kosong", "info", "Peran output DSP belum diisi.",
                   "Tanpa peran (utama/sub/monitor), program tidak bisa menilai crossover dan keseimbangan sistem.",
                   "Semua output berperan '(belum diisi)'.", "Lihat di software Protea output mana ke speaker mana.",
                   "Isi peran tiap output di halaman Data DSP.", "Tidak ada risiko ke suara.")]
    # crossover utama vs sub
    for m in mains:
        if m["hpf_hz"] is None:
            target = subs[0]["lpf_hz"] if subs and subs[0]["lpf_hz"] else 100.0
            out.append(_f(f"dsp-utama-tanpa-hpf-o{m['no']}", "perhatian", f"{_name(m)} tanpa high-pass (HPF).",
                          "Speaker utama ikut menerima nada sangat rendah yang seharusnya dikerjakan sub: bass menumpuk "
                          "(boomy), speaker utama cepat terdistorsi, dan headroom berkurang.",
                          f"HPF output {m['no']} kosong; subwoofer {'ada' if subs else 'tidak tercatat'}.",
                          "Bandingkan suara bass dan kejernihan vokal dengan HPF diaktifkan sementara (di luar ibadah).",
                          f"Aktifkan HPF di sekitar {target:.0f} Hz (titik awal, sejajar dengan low-pass sub), tipe LR24.",
                          "Bass bisa terasa berkurang bila sub belum cukup keras; atur level sub setelahnya, ukur bila bisa.",
                          [{"alamat": f"dsp:out{m['no']}/hpf_hz", "sekarang": None, "usulan": target,
                            "teks": f"{_name(m)}: HPF → ≈{target:.0f} Hz (LR24)"}]))
        for s in subs:
            if m["hpf_hz"] is None or s["lpf_hz"] is None:
                continue
            if s["lpf_hz"] > m["hpf_hz"] * XO_TOL:
                out.append(_f(f"dsp-xo-overlap-o{m['no']}-o{s['no']}", "perhatian",
                              f"Crossover tumpang tindih: sub sampai ≈{s['lpf_hz']:.0f} Hz, {_name(m)} mulai ≈{m['hpf_hz']:.0f} Hz.",
                              "Di antara dua frekuensi itu sub dan speaker utama sama-sama berbunyi, sehingga nada rendah "
                              "menumpuk (salah satu penyebab umum bass boomy) dan bisa saling meniadakan bila fase/delay tidak pas.",
                              f"Sub LPF {s['lpf_hz']:.0f} Hz ({s['lpf_tipe'] or 'tipe ?'}), utama HPF {m['hpf_hz']:.0f} Hz ({m['hpf_tipe'] or 'tipe ?'}).",
                              "Ukur atau dengarkan dari kursi tengah dengan musik bass; bandingkan setelah crossover disamakan.",
                              f"Samakan titik crossover: LPF sub ≈ HPF utama (≈{m['hpf_hz']:.0f} Hz), tipe sama (mis. LR24). "
                              "Lalu cek polaritas/delay sub dengan pengukuran (Tahap 2).",
                              "Bass bisa berubah karakter; lakukan di luar ibadah dan simpan preset lama dulu.",
                              [{"alamat": f"dsp:out{s['no']}/lpf_hz", "sekarang": s["lpf_hz"], "usulan": m["hpf_hz"],
                                "teks": f"{_name(s)}: LPF ≈{s['lpf_hz']:.0f} → ≈{m['hpf_hz']:.0f} Hz"}]))
            elif m["hpf_hz"] > s["lpf_hz"] * XO_TOL:
                out.append(_f(f"dsp-xo-lubang-o{m['no']}-o{s['no']}", "perhatian",
                              f"Ada celah crossover: sub berhenti ≈{s['lpf_hz']:.0f} Hz, {_name(m)} baru mulai ≈{m['hpf_hz']:.0f} Hz.",
                              "Frekuensi di antaranya tidak dikerjakan dengan baik oleh keduanya: bass atas/low-mid terasa "
                              "kosong, lalu operator cenderung menambah EQ bass di mixer sehingga suara jadi tidak seimbang.",
                              f"Sub LPF {s['lpf_hz']:.0f} Hz, utama HPF {m['hpf_hz']:.0f} Hz.",
                              "Dengarkan bass gitar/keys di kursi tengah; ukur respons bila bisa.",
                              f"Naikkan LPF sub mendekati HPF utama (≈{m['hpf_hz']:.0f} Hz), atau turunkan HPF utama bila speaker mampu.",
                              "Sub bisa mulai terdengar 'berbicara' bila terlalu tinggi; jangan melewati ±120 Hz tanpa pengukuran.",
                              [{"alamat": f"dsp:out{s['no']}/lpf_hz", "sekarang": s["lpf_hz"], "usulan": m["hpf_hz"],
                                "teks": f"{_name(s)}: LPF ≈{s['lpf_hz']:.0f} → ≈{m['hpf_hz']:.0f} Hz"}]))
    # limiter
    for o in mains + subs:
        if o["limiter_thr_db"] is None:
            out.append(_f(f"dsp-limiter-o{o['no']}", "info", f"Limiter {_name(o)} belum tercatat.",
                          "Limiter melindungi speaker dari puncak berlebih; tanpa data, program tidak tahu apakah speaker terlindungi.",
                          "limiter_thr_db kosong di Data DSP.",
                          "Lihat halaman limiter di software Protea dan isi threshold/attack/release.",
                          "Isi data limiter. Nilai yang tepat bergantung spesifikasi speaker/amplifier, jadi program tidak mengusulkan angka.",
                          "Tidak ada risiko ke suara; mengubah limiter tanpa spesifikasi berisiko merusak speaker."))
    # boost EQ sistem
    for o in mains + [x for x in outs if x["peran"] == "monitor"]:
        for i, b in enumerate(o["peq"]):
            if b["db"] > BOOST_MAX_DB:
                where = f"{b['hz'] / 1000:.1f} kHz" if b["hz"] >= 1000 else f"{b['hz']:.0f} Hz"
                out.append(_f(f"dsp-boost-o{o['no']}-b{i + 1}", "perhatian",
                              f"EQ sistem {_name(o)} di-boost +{b['db']:.1f} dB di ≈{where}.",
                              "Boost besar di EQ sistem menaikkan frekuensi itu untuk SEMUA mic sekaligus: lebih cepat "
                              "feedback, dan terdengar tajam bila di 2–5 kHz.",
                              f"PEQ band {i + 1}: {where}, +{b['db']:.1f} dB, Q {b['q']}.",
                              "Dengarkan vokal dan cek feedback saat boost dikurangi bertahap.",
                              "Kurangi boost ke sekitar +3 dB; perbaiki kekurangan dengan penempatan speaker atau EQ channel.",
                              "Bagian itu terasa berkurang; lakukan bertahap.",
                              [{"alamat": f"dsp:out{o['no']}/peq{i + 1}_db", "sekarang": b["db"], "usulan": 3.0,
                                "teks": f"{_name(o)} PEQ {i + 1} (≈{where}): +{b['db']:.1f} → +3.0 dB"}]))
    # kiri vs kanan
    left = next((o for o in mains if o["peran"] == "utama_kiri"), None)
    right = next((o for o in mains if o["peran"] == "utama_kanan"), None)
    if left and right:
        diffs = []
        for k, lab, tol in (("gain_db", "gain", 1.5), ("delay_ms", "delay", 1.0), ("hpf_hz", "HPF", 5.0)):
            a, b = left[k], right[k]
            if a is not None and b is not None and abs(a - b) > tol:
                diffs.append(f"{lab} {a:g} vs {b:g}")
        if len(left["peq"]) != len(right["peq"]):
            diffs.append(f"jumlah band EQ {len(left['peq'])} vs {len(right['peq'])}")
        if left["polaritas_terbalik"] != right["polaritas_terbalik"]:
            diffs.append("polaritas berbeda")
        if diffs:
            out.append(_f("dsp-kiri-kanan", "info", "Setting speaker utama kiri dan kanan berbeda.",
                          "Bisa disengaja (posisi speaker/pilar berbeda), tapi bisa juga sisa setelan lama yang membuat "
                          "suara condong ke satu sisi atau berbeda karakter.",
                          "Perbedaan: " + "; ".join(diffs) + ".",
                          "Dengarkan dari tengah ruangan dengan suara mono (mis. khotbah): apakah terasa di tengah?",
                          "Pastikan perbedaan itu disengaja; bila tidak, samakan.",
                          "Perbedaan yang disengaja (mis. karena pilar) jangan dihapus tanpa pengukuran."))
    # polaritas & delay sub
    for s in subs:
        for m in mains:
            if s["polaritas_terbalik"] != m["polaritas_terbalik"]:
                out.append(_f(f"dsp-polaritas-o{s['no']}-o{m['no']}", "info",
                              f"Polaritas {_name(s)} berbeda dengan {_name(m)}.",
                              "Bisa disengaja untuk menyamakan fase di titik crossover, atau tidak sengaja sehingga bass "
                              "saling meniadakan di area crossover.",
                              "polaritas_terbalik berbeda di Data DSP.",
                              "Ukur respons di kursi tengah dengan dua posisi polaritas (Tahap 2); pilih yang bass-nya lebih penuh.",
                              "Tentukan dengan pengukuran, bukan tebakan.", "Salah polaritas membuat bass tipis di crossover."))
                break
    if subs and mains and all((o["delay_ms"] or 0) == 0 for o in subs + mains):
        out.append(_f("dsp-delay", "info", "Delay sub dan speaker utama semuanya 0 ms.",
                      "Bila jarak sub dan speaker utama ke jemaat berbeda, bunyinya tiba tidak bersamaan sehingga bass "
                      "di crossover bisa tipis atau menumpuk.",
                      "delay_ms = 0 di semua output utama dan sub.",
                      "Ukur dengan mic di kursi tengah (impulse response, Tahap 2).",
                      "Atur delay berdasarkan pengukuran; program tidak mengusulkan angka tanpa ukur.",
                      "Delay yang salah bisa membuat bass lebih buruk."))
    return out


def summary(dsp: dict | None) -> list[dict]:
    """Ringkas untuk dikirim ke Gemini."""
    if not dsp:
        return []
    keep = ("no", "nama", "peran", "sumber", "gain_db", "mute", "polaritas_terbalik", "delay_ms", "hpf_hz", "hpf_tipe",
            "lpf_hz", "lpf_tipe", "limiter_thr_db", "limiter_attack_ms", "limiter_release_ms", "peq")
    return [{k: o[k] for k in keep if o.get(k) not in (None, "", [], False) or k in ("no", "peran")}
            for o in dsp["output"] if o["peran"] != "tidak_dipakai"]
