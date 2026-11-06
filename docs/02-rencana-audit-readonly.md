# 02 – Rencana Audit READ-ONLY Midas MR18 via LAN (Tahap 1)

Tujuan: mengetahui konfigurasi nyata mixer **tanpa mengubah apa pun**.

## Prinsip read-only yang ditegakkan di kode

Klien hanya boleh mengirim pesan *query* (alamat OSC tanpa argumen). Semua pesan lain diblokir oleh lapisan penjaga, dan setiap pesan keluar dicatat di log.

Hal yang **harus diverifikasi** pada perangkat (berdasarkan pengetahuan protokol X Air, belum diuji pada MR18 ini):
- Port UDP kontrol kemungkinan 10024.
- Langganan `/xremote` perlu diperbarui sekitar tiap 10 detik.
- Meter diminta lewat `/meters`.
- Daftar alamat OSC: cocokkan dengan dokumentasi OSC X Air / firmware yang terpasang.

## Langkah

0. **Persiapan tanpa menyentuh mixer.** Catat versi firmware. Simpan **scene/show lewat aplikasi resmi** sebagai baseline (tidak perlu kode). Jangan pernah me-*load* scene.
1. **Temukan perangkat.** IP dari daftar DHCP router; cek ping dan respons query info.
2. **Dump konfigurasi menyeluruh** untuk semua channel, bus, FX, main:
   - Input: nama, sumber input, gain/preamp, phantom, polaritas, low cut
   - Pengolahan: gate, compressor, EQ
   - Distribusi: fader, mute, pan, send ke bus/FX
   - Bus, FX return, main L/R (EQ/dinamika)
   - **Routing output** (ke DSP dan monitor) dan routing USB
3. **Verifikasi.** Dump dua kali; selisih harus nol; bandingkan dengan file scene dari langkah 0. Simpan JSON dengan timestamp + hash.
4. **Rekam meter pasif** saat soundcheck/ibadah (input, gain reduction, bus, main), ~10 sampel/detik, untuk analisis gain staging.
5. **Laporan audit:** diagram aliran sinyal dari konfigurasi nyata, tabel channel, temuan (gain terlalu tinggi, EQ bertumpuk, low cut mati, FX bocor ke main, compressor tidak bekerja, dll.).

## Definisi selesai

- Dua dump identik dan cocok dengan scene.
- Nol pesan tulis terkirim (dibuktikan log penjaga).
- Ibadah tidak terpengaruh.

## Hubungan gejala → data

| Gejala | Data dari audit/pengukuran |
|---|---|
| Vokal kurang jelas / tajam | EQ & kompresor channel, gain, respons ruangan, delay |
| Feedback / mendenging | Gain vokal, EQ, level monitor, RTA di frekuensi kritis |
| Bass boomy | Crossover/sub di DSP, level sub, mode ruangan, aux/bus low-end |
| Musik terlalu keras | Level bus musik vs vokal, SPL, limiter |
| Khotbah kurang jelas | Gain/HPF/EQ mic khotbah, STI/clarity, delay |
| Mixing tidak konsisten | Perbedaan konfigurasi antar ibadah (diff baseline) |
