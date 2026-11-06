# 03 – Pengambilan Audio untuk Analisis (terpisah dari OSC)

OSC hanya membawa angka parameter dan meter kasar, tidak bisa dipakai untuk FFT. Audio harus lewat jalur audio.

## KENDALA (2026-11-06): USB MR18 sudah dipakai komputer multimedia

USB MR18 dipakai komputer multimedia untuk memutar playback/video ke mixer. Memindah kabel ke laptop
mematikan playback. Konsekuensi:
- Fitur saat ini (audit, meter, pantau live) **tidak terpengaruh**: semuanya lewat LAN, bukan USB.
- Pengukuran akustik Tahap 2 dilakukan **di luar ibadah**, jadi USB boleh dipindah sementara dengan checklist
  (catat, pindah, ukur, kembalikan, cek ulang output audio EasyWorship/multimedia sebelum ibadah).
- Analisis audio **live saat ibadah** (deteksi feedback, Tahap 5) butuh jalur audio permanen ke laptop; opsi
  dibahas saat sampai di sana (rekam dari komputer multimedia lewat USB yang sama, atau interface terpisah
  yang diberi sinyal dari output aux MR18). Belum diputuskan; tidak membeli apa pun sekarang.

## Jalur yang direkomendasikan: USB MR18 sebagai audio interface multichannel

(Jumlah channel dan perilaku driver perlu diverifikasi pada perangkat.)

- Satu perangkat = satu clock, tidak ada drift antar channel.
- Tap yang dibutuhkan:
  1. **Referensi:** main L/R (sinyal yang masuk ke DSP)
  2. **Pengukuran:** mikrofon di ruangan → satu channel MR18 yang di-mute dari mix → USB
  3. **Opsional:** channel vokal individual, sebelum/sesudah EQ
- **Pengukuran dua-channel (referensi vs mikrofon ruangan)** memberi respons frekuensi, delay akustik, dan impulse response sistem nyata (DSP + speaker + ruangan), bisa dengan pink noise atau musik.
- Mengubah *routing USB* adalah **perubahan tulis**: lakukan sebagai langkah setup terpisah dari audit, dengan baseline, persetujuan operator, di luar jam ibadah.
- Awalnya hanya **merekam**. Memutar sweep ke PA butuh USB return dan level terkontrol: di luar ibadah, limiter aktif.
- **Cadangan:** jika USB MR18 berkonflik dengan EasyWorship, pakai audio interface kecil terpisah (opsi kedua, bukan pembelian sekarang).

## Mikrofon drum sebagai mikrofon pengukuran

Model belum diketahui, belum dievaluasi. Prinsip umum: mikrofon drum dinamis responsnya tidak rata dan SPL absolut tidak bisa diukur tanpa kalibrasi, tetapi sering cukup untuk pengukuran **relatif** (delay, pola resonansi, sebelum/sesudah, deteksi feedback). Evaluasi setelah model dan kurva pabrikan diketahui; jelaskan keterbatasannya secara jujur.

## Perangkat lunak yang dipertimbangkan

REW, Open Sound Meter, Python (numpy/scipy, pustaka audio). Keputusan dikunci setelah audit.
