"""Konteks HTTPS yang dipakai semua sambungan keluar (pembaruan, Gemini).

Python di Windows membaca sertifikat induk dari toko sertifikat Windows, dan toko itu kadang belum memuat sertifikat induk
GitHub/Google (Windows mengunduhnya sendiri hanya saat browser memerlukannya; Windows lama atau yang jarang diperbarui sering
ketinggalan). Karena itu toko Windows DIPAKAI, lalu ditambah bundel sertifikat Mozilla (certifi) yang ikut terbungkus di exe.
Sertifikat yang dipasang antivirus/proxy di toko Windows tetap dipercaya. Verifikasi nama situs dan masa berlaku tidak dilonggarkan.
"""

from __future__ import annotations

import functools
import ssl

# kode galat verifikasi OpenSSL -> (ringkasan, penyebab yang paling mungkin)
CODES = {
    20: ("sertifikat induk tidak dikenal", "toko sertifikat Windows belum memuat sertifikat induk GitHub; jalankan Windows Update"),
    21: ("rantai sertifikat tidak lengkap", "toko sertifikat Windows belum memuat sertifikat induk GitHub; jalankan Windows Update"),
    18: ("sertifikat buatan sendiri", "ada perangkat/antivirus/proxy yang memeriksa HTTPS dan memakai sertifikatnya sendiri"),
    19: ("sertifikat buatan sendiri dalam rantai", "ada antivirus/proxy yang memeriksa HTTPS dan memakai sertifikatnya sendiri"),
    10: ("sertifikat kedaluwarsa", "tanggal komputer terlalu maju, atau Windows tidak diperbarui"),
    9: ("sertifikat belum berlaku", "tanggal/jam komputer terlalu mundur"),
    62: ("nama situs tidak cocok dengan sertifikat", "sambungan dialihkan ke situs lain (jaringan yang memblokir, atau portal login wifi)"),
}


@functools.lru_cache(maxsize=1)
def context() -> ssl.SSLContext:
    """Toko sertifikat sistem + bundel certifi (bila terpasang)."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:       # certifi tidak ada (mis. dijalankan dari kode sumber): cukup toko sistem
        pass
    return ctx


def system_context() -> ssl.SSLContext:
    """Hanya toko sertifikat sistem (untuk membandingkan saat diagnosis)."""
    return ssl.create_default_context()


def explain(exc: BaseException) -> str:
    """Penjelasan galat sertifikat: apa yang ditolak dan penyebab yang paling mungkin ('' bila bukan galat sertifikat)."""
    e = exc
    while e is not None and not isinstance(e, ssl.SSLCertVerificationError):
        e = e.__cause__ or e.__context__ or (e.reason if isinstance(getattr(e, "reason", None), BaseException) else None)
    if e is None:
        return ""
    ringkas, sebab = CODES.get(getattr(e, "verify_code", None), (getattr(e, "verify_message", None) or "sertifikat ditolak", "antivirus/proxy yang memeriksa HTTPS, atau jam komputer salah"))
    return f"{ringkas}: kemungkinan {sebab}"
