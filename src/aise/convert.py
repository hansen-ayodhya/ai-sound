"""Konversi nilai mentah mixer (0..1) <-> satuan manusia (dB, Hz).

STATUS: PERKIRAAN. Rumus dari keluarga X Air / X32, BELUM dicocokkan dengan MR18
ini. Rumus yang sama dipakai di viewer/mixer.html (objek EST); ubah keduanya
bersamaan. Setelah audit nyata membuktikan rumusnya, set VERIFIED = True.
"""

from __future__ import annotations

import math

VERIFIED = False


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def hpf_hz(raw: float) -> float:
    return 20.0 * 20.0 ** raw


def hpf_raw(hz: float) -> float:
    return _clamp01(math.log(hz / 20.0) / math.log(20.0))


def eq_hz(raw: float) -> float:
    return 20.0 * 1000.0 ** raw


def eq_raw_hz(hz: float) -> float:
    return _clamp01(math.log(hz / 20.0) / math.log(1000.0))


def eq_gain_db(raw: float) -> float:
    return raw * 30.0 - 15.0


def eq_gain_raw(db: float) -> float:
    return _clamp01((db + 15.0) / 30.0)


def eq_q(raw: float) -> float:
    return 10.0 * (0.3 / 10.0) ** raw


# ---------- dinamika (PERKIRAAN) ----------
RATIOS = [1.1, 1.3, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0, 20.0, 100.0]


def ratio_index(raw) -> int | None:
    """Rasio bisa datang sebagai indeks (int) atau 0..1 (float)."""
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(0, min(len(RATIOS) - 1, raw))
    return max(0, min(len(RATIOS) - 1, round(float(raw) * (len(RATIOS) - 1))))


def ratio_value(raw) -> float | None:
    i = ratio_index(raw)
    return None if i is None else RATIOS[i]


def ratio_index_for(value: float) -> int:
    return min(range(len(RATIOS)), key=lambda i: abs(RATIOS[i] - value))


def comp_thr_db(raw: float) -> float:
    return raw * 60.0 - 60.0


def comp_thr_raw(db: float) -> float:
    return _clamp01((db + 60.0) / 60.0)


def gate_thr_db(raw: float) -> float:
    return raw * 80.0 - 80.0


def gate_thr_raw(db: float) -> float:
    return _clamp01((db + 80.0) / 80.0)


def gate_range_db(raw: float) -> float:
    return 3.0 + raw * 57.0


def attack_ms(raw: float) -> float:
    return raw * 120.0


def release_ms(raw: float) -> float:
    return 5.0 * (4000.0 / 5.0) ** raw


# ---------- fader / kiriman (PERKIRAAN, kurva keluarga X Air) ----------
def fader_db(raw: float) -> float:
    if raw <= 0:
        return float("-inf")
    if raw >= 0.5:
        return raw * 40.0 - 30.0
    if raw >= 0.25:
        return raw * 80.0 - 50.0
    if raw >= 0.0625:
        return raw * 160.0 - 70.0
    return raw * 480.0 - 90.0


def fader_raw(db: float) -> float:
    if db == float("-inf") or db <= -90:
        return 0.0
    if db >= -10:
        return _clamp01((db + 30.0) / 40.0)
    if db >= -30:
        return (db + 50.0) / 80.0
    if db >= -60:
        return (db + 70.0) / 160.0
    return (db + 90.0) / 480.0
