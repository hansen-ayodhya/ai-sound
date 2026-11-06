"""Konsultan AI (Google Gemini) — opsional, dipanggil hanya saat operator menekan tombol.

Prinsip:
- Aturan lokal tetap dasar; Gemini hanya menggabungkan, memprioritaskan, dan menjelaskan.
- Yang dikirim: RINGKASAN angka (peran channel, setting dalam dB/Hz perkiraan, temuan aturan,
  peringatan live). TIDAK dikirim: audio, IP jaringan, PIN, API key.
- Jawaban wajib JSON terstruktur dan DIPERIKSA di sini: usulan hanya untuk parameter yang
  diizinkan dan dalam rentang wajar; selebihnya dibuang. Tidak pernah diterapkan ke mixer.
- API key hanya disimpan di data/gemini.json di komputer ini; tidak pernah dikirim ke browser.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import channels, convert

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 90
MAX_QUESTION = 600


class AIError(RuntimeError):
    pass


# ---------- penyimpanan kunci ----------
def load_config(path: Path) -> dict:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(path)


def public_status(cfg: dict) -> dict:
    key = cfg.get("key") or ""
    return {"configured": bool(key), "model": cfg.get("model") or None,
            "key_hint": ("…" + key[-4:]) if len(key) >= 8 else None, "models": cfg.get("models") or []}


def valid_key_format(key: str) -> bool:
    """Longgar: karakter cetak tanpa spasi, 20–300 karakter (format kunci Google bisa berubah)."""
    return bool(re.fullmatch(r"[\x21-\x7e]{20,300}", key or ""))


# ---------- HTTP ----------
def _request(url: str, key: str, body: dict | None = None, base: str = API_BASE) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + url, data=data, method="GET" if body is None else "POST")
    req.add_header("x-goog-api-key", key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        if e.code in (400, 401, 403) and "API key" in detail:
            raise AIError("API key ditolak Google. Periksa lagi kuncinya di Google AI Studio.") from None
        if e.code == 429:
            raise AIError("Batas pemakaian Gemini tercapai (kuota/tier gratis). Coba lagi nanti.") from None
        raise AIError(f"Gemini menolak permintaan ({e.code}): {detail[:300]}") from None
    except urllib.error.URLError as e:
        raise AIError(f"Tidak bisa menghubungi Gemini. Cek koneksi internet komputer ini. ({e.reason})") from None
    except TimeoutError:
        raise AIError("Gemini terlalu lama menjawab. Coba lagi.") from None


def list_models(key: str, base: str = API_BASE) -> list[str]:
    res = _request("/models?pageSize=1000", key, base=base)
    out = []
    for m in res.get("models", []):
        if "generateContent" in (m.get("supportedGenerationMethods") or []):
            name = str(m.get("name", "")).removeprefix("models/")
            if name.startswith("gemini"):
                out.append(name)
    return out


_SKIP = re.compile(r"(tts|image|live|audio|embed|vision|robotics|computer|native|exp)", re.I)


def pick_model(models: list[str]) -> str | None:
    """Pilih model Flash terbaru yang stabil (bukan lite/preview) bila ada."""
    def version(n: str) -> float:
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0

    cands = [m for m in models if not _SKIP.search(m)]
    for pref in (lambda m: "flash" in m and "lite" not in m and "preview" not in m,
                 lambda m: "flash" in m and "preview" not in m,
                 lambda m: "flash" in m,
                 lambda m: True):
        pool = [m for m in cands if pref(m)]
        if pool:
            return sorted(pool, key=lambda m: (version(m), -len(m)), reverse=True)[0]
    return None


# ---------- konteks yang dikirim ----------
SYSTEM = """Anda adalah sound engineer gereja profesional yang membantu operator mixer Midas MR18.
Konteks tetap:
- Sistem: Midas MR18 -> speaker management Ashly Protea 4.8SP -> 2 speaker utama aktif (Mickey) + 2 subwoofer aktif; 2 monitor panggung.
- Ruangan: plafon relatif rendah, pilar besar di tengah, banyak permukaan keras yang memantul.
- Keluhan operator: vokal kurang jelas, sering feedback/mendenging, bass boomy, musik kadang terlalu keras, vokal tajam/menusuk, khotbah kurang jelas, mixing tidak konsisten.
- Konfigurasi DSP Ashly dan pengukuran audio/ruangan BELUM tersedia. Konversi nilai mixer ke dB/Hz masih PERKIRAAN.
Aturan menjawab:
1. Hanya berdasarkan data yang diberikan. Jangan mengarang pengukuran, frekuensi resonansi, atau model perangkat.
2. Cari akar masalah dan urutkan prioritas; jangan hanya menyarankan naik/turun EQ.
3. Setiap rekomendasi memuat: masalah, kemungkinan penyebab, data pembuktian (dari data yang ada), langkah uji, rekomendasi, risiko perubahan, dan tingkat keyakinan (rendah/sedang/tinggi).
4. Usulan angka hanya untuk parameter yang tersedia di skema dan hanya bila didukung data; bila data kurang, kosongkan usulan dan tulis data apa yang dibutuhkan di 'butuh_data'.
5. Ingat: perubahan dilakukan operator sendiri; jangan menyarankan perubahan routing, master, atau limiter tanpa data DSP.
6. Bahasa Indonesia yang sederhana dan praktis untuk operator gereja."""

PARAMS = (["hpf_on", "hpf_hz", "eq_on", "comp_on", "comp_thr_db", "comp_ratio", "gate_on", "gate_thr_db", "fader_db"]
          + [f"eq{b}_{k}" for b in range(1, 5) for k in ("hz", "db", "q")]
          + [f"send_bus{b}_db" for b in range(1, 7)] + [f"send_fx{f}_db" for f in range(1, 5)])

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "ringkasan": {"type": "STRING"},
        "jawaban": {"type": "STRING"},
        "prioritas": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "judul": {"type": "STRING"}, "ch": {"type": "INTEGER", "nullable": True},
                "masalah": {"type": "STRING"}, "penyebab": {"type": "STRING"}, "data": {"type": "STRING"},
                "uji": {"type": "STRING"}, "rekomendasi": {"type": "STRING"}, "risiko": {"type": "STRING"},
                "keyakinan": {"type": "STRING", "enum": ["rendah", "sedang", "tinggi"]},
                "usulan": {"type": "ARRAY", "items": {
                    "type": "OBJECT",
                    "properties": {"parameter": {"type": "STRING", "enum": PARAMS}, "nilai": {"type": "NUMBER"}},
                    "required": ["parameter", "nilai"]}},
            },
            "required": ["judul", "masalah", "penyebab", "data", "uji", "rekomendasi", "risiko", "keyakinan"]}},
        "butuh_data": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["ringkasan", "prioritas", "butuh_data"],
}


def _r(v, nd=1):
    return None if v is None else (round(v, nd) if v != float("-inf") else "-inf")


def channel_summary(dump: dict, profile: dict | None) -> list[dict]:
    v = dump.get("values", {})

    def g(a):
        x = v.get(a)
        return x[0] if x else None

    out = []
    for n in range(1, 17):
        c, h = f"/ch/{n:02d}", f"/headamp/{n:02d}"
        if not any(k.startswith(c + "/") and v[k] is not None for k in v):
            continue
        src = channels.source_for(profile, n)
        item: dict = {"ch": n, "nama_mixer": g(c + "/config/name") or ""}
        if src:
            item.update(sumber=src["nama"], jenis=channels.TYPES[src["jenis"]]["label"], koneksi=src["koneksi"] or None,
                        mic=src["mic"] or None)
        if g(c + "/mix/on") is not None:
            item["aktif"] = g(c + "/mix/on") == 1
        if g(c + "/mix/fader") is not None:
            item["fader_db"] = _r(convert.fader_db(g(c + "/mix/fader")))
        if g(h + "/phantom") is not None:
            item["phantom_48v"] = g(h + "/phantom") == 1
        if g(c + "/preamp/hpon") is not None:
            item["low_cut"] = {"on": g(c + "/preamp/hpon") == 1,
                               "hz": _r(convert.hpf_hz(g(c + "/preamp/hpf")), 0) if g(c + "/preamp/hpf") is not None else None}
        if g(c + "/eq/on") is not None:
            bands = []
            for b in range(1, 5):
                f, gn, q = g(f"{c}/eq/{b}/f"), g(f"{c}/eq/{b}/g"), g(f"{c}/eq/{b}/q")
                if f is not None and gn is not None:
                    bands.append({"band": b, "hz": _r(convert.eq_hz(f), 0), "db": _r(convert.eq_gain_db(gn)),
                                  "q": _r(convert.eq_q(q)) if q is not None else None})
            item["eq"] = {"on": g(c + "/eq/on") == 1, "band": bands}
        if g(c + "/dyn/on") is not None:
            item["kompresor"] = {"on": g(c + "/dyn/on") == 1,
                                 "thr_db": _r(convert.comp_thr_db(g(c + "/dyn/thr"))) if g(c + "/dyn/thr") is not None else None,
                                 "rasio": convert.ratio_value(g(c + "/dyn/ratio"))}
        if g(c + "/gate/on") is not None:
            item["gate"] = {"on": g(c + "/gate/on") == 1,
                            "thr_db": _r(convert.gate_thr_db(g(c + "/gate/thr"))) if g(c + "/gate/thr") is not None else None}
        sends = {}
        for b in range(1, 7):
            x = g(f"{c}/mix/{b:02d}/level")
            if x is not None:
                sends[f"bus{b}"] = _r(convert.fader_db(x))
        for fx in range(1, 5):
            x = g(f"{c}/mix/{6 + fx:02d}/level")
            if x is not None:
                sends[f"fx{fx}"] = _r(convert.fader_db(x))
        if sends:
            item["kiriman_db"] = sends
        out.append(item)
    return out


SITUASI = {"umum": "ibadah umum", "lagu": "sesi pujian/lagu", "khotbah": "khotbah", "soundcheck": "soundcheck sebelum ibadah"}


def build_prompt(dump: dict | None, profile: dict | None, findings: list[dict], live: dict | None,
                 situasi: str, pertanyaan: str) -> str:
    ctx = {
        "situasi": SITUASI.get(situasi, "ibadah umum"),
        "pertanyaan_operator": (pertanyaan or "").strip()[:MAX_QUESTION] or None,
        "konversi_terverifikasi": convert.VERIFIED,
        "audit_dibuat": (dump or {}).get("created"),
        "channel": channel_summary(dump, profile) if dump else [],
        "sumber_tanpa_channel": [s["nama"] for s in (profile or {}).get("sources", []) if not s.get("ch")],
        "temuan_aturan_lokal": [{"id": f["id"], "ch": f.get("ch"), "tingkat": f["severity"], "masalah": f["masalah"],
                                 "usulan": [u["teks"] for u in f.get("usulan", [])]} for f in findings],
    }
    if live:
        ctx["pantau_live"] = {
            "sedang_berjalan": live.get("running"),
            "peringatan_terakhir": [{"jam": a["time"], "jenis": a["kind"], "label": a["label"], "level_db": a["level_db"]}
                                    for a in (live.get("alerts") or [])[-30:]],
            "catatan": "pemetaan meter ke channel masih perkiraan",
        }
    return ("Data sistem saat ini (JSON):\n" + json.dumps(ctx, ensure_ascii=False)
            + "\n\nBerikan analisis sesuai aturan. Bila ada pertanyaan operator, jawab di 'jawaban'.")


# ---------- memeriksa jawaban ----------
def _between(x, lo, hi):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi


def to_usulan(ch: int, parameter: str, nilai, dump: dict) -> dict | None:
    """Ubah usulan Gemini menjadi alamat + nilai mentah. None bila tidak lolos pemeriksaan."""
    c = f"/ch/{ch:02d}"
    cur = dump.get("values", {}).get

    def now(a):
        x = cur(a)
        return x[0] if x else None

    p = parameter
    if p in ("hpf_on", "eq_on", "comp_on", "gate_on"):
        if nilai not in (0, 1, True, False):
            return None
        a = {"hpf_on": f"{c}/preamp/hpon", "eq_on": f"{c}/eq/on", "comp_on": f"{c}/dyn/on", "gate_on": f"{c}/gate/on"}[p]
        name = {"hpf_on": "Low cut", "eq_on": "EQ", "comp_on": "Kompresor", "gate_on": "Gate"}[p]
        return {"alamat": a, "sekarang": now(a), "usulan": int(bool(nilai)), "teks": f"{name}: → {'ON' if nilai else 'OFF'}"}
    if p == "hpf_hz" and _between(nilai, 20, 400):
        a = f"{c}/preamp/hpf"
        return {"alamat": a, "sekarang": now(a), "usulan": round(convert.hpf_raw(nilai), 4), "teks": f"Frekuensi low cut: → ≈{nilai:.0f} Hz"}
    m = re.fullmatch(r"eq([1-4])_(hz|db|q)", p)
    if m:
        b, k = m.group(1), m.group(2)
        if k == "hz" and _between(nilai, 20, 20000):
            a = f"{c}/eq/{b}/f"
            return {"alamat": a, "sekarang": now(a), "usulan": round(convert.eq_raw_hz(nilai), 4), "teks": f"EQ band {b} frekuensi: → ≈{nilai:.0f} Hz"}
        if k == "db" and _between(nilai, -15, 15):
            a = f"{c}/eq/{b}/g"
            return {"alamat": a, "sekarang": now(a), "usulan": round(convert.eq_gain_raw(nilai), 4), "teks": f"EQ band {b}: → ≈{nilai:+.1f} dB"}
        if k == "q" and _between(nilai, 0.3, 10):
            a = f"{c}/eq/{b}/q"
            raw = math.log(nilai / 10.0) / math.log(0.3 / 10.0)
            return {"alamat": a, "sekarang": now(a), "usulan": round(max(0.0, min(1.0, raw)), 4), "teks": f"EQ band {b} Q: → ≈{nilai:.1f}"}
        return None
    if p == "comp_thr_db" and _between(nilai, -60, 0):
        a = f"{c}/dyn/thr"
        return {"alamat": a, "sekarang": now(a), "usulan": round(convert.comp_thr_raw(nilai), 4), "teks": f"Threshold kompresor: → ≈{nilai:.0f} dB"}
    if p == "comp_ratio" and _between(nilai, 1.1, 100):
        a = f"{c}/dyn/ratio"
        i = convert.ratio_index_for(nilai)
        return {"alamat": a, "sekarang": now(a), "usulan": i, "teks": f"Rasio kompresor: → {convert.RATIOS[i]:g}:1"}
    if p == "gate_thr_db" and _between(nilai, -80, 0):
        a = f"{c}/gate/thr"
        return {"alamat": a, "sekarang": now(a), "usulan": round(convert.gate_thr_raw(nilai), 4), "teks": f"Threshold gate: → ≈{nilai:.0f} dB"}
    m = re.fullmatch(r"(fader|send_bus([1-6])|send_fx([1-4]))_db", p)
    if m and _between(nilai, -90, 10):
        if p == "fader_db":
            a, name = f"{c}/mix/fader", "Fader"
        elif m.group(2):
            a, name = f"{c}/mix/{int(m.group(2)):02d}/level", f"Kiriman ke Bus {m.group(2)}"
        else:
            a, name = f"{c}/mix/{6 + int(m.group(3)):02d}/level", f"Kiriman ke FX {m.group(3)}"
        txt = "mati (−∞)" if nilai <= -90 else f"≈{nilai:.0f} dB"
        return {"alamat": a, "sekarang": now(a), "usulan": round(convert.fader_raw(nilai), 4), "teks": f"{name}: → {txt}"}
    return None


def _s(x, n=1500) -> str:
    return str(x if x is not None else "").strip()[:n]


def sanitize(resp: dict, dump: dict | None) -> dict:
    """Saring jawaban Gemini menjadi struktur aman untuk ditampilkan."""
    if not isinstance(resp, dict):
        raise AIError("Jawaban Gemini bukan format yang diharapkan.")
    items, dropped = [], 0
    for i, p in enumerate(resp.get("prioritas") or []):
        if not isinstance(p, dict) or i >= 12:
            continue
        ch = p.get("ch")
        ch = ch if isinstance(ch, int) and not isinstance(ch, bool) and 1 <= ch <= 16 else None
        usulan = []
        for u in (p.get("usulan") or [])[:8]:
            if not isinstance(u, dict) or ch is None or not dump:
                dropped += 1
                continue
            ok = to_usulan(ch, str(u.get("parameter", "")), u.get("nilai"), dump)
            if ok:
                usulan.append(ok)
            else:
                dropped += 1
        keyakinan = p.get("keyakinan") if p.get("keyakinan") in ("rendah", "sedang", "tinggi") else "rendah"
        items.append({
            "id": f"gemini-{i + 1}" + (f"-ch{ch:02d}" if ch else ""), "ch": ch, "sumber": "gemini",
            "severity": "perhatian" if keyakinan == "tinggi" else "info", "keyakinan": keyakinan,
            "channel": f"Ch {ch:02d}" if ch else "-", "masalah": _s(p.get("judul") or p.get("masalah"), 200),
            "penyebab": _s(p.get("penyebab")), "data": _s(p.get("data")), "uji": _s(p.get("uji")),
            "rekomendasi": _s(p.get("rekomendasi")), "risiko": _s(p.get("risiko")), "usulan": usulan,
        })
    return {"ringkasan": _s(resp.get("ringkasan"), 2000), "jawaban": _s(resp.get("jawaban"), 3000),
            "prioritas": items, "butuh_data": [_s(x, 300) for x in (resp.get("butuh_data") or [])[:10]],
            "usulan_dibuang": dropped}


def _generate(cfg: dict, parts: list, schema: dict, base: str) -> dict:
    key, model = cfg.get("key"), cfg.get("model")
    if not key or not model:
        raise AIError("Gemini belum diatur. Isi API key di Beranda (dari laptop/komputer utama).")
    if not re.fullmatch(r"[A-Za-z0-9.\-_]{3,80}", model):
        raise AIError("Nama model tidak valid.")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json", "responseSchema": schema},
    }
    url = f"/models/{model}:generateContent"
    try:
        res = _request(url, key, body, base=base)
    except AIError as e:
        if "schema" in str(e).lower():
            body["generationConfig"].pop("responseSchema")  # model tidak mendukung skema: minta JSON saja
            res = _request(url, key, body, base=base)
        else:
            raise
    try:
        text = res["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        reason = (res.get("promptFeedback") or {}).get("blockReason") or (res.get("candidates") or [{}])[0].get("finishReason")
        raise AIError(f"Gemini tidak memberi jawaban ({reason or 'tanpa alasan'}).") from None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        raise AIError("Jawaban Gemini bukan JSON yang valid. Coba lagi.") from None


def analyze(cfg: dict, dump: dict | None, profile: dict | None, findings: list[dict], live: dict | None,
            situasi: str = "umum", pertanyaan: str = "", base: str = API_BASE) -> dict:
    parsed = _generate(cfg, [{"text": build_prompt(dump, profile, findings, live, situasi, pertanyaan)}],
                       RESPONSE_SCHEMA, base)
    out = sanitize(parsed, dump)
    out.update(model=cfg.get("model"), dibuat=time.strftime("%Y-%m-%dT%H:%M:%S%z"), situasi=situasi,
               pertanyaan=_s(pertanyaan, MAX_QUESTION))
    return out


# ---------- menilai klip suara ----------
ASPEK = ["kejelasan_vokal", "kejelasan_khotbah", "boomy", "tajam", "gema", "feedback",
         "keseimbangan_musik_vokal", "terlalu_keras"]
NILAI = ["baik", "cukup", "kurang", "tidak_dapat_dinilai"]
CLIP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        **RESPONSE_SCHEMA["properties"],
        "penilaian": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"aspek": {"type": "STRING", "enum": ASPEK}, "nilai": {"type": "STRING", "enum": NILAI},
                           "alasan": {"type": "STRING"}},
            "required": ["aspek", "nilai", "alasan"]}},
    },
    "required": ["ringkasan", "penilaian", "prioritas", "butuh_data"],
}


def clip_prompt(rec: dict, dump: dict | None, profile: dict | None, findings: list[dict]) -> str:
    ctx = {
        "klip": {"posisi_rekam": rec["meta"].get("posisi") or None, "situasi": SITUASI.get(rec["meta"]["situasi"]),
                 "catatan_operator": rec["meta"].get("catatan") or None, "durasi_detik": rec["audio"]["seconds"],
                 "perekam": "kemungkinan mikrofon HP (tidak dikalibrasi; mewarnai suara)"},
        "analisis_lokal": rec.get("analisis") or {},
        "channel": channel_summary(dump, profile) if dump else [],
        "temuan_aturan_lokal": [{"ch": f.get("ch"), "masalah": f["masalah"]} for f in findings][:30],
        "konversi_terverifikasi": convert.VERIFIED,
    }
    return ("Dengarkan klip audio terlampir (suara di ruangan gereja) dan nilai sesuai aturan.\n"
            "Angka 'analisis_lokal' dihitung program dari klip yang sama: band = level relatif per pita 1/3 oktaf (dB), "
            "groups = rata-rata per kelompok frekuensi relatif terhadap pita tengah, peaks = puncak sempit yang menonjol "
            "(persisten = fraksi waktu muncul; tinggi = kemungkinan dengung/feedback). Pakai angka ini untuk frekuensi; "
            "jangan menebak frekuensi hanya dari pendengaran.\n"
            "Isi 'penilaian' untuk setiap aspek yang relevan (pakai 'tidak_dapat_dinilai' bila klip tidak memuatnya), "
            "lalu 'prioritas' dengan akar masalah dan langkah uji. Usulan angka hanya bila channel penyebabnya jelas.\n\n"
            "Data (JSON):\n" + json.dumps(ctx, ensure_ascii=False))


def analyze_clip(cfg: dict, rec: dict, wav: bytes, dump: dict | None, profile: dict | None, findings: list[dict],
                 base: str = API_BASE) -> dict:
    import base64
    parts = [{"text": clip_prompt(rec, dump, profile, findings)},
             {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode("ascii")}}]
    parsed = _generate(cfg, parts, CLIP_SCHEMA, base)
    out = sanitize(parsed, dump)
    pen = []
    for x in (parsed.get("penilaian") or [])[:12]:
        if isinstance(x, dict) and x.get("aspek") in ASPEK and x.get("nilai") in NILAI:
            pen.append({"aspek": x["aspek"], "nilai": x["nilai"], "alasan": _s(x.get("alasan"), 400)})
    out.update(penilaian=pen, model=cfg.get("model"), dibuat=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               situasi=rec["meta"]["situasi"], klip=rec["id"])
    for p in out["prioritas"]:
        p["id"] = p["id"].replace("gemini-", "gemini-klip-", 1)
    return out
