# 06 – Log Proyek dan Langkah Berikutnya

## Posisi terakhir (2026-11-01)

- Brief proyek dimigrasikan dari ChatGPT.
- Tahap 0 selesai: arsitektur, diagram koneksi, rencana audit read-only, rencana pengambilan audio, daftar informasi yang dibutuhkan (dokumen 01–05).
- Belum ada kode. Belum ada perubahan pada mixer/DSP.

## Keputusan yang sudah diambil

| Keputusan | Alasan |
|---|---|
| Audit read-only dulu, belum membangun aplikasi | Hindari asumsi; keselamatan ibadah |
| LAN dulu, USB belakangan dan di luar ibadah | Risiko default audio device Windows |
| Tahap 1–2 sebaiknya pakai laptop terpisah | Jaga PC EasyWorship steril; pengukuran butuh mobilitas |
| Audio lewat USB, kontrol lewat OSC, dua jalur terpisah | OSC tidak membawa audio |
| Evaluasi mic drum sebelum membeli measurement mic | Prioritaskan perangkat yang sudah ada |

## Langkah berikutnya

1. Terima jawaban pertanyaan #1 (DSP) dan item lain di [05](05-informasi-perangkat.md).
2. Simpan scene baseline MR18 lewat aplikasi resmi (di luar ibadah) dan simpan file-nya di penyimpanan aman.
3. Mulai Tahap 1 sesuai [02](02-rencana-audit-readonly.md): hanya setelah informasi minimum tersedia.

## Update 2026-11-01 (malam, laptop Windows 11, MR18 tidak di dekat laptop)

- DSP teridentifikasi: **Ashly Protea 4.8SP** (sudah discontinued).
- Kontrol: USB dengan software Protea (Windows), RS-232 untuk kontrol pihak ketiga; dokumen "4.8SP, 3.6SP Control Protocol" ada di situs Ashly. Ethernet **tidak** ditemukan di sumber; anggap tidak ada sampai dicek di unit.
- Konsekuensi: DSP tidak bisa dibaca lewat LAN seperti MR18. Pembacaan konfigurasi DSP kemungkinan lewat USB dari laptop, saat laptop terhubung ke DSP. Jalur ini juga bisa menulis, jadi aturan read-only berlaku: hanya simpan/baca preset, jangan kirim apa pun.
- Cek dulu: unit mungkin memakai proteksi password; jangan reset atau menebak password.
- Laptop: Windows 11. Pekerjaan offline sebelum mixer terjangkau: lihat daftar di sesi.

## Catatan

Repositori ini awalnya berisi proyek lain (aplikasi POS); proyek ini ditempatkan di folder `ai-sound-engineer/` agar terpisah dan tidak menyentuh file yang sudah ada. Bila ingin repositori sendiri, folder ini bisa dipindahkan utuh.
