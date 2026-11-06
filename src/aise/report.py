"""Generator laporan audit (Markdown) dari file dump. Murni analisis offline.

Temuan di sini adalah TEMUAN AWAL berbasis konfigurasi (aturan sederhana,
sebagian memakai nama channel). Belum dibuktikan dengan pengukuran, dan arti
nilai ON/OFF belum diverifikasi pada MR18 ini. Setiap temuan memuat enam
unsur: masalah, kemungkinan penyebab, data pembuktian, langkah uji,
rekomendasi, dan risiko perubahan.

    python -m aise.report data/dump1.json --out data/report.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

VOICE_RE = re.compile(r"\b(vok|vocal|vox|khotbah|pastor|pdt|speech|choir|paduan|singer|mc)", re.I)
NONMIC_RE = re.compile(r"\b(laptop|pc|playback|track|keys|synth|piano|easyworship|ew)", re.I)


@dataclass
class Finding:
    id: str
    severity: str  # info | perhatian
    channel: str
    masalah: str
    penyebab: str
    data: str
    uji: str
    rekomendasi: str
    risiko: str
    ch: int | None = None  # nomor channel bila temuan terkait satu channel


def _first(values: dict, addr: str):
    v = values.get(addr)
    return v[0] if v else None


def _channels(values: dict) -> list[int]:
    return sorted({int(m.group(1)) for k in values if (m := re.match(r"^/ch/(\d+)/", k))})


def _label(values: dict, n: int) -> tuple[str, str]:
    name = _first(values, f"/ch/{n:02d}/config/name")
    name = str(name).strip() if name not in (None, "") else ""
    return name, f"Ch {n:02d}" + (f" '{name}'" if name else "")


def analyze(dump: dict) -> list[Finding]:
    values = dump["values"]
    findings: list[Finding] = []
    queried = max(len(values), 1)
    answered = sum(v is not None for v in values.values())
    if answered / queried < 0.5:
        findings.append(Finding(
            "dump-kosong", "perhatian", "-",
            "Kurang dari separuh alamat dijawab mixer.",
            "Daftar alamat atau port di alat ini berbeda dari yang dipakai MR18 Anda.",
            f"{answered} dari {queried} alamat dijawab.",
            "Jalankan `--only /info`, lalu bandingkan dengan dokumentasi OSC firmware yang terpasang.",
            "Koreksi addresses.py berdasarkan balasan nyata sebelum memakai laporan ini.",
            "Tidak ada risiko ke mixer (hanya baca); risikonya laporan menyesatkan jika dipakai apa adanya."))
    for n in _channels(values):
        name, label = _label(values, n)
        c = f"/ch/{n:02d}"
        is_voice = bool(name and VOICE_RE.search(name))
        if is_voice and _first(values, f"{c}/preamp/hpon") == 0:
            findings.append(Finding(
                f"hpf-mati-ch{n:02d}", "perhatian", label,
                f"Low cut (HPF) mati pada channel suara: {label}.",
                "Dengung/hembusan napas/handling noise dan energi bass ikut masuk ke PA, menambah suara boomy dan mengurangi kejernihan.",
                f"{c}/preamp/hpon = 0 (dump {dump.get('created', '?')}).",
                "Dengarkan atau ukur RTA di bawah ±100 Hz saat orang berbicara/bernyanyi; bandingkan dengan HPF dinyalakan sementara oleh operator.",
                "Nyalakan HPF pada channel suara; titik awal umum 80–120 Hz, disetel dengan telinga dan pengukuran.",
                "Terlalu tinggi membuat suara tipis. Ubah hanya oleh operator, satu channel sekali, catat nilai lama."))
        if is_voice and _first(values, f"{c}/dyn/on") == 0:
            findings.append(Finding(
                f"tanpa-komp-ch{n:02d}", "info", label,
                f"Kompresor mati pada channel suara: {label}.",
                "Level vokal naik-turun lebar; operator mengejar fader, hasil mixing tidak konsisten.",
                f"{c}/dyn/on = 0.",
                "Rekam meter channel ini selama satu lagu/khotbah dan lihat rentang level (monitor meter).",
                "Pertimbangkan kompresi ringan setelah rentang level terbukti lebar.",
                "Kompresi berlebihan memperbesar risiko feedback; setel gain staging dulu."))
        if name and NONMIC_RE.search(name) and _first(values, f"/headamp/{n:02d}/phantom") == 1:
            findings.append(Finding(
                f"phantom-ch{n:02d}", "perhatian", label,
                f"Phantom 48V menyala pada channel yang tampak bukan mikrofon: {label}.",
                "Sumber seperti output laptop/keyboard tidak memerlukan 48V; sebagian perangkat bisa rusak atau berdengung.",
                f"/headamp/{n:02d}/phantom = 1.",
                "Cek apa yang fisik terpasang ke input itu.",
                "Matikan 48V jika sumbernya bukan mikrofon kondenser/DI aktif yang membutuhkannya.",
                "Mematikan 48V pada kondenser membuatnya diam; ubah hanya oleh operator dengan fader turun."))
    for f in findings:
        m = re.search(r"ch(\d+)$", f.id)
        f.ch = int(m.group(1)) if m else None
    return findings


def findings_json(dump: dict) -> list[dict]:
    return [asdict(f) for f in analyze(dump)]


def render(dump: dict, source: str = "") -> str:
    values = dump["values"]
    findings = analyze(dump)
    answered = sum(v is not None for v in values.values())
    out = [
        "# Laporan Audit Mixer (otomatis, temuan awal)",
        "",
        f"- Sumber: `{source or '?'}`  |  Dibuat: {dump.get('created', '?')}  |  Hash: `{str(dump.get('sha256', '?'))[:12]}`",
        f"- Alamat dijawab: {answered} / {len(values)}",
        "- **Catatan:** temuan berdasarkan konfigurasi saja dan sebagian memakai nama channel. Belum dibuktikan dengan pengukuran; arti ON/OFF belum diverifikasi pada MR18 ini.",
        "",
        "## Ringkasan channel",
        "",
        "| Ch | Nama | HPF | Gate | Komp | EQ | Aktif | 48V |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def onoff(v):
        return "?" if v is None else ("ON" if v == 1 else "OFF")

    for n in _channels(values):
        name, _ = _label(values, n)
        c = f"/ch/{n:02d}"
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            f"{n:02d}", name or "-",
            onoff(_first(values, f"{c}/preamp/hpon")), onoff(_first(values, f"{c}/gate/on")),
            onoff(_first(values, f"{c}/dyn/on")), onoff(_first(values, f"{c}/eq/on")),
            onoff(_first(values, f"{c}/mix/on")), onoff(_first(values, f"/headamp/{n:02d}/phantom"))))
    out += ["", f"## Temuan ({len(findings)})", ""]
    if not findings:
        out.append("Tidak ada temuan dari aturan yang ada. Ini bukan berarti sistem sudah baik: aturan masih sedikit dan belum ada pengukuran.")
    for i, f in enumerate(findings, 1):
        out += [
            f"### {i}. {f.masalah}  _[{f.severity}]_",
            "",
            f"1. **Masalah:** {f.masalah}",
            f"2. **Kemungkinan penyebab:** {f.penyebab}",
            f"3. **Data pembuktian:** {f.data}",
            f"4. **Langkah uji:** {f.uji}",
            f"5. **Rekomendasi:** {f.rekomendasi}",
            f"6. **Risiko perubahan:** {f.risiko}",
            "",
        ]
    out += ["## Belum bisa dinilai dari dump ini", "",
            "- Gain staging (butuh rekaman meter), respons ruangan dan speaker (butuh pengukuran),",
            "- konfigurasi DSP Ashly Protea 4.8SP (crossover, delay, limiter, system EQ),",
            "- routing output MR18 ke DSP (butuh pembacaan routing dan konfirmasi fisik)."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Buat laporan audit dari dump")
    p.add_argument("dump")
    p.add_argument("--out")
    args = p.parse_args(argv)
    dump = json.loads(Path(args.dump).read_text(encoding="utf-8"))
    if "values" not in dump:
        print("Bukan file dump yang valid.", file=sys.stderr)
        return 2
    text = render(dump, args.dump)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"laporan -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
