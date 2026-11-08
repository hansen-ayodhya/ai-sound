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

## Update: perbaikan kolom API key + zoom & jumlah channel

- Kolom API key tersembunyi bila Beranda dibuka lewat IP jaringan komputer itu sendiri (dianggap perangkat lain). Kini semua alamat milik komputer yang menjalankan aplikasi dianggap lokal; bila tetap dari perangkat lain, ada pesan jelas berisi alamat localhost. Kolom password kini diberi gaya yang sama.
- Tampilan Mixer: Zoom −/+, "Pas layar", 100%; jumlah channel 4/6/8/12/Semua dengan halaman ◀ ▶; "Hanya yang ada di Daftar Channel"; tampilkan/sembunyikan Bus & Main. Pilihan disimpan per perangkat (browser). HP: bawaan 4 channel, zoom 80%.

## Update: API key dari perangkat mana pun + pilih channel bebas + landscape HP

- Kolom API key selalu tampil; kunci boleh DITULIS dari perangkat yang sudah masuk (PIN), tetap tidak pernah dikirim balik ke browser. Format kunci dilonggarkan (karakter cetak 20–300). Tombol Tampilkan/Sembunyikan + baris diagnosis (dibuka dari mana, alamat).
- Tampilan Mixer: pilihan 4/6/8/12 + halaman diganti "☰ Pilih channel": centang bebas tiap CH 01–16, Bus 1–6, Main; tombol cepat Semua / Kosongkan / Sesuai Daftar Channel / Hanya yang ON. Disimpan per perangkat.
- HP: tombol "↻ Landscape" (layar penuh + kunci mendatar bila HP mengizinkan; bila tidak, petunjuk putar otomatis). Saat mendatar, header diringkas dan "Pas layar" menyesuaikan lebar dan tinggi.

## Update: kunci Gemini format baru "AQ."

- Google AI Studio kini membuat kunci "Auth key" berawalan `AQ.` (bukan `AIza`). Pengecekan lama menolak karena ada titik → sudah dilonggarkan (versi 0a7b353+); petunjuk di Beranda kini mengenali kedua format.
- Kunci dikirim lewat header `x-goog-api-key`; bila ditolak (401/403), dicoba sekali lewat parameter `?key=`. Pesan khusus untuk `ACCESS_TOKEN_TYPE_UNSUPPORTED` (dilaporkan sebagian pengguna kunci AQ.) dan izin proyek.
- Belum diuji dengan kunci AQ. asli dari sesi ini.

## Update: saran Gemini dalam grafik + tiga warna

- Gemini kini diminta memberi usulan angka TITIK AWAL (langkah kecil) untuk tiap saran yang menyangkut satu channel; tetap disaring rentang/parameternya.
- Hasil "✦ Analisis AI" menampilkan grafik (EQ / kompresor & gate / kiriman) langsung di bawah tiap saran, dengan tombol "Buka detail CH". Hasil Klip Suara juga menjadi analisis terakhir dan punya tautan "Lihat di grafik" (mixer.html#ch=N).
- Tiga warna konsisten: hijau = sekarang, ungu = usulan program (aturan lokal), biru muda = usulan Gemini. Kedua bayangan digambar bersamaan; lampu strip, legenda, daftar usulan, batang kiriman, dan kartu ikut berwarna sesuai sumber.
- Perbaikan: definisi ganda hasProposalRe (yang lama menimpa yang baru) dihapus.

## Update: halaman Data DSP (Ashly Protea 4.8SP)

- `viewer/dsp.html` + `aise/dsp.py`. Program TIDAK berkomunikasi dengan DSP.
- Unggah file preset (maks 5 MB) → disimpan di `data/dsp/presets/`, jenisnya dikenali (XML/JSON/INI/teks/biner) dan isinya ditampilkan sekilas. Format preset Ashly belum diketahui; belum ada pembaca otomatis.
- "✦ Baca dengan Gemini" (izin per kali): dari tangkapan layar software Protea, atau dari preset bila isinya teks. Hasil = DRAF di formulir (kotak biru muda), nilai di luar rentang dibuang, yang tak terbaca dicatat; operator memeriksa lalu Simpan.
- Formulir 8 output (peran, nama, sumber, gain, mute, polaritas, delay, HPF/LPF + tipe, limiter, PEQ) + 4 input → `data/dsp/dsp.json`.
- Saran program sistem: crossover sub–utama tumpang tindih / berlubang, utama tanpa HPF, limiter belum tercatat, boost EQ sistem > +6 dB, beda kiri–kanan, beda polaritas sub–utama, delay semua 0. Grafik crossover/EQ sistem dengan bayangan ungu usulan.
- Data DSP + temuan DSP ikut dikirim ke Gemini pada Analisis AI dan Klip Suara.
- 94 tes lulus; diuji di browser dengan preset contoh + Gemini tiruan.
- Berikutnya: bila operator mengirim contoh file preset asli, buat pembaca otomatis sesuai formatnya.

## Update: setting ikut live, tombol Baca setting, Gemini di grafik saat data contoh, catatan ringkas

- Selama live, setting mixer dibaca ulang otomatis (5 dtk setelah mulai, lalu tiap 60 dtk; hanya alamat yang terbukti dijawab) → `data/live-setting/` + latest.json; Tampilan Mixer memuat ulang saran/grafik otomatis.
- Tombol "↻ Baca setting": saat live mempercepat pembacaan; saat tidak live membaca sekali (pakai IP tersimpan).
- Penyebab "usulan Gemini tidak di grafik": tanpa audit (melihat data contoh) semua usulan dibuang. Kini usulan tetap disimpan tanpa data mixer, dan bila operator melihat data contoh, data contoh ikut dikirim ke Gemini (berlabel DATA CONTOH). Hasil menampilkan berapa usulan yang digambar atau alasannya.
- Catatan umum di atas strip jadi satu baris yang bisa dibuka; peringatan live bisa diringkas. Pilihan diingat per perangkat.
- Beban ke mixer (perkiraan, belum diukur di MR18): meter ~20 paket kecil/dtk dari mixer; query dibatasi ≤50/dtk; baca ulang setting tiap 60 dtk. Pemrosesan audio MR18 terpisah dari jaringan.

## Update 2026-11-07: uji pertama di MR18 asli

- Koneksi OSC berhasil: port 10024, MR18 firmware 1.16, 777/777 alamat dijawab, nama channel terbaca.
- Dicocokkan dengan M-AIR oleh operator: nama channel, fader, HPF on/off, gain, frekuensi HPF → tanda ≈ dihapus untuk fader, gain, HPF. EQ band terbaca 4 (sebelumnya satu band tersaring karena Q kosong; diperbaiki).
- DSP: software Protea yang terpasang adalah versi "NE" (jaringan), bukan USB; perlu versi non-NE untuk 4.8SP via USB. Belum tersambung.
- Belum diverifikasi: kompresor, gate, kiriman bus/FX (alamat tebakan), pemetaan meter ke channel, tipe band EQ (shelf).

## Update 2026-11-07: data contoh dihapus

- File `viewer/sample-*.json`, tombol "Data contoh", banner DATA CONTOH, dan opsi `pakai_contoh` di Analisis AI dihapus (sudah tersambung ke MR18 asli). Tampilan kosong kini memberi petunjuk: ↻ Baca setting / Audit di Beranda.

## Update 2026-11-07: verifikasi pelaksanaan rekomendasi

- `aise/recs.py` + `data/recs.json` + `GET /api/recs`: tiap rekomendasi yang punya usulan angka dicatat bersama nilai awalnya, lalu dibandingkan dengan setting mixer setiap kali terbaca (live 60 dtk, ↻ Baca setting, Audit, atau saat halaman dimuat).
- Status: belum / sebagian / selesai. Toggle: harus sama persis. Rasio kompresor: ±1 langkah. Angka lain: selesai bila mencapai ≥70% jarak ke usulan atau selisih ≤0,03. Rekomendasi aturan lokal yang aturannya MASIH memicu tidak pernah dianggap selesai. Usulan DSP tidak dilacak (DSP tidak terbaca). Id Gemini diberi cap waktu supaya tiap analisis dilacak sendiri.
- Tampilan: yang selesai hilang dari saran aktif (lencana ⚠/💡, bayangan grafik) dan diganti lencana ✓ hijau di strip + bagian "✓ Sudah dilakukan" di panel channel (jam verifikasi, nilai awal, nilai sekarang); yang sebagian diberi status kuning; yang sempat selesai lalu dibatalkan ditandai "↺". Baris sumber menampilkan jumlah ✓.
- 108 tes lulus; diuji di browser dengan mixer tiruan yang diubah operator.

## Update 2026-11-07: peringatan live lebih jelas + perubahan dari M-AIR tampil realtime

- Peringatan live: hanya meter channel input (indeks 0–15) yang dinilai; meter lain (bus/main/FX, dulu berlabel "Meter 25") diabaikan karena belum dipetakan. Channel yang di-mute tidak diberi peringatan "sinyal kecil". Pesan dipersingkat (satu kalimat + tindakan); nama channel ada di kolom label.
- Realtime: selama live, aplikasi berlangganan kabar perubahan mixer (`/xremote`, diperbarui tiap 8 dtk). Ini satu-satunya tambahan di penjaga read-only: tanpa argumen, hanya MENERIMA; dengan argumen tetap ditolak. Kabar perubahan (EQ, fader, HPF, dll. dari M-AIR) langsung diterapkan ke strip dan panel channel yang terbuka (posisi gulir terjaga), strip disorot biru 2,5 dtk, lalu ±2,5 dtk setelah reda aturan/saran/status ✓ dihitung ulang. Server menggabungkan perubahan ke `live-setting/dump1.json` (jeda 1,5 dtk). Baca ulang penuh tiap 60 dtk tetap berjalan sebagai cadangan.
- BELUM terbukti di MR18 asli: bahwa mixer benar-benar mengirim kabar setelah /xremote. Bila tidak ada kabar, tampilan tetap diperbarui oleh pembacaan 60 dtk/tombol Baca setting.
- 112 tes lulus; diuji di browser dengan mixer tiruan yang mendorong perubahan.

## Update 2026-11-07: aturan EQ + Ukur Ruangan

- Aturan EQ dari setting mixer (`live.analyze_eq_problems`, `GET /api/live/eq-problems`, tampil di panel channel): boomy, berlumpur, harsh, terlalu cerah, kurang jelas, potongan di bawah 60 Hz, gain ekstrem (≥9 dB), dua band bertumpuk. Bug awal: endpoint memanggil fungsi yang tidak ada sehingga tidak ada peringatan; kini diuji lewat server.
- Halaman baru `viewer/room.html` + `viewer/room-core.js` (semua dihitung di browser, tidak menyentuh mixer): pink noise dari laptop (atau unduh WAV), rekam lewat mic/input USB atau pilih file rekaman, beberapa titik ukur, spektrum 1/3 oktaf (FFT 16384, jendela Hann, rata-rata daya), target datar atau pedoman umum, usulan maks 5 band EQ (potong lebih disukai; boost maks +3 dB; cekungan sempit dianggap masalah penempatan, bukan EQ), keyakinan diturunkan bila hasil berbeda jauh antar titik. Rentang yang dinilai dibatasi menurut jenis mic (mic vokal tidak datar). Hanya BENTUK kurva dinilai karena mic tidak terkalibrasi.
- BELUM terbukti di perangkat asli: mic Shure wireless lewat MR18/USB audio masuk ke browser, dan kecocokan usulan dengan telinga di ruangan. Diuji dengan rekaman sintetis (tonjolan 200 Hz, cekungan 3,15 kHz) di Chromium.

## Update 2026-11-07: visual rekomendasi fader dan gain + tombol Strip: Fader/Gain

- Tombol "Strip: Fader / Gain" (tersimpan per perangkat) mengganti isi fader di semua strip channel: posisi fader atau gain preamp (rentang -12..+60 dB, sudah cocok dengan M-AIR). Usulan digambar sebagai garis putus-putus di jalur fader (ungu = program, biru muda = Gemini) dengan angka usulan di bawahnya; usulan untuk mode lain tampil sebagai teks ("gain → +31 dB").
- Panel channel punya bagian "Gain & Fader" (batang sekarang + bayangan usulan). Lampu 48V tidak lagi ikut menyala karena usulan gain.
- Gemini boleh mengusulkan `gain_db` (-12..+60) selain `fader_db`; 48V tetap dilarang.
- Aturan program baru dari meter live: clipping -> usulan gain turun 4 dB, hampir clipping berulang -> turun 3 dB (id `live-gain-chN`, berlaku 60 dtk setelah kejadian terakhir, dilacak oleh recs sehingga ditandai ✓ setelah operator menurunkan gain). Bergantung pada pemetaan meter -> channel yang BELUM terbukti.
- Ukur Ruangan: tombol "Rekam dengan HP" (capture) dan catatan mematikan EQ/low cut/kompresor/gate di channel sumber pink noise.
- Tes: tests/test_level_recs.py; uji browser dengan mixer tiruan (mode fader/gain, bayangan, panel).

## Update 2026-11-07: tampilan mixer dirapikan, langsung live, fader gaya M-AIR

- Header satu baris 38 px: nama, status koneksi ("LIVE · 20 fps", atau pesan jelas bila belum terhubung), pilihan strip Fader/Gain, ↻ baca setting, ☰ pilih channel, ✦ AI, dan menu ⋯ (pantau live, Daftar Channel, Audit terakhir, Buka dump, zoom, halaman lain, catatan). Bendera status, baris sumber, bar zoom, dan catatan kaki dipindah ke menu/tooltip.
- Halaman langsung memulai pemantauan live begitu dibuka (butuh IP mixer yang tersimpan di Beranda). Meter simulasi dan tombolnya dihapus; sebelum ada data, meter tampak mati.
- Strip bergaya M-AIR: skala 10/5/0/-5/-10/-20/-30/-50/-∞ berjarak sama (sesuai posisi mentah k/8), tombol fader lebar bergaris putih, meter hijau-kuning-merah di kanan dengan penunjuk puncak dan lampu clip, nilai dB di atas, nama channel di bawah. Garis putus-putus usulan (ungu/biru muda) mengikuti skala yang sama. Strip tidak lagi memuat kurva EQ mini dan batang kiriman; keduanya ada di panel detail.
- Peringatan live default diringkas satu baris.

## Update 2026-11-07: Mode kontrol (menerapkan rekomendasi dari aplikasi)

- Pertama kalinya aplikasi bisa MENGUBAH mixer, lewat jalur tulis terpisah `aise/writer.py`. `ReadOnlyClient` dan `guard.py` tidak diubah (tes tetap membuktikan klien baca menolak tulis).
- Mati secara default. Dinyalakan dari menu ⋯ → "Nyalakan mode kontrol" dengan dua centang (scene sudah disimpan, paham perubahan langsung terdengar). Syarat: pantau live berjalan. Mati sendiri setelah 10 menit tanpa aktivitas; nilai terakhir dibiarkan (tidak dipulihkan paksa). Boleh dari semua perangkat yang sudah masuk (PIN).
- Daftar putih (`writer.SPECS`) channel 01-16: gain, fader, low cut (on/frekuensi), EQ (on, gain, frekuensi, Q tiap band), kompresor (on, threshold, rasio). TIDAK PERNAH: 48V, mute, routing, fader Main/bus, kiriman, gate, scene, reset. Angka kompresor masih perkiraan (≈), ditandai di UI.
- Cara kerja: tombol "Terapkan" pada tiap titik usulan -> slider dari nilai awal (dibaca langsung dari mixer saat itu) ke target rekomendasi. Mouse atau keyboard (← → 2%, Shift 10%, Home awal, End rekomendasi). Target harus cocok dengan rekomendasi yang tercatat di recs. Slider tidak bisa melewati awal/target. Sakelar dan rasio memakai bilangan bulat; menaikkan gain dan sakelar butuh konfirmasi; ayunan gain maks 20 dB.
- Pencegah glitch: maks 10 kiriman/detik per parameter (3 per tick total), batas langkah per kiriman (gain 1 dB, EQ gain 1 dB, fader/frekuensi 0,02, threshold 0,05), nilai terbaru menggantikan yang antre, maks 6 parameter sekaligus, baca balik setelah selesai (verifikasi), dan kontrol dilepas bila operator mengubah parameter yang sama di M-AIR (gema dari kiriman sendiri diabaikan).
- Catatan: `data/changes.jsonl` (arm, kunci, restore, konflik, verifikasi, dll.), `data/control-snapshot-*.json` (nilai awal saat dinyalakan). Tombol "Kembalikan semua perubahan" dan "Kembalikan ke awal" per titik.
- Rekomendasi EQ aturan kini ikut dicatat recs (id memuat nomor band) sehingga bisa diterapkan dan ditandai ✓. Fader strip berwarna merah saat mode Gain.
- Bug tampilan realtime diperbaiki: muat ulang file setting bisa membuat strip mundur sebentar; kini kabar perubahan terbaru diterapkan ulang setelah muat ulang.
- BELUM terbukti di MR18 asli: menulis nilai lewat OSC, gema perubahan ke pelanggan /xremote dari tulisan sendiri, dan pembulatan nilai oleh mixer. Uji pertama sebaiknya di luar ibadah, scene tersimpan, pada channel kosong. Diuji dengan mixer tiruan: tes tests/test_control.py dan uji browser.

## Update 2026-11-07: saran dinamis + memori belajar ruangan

- Daftar "Peringatan live": yang lebih tua dari 2 menit disembunyikan otomatis ("N lama disembunyikan"); tetap ada di data/live-*.jsonl. Server menambahkan `ago` (detik) pada tiap peringatan.
- Menu ⋯ → "Analisis ulang sekarang" dan "Otomatis: Mati/5/10 menit" (default mati, memakai token). Otomatis dilewati bila setting mixer tidak berubah sejak analisis terakhir, dan mati sendiri bila batas pemakaian Gemini tercapai.
- Memori belajar lokal (`aise/memori.py`, data/memori/catatan.jsonl, maks ±2000 catatan, tanpa API key): saran baru, rekomendasi yang terverifikasi selesai, perubahan lewat Mode kontrol (diterapkan/dikembalikan/diubah operator), masalah clip/hot per channel, hasil Ukur Ruangan (tombol "Simpan ke memori AI"), dan penilaian operator ("Bagus / Kurang" pada rekomendasi yang ✓).
- `profil()` meringkas (jumlah catatan, masalah berulang ≥2 kali, 20 perubahan terakhir beserta penilaian, ukur ruangan terakhir, `cukup_data` bila ≥5 penilaian) dan ikut ke prompt Gemini sebagai `riwayat`; SYSTEM menegaskan itu bukti terbatas dari satu ruangan, jangan mengulang usulan yang dinilai "kurang", dan sebut bila data belum cukup.
- Halaman `memori.html` (menu "Memori AI"): lihat profil dan catatan terbaru, hapus per jenis atau semua. API: GET /api/memori, POST /api/memori/{nilai,ukur,hapus}.
- Belum terbukti: manfaat riwayat bagi kualitas saran Gemini (butuh banyak ibadah dan penilaian); sekarang hanya mengumpulkan dan meringkas tanpa menyimpulkan sendiri.
- Tes: tests/test_memori.py; uji browser menu, halaman Memori AI, dan Ukur Ruangan.

## Update 2026-11-07: batas peringatan 5 menit, nama channel tanpa akhiran

- Peringatan live disembunyikan setelah 5 menit (sebelumnya 2); tetap ada di log.
- Akhiran angka otomatis pada nama channel dari mixer (mis. "Lead Keyb_27", "Bass_19") tidak ditampilkan lagi: `channels.clean_name` (Python: label live, laporan, prompt Gemini, isi otomatis Daftar Channel) dan `cleanName` di viewer (strip, panel, pemilih channel, tabel). Hanya `_` + 1-3 angka di akhir yang dibuang. Data mentah di dump tidak diubah.

## Update 2026-11-08: rekaman HP + rekaman mixer ke Gemini (panel ✦ Analisis AI)

- Dua slot rekaman di panel Analisis AI: "Suara di ruangan (HP)" dan "Rekaman dari mixer" (opsional, untuk dibandingkan). Tiap slot: 🎤 Rekam (hanya bila browser mengizinkan mic: localhost; slot mixer punya pilihan input, mis. USB MR18), 📱 Rekam dengan HP (aplikasi perekam HP lewat `capture`), Pilih file, Hapus. Maks 20 detik, diubah ke WAV mono 16 kHz di browser.
- Yang dikirim ke Gemini: kedua rekaman (diberi peran), data setting mixer seperti analisis biasa, level meter live saat kirim, spektrum 1/3 oktaf relatif tiap rekaman (dihitung di browser dari rekaman asli, 24 pita) dan selisih "ruangan - mixer" bila ada kedua rekaman. Prompt menegaskan: mic HP tidak terkalibrasi, frekuensi dari angka spektrum, bukti terbatas, rekaman mungkin tidak dari momen yang sama.
- Server (`_clean_audio`): wajib centang izin, maks 2 rekaman dengan peran berbeda, WAV valid 0,5-30 dtk; tidak disimpan di laptop. Hasil memakai jalur analisis yang sama (usulan tercatat, bisa diterapkan lewat Mode kontrol).
- Belum terbukti: kualitas perbandingan HP vs mixer pada ruangan nyata, dan perekaman dari input USB MR18 di Windows (butuh driver).
- Tes: tests/test_ai_audio.py; uji browser dua slot dengan file rekaman.

## Update 2026-11-08: peringatan live dinilai realtime + abaikan rekomendasi

- Peringatan live tidak lagi hilang karena timer. Server menilai tiap peringatan terhadap kondisi sekarang dan menandainya `resolved` (dengan `reason`): clip/hot teratasi bila level tidak mendekati clipping selama 15 dtk, atau gain sudah turun >= 3 dB dari saat peringatan, atau channel di-mute; sinyal lemah teratasi bila sinyal sudah normal, gain naik >= 3 dB, atau di-mute; "data meter berhenti" teratasi saat data kembali. Sekali teratasi tidak hidup lagi (kejadian baru = peringatan baru).
- Tampilan: hanya yang aktif ditampilkan ("N aktif · M teratasi"); tautan "tampilkan yang teratasi" menunjukkan sisanya dengan tanda ✓ dan alasannya.
- Tombol "🚫 Abaikan rekomendasi ini" pada kartu rekomendasi (termasuk kartu EQ di panel channel). Yang diabaikan tidak ditampilkan dan tidak dilacak (`data/diabaikan.json`, kunci = channel + himpunan alamat usulan, jadi usulan serupa dari aturan maupun Gemini ikut tersaring). Menu ⋯ -> "Rekomendasi diabaikan (N)" untuk melihat dan mengembalikan. Pengabaian dicatat di memori AI dan disebut di prompt Gemini (`diabaikan_operator`).
- Panel detail kini mulai di bawah header dan header di atas panel, supaya menu ⋯ tetap bisa dipakai saat panel terbuka.
- Tes: tests/test_alerts_ignore.py; uji browser.

## Update 2026-11-08: klik di luar panel menutup panel rekomendasi

- Klik di luar panel detail menutupnya (fase capture, jadi klik pada strip lain langsung membuka panel channel itu, dan menu ⋯ tetap bisa membuka panel "Rekomendasi diabaikan"). Klik yang dimulai di dalam panel (mis. menggeser slider sampai keluar panel) tidak menutupnya; kotak konfirmasi Mode kontrol dikecualikan. Esc tetap menutup.

## Update 2026-11-08: perbaikan pembacaan setting dan grafik kompresor/gate

- Bug: saat live, satu balasan UDP yang hilang pada pembacaan ulang setting menimpa nilai yang tadinya terbaca dengan kosong, lalu alamat itu tidak ditanyakan lagi (daftar dipangkas). Akibatnya threshold/release kompresor tampil "—" dan kurva "sekarang" datar. Kini: nilai lama dipertahankan bila balasan hilang, alamat baru dilewati setelah 3 kali berturut-turut tidak dijawab, dan membaca memakai 1 ulangan.
- Grafik kompresor/gate: bila ON tetapi threshold/rasio tidak terbaca, kurva "sekarang" tidak digambar (teks "nilai sekarang tidak terbaca") dan baris keterangan menyarankan ↻. Baris usulan menulis hanya bagian yang benar-benar diusulkan (mis. "Gemini mengusulkan: threshold -22 dB (sudah sama dengan mixer). Bagian lain tidak diubah."); di grafik usulan, bagian yang tidak diusulkan mengikuti nilai mixer sekarang. Karena itu grafik usulan ikut bergeser bila Anda mengubah bagian lain di mixer.
- Tes: tests/test_refresh_keep.py.

## Update 2026-11-08: fader yang tidak terbaca dan pemulihan otomatis

- Channel yang fader/gain/statusnya tidak terbaca (mis. DRUM L/R) kini menampilkan tanda tempat fader putus-putus "?" (bukan kosong) dan nilai "—", dengan tooltip penjelasan.
- Pemulihan otomatis: bila ada fader/gain/status channel yang tampil bernilai kosong, tampilan meminta pembacaan ulang setting (dicek tiap 5 detik, maks 1x per 15 detik). Tombol ↻ (dan permintaan itu) juga menghapus daftar alamat yang sempat dilewati, sehingga semuanya ditanyakan lagi.
- Akar masalahnya sama dengan perbaikan sebelumnya (balasan UDP yang hilang saat pembacaan ulang menghapus nilai dan memangkas alamat); exe lama masih punya bug itu.
- Tes: tests/test_refresh_keep.py (tambahan); uji browser fader yang terbaca belakangan.

## Update 2026-11-08: peringatan "sinyal sangat kecil" mati secara default

- Peringatan sinyal sangat kecil (sumber yang memang tidak dipakai) kini mati secara default; saklar ada di menu ⋯ -> Peringatan (tersimpan di data/prefs.json, berlaku langsung tanpa memulai ulang live). Saat dimatikan, peringatan lama jenis ini ikut ditandai teratasi ("dimatikan"). Peringatan clipping, hampir clipping, dan koneksi tidak berubah.
- Tes: tests/test_alerts_ignore.py (tambahan).

## Update 2026-11-08: Bus siaran (YouTube lewat USB)

- `aise/siaran.py` + menu ⋯ -> "Siaran YouTube (bus USB)": bus siaran dipilih lewat pengaturan (`prefs.json` `stream_bus`, bawaan Bus 3 sesuai keterangan operator, 0 = tidak ada). Panel menampilkan setting bus (mute, fader, EQ, kompresor), batang kiriman tiap channel ke bus itu, temuan, dan tombol "Cek routing USB".
- Temuan aturan (read-only, juga masuk daftar catatan umum dan prompt Gemini sebagai `siaran_youtube`): bus di-mute, fader di atas 0 dB, boost EQ bus > 4 dB, bus tanpa kompresor/pembatas, kiriman channel di atas 0 dB, vokal/khotbah jauh lebih pelan dari musik/drum (selisih kiriman >= 6 dB, memakai Daftar Channel), dan daftar cek sisi komputer streaming (meter, sample rate 48 kHz, matikan peredam bising/AGC, sumber USB = bus yang benar, target sekitar -14 LUFS).
- Pembacaan rutin menambah alamat detail bus siaran saja (kompresor threshold/rasio, EQ 4 band) supaya pembacaan tetap cepat.
- Cek routing USB: menanyakan ~20 alamat kandidat (`/routing/usb/..`, `/outputs/usb/../src`, dst.), hanya membaca. Alamat ini DUGAAN dan belum terbukti untuk MR18; bila tidak ada jawaban, panel menunjukkan langkah cek manual di M-AIR.
- Batas: level sinyal bus belum bisa dipantau (pemetaan meter bus belum terbukti); kiriman channel bukan level sinyal. Tidak ada penulisan ke bus (daftar putih Mode kontrol tidak memuat bus).
- Tes: tests/test_siaran.py.

## Update 2026-11-08: pengecekan siaran YouTube berkala

- Kolom link YouTube di panel Siaran (disimpan di prefs.json: `youtube_url`, `stream_check_min`; jeda bawaan 10 menit, pilihan mati/5/10/15/30). Link divalidasi ketat (https, host youtube.com/youtu.be saja, tanpa spasi/kredensial) dan diberikan ke yt-dlp setelah `--` tanpa shell.
- `aise/stream.py`: yt-dlp mengambil alamat audio, ffmpeg mengambil 15 detik (WAV mono 48 kHz); analisis lokal tanpa token: puncak, RMS, clipping (sampel >= 0,99 FS), kekerasan terintegrasi (BS.1770, uji sinus -20 dBFS = -23,0 LUFS), senyap, dan spektrum 1/3 oktaf (tidak dinilai otomatis, hanya untuk Gemini). Status baik/perhatian/bermasalah/senyap/error dengan pesan bahasa biasa; riwayat di data/stream-checks.jsonl; lencana ⚠ di menu bila bermasalah.
- Gemini hanya lewat tombol "Analisis Gemini" (konfirmasi, memakai token): cuplikan segar 16 kHz + analisis lokal + data mixer/Bus siaran dikirim dengan peran `youtube`; prompt menjelaskan bahwa itu tertunda dan sudah dikompres ulang YouTube.
- Alat luar: Pasang-Alat-Siaran.bat (winget: yt-dlp.yt-dlp, Gyan.FFmpeg) ikut paket exe; alat dicari di folder tools/ di samping exe, folder exe, lalu PATH.
- BELUM terbukti: pengambilan audio dari siaran YouTube sungguhan (diuji dengan yt-dlp/ffmpeg palsu dan WAV sintetis); yt-dlp bisa rusak bila YouTube berubah (perlu diperbarui).
- Tes: tests/test_stream.py.

## Update 2026-11-08: pemasangan alat siaran dibuat lebih pasti

- Pasang-Alat-Siaran.bat kini mengunduh yt-dlp.exe (rilis GitHub) dan ffmpeg.exe (BtbN FFmpeg-Builds) langsung ke folder `tools` di samping exe lewat PowerShell (TLS 1.2), tanpa winget dan tanpa PATH. File harus disimpan di folder yang sama dengan exe.
- `find_tool` mencari di `tools/`, folder exe, `data/tools`, lokasi winget (`%LOCALAPPDATA%\Microsoft\WinGet`), lalu PATH. Pesan "belum terpasang" kini menyebut folder `tools` yang dicari (juga di panel).
- BELUM terbukti: skrip .bat belum dijalankan di Windows sungguhan (tidak ada Windows/PowerShell di lingkungan pengembangan); logika pencarian alat diuji dengan tes.

## Catatan

Sejak 2026-11-06 proyek ini punya repo sendiri: `hansen-ayodhya/ai-sound` (branch `main`). Sebelumnya berada di folder `ai-sound-engineer/` pada repo `hansen-ayodhya/wtc` (branch `claude/ai-sound-engineer-church-audit-r81rqp`); riwayat commit ikut dipindahkan. Pengembangan selanjutnya hanya di repo ini.
