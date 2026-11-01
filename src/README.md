# Alat audit READ-ONLY (Tahap 1)

Hanya pustaka standar Python (3.10+), tanpa instalasi paket.

## Tes (tanpa mixer, memakai mixer tiruan)
    python -I -m unittest discover -s tests -v

## Dump konfigurasi MR18 (nanti, di jaringan yang sama dengan mixer, di luar ibadah)
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
