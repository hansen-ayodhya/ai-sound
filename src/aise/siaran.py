"""Bus siaran (mis. live YouTube lewat USB): ringkasan dan temuan dari setting mixer (hanya membaca).

Batas yang jujur: level SINYAL di bus belum bisa dipantau (pemetaan meter bus belum terbukti) dan level kiriman
channel hanya menunjukkan berapa banyak channel itu dikirim ke bus, bukan seberapa keras suaranya.
"""

from __future__ import annotations

from . import channels, convert

N_BUSES = 6
SEND_OVER_DB = 0.0       # kiriman di atas ini dianggap terlalu panas
FADER_OVER_DB = 0.5
EQ_BOOST_DB = 4.0
BALANCE_GAP_DB = 6.0     # musik/drum lebih keras dari vokal sebanyak ini = vokal berisiko tenggelam


def bus_detail_addresses(n: int) -> list[str]:
    b = f"/bus/{n}"
    out = [f"{b}/dyn/thr", f"{b}/dyn/ratio"]
    for band in range(1, 5):
        out += [f"{b}/eq/{band}/f", f"{b}/eq/{band}/g", f"{b}/eq/{band}/q"]
    return out


def probe_addresses() -> list[str]:
    """Alamat kandidat routing USB (DUGAAN, belum diverifikasi pada MR18). Hanya dibaca; yang tidak dijawab diabaikan."""
    out = [f"/routing/usb/{i:02d}" for i in range(1, 9)]
    out += [f"/outputs/usb/{i:02d}/src" for i in range(1, 9)]
    out += ["/config/routing/usb", "/config/routing/main", "/routing/main/01", "/outputs/main/01/src"]
    return out


def _v(values: dict, addr: str):
    x = values.get(addr)
    return x[0] if x else None


def summary(dump: dict | None, bus: int, profile: dict | None = None) -> dict | None:
    if not dump or not 1 <= bus <= N_BUSES:
        return None
    v, b = dump.get("values", {}), f"/bus/{bus}"
    fad = _v(v, f"{b}/mix/fader")
    bands = []
    for i in range(1, 5):
        f, g = _v(v, f"{b}/eq/{i}/f"), _v(v, f"{b}/eq/{i}/g")
        if f is not None and g is not None:
            bands.append({"band": i, "hz": round(convert.eq_hz(f)), "db": round(convert.eq_gain_db(g), 1)})
    sends = []
    for n in range(1, 17):
        lv = _v(v, f"/ch/{n:02d}/mix/{bus:02d}/level")
        if lv is None:
            continue
        src = channels.source_for(profile, n)
        sends.append({"ch": n, "nama": channels.clean_name(_v(v, f"/ch/{n:02d}/config/name")),
                      "peran": src["nama"] if src else "", "jenis": src["jenis"] if src else "",
                      "aktif": _v(v, f"/ch/{n:02d}/mix/on") != 0, "db": None if lv is None else round(convert.fader_db(lv), 1)})
    sends.sort(key=lambda s: (s["db"] if s["db"] is not None else -999), reverse=True)
    thr, ratio = _v(v, f"{b}/dyn/thr"), _v(v, f"{b}/dyn/ratio")
    return {"bus": bus, "nama": channels.clean_name(_v(v, f"{b}/config/name")), "on": _v(v, f"{b}/mix/on"),
            "fader_db": None if fad is None else round(convert.fader_db(fad), 1), "eq_on": _v(v, f"{b}/eq/on"),
            "eq": bands, "dyn_on": _v(v, f"{b}/dyn/on"),
            "dyn_thr_db": None if thr is None else round(convert.comp_thr_db(thr), 1),
            "dyn_rasio": None if ratio is None else convert.ratio_value(ratio), "kiriman": sends}


def _finding(fid, sev, bus, masalah, penyebab, data, uji, rek, risiko="Rendah."):
    return {"id": fid, "severity": sev, "channel": f"Bus {bus}", "masalah": masalah, "penyebab": penyebab, "data": data,
            "uji": uji, "rekomendasi": rek, "risiko": risiko, "ch": None, "usulan": []}


def findings(dump: dict | None, bus: int, profile: dict | None = None) -> list[dict]:
    s = summary(dump, bus, profile)
    if not s:
        return []
    out = []
    nm = f"Bus {bus}" + (f" ({s['nama']})" if s["nama"] else "")
    if s["on"] == 0:
        out.append(_finding("siaran-mute", "perhatian", bus, f"{nm} sedang di-mute: siaran tidak mendapat suara",
                            "Tombol mute Bus aktif.", "mix/on = 0", "Cek apakah siaran memang sedang dijeda.", "Matikan mute Bus bila siaran harus berjalan."))
    if s["fader_db"] is not None and s["fader_db"] > FADER_OVER_DB:
        out.append(_finding("siaran-fader", "perhatian", bus, f"Fader {nm} di atas 0 dB ({s['fader_db']:+.1f} dB)",
                            "Fader di atas 0 dB menambah level dan memperbesar risiko sinyal USB over (suara pecah di YouTube).",
                            f"Fader Bus: {s['fader_db']:+.1f} dB.", "Putar siaran dan lihat meter di komputer streaming: puncak jangan menyentuh merah.",
                            "Turunkan fader Bus ke sekitar 0 dB atau lebih rendah (ubah manual di M-AIR), lalu atur level di software streaming."))
    boosts = [b for b in s["eq"] if b["db"] > EQ_BOOST_DB]
    if s["eq_on"] == 1 and boosts:
        out.append(_finding("siaran-eq", "info", bus, f"EQ {nm} menaikkan level cukup besar",
                            "Boost EQ pada bus menambah level total dan bisa menyebabkan over.",
                            "; ".join(f"band {b['band']}: {b['hz']} Hz {b['db']:+.1f} dB" for b in boosts),
                            "Bypass EQ Bus sebentar dan bandingkan suara siaran.", "Kurangi boost, atau turunkan fader Bus sebesar boost terbesar."))
    if s["dyn_on"] == 0:
        out.append(_finding("siaran-dyn", "info", bus, f"{nm} tanpa kompresor/pembatas",
                            "Tanpa kompresor, puncak suara bisa menembus batas digital USB dan terdengar pecah di siaran.",
                            "dyn/on = 0", "Nyalakan kompresor Bus lalu putar bagian paling keras ibadah.",
                            "Nyalakan kompresor Bus sebagai pembatas: threshold sekitar -6 sampai -3 dB, rasio tinggi (mis. 10:1 atau lebih), attack cepat. "
                            "Angka kompresor MR18 masih perkiraan; dengarkan hasilnya."))
    active = [x for x in s["kiriman"] if x["aktif"] and x["db"] is not None]
    over = [x for x in active if x["db"] > SEND_OVER_DB]
    if over:
        out.append(_finding("siaran-kiriman", "perhatian", bus, f"Kiriman ke {nm} di atas 0 dB pada {len(over)} channel",
                            "Banyak sinyal dijumlahkan di bus; kiriman di atas 0 dB cepat membuat bus over.",
                            "; ".join(f"{x['nama'] or 'CH %02d' % x['ch']}: {x['db']:+.1f} dB" for x in over),
                            "Putar semua sumber bersamaan, lalu lihat meter Bus di M-AIR.", "Turunkan kiriman channel tersebut (manual di M-AIR)."))
    voice = [x for x in active if channels.TYPES.get(x["jenis"], {}).get("voice")]
    other = [x for x in active if x["jenis"] and not channels.TYPES.get(x["jenis"], {}).get("voice")]
    if voice and other:
        mv, mo = max(voice, key=lambda x: x["db"]), max(other, key=lambda x: x["db"])
        if mo["db"] - mv["db"] >= BALANCE_GAP_DB:
            out.append(_finding("siaran-balance", "perhatian", bus, f"Di {nm}, {mo['peran'] or mo['nama']} jauh lebih keras dari vokal",
                                "Kiriman musik/drum lebih tinggi dari kiriman suara, jadi vokal atau khotbah berisiko tenggelam di siaran.",
                                f"{mo['peran'] or mo['nama']}: {mo['db']:+.1f} dB, {mv['peran'] or mv['nama']}: {mv['db']:+.1f} dB. "
                                "Ini selisih kiriman, bukan level sinyal sebenarnya.",
                                "Dengarkan siaran lewat headphone dari komputer streaming.", f"Turunkan kiriman {mo['peran'] or mo['nama']} atau naikkan kiriman suara (manual di M-AIR)."))
    out.append(_finding("siaran-cek", "info", bus, f"Cek jalur siaran {nm} ke YouTube (di luar mixer)",
                        "Suara pecah sering berasal dari komputer streaming, bukan hanya dari mixer.",
                        "Level sinyal Bus belum bisa dipantau aplikasi ini.",
                        "1) Meter input di software streaming (mis. OBS): puncak sebaiknya sekitar -12 sampai -6 dB, tidak merah. "
                        "2) Sample rate perangkat USB di komputer dan software harus sama (48 kHz). "
                        "3) Matikan peredam bising, pengatur level otomatis, dan efek di Windows/software untuk input ini. "
                        "4) Pastikan sumber USB yang dikirim mixer adalah Bus yang benar (lihat routing USB di M-AIR, nama menu bisa berbeda).",
                        "Naikkan atau turunkan level di software streaming, jangan di fader Main. Target siaran YouTube sekitar -14 LUFS dengan puncak di bawah -1 dB."))
    return out
