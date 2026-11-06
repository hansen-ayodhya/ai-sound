# 07 – Panduan Membaca Ashly Protea 4.8SP (READ-ONLY, via USB)

Status: **panduan persiapan, belum dijalankan.** Informasi tentang unit ini berasal dari halaman dealer dan salinan manual pihak ketiga, bukan dokumen resmi Ashly yang dibaca langsung. Nama menu/tombol di software Protea **belum diverifikasi**; ikuti manual resmi dan hentikan bila ragu.

## Fakta yang diketahui (perlu dicek di unit)

- 4 input × 8 output, sudah tidak diproduksi.
- Kontrol: USB (software Protea, Windows) dan RS-232 (kontrol pihak ketiga). Ethernet tidak ditemukan di sumber.
- Dokumen "4.8SP, 3.6SP Control Protocol" tersedia di situs Ashly (knowledge base).
- Bisa memakai proteksi password.

## Aturan keselamatan

1. **Jangan kirim apa pun ke unit.** Hanya melihat, memotret layar, dan menyimpan preset/salinan ke PC.
2. **Hindari perintah "kirim/sync/upload ke unit".** Bila software menanyakan arah sinkronisasi saat terhubung, pilih arah **unit → komputer** (ambil dari unit), dan jangan lanjut kalau tidak yakin arahnya.
3. **Jangan reset, jangan menebak password.** Bila diminta password, berhenti dan cari tahu dari pengelola sebelumnya.
4. **Lakukan di luar ibadah**, dengan operator hadir, dan tanpa ada audio berjalan lewat PA.
5. **Latih dulu secara offline:** buka software tanpa unit terhubung dan pelajari tampilannya (belum ada risiko).
6. Jangan mencabut atau mencolok USB saat software sedang menulis/menyinkronkan.

## Persiapan di laptop (tanpa unit)

1. Unduh software Protea dan manual resmi dari situs Ashly (cek versi yang mendukung Windows 11 di halaman dukungan Ashly).
2. Unduh dokumen "4.8SP, 3.6SP Control Protocol" untuk keperluan baca terprogram di masa depan (belum dipakai).
3. Pasang software; jangan colok USB dulu.

## Yang dicatat dari unit (foto layar atau ekspor ke file)

| Bagian | Yang dicatat |
|---|---|
| Input 1–4 | Sumber (dari output MR18 yang mana), gain, mute, EQ input, delay input |
| Output 1–8 | Pemakaian (Mickey L/R, sub, monitor, kosong), mute, polaritas, level |
| Crossover per output | Frekuensi HPF dan LPF, tipe filter, slope |
| EQ per output | Setiap band: frekuensi, gain, Q, tipe |
| Delay per output | Nilai dalam ms/jarak |
| Limiter per output | Threshold, attack, release |
| Routing | Input mana ke output mana |
| Keamanan | Apakah ada password/lock (jangan catat passwordnya di repo) |
| Identitas | Versi firmware/software, nomor seri bila terlihat |

## Format temuan (enam unsur)

| Unsur | Isi |
|---|---|
| Masalah | Tidak tahu konfigurasi DSP, sehingga rekomendasi system EQ/crossover/delay/limiter tidak bisa divalidasi |
| Kemungkinan penyebab | DSP hanya bisa dibaca lewat USB dan belum pernah didokumentasikan |
| Data yang dibutuhkan | Tabel di atas, plus foto kabel output MR18 → DSP |
| Langkah uji | Sambung USB di luar ibadah, ambil data dari unit ke komputer, simpan salinan, bandingkan dengan foto layar |
| Rekomendasi | Baca dan simpan dulu; baru bahas perubahan setelah ada pengukuran |
| Risiko | Salah arah sinkronisasi bisa menimpa preset unit; salah colok/cabut saat transfer. Karena itu: aturan 1–6 di atas, dan foto layar sebagai cadangan |

## Data dari DSP dan repo

Preset/ekspor DSP disimpan di `data/` (sudah di-`.gitignore`), bukan di repo. Kirim ke percakapan hanya ringkasan atau foto layar yang diperlukan.
