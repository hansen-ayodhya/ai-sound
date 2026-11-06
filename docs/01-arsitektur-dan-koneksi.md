# 01 – Arsitektur dan Koneksi

Tanda **(?)** = belum dikonfirmasi. Jangan diisi dengan asumsi.

## Empat jalur yang dipisahkan

| Jalur | Isi | Medium | Catatan |
|---|---|---|---|
| Audio live | Mikrofon → MR18 → DSP → speaker | Kabel analog (atau AES (?)) | Tidak disentuh sistem AI |
| Kontrol | Baca parameter mixer, meter kasar | LAN, OSC via UDP | Hanya angka parameter, bukan audio |
| Audio analisis | Tap sinyal untuk FFT/RTA/IR | USB MR18 ke PC | Terpisah total dari OSC |
| Keputusan | Diagnosis, rekomendasi, persetujuan, log | Dashboard lokal + (opsional) cloud | Cloud hanya menerima ringkasan fitur, bukan audio mentah |

Mixer digital dan DSP tetap berjalan normal bila kabel LAN atau USB dicabut. Jalur AI tidak berada di dalam jalur audio ibadah.

## Diagram koneksi saat ini

```
                          ┌──────────── ROUTER ────────────┐
                          └───┬───────────────────┬────────┘
                         LAN  │                   │  LAN
                              │                   │      (Wi-Fi router: tablet M-AIR? (?))
                    ┌─────────┴────────┐    ┌─────┴───────────┐
                    │  PC EasyWorship  │    │  Midas MR18     │
                    │  (+ AI engine    │    │                 │
                    │   nanti)         │    │                 │
                    └───────┬──────────┘    └─┬───────┬───────┘
                            │      USB (Tahap 2: │ audio analisis)
                            └────────────────────┘
                            Audio EasyWorship → mixer: jalur (?)

  AUDIO LIVE (analog):
  Mic/DI/wireless ──► MR18 input ──► [MR18 mix] ──► Output MR18 (?)
                                                       │ (L/R? sub send?)
                                                       ▼
                                                  Speaker Mgmt / DSP (merek (?))
                                                  ├─► Mickey aktif L
                                                  ├─► Mickey aktif R
                                                  ├─► Subwoofer 1, 2

  Monitor panggung: MR18 Aux (?) ──► [lewat DSP? (?)] ──► Monitor ×2

  PENGUKURAN (Tahap 2):
  Mic drum ──XLR panjang──► MR18 input khusus (mute dari mix) ──USB──► PC
```

## Menghubungkan PC EasyWorship dan MR18 tanpa mengganggu ibadah

Risiko terbesar bukan jaringan, tetapi **driver audio USB**: Windows bisa mengganti *default audio device* ke MR18 saat USB dicolok sehingga EasyWorship bisa diam saat ibadah.

1. Mulai **hanya dengan LAN, tanpa USB**. LAN sudah terpasang dan hanya membawa data parameter kecil.
2. **Jangan ubah IP mixer.** Pakai *DHCP reservation* di router. Matikan Wi-Fi bawaan MR18 jika tidak dipakai.
3. Semua uji coba (terutama USB dan driver) dilakukan **di luar jam ibadah**.
4. Sebelum USB dicolok, catat perangkat audio default Windows dan output yang dipakai EasyWorship, lalu kunci keduanya secara eksplisit.
5. Proses AI berprioritas rendah, tidak aktif saat EasyWorship menjalankan video. Tidak ada update/antivirus scan di jam ibadah.
6. **Rekomendasi:** Tahap 1–2 pakai laptop terpisah di LAN yang sama (pengukuran akustik juga butuh mobilitas). Pemakaian PC yang sama dipertimbangkan setelah stabil.

| Item | Isi |
|---|---|
| Masalah | Risiko gangguan ke ibadah jika AI berbagi PC dengan EasyWorship |
| Kemungkinan penyebab | Rebutan CPU/GPU/USB, perubahan default audio device |
| Data yang dibutuhkan | Spesifikasi PC, OS, jalur audio EasyWorship ke mixer |
| Uji | Uji beban saat EasyWorship berjalan, di luar ibadah |
| Rekomendasi | Laptop terpisah dulu |
| Risiko | Rendah; biaya nol bila laptop tersedia |
