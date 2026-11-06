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
