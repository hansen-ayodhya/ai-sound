"""Mesin analisa realtime (READ-ONLY): menilai meter tiap frame mengikuti kebiasaan kerja soundman berpengalaman.

Yang dinilai, semuanya dari LEVEL meter dan setting mixer (program tidak mendengar isi suara):
  panas      puncak sering mendekati batas, sebelum sempat clipping        -> turunkan gain
  struktur   gain terlalu rendah sehingga fader dipaksa naik              -> naikkan gain, turunkan fader sama besar
  kompresor  kompresor menyala tetapi threshold jauh di atas puncak sinyal -> turunkan threshold
  feedback   level mic vokal naik lalu menetap nyaris rata (pola dengung)  -> turunkan fader
  tenggelam  perkiraan campuran: jumlah musik menutupi vokal terkeras      -> turunkan musik terkeras

BELUM DIVERIFIKASI pada MR18: pemetaan meter ke channel, titik ambil meter (dianggap sebelum fader),
serta konversi fader dan threshold kompresor. Karena itu setiap temuan menyebut dasar datanya.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque

from . import balance, channels, convert

N_CH = 16
WINDOW_S = 30.0          # riwayat meter yang disimpan per channel
MID_S = 10.0             # jendela penilaian utama
EVAL_S = 0.5             # temuan dihitung ulang paling sering tiap sekian detik
STALE_S = 2.0            # tanpa frame meter selama ini: tidak ada temuan (data basi tidak dinilai)
HOLD_S = 6.0             # temuan bertahan sekian detik setelah kondisinya hilang (supaya tidak berkedip)
TARGET_PEAK_DB = -10.0   # puncak yang dituju setelah gain diturunkan
LOW_PEAK_DB = -26.0      # puncak di bawah ini dengan fader dipaksa naik = gain kurang
PUSHED_FADER_DB = 5.0
STRUCT_TARGET_DB = -14.0
MAX_GAIN_DB = 50.0
FB_MIN_DB = -18.0        # dugaan feedback hanya dinilai bila level menetap di atas ini
FB_RISE_DB = 6.0
FB_CUT_DB = 3.0
BAL_STEP_DB = 2.0
COMP_MARGIN_DB = 10.0

# Kepekaan: makin tinggi makin cepat dan makin dini memberi temuan (lebih banyak temuan, termasuk yang ringan).
SENS = {
    "rendah": {"panas_db": -4.0, "panas_frac": 0.10, "tahan": 6.0, "fb_std": 0.5, "fb_s": 2.5, "geser": 4.0, "tanpa_acuan": 10.0},
    "normal": {"panas_db": -6.0, "panas_frac": 0.06, "tahan": 4.0, "fb_std": 0.7, "fb_s": 2.0, "geser": 3.0, "tanpa_acuan": 8.0},
    "tinggi": {"panas_db": -8.0, "panas_frac": 0.04, "tahan": 2.5, "fb_std": 0.9, "fb_s": 1.5, "geser": 2.0, "tanpa_acuan": 6.0},
}
DEFAULT_SENS = "tinggi"
MAP_NOTE = "Pemetaan meter ke channel masih perkiraan: cocokkan dengan meter channel di M-AIR."


def _pct(sorted_vals: list[float], p: float) -> float:
    return sorted_vals[int(round(p * (len(sorted_vals) - 1)))]


def _dbsum(dbs: list[float]) -> float:
    return 10 * math.log10(sum(10 ** (d / 10) for d in dbs))


def channel_stats(frames: list[tuple[float, float]], now: float) -> dict | None:
    """Statistik satu channel dari riwayat (waktu, level dB). None bila data kurang dari 2 detik."""
    if len(frames) < 10 or now - frames[0][0] < 2.0:
        return None
    allv = sorted(v for _, v in frames)
    floor = _pct(allv, 0.10)
    gate = min(-35.0, max(-55.0, floor + 10.0))          # di atas ini dianggap "ada suara"
    mid = [v for t, v in frames if now - t <= MID_S]
    act_mid = [v for v in mid if v >= gate]
    act_all = sorted(v for v in allv if v >= gate)
    return {
        "rentang_s": now - frames[0][0], "lantai": floor, "gate": gate,
        "aktivitas": len(act_mid) / len(mid) if mid else 0.0,
        "puncak": max(mid) if mid else -120.0,
        "aktif": balance.active_db(act_mid) if act_mid else None,
        "p95": _pct(act_all, 0.95) if act_all else None,
        "median": _pct(act_all, 0.50) if act_all else None,
        "mid": mid, "act_mid": act_mid,
    }


class Engine:
    def __init__(self, get_values, get_profile, sens: str = DEFAULT_SENS, clock=time.monotonic):
        self._get_values, self._get_profile, self._clock = get_values, get_profile, clock
        self._lock = threading.Lock()
        self._hist: list[deque] = [deque() for _ in range(N_CH)]
        self._last: float | None = None
        self._since: dict[tuple, float] = {}        # kondisi -> sejak kapan benar terus-menerus
        self._true_at: dict[tuple, float] = {}      # kondisi -> terakhir benar
        self._frozen: dict[tuple, dict] = {}        # kondisi -> temuan (nilai usulan dibekukan saat pertama muncul)
        self._gain: dict[int, float] = {}
        self._cache: tuple[float, list] | None = None
        self._profile: tuple[float, dict | None] | None = None
        self.sens = sens if sens in SENS else DEFAULT_SENS
        self.acuan_db: float | None = None          # selisih vokal - musik yang ditandai operator sebagai "pas"
        self.balance_est: dict | None = None
        self.n_aktif = 0
        self.frames = 0

    # ---------- masukan ----------
    def feed(self, levels: list[float], t: float | None = None) -> None:
        t = self._clock() if t is None else t
        with self._lock:
            for i in range(min(N_CH, len(levels))):
                h = self._hist[i]
                h.append((t, levels[i]))
                while h and t - h[0][0] > WINDOW_S:
                    h.popleft()
            self._last = t
            self.frames += 1
            if self._cache is None or t - self._cache[0] >= EVAL_S:      # mesin menilai sendiri, tidak menunggu ada yang bertanya
                try:
                    out = self._evaluate(t)
                except Exception:                  # mesin tidak boleh menjatuhkan pemantauan
                    out = []
                self._cache = (t, out)

    def set_sens(self, sens: str) -> None:
        if sens not in SENS:
            raise ValueError("Kepekaan harus rendah, normal, atau tinggi.")
        with self._lock:
            self.sens = sens
            self._since.clear()
            self._cache = None

    def reset(self) -> None:
        with self._lock:
            for h in self._hist:
                h.clear()
            self._since.clear(), self._true_at.clear(), self._frozen.clear(), self._gain.clear()
            self._last, self._cache, self.balance_est, self.n_aktif = None, None, None, 0

    # ---------- keluaran ----------
    def findings(self) -> list[dict]:
        now = self._clock()
        with self._lock:
            if self._last is None or self._cache is None or now - self._last > STALE_S:
                return []
            return list(self._cache[1])

    def brief(self) -> dict:
        now = self._clock()
        with self._lock:
            return {"kepekaan": self.sens, "aktif": self.n_aktif, "balance": self.balance_est, "acuan": self.acuan_db,
                    "umur": None if self._last is None else round(now - self._last, 1), "frame": self.frames}

    # ---------- penilaian ----------
    def _prof(self, now: float) -> dict | None:
        if self._profile is None or now - self._profile[0] > 5.0:
            try:
                self._profile = (now, self._get_profile())
            except Exception:
                self._profile = (now, None)
        return self._profile[1]

    def _held(self, key: tuple, ok: bool, now: float, need: float) -> bool:
        """Kondisi harus benar terus-menerus selama `need` detik sebelum dianggap berlaku."""
        if not ok:
            self._since.pop(key, None)
            return False
        self._true_at[key] = now
        return now - self._since.setdefault(key, now) >= need

    def _drop_channel(self, ch: int) -> None:
        self._hist[ch - 1].clear()
        for d in (self._since, self._true_at, self._frozen):
            for k in [k for k in d if k[1] == ch]:
                del d[k]

    def _evaluate(self, now: float) -> list[dict]:
        P = SENS[self.sens]
        values = self._get_values() or {}
        profile = self._prof(now)
        voc, mus = balance.groups(profile)

        def first(a):
            v = values.get(a)
            return v[0] if v else None

        def name(ch):
            src = channels.source_for(profile, ch)
            nm = channels.display_name(profile, first(f"/ch/{ch:02d}/config/name"), ch)
            return nm + (f" ({src['nama']})" if src and src["nama"].strip().lower() != nm.lower() else "")

        st: dict[int, dict] = {}
        cfg: dict[int, dict] = {}
        for ch in range(1, N_CH + 1):
            graw, fraw, on = first(f"/headamp/{ch:02d}/gain"), first(f"/ch/{ch:02d}/mix/fader"), first(f"/ch/{ch:02d}/mix/on")
            gdb = convert.gain_db(graw) if isinstance(graw, (int, float)) else None
            if gdb is not None:
                if ch in self._gain and abs(self._gain[ch] - gdb) >= 0.5:
                    self._drop_channel(ch)                 # gain berubah: level lama tidak berlaku, amati dari awal
                self._gain[ch] = gdb
            fdb = convert.fader_db(fraw) if isinstance(fraw, (int, float)) else None
            cfg[ch] = {"graw": graw, "gdb": gdb, "fraw": fraw, "fdb": fdb, "on": on}
            s = channel_stats(list(self._hist[ch - 1]), now)
            if s:
                st[ch] = s
        self.n_aktif = sum(1 for s in st.values() if s["aktivitas"] >= 0.1)
        made: dict[tuple, dict] = {}

        def base(fid, ch, sev, masalah, penyebab, data, uji, rek, risiko, yakin, usulan):
            return {"id": fid, "ch": ch, "sumber": "program", "mesin": True, "severity": sev, "channel": f"CH {ch:02d}",
                    "masalah": masalah, "penyebab": penyebab, "data": data + " " + MAP_NOTE, "uji": uji, "rekomendasi": rek,
                    "risiko": risiko, "keyakinan": yakin, "usulan": usulan}

        for ch, s in st.items():
            c, nm = cfg[ch], name(ch)
            act = s["act_mid"]
            # --- panas: mendekati batas sebelum clipping ---
            hot = [v for v in act if v >= P["panas_db"]]
            ok = c["gdb"] is not None and s["aktivitas"] >= 0.1 and len(hot) >= max(3, P["panas_frac"] * len(act))
            if self._held(("panas", ch), ok, now, P["tahan"]):
                d = min(6.0, max(2.0, math.ceil(s["puncak"] - TARGET_PEAK_DB)))
                tgt = max(-12.0, round(c["gdb"] - d))
                if tgt < c["gdb"]:
                    made[("panas", ch)] = base(
                        f"mesin-panas-ch{ch}", ch, "info", f"{nm}: sinyal terlalu panas (puncak {s['puncak']:.1f} dB), belum clipping",
                        "Gain preamp sedikit terlalu tinggi untuk kerasnya sumber ini. Ruang kepala tinggal sedikit: satu teriakan atau pukulan keras akan clipping.",
                        f"Dalam {MID_S:.0f} detik terakhir {len(hot)} dari {len(act)} pembacaan berada di atas {P['panas_db']:.0f} dB; gain sekarang {c['gdb']:+.0f} dB.",
                        "Minta sumber ini bersuara sekeras bagian paling keras, lalu lihat meter channel di M-AIR: puncak sebaiknya sekitar -10 dB.",
                        f"Turunkan gain dari {c['gdb']:+.0f} ke sekitar {tgt:+.0f} dB (puncak menuju {TARGET_PEAK_DB:.0f} dB), lalu atur volume lewat fader.",
                        "Rendah. Gain juga mengecilkan kiriman ke monitor; naikkan fader atau kiriman bila terasa kurang.", "sedang",
                        [{"alamat": f"/headamp/{ch:02d}/gain", "sekarang": c["graw"], "usulan": round(convert.gain_raw(tgt), 4),
                          "teks": f"Gain preamp: {c['gdb']:+.0f} → {tgt:+.0f} dB"}])
            # --- struktur gain: gain rendah, fader dipaksa ---
            ok = (c["gdb"] is not None and c["fdb"] is not None and c["on"] != 0 and s["rentang_s"] >= 20.0 and s["aktivitas"] >= 0.3
                  and s["p95"] is not None and s["p95"] <= LOW_PEAK_DB and c["fdb"] >= PUSHED_FADER_DB and c["gdb"] < MAX_GAIN_DB)
            if self._held(("struktur", ch), ok, now, max(P["tahan"], 6.0)):
                d = min(12.0, max(3.0, round(STRUCT_TARGET_DB - s["p95"])), MAX_GAIN_DB - c["gdb"])
                gt, ft = round(c["gdb"] + d), round(c["fdb"] - d, 1)
                made[("struktur", ch)] = base(
                    f"mesin-struktur-ch{ch}", ch, "info", f"{nm}: gain terlalu rendah, fader dipaksa naik ({c['fdb']:+.0f} dB)",
                    "Sinyal masuk kecil lalu dikejar dengan fader. Akibatnya desis ikut naik, kiriman ke monitor dan siaran kecil, dan fader kehabisan ruang.",
                    f"Puncak sinyal sekitar {s['p95']:.0f} dB; gain {c['gdb']:+.0f} dB; fader {c['fdb']:+.1f} dB.",
                    "Lihat meter channel di M-AIR saat sumber bersuara normal: puncak sebaiknya sekitar -18 sampai -10 dB dengan fader dekat 0 dB.",
                    f"Naikkan gain {d:.0f} dB (ke {gt:+.0f} dB) dan turunkan fader sama besar (ke {ft:+.1f} dB): volume di speaker tetap, sinyal lebih sehat.",
                    "Sedang: gain ikut mengeraskan kiriman ke monitor (risiko feedback di panggung). Turunkan kiriman monitor channel ini bila perlu.", "sedang",
                    [{"alamat": f"/headamp/{ch:02d}/gain", "sekarang": c["graw"], "usulan": round(convert.gain_raw(gt), 4), "teks": f"Gain preamp: {c['gdb']:+.0f} → {gt:+.0f} dB"},
                     {"alamat": f"/ch/{ch:02d}/mix/fader", "sekarang": c["fraw"], "usulan": round(convert.fader_raw(ft), 4), "teks": f"Fader: {c['fdb']:+.1f} → {ft:+.1f} dB"}])
            # --- kompresor menyala tetapi tidak pernah bekerja ---
            thr = first(f"/ch/{ch:02d}/dyn/thr")
            thr_db = convert.comp_thr_db(thr) if isinstance(thr, (int, float)) else None
            ok = (first(f"/ch/{ch:02d}/dyn/on") == 1 and thr_db is not None and s["rentang_s"] >= 20.0 and s["aktivitas"] >= 0.3
                  and s["p95"] is not None and thr_db >= s["p95"] + COMP_MARGIN_DB)
            if self._held(("kompresor", ch), ok, now, max(P["tahan"], 6.0)):
                tt = max(-40.0, round(s["median"]))
                made[("kompresor", ch)] = base(
                    f"mesin-kompresor-ch{ch}", ch, "info", f"{nm}: kompresor menyala tetapi kemungkinan tidak pernah bekerja",
                    "Threshold berada jauh di atas puncak sinyal, jadi sinyal tidak pernah menyentuhnya dan level tetap naik-turun bebas.",
                    f"Threshold ≈{thr_db:.0f} dB; puncak sinyal sekitar {s['p95']:.0f} dB (angka kompresor dan titik ambil meter masih perkiraan).",
                    "Lihat meter GR (gain reduction) channel ini di M-AIR saat sumber bersuara keras: kalau tidak pernah bergerak, temuan ini benar.",
                    f"Turunkan threshold ke sekitar {tt:.0f} dB sampai meter GR bergerak 3-6 dB di bagian keras.",
                    "Rendah. Suara keras jadi sedikit lebih pelan; naikkan fader bila perlu.", "rendah",
                    [{"alamat": f"/ch/{ch:02d}/dyn/thr", "sekarang": thr, "usulan": round(convert.comp_thr_raw(tt), 4), "teks": f"Threshold: ≈{thr_db:.0f} → ≈{tt:.0f} dB"}])
            # --- dugaan feedback: hanya mic vokal/khotbah yang terdaftar di Daftar Channel ---
            if ch in voc and c["fdb"] is not None and c["fdb"] > -60.0 and c["on"] != 0:
                fr = list(self._hist[ch - 1])
                last = [v for t, v in fr if now - t <= P["fb_s"]]
                prev = [v for t, v in fr if P["fb_s"] < now - t <= P["fb_s"] + 5.0]
                ok = (len(last) >= 8 and fr[0][0] <= now - P["fb_s"] - 1.0 and len(prev) >= 8 and min(last) >= FB_MIN_DB
                      and statistics.pstdev(last) <= P["fb_std"]
                      and (statistics.fmean(last) - statistics.median(prev) >= FB_RISE_DB or statistics.pstdev(prev) >= 3.0))
                if self._held(("feedback", ch), ok, now, 0.0):
                    ft = round(c["fdb"] - FB_CUT_DB, 1)
                    made[("feedback", ch)] = base(
                        f"mesin-feedback-ch{ch}", ch, "perhatian", f"{nm}: kemungkinan feedback (level menetap rata {statistics.fmean(last):.0f} dB)",
                        "Level naik lalu menetap hampir tanpa gerak. Suara orang selalu naik-turun; garis rata seperti ini khas dengung feedback.",
                        f"Selama {P['fb_s']:.1f} detik level hanya bergerak ±{statistics.pstdev(last):.1f} dB di sekitar {statistics.fmean(last):.0f} dB. "
                        "Dinilai dari bentuk level saja: nada panjang penyanyi bisa terlihat serupa.",
                        "Dengar: apakah ada dengung atau lengking yang menetap? Kalau ya, cari frekuensinya di RTA M-AIR lalu potong sempit di EQ channel.",
                        f"Turunkan fader {FB_CUT_DB:.0f} dB dulu (dari {c['fdb']:+.1f} ke {ft:+.1f} dB) untuk menghentikan dengung, lalu perbaiki penyebabnya (posisi mic terhadap monitor, EQ).",
                        "Rendah: mic ini jadi sedikit lebih pelan. Kembalikan bila ternyata bukan feedback.", "rendah",
                        [{"alamat": f"/ch/{ch:02d}/mix/fader", "sekarang": c["fraw"], "usulan": round(convert.fader_raw(ft), 4), "teks": f"Fader: {c['fdb']:+.1f} → {ft:+.1f} dB"}])

        # --- perkiraan campuran: vokal vs musik (level meter + posisi fader) ---
        self.balance_est = None
        contrib = {ch: s["aktif"] + cfg[ch]["fdb"] for ch, s in st.items()
                   if s["aktif"] is not None and cfg[ch]["fdb"] is not None and cfg[ch]["fdb"] > -90 and cfg[ch]["on"] != 0}
        v_on = [ch for ch in voc if ch in contrib and st[ch]["aktivitas"] >= 0.3]
        m_on = [ch for ch in mus if ch in contrib and st[ch]["aktivitas"] >= 0.2]
        top: list[int] = []
        if v_on and m_on:
            lead, msum = max(contrib[ch] for ch in v_on), _dbsum([contrib[ch] for ch in m_on])
            sel = lead - msum
            self.balance_est = {"vokal": round(lead, 1), "musik": round(msum, 1), "selisih": round(sel, 1)}
            limit = (self.acuan_db - P["geser"]) if self.acuan_db is not None else -P["tanpa_acuan"]
            if sel <= limit:
                loud = max(contrib[ch] for ch in m_on)
                top = sorted((ch for ch in m_on if contrib[ch] >= loud - 3.0), key=lambda ch: -contrib[ch])[:2]
                dasar = (f"acuan 'pas' Anda {self.acuan_db:+.1f} dB, sekarang {sel:+.1f} dB" if self.acuan_db is not None
                         else f"belum ada acuan 'pas', memakai ambang umum ({-P['tanpa_acuan']:.0f} dB); sekarang {sel:+.1f} dB")
        for ch in mus:
            if self._held(("tenggelam", ch), ch in top, now, max(P["tahan"], 6.0)):
                c, nm = cfg[ch], name(ch)
                ft = round(c["fdb"] - BAL_STEP_DB, 1)
                made[("tenggelam", ch)] = base(
                    f"mesin-tenggelam-ch{ch}", ch, "perhatian", f"{nm}: musik menutupi vokal (perkiraan campuran)",
                    "Jumlah sinyal musik yang dikirim ke speaker lebih besar dari vokal terkeras, dan sumber ini penyumbang terbesarnya.",
                    f"Selisih vokal - musik: {dasar}. Dihitung dari meter dan posisi fader: tidak menghitung EQ, suara langsung dari panggung (drum akustik, ampli), dan akustik ruangan.",
                    "Dengar di posisi jemaat: apakah kata-kata vokal tertutup musik? Untuk kepastian jalankan Cek Balance dengan mic ruangan.",
                    f"Turunkan fader {nm} {BAL_STEP_DB:.0f} dB (dari {c['fdb']:+.1f} ke {ft:+.1f} dB), dengarkan, ulangi bila masih tertutup. Menurunkan musik lebih aman daripada menaikkan vokal.",
                    "Rendah. Bila musik terasa kurang bertenaga, kembalikan sebagian.", "rendah" if self.acuan_db is None else "sedang",
                    [{"alamat": f"/ch/{ch:02d}/mix/fader", "sekarang": c["fraw"], "usulan": round(convert.fader_raw(ft), 4), "teks": f"Fader: {c['fdb']:+.1f} → {ft:+.1f} dB"}])

        # Bekukan temuan saat pertama muncul; lepas bila kondisinya hilang cukup lama atau nilainya sudah diubah.
        for k, f in made.items():
            self._frozen.setdefault(k, f)
        for k in list(self._frozen):
            f = self._frozen[k]
            moved = any(not isinstance(first(u["alamat"]), (int, float)) or abs(first(u["alamat"]) - u["sekarang"]) > 0.004 for u in f["usulan"])
            if moved or now - self._true_at.get(k, -1e9) > HOLD_S:
                del self._frozen[k]
                if moved:
                    self._since.pop(k, None)
        return sorted(self._frozen.values(), key=lambda f: (f["severity"] != "perhatian", f["ch"], f["id"]))
