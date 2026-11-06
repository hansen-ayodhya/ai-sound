# Alat audit READ-ONLY (Tahap 1)

## Cara paling mudah (semua lewat browser)
Klik dua kali `AI-Sound-Engineer.bat`. Browser terbuka di `http://localhost:8765` dengan halaman
Beranda: koneksi + uji kecil, audit lengkap, rekam meter, perawatan, dan laporan. Dari situ ada
Tampilan Mixer dan Tabel. Biarkan jendela hitam tetap terbuka selama dipakai.

Keamanan: server hanya di 127.0.0.1, Host dicek, setiap panggilan API butuh token acak yang hanya
tertanam di halaman server ini, dan semua akses mixer tetap lewat klien READ-ONLY.
Cadangan tanpa browser: `Menu-Teks.bat` (menu teks lama).

## Membuka dari HP, tablet, atau komputer EasyWorship
Klik dua kali `AI-Sound-Engineer-Jaringan.bat` (bukan yang biasa). Jendela hitam dan Beranda di laptop
menampilkan alamat (mis. `http://192.168.1.23:8765`) dan PIN 6 angka. Di perangkat lain yang tersambung
ke jaringan gereja yang sama, buka alamat itu dan masukkan PIN. PIN disimpan di `data/pin.txt`
(hapus file itu untuk membuat PIN baru). Laptop tetap menjadi "mesin"; harus menyala.
Bila Windows menanyakan firewall, izinkan untuk jaringan Private saja.


Perintah di bawah ini hanya untuk yang ingin memakai terminal.

Hanya pustaka standar Python (3.10+), tanpa instalasi paket.

## Tes (tanpa mixer, memakai mixer tiruan)
    python -I -m unittest discover -s tests -v

## Dump konfigurasi MR18 (nanti, di jaringan yang sama dengan mixer, di luar ibadah)
    (UJI KECIL DULU) python -m aise.dump --host <IP_MR18> --only /info /ch/01/config/name
    set PYTHONPATH=src            (Windows cmd)   /   $env:PYTHONPATH="src" (PowerShell)
    python -m aise.dump --host <IP_MR18> --out data/dump1.json
    python -m aise.dump --host <IP_MR18> --out data/dump2.json
    python -m aise.dump --diff data/dump1.json data/dump2.json     # harus IDENTIK

Jaminan read-only: hanya query (tanpa argumen) ke allowlist di `guard.py`, diperiksa dua kali
(sebelum dan sesudah encode), semua pesan dicatat di `data/osc_log.jsonl`.

Belum diverifikasi pada perangkat nyata: port UDP 10024, daftar alamat di `addresses.py`,
allowlist di `guard.py`. Alamat yang tidak dijawab tercatat `null`. Dump penuh dengan banyak
alamat tak dijawab bisa lama (timeout 0,5 s x 2 percobaan per alamat).

## Penampil dump (tanpa server)
Buka `viewer/index.html` di browser (klik dua kali). Pilih `dump1.json` sebagai Dump A, dan
dump lain sebagai Dump B untuk membandingkan. Coba dulu dengan `viewer/sample-dump-a.json`
dan `sample-dump-b.json` (DATA CONTOH, bukan dari mixer). Hanya menampilkan; tidak mengirim apa pun.

## Meter dan laporan
    python -m aise.monitor --host <IP_MR18> --path /meters/1 --seconds 30 --out data/meter1.json
    python -m aise.report data/dump1.json --out data/report.md

Meter: path, format blob, dan pemetaan indeks ke channel BELUM diverifikasi. Laporan: temuan awal
dari konfigurasi saja, belum dibuktikan dengan pengukuran.
