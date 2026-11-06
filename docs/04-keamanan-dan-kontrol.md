# 04 – Keamanan dan Kontrol

Kemampuan teknis mengontrol mixer **bukan** izin untuk mengubah semua parameter.

## Aturan

1. Tahap pertama **READ-ONLY**.
2. Tidak ada perubahan otomatis pada mixer atau DSP.
3. Simpan **baseline** konfigurasi sebelum eksperimen apa pun.
4. Setiap rekomendasi disertai alasan teknis.
5. Perubahan hanya setelah **persetujuan operator**.
6. Log perubahan + mekanisme **rollback**.
7. Tidak pernah mengubah otomatis: routing, master output, limiter, atau parameter kritis lain.
8. Sediakan **manual override**: operator selalu bisa mengambil alih; mencabut LAN/USB tidak mengganggu audio.

## Rencana penegakan

- Penjaga tulis di lapisan kode (whitelist query-only pada Tahap 1).
- Daftar parameter "terlarang" yang tidak bisa diubah AI walaupun disetujui lewat alur biasa (butuh prosedur terpisah).
- Log append-only: waktu, sumber, nilai lama/baru, alasan, siapa menyetujui.
- Perubahan hanya di luar ibadah pada fase awal; satu perubahan per langkah, dengan nilai lama tersimpan untuk rollback.

## Catatan keselamatan pengukuran

Sweep/noise ke PA hanya di luar ibadah, level rendah, limiter DSP aktif, dengan operator hadir.
