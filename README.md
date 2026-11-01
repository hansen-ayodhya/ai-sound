# AI Sound Engineer – Sistem Audio Gereja

Proyek untuk membangun AI Sound Engineer yang bertindak seperti sound engineer profesional yang proaktif: memahami kondisi sistem audio gereja, menemukan akar masalah, dan membantu operator menghasilkan suara yang jernih, nyaman, konsisten, dan profesional.

> **Status: Tahap 0 selesai (desain). Tahap 1 (System Audit) belum dimulai. Belum ada kode. Tidak ada setting mixer/DSP yang pernah diubah.**

Dokumen ini adalah titik awal untuk melanjutkan proyek (dimigrasikan dari percakapan ChatGPT sebelumnya). Mulai dari [docs/06-log-proyek.md](docs/06-log-proyek.md) untuk melihat posisi terakhir dan langkah berikutnya.

## Visi

AI harus mampu menganalisis kualitas suara, mengidentifikasi penyebab vokal tidak jelas, mendeteksi potensi feedback, menganalisis keseimbangan musik/vokal/bass/khotbah, merekomendasikan setting mixer dan speaker management, membantu soundcheck, memantau audio secara berkelanjutan, dan memberi rekomendasi **sebelum** operator melakukan perubahan.

## Masalah yang ingin diselesaikan (cari akar masalah, bukan sekadar naik/turun EQ)

1. Vokal kurang jelas
2. Sering feedback / suara mendenging
3. Bass terlalu boomy
4. Musik terkadang terlalu keras
5. Vokal tajam / menusuk telinga
6. Suara khotbah kurang jelas
7. Hasil mixing tidak konsisten

Faktor yang dipertimbangkan: gain staging, karakter mikrofon, EQ/compressor channel, FX dan routing mixer, penempatan speaker, crossover dan integrasi subwoofer, delay alignment, system EQ dan limiter, akustik ruangan, interaksi suara langsung dengan pantulan.

## Perangkat yang dimiliki

| Perangkat | Detail | Status |
|---|---|---|
| Mixer | Midas MR18, terhubung ke router via LAN | Terkonfirmasi |
| Komputer | Menjalankan EasyWorship, terhubung ke router via LAN | Spesifikasi belum diketahui |
| Speaker utama | Mickey aktif ×2 (kiri/kanan) | Model lengkap belum diketahui |
| Subwoofer | Aktif ×2 | Spesifikasi belum diketahui |
| Monitor panggung | ×2 | Routing belum dikonfirmasi |
| Speaker management / DSP | Semua speaker utama + sub melewatinya | **Merek/model belum diketahui** |
| Mikrofon pengukuran | Mikrofon drum bekas | Merek/model belum diketahui; evaluasi dulu sebelum beli measurement mic |

Daftar lengkap yang belum diketahui ada di [docs/05-informasi-perangkat.md](docs/05-informasi-perangkat.md). **Jangan mengarang informasi tersebut.**

## Tahap pengembangan

| Tahap | Isi | Status |
|---|---|---|
| 0 | Desain arsitektur, rencana audit | Selesai (dokumen) |
| 1 | System Audit (READ-ONLY): baca MR18 via LAN, identifikasi DSP, simpan baseline | Belum dimulai |
| 2 | Acoustic Analysis: uji mic drum, ukur beberapa posisi kursi, respons speaker & ruangan | Belum |
| 3 | AI Diagnosis: gabungkan data mixer + DSP + pengukuran, cari root cause | Belum |
| 4 | Assisted Mixing: rekomendasi → persetujuan operator → penerapan, log, rollback | Belum |
| 5 | Intelligent Monitoring: real-time clipping/feedback/keseimbangan | Belum |

## Dokumen

- [01 – Arsitektur dan koneksi](docs/01-arsitektur-dan-koneksi.md)
- [02 – Rencana audit READ-ONLY MR18](docs/02-rencana-audit-readonly.md)
- [03 – Pengambilan audio untuk analisis](docs/03-pengambilan-audio.md)
- [04 – Keamanan dan kontrol](docs/04-keamanan-dan-kontrol.md)
- [05 – Informasi perangkat (checklist)](docs/05-informasi-perangkat.md)
- [06 – Log proyek dan langkah berikutnya](docs/06-log-proyek.md)

## Cara bekerja

Peran: live sound engineer, audio system engineer, acoustic measurement specialist, AI agent architect, software integration engineer. Pendekatan teknis, praktis, bertahap. Jangan langsung membangun aplikasi, jangan langsung menyarankan membeli banyak perangkat, prioritaskan perangkat yang sudah ada.

Setiap rekomendasi harus memuat: (1) masalah, (2) kemungkinan penyebab, (3) data pembuktian, (4) langkah pengujian, (5) solusi, (6) risiko perubahan. Jika informasi kurang, tanyakan **satu** hal paling penting dulu.
