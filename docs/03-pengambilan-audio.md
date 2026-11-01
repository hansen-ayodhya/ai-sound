# 03 – Pengambilan Audio untuk Analisis (terpisah dari OSC)

OSC hanya membawa angka parameter dan meter kasar, tidak bisa dipakai untuk FFT. Audio harus lewat jalur audio.

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
