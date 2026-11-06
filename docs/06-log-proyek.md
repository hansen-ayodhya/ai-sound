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

## Update: kode penjaga read-only (offline)

- Ditambahkan `src/aise/` (klien OSC read-only, penjaga, mixer tiruan, dump + diff) dan `tests/`. 8 tes lulus melawan mixer tiruan, termasuk bukti nol pesan tulis.
- BELUM diuji pada MR18 nyata. Yang harus diverifikasi saat di dekat mixer: port UDP, format balasan, daftar alamat, allowlist.
- Langkah berikutnya: saat dekat MR18 (di luar ibadah) simpan scene baseline via aplikasi resmi, lalu jalankan dump dua kali dan diff.

## Update 2026-11-06: pekerjaan offline (B)

- Penjaga diperluas: boleh `/meters <path> [int...]` (baca level) dengan validasi ketat; semua tulis tetap ditolak.
- `aise.meters` + `aise.monitor`: rekam meter dan ringkas puncak/clipping. Format blob dan pemetaan indeks ke channel belum diverifikasi pada MR18.
- `aise.report`: laporan audit Markdown dari dump, dengan temuan enam-unsur (HPF mati di channel suara, kompresor mati, phantom di non-mic, dump kosong). Aturan masih sedikit dan sebagian berdasarkan nama channel.
- `docs/07-ashly-protea-4-8sp.md`: panduan baca DSP, belum dijalankan.
- 22 tes lulus (mixer tiruan). Belum ada yang diuji pada MR18/DSP nyata.
- Berikutnya: uji kecil `--only /info` di dekat MR18, dump penuh x2, lalu koreksi alamat/format berdasarkan balasan.

## Update: menu tanpa mengetik

- `AI-Sound-Engineer.bat` + `aise.menu`: menu bernomor (penampil, uji kecil, audit lengkap, meter, laporan, update, cek program).
- `aise.audit`: cek koneksi cepat, dump 2x, bandingkan, laporan; disimpan per tanggal, baseline lama tidak tertimpa.
- Penampil memuat audit terakhir otomatis saat dibuka dari menu (localhost:8765, hanya dari laptop itu).
- 26 tes lulus. Diuji ujung-ke-ujung dengan mixer tiruan; belum dengan MR18 nyata.

## Update: purwarupa tampilan mixer

- `viewer/mixer.html` (menu pilihan 8): channel strip mirip aplikasi mixer (nama, lampu 48V/HPF/GATE/COMP/EQ, kurva EQ, fader, mute, kiriman bus), bus, main, dan lencana saran AI per channel. Klik strip untuk detail dan kartu saran enam-unsur.
- HANYA MEMBACA. Tombol Setujui/Tolak sengaja nonaktif sampai Tahap 4.
- Angka dB/Hz memakai rumus PERKIRAAN keluarga X Air (bertanda ≈), belum dicocokkan dengan MR18.
- Meter bergerak adalah SIMULASI; belum tersambung ke mixer.
- Audit kini juga menulis `findings.json` (temuan per channel) untuk lencana di tampilan mixer.

## Update: aplikasi web lokal (semua lewat browser)

- `aise.server` + `viewer/home.html`: Beranda dengan tombol Uji kecil, Audit lengkap (wajib centang "tidak ibadah" dan "scene disimpan"), Rekam meter, Update, Cek program, Matikan; laporan terakhir tampil di halaman.
- `AI-Sound-Engineer.bat` kini membuka aplikasi web; menu teks lama tetap ada di `Menu-Teks.bat`.
- Tidak bisa di-hosting online: web di internet tidak bisa menjangkau mixer di jaringan gereja.
- Keamanan: hanya 127.0.0.1, cek Host, token API per sesi, satu pekerjaan mixer pada satu waktu.
- 32 tes lulus; diuji ujung-ke-ujung di browser dengan mixer tiruan. Belum dengan MR18 nyata; .bat belum dicoba di Windows.

## Update: mode live

- `aise.live`: stream meter terus-menerus (`ReadOnlyClient.stream_meters`) + aturan lokal cepat: clipping (≥ -1 dB), panas berulang (≥ -3 dB, 3x/5 dtk), sinyal lemah (puncak -60..-40 dB selama 30 dtk), dan koneksi meter terputus (3 dtk). Ada cooldown agar tidak membanjiri.
- Tampilan Mixer: tombol Mulai/Hentikan pantau live, meter asli, strip menyala (merah/kuning/biru), daftar peringatan. Peringatan disimpan di `data/live-*.jsonl`.
- Selama live, pekerjaan lain ke mixer ditolak (satu koneksi mixer pada satu waktu). Berhenti otomatis setelah 4 jam.
- BELUM: keseimbangan musik vs vokal (butuh meter sesudah fader/bus yang terbukti), deteksi feedback dan frekuensi (butuh audio USB, Tahap 2).
- Belum diverifikasi: path/format meter, pemetaan indeks 0..15 = CH 01..16, ambang yang cocok untuk gereja ini.
- 42 tes lulus; diuji di browser dengan mixer tiruan yang mengalirkan meter.

## Update: kendala USB

- USB MR18 dipakai komputer multimedia untuk playback; tidak dipindah ke laptop. Fitur LAN tetap jalan. Rincian dan opsi di docs/03.

## Update: akses dari perangkat lain di jaringan gereja

- Komputer multimedia = komputer EasyWorship (dikonfirmasi).
- `AI-Sound-Engineer-Jaringan.bat` (`--lan`): HP/tablet/komputer EasyWorship membuka aplikasi lewat alamat IP laptop + PIN 6 angka. Tidak perlu memasang apa pun di komputer EasyWorship.
- Keamanan: PIN wajib untuk perangkat lain (5x salah = kunci 5 menit), sesi cookie HttpOnly/SameSite, token API tetap wajib, Host harus IP pribadi, PIN tidak pernah dikirim ke perangkat lain, folder data hanya membuka .json/.md, update/cek/matikan hanya dari laptop. HTTP tidak terenkripsi: pakai hanya di Wi-Fi gereja berpassword.
- BUKAN akses internet. Akses dari luar gereja (mis. dari rumah) belum dibuat; bila nanti perlu, pilihan aman adalah VPN pribadi (mis. Tailscale), bukan membuka port ke internet.
- 50 tes lulus; diuji di browser ukuran HP lewat IP + PIN.

## Update: versi .exe

- Dibuat otomatis oleh GitHub Actions (`.github/workflows/build-exe.yml`, runner Windows): tes → PyInstaller 6.11.1 (satu file) → uji exe menyajikan Beranda → artifact `AI-Sound-Engineer-exe` (exe + PETUNJUK-EXE.txt, disimpan 90 hari).
- Exe bawaan mode JARINGAN (PIN); `--lokal` untuk hanya komputer itu. Folder `data` di samping exe. Versi tertanam saat build.
- Update = ganti file exe secara manual di luar ibadah (pilihan pengguna). Tombol Update/Cek program disembunyikan di versi exe.
- Perbaikan khusus Windows: UDP "connection reset" dianggap "tidak dijawab" (bukan error).
- Batasan: exe belum ditandatangani → SmartScreen/antivirus bisa memperingatkan.

## Update: rekomendasi visual (EQ dengan bayangan usulan)

- Temuan kini membawa `usulan` nilai konkret (alamat, nilai sekarang, nilai usulan, teks). Tidak pernah dikirim otomatis.
- Aturan baru: boost EQ > +6 dB pada channel suara → usul turun ke +3 dB (risiko feedback/tajam). Low cut usulan 100 Hz (vokal) / 120 Hz (khotbah).
- Tampilan Mixer: grafik EQ besar di panel detail (kurva sekarang hijau, bayangan usulan ungu putus-putus, titik band + panah, garis low cut), daftar "Usulan perubahan"; strip kecil juga menampilkan bayangan.
- Konversi dipindah ke `aise/convert.py` (masih PERKIRAAN; sama dengan objek EST di mixer.html).
- Usulan EQ berbasis suara (mis. potong frekuensi yang menumpuk) menunggu pengukuran audio (Tahap 2).

## Update: Daftar Channel

- `aise/channels.py` + formulir di Tampilan Mixer (tombol "Daftar Channel"): sumber, jenis, channel (stereo L/R, atau Aux/USB return), koneksi, mic, catatan. Disimpan di `data/channels.json`.
- Daftar bawaan = 11 sumber dari operator; nomor channel SENGAJA kosong. Tombol "Isi otomatis dari nama di mixer" mengisi channel kosong dari nama di audit terakhir dan menandainya ungu untuk dicek.
- Aturan AI memakai Daftar Channel bila ada (jenis menentukan: suara/bicara/line, low cut 100/120 Hz, 48V), fallback tebakan nama. Aturan baru: nama mixer tidak cocok dengan daftar; sumber belum diberi channel.
- Menyimpan daftar menghitung ulang findings.json dan report.md audit terakhir. Label peringatan live ikut menyebut sumber.
- 66 tes lulus; diuji di browser (isi otomatis, simpan, label sumber di strip).

## Update: saran visual untuk kompresor, gate, dan kiriman FX/monitor

- Panel detail channel: daftar "Usulan perubahan" di atas, lalu grafik EQ, grafik kompresor & gate (kurva masuk→keluar, garis threshold), dan batang kiriman Bus 1–6 / FX 1–4; semuanya dengan bayangan ungu untuk usulan. Lampu GATE/COMP/EQ/HPF di strip diberi garis ungu bila ada usulan.
- Aturan baru dengan usulan nilai: kompresor mati di vokal (titik awal 3:1, ≈-20 dB), rasio ≥10:1 di vokal (→4:1), gate vokal threshold > -40 dB (→ -50 dB), gate pada sumber line (→ OFF), kiriman FX dari mic khotbah > -20 dB (→ mati).
- Alamat baru (DUGAAN, belum diverifikasi): gate/range, dyn/attack, dyn/release, kiriman FX mix/07..10. Jenis efek FX belum dibaca/dipetakan.
- 73 tes lulus; diuji di browser (desktop dan HP).

## Update: konsultan AI (Google Gemini)

- `aise/ai.py`: dipanggil hanya saat tombol "✦ Analisis AI" ditekan. Mengirim ringkasan angka (peran channel dari Daftar Channel, setting dB/Hz perkiraan, temuan aturan, peringatan live terakhir) + situasi (umum/lagu/khotbah/soundcheck) + pertanyaan operator. Tidak mengirim audio, IP, PIN, atau API key.
- Jawaban wajib JSON terstruktur (enam unsur + keyakinan + usulan parameter dari daftar tertutup). Diperiksa lokal: parameter tak dikenal / di luar rentang dibuang; hasil ditandai "✦ Gemini" dan bisa ditampilkan sebagai bayangan ungu. Tidak pernah diterapkan ke mixer.
- Model tidak ditulis tetap: daftar model diambil dari Google untuk kunci itu, dipilih Flash stabil terbaru; bisa diganti di Beranda.
- API key di `data/gemini.json`, hanya bisa diatur dari komputer yang menjalankan aplikasi, tidak pernah dikirim ke browser. Folder data kini hanya membuka hasil audit/meter/AI; gemini.json, pin, ip, channels tertutup.
- Diuji dengan server Gemini tiruan (belum dengan kunci asli dari sesi ini).

## Update: Klip Suara (Gemini bisa "mendengar")

- Halaman `viewer/clips.html`: unggah rekaman HP (m4a/mp3/wav), pilih potongan 5/10/20/30 dtk (bawaan 10), browser menghitung spektrum 1/3 oktaf (relatif 500 Hz–2 kHz), kelompok bass/low-mid/presence/air, puncak sempit yang bertahan (kandidat dengung/feedback), level & clipping; indikasi diberi label "indikasi", bukan pengukuran terkalibrasi.
- Klip disimpan (WAV 16 kHz mono) di `data/clips/` (maks 50) untuk dibandingkan sebelum/sesudah.
- Kirim ke Gemini hanya dengan tombol + centang izin per klip. Yang dikirim: potongan audio + angka analisis + ringkasan setting. Jawaban: penilaian per aspek (kejelasan vokal/khotbah, boomy, tajam, gema, feedback, keseimbangan, terlalu keras) + prioritas enam unsur; usulan angka tetap diperiksa.
- Biaya: ±32 token/detik audio (10 dtk ≈ 320 token) menurut dokumentasi Gemini.
- Operator mengizinkan semua jenis klip (lagu, khotbah, soundcheck) dikirim, dengan izin per klip.
- 84 tes lulus; diuji di browser dengan rekaman sintetis + Gemini tiruan.

## Catatan

Sejak 2026-11-06 proyek ini punya repo sendiri: `hansen-ayodhya/ai-sound` (branch `main`). Sebelumnya berada di folder `ai-sound-engineer/` pada repo `hansen-ayodhya/wtc` (branch `claude/ai-sound-engineer-church-audit-r81rqp`); riwayat commit ikut dipindahkan. Pengembangan selanjutnya hanya di repo ini.
