/* Perhitungan "Ukur Ruangan": spektrum 1/3 oktaf dari rekaman pink noise + rekomendasi EQ.
 * Semua dihitung di browser. Tidak ada yang dikirim ke mixer maupun ke internet.
 * Mic tidak terkalibrasi: yang dinilai hanya BENTUK kurva (bukan level mutlak). */
(function (root) {
  "use strict";
  const NOMINAL = [63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500];
  const CENTERS = NOMINAL.map((_, i) => 1000 * Math.pow(2, (i - 12) / 3));
  const FFT_N = 16384;
  const REF_RANGE = [250, 4000];
  const TRUST = {
    genggam: { label: "Mic genggam vokal (mis. SM58 / Beta 58)", lo: 100, hi: 4000 },
    lavalier: { label: "Mic lavalier / headset", lo: 200, hi: 4000 },
    ukur: { label: "Mic ukur / mic datar", lo: 63, hi: 10000 },
  };
  const TARGETS = {
    datar: { label: "Datar (pink noise tampak lurus)", pts: [[63, 0], [12500, 0]] },
    umum: { label: "Pedoman umum (bass naik sedikit, treble turun sedikit)",
      pts: [[63, 3], [125, 2.5], [250, 1], [500, 0], [1000, 0], [2000, -0.5], [4000, -1.5], [8000, -3], [12500, -4]] },
  };
  const MAX_BANDS = 5;
  const CUT_MIN = 3, BOOST_MIN = 4, STOP_BELOW = 2.5, MAX_CUT = -6, MAX_BOOST = 3, SPREAD_WARN = 5;

  function fft(re, im) {
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = -2 * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang), half = len >> 1;
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < half; k++) {
          const a = i + k, b = a + half;
          const xr = re[b] * cr - im[b] * ci, xi = re[b] * ci + im[b] * cr;
          re[b] = re[a] - xr; im[b] = im[a] - xi; re[a] += xr; im[a] += xi;
          const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
        }
      }
    }
  }

  const HANN = new Float64Array(FFT_N);
  for (let i = 0; i < FFT_N; i++) HANN[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (FFT_N - 1));

  // Rata-rata daya per bin dari banyak potongan sinyal.
  function Averager(sampleRate) {
    this.sr = sampleRate;
    this.power = new Float64Array(FFT_N / 2);
    this.frames = 0;
    this.peak = 0;
    this.sumSq = 0;
    this.count = 0;
  }
  Averager.prototype.add = function (samples) {
    if (samples.length < FFT_N) return false;
    const re = new Float64Array(FFT_N), im = new Float64Array(FFT_N);
    for (let i = 0; i < FFT_N; i++) {
      const v = samples[i];
      const a = Math.abs(v);
      if (a > this.peak) this.peak = a;
      this.sumSq += v * v;
      re[i] = v * HANN[i];
    }
    this.count += FFT_N;
    fft(re, im);
    for (let k = 0; k < FFT_N / 2; k++) this.power[k] += re[k] * re[k] + im[k] * im[k];
    this.frames++;
    return true;
  };
  Averager.prototype.rmsDb = function () { return this.count ? 10 * Math.log10(this.sumSq / this.count + 1e-20) : -Infinity; };
  Averager.prototype.peakDb = function () { return 20 * Math.log10(this.peak + 1e-10); };
  Averager.prototype.bands = function () {
    if (!this.frames) return null;
    const binHz = this.sr / FFT_N, out = [];
    for (let i = 0; i < CENTERS.length; i++) {
      const lo = CENTERS[i] / Math.pow(2, 1 / 6), hi = CENTERS[i] * Math.pow(2, 1 / 6);
      if (hi > this.sr / 2) { out.push(null); continue; }
      let sum = 0;
      for (let k = Math.ceil(lo / binHz); k * binHz < hi; k++) sum += this.power[k];
      out.push(10 * Math.log10(sum / this.frames + 1e-30));
    }
    return out;
  };

  function interp(pts, f) {
    if (f <= pts[0][0]) return pts[0][1];
    for (let i = 1; i < pts.length; i++) {
      if (f <= pts[i][0]) {
        const t = Math.log(f / pts[i - 1][0]) / Math.log(pts[i][0] / pts[i - 1][0]);
        return pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1]);
      }
    }
    return pts[pts.length - 1][1];
  }
  function refMean(vals) {
    let s = 0, n = 0;
    for (let i = 0; i < CENTERS.length; i++) {
      if (CENTERS[i] >= REF_RANGE[0] && CENTERS[i] <= REF_RANGE[1] && vals[i] != null) { s += vals[i]; n++; }
    }
    return n ? s / n : 0;
  }
  const normalize = (vals) => { const m = refMean(vals); return vals.map((v) => (v == null ? null : v - m)); };
  function targetCurve(name) {
    const raw = CENTERS.map((f) => interp((TARGETS[name] || TARGETS.datar).pts, f));
    return normalize(raw);
  }

  // Rata-rata beberapa titik dalam domain daya (setelah tiap titik dinormalkan sendiri).
  function combine(positions) {
    const norm = positions.map((p) => normalize(p));
    const avg = [], spread = [];
    for (let i = 0; i < CENTERS.length; i++) {
      const v = norm.map((p) => p[i]).filter((x) => x != null);
      if (!v.length) { avg.push(null); spread.push(0); continue; }
      avg.push(10 * Math.log10(v.reduce((s, x) => s + Math.pow(10, x / 10), 0) / v.length));
      spread.push(Math.max(...v) - Math.min(...v));
    }
    return { avg: normalize(avg), spread, norm };
  }

  // Respons filter peaking (RBJ) dalam dB.
  function peakingDb(f, f0, gain, q, sr) {
    sr = sr || 48000;
    const A = Math.pow(10, gain / 40), w0 = 2 * Math.PI * f0 / sr, al = Math.sin(w0) / (2 * q), c = Math.cos(w0);
    const b0 = 1 + al * A, b1 = -2 * c, b2 = 1 - al * A, a0 = 1 + al / A, a1 = -2 * c, a2 = 1 - al / A;
    const w = 2 * Math.PI * f / sr, cw = Math.cos(w), sw = Math.sin(w), c2 = Math.cos(2 * w), s2 = Math.sin(2 * w);
    const nr = b0 + b1 * cw + b2 * c2, ni = -(b1 * sw + b2 * s2), dr = a0 + a1 * cw + a2 * c2, di = -(a1 * sw + a2 * s2);
    return 10 * Math.log10((nr * nr + ni * ni) / (dr * dr + di * di));
  }
  const qFromOctaves = (w) => Math.sqrt(Math.pow(2, w)) / (Math.pow(2, w) - 1);
  const roundQ = (q) => Math.round(Math.max(0.7, Math.min(8, q)) * 10) / 10;
  const roundHz = (f) => (f < 1000 ? Math.round(f / 5) * 5 : Math.round(f / 50) * 50);

  function describe(f) {
    if (f < 120) return "bass dalam / dentuman";
    if (f < 300) return "badan suara (menggumpal atau berlumpur bila berlebih)";
    if (f < 1000) return "suara 'kotak' atau hidung di area tengah-rendah";
    if (f < 2500) return "presence (suara maju ke depan)";
    if (f < 5000) return "kejelasan kata (kasar bila berlebih)";
    return "kecerahan / desis";
  }
  const fmtHz = (f) => (f >= 1000 ? String(Math.round(f / 10) / 100).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "").replace(".", ",") + " kHz" : Math.round(f) + " Hz");
  const sgn = (x) => (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(1).replace(".", ",");
  const num = (x) => Math.abs(x).toFixed(1).replace(".", ",");

  /* positions: array of 24-nilai dB (per titik). opts: {target, mic}
   * Hasil: {avg, spread, target, dev, bands:[{hz,db,q,aman,teks}], notes:[], trusted:[lo,hi]} */
  function analyze(positions, opts) {
    opts = opts || {};
    const trust = TRUST[opts.mic] || TRUST.genggam;
    const tgt = targetCurve(opts.target || "datar");
    const comb = combine(positions);
    const dev = comb.avg.map((v, i) => (v == null ? null : v - tgt[i]));
    const inRange = (i) => CENTERS[i] >= trust.lo && CENTERS[i] <= trust.hi && dev[i] != null;
    const work = dev.slice();
    const bands = [], notes = [];

    for (let n = 0; n < MAX_BANDS; n++) {
      let bi = -1, bv = 0;
      for (let i = 0; i < work.length; i++) {
        if (!inRange(i)) continue;
        const need = work[i] > 0 ? CUT_MIN : BOOST_MIN;
        if (Math.abs(work[i]) >= need && Math.abs(work[i]) > Math.abs(bv)) { bi = i; bv = work[i]; }
      }
      if (bi < 0 || Math.abs(bv) < STOP_BELOW) break;
      let lo = bi, hi = bi;
      while (lo > 0 && inRange(lo - 1) && Math.sign(work[lo - 1]) === Math.sign(bv) && Math.abs(work[lo - 1]) >= Math.abs(bv) / 2) lo--;
      while (hi < work.length - 1 && inRange(hi + 1) && Math.sign(work[hi + 1]) === Math.sign(bv) && Math.abs(work[hi + 1]) >= Math.abs(bv) / 2) hi++;
      const width = Math.max(1, hi - lo + 1) / 3;       // oktaf
      const f0 = CENTERS[bi];
      const spreadHere = Math.max(...comb.spread.slice(Math.max(0, lo - 1), hi + 2));
      const narrowDip = bv < 0 && hi - lo + 1 <= 1;
      let gain, aman = true, teks;
      const where = `${fmtHz(roundHz(f0))}`;
      const kasus = positions.length > 1
        ? (spreadHere >= SPREAD_WARN ? `Berbeda jauh antar titik (selisih ${spreadHere.toFixed(0)} dB): sebagian efek posisi, koreksi EQ hanya setengah.`
          : `Konsisten di semua ${positions.length} titik.`)
        : "Baru 1 titik ukur; tambah titik lain sebelum yakin.";
      if (bv > 0) {
        gain = Math.max(MAX_CUT, -bv * 0.7);
        if (positions.length > 1 && spreadHere >= SPREAD_WARN) gain /= 2;
        gain = Math.round(gain * 2) / 2;
        teks = `Potong ${num(gain)} dB di ${where}: area ${describe(f0)} terlalu menonjol (≈ +${bv.toFixed(0)} dB dari seharusnya). ${kasus}`;
      } else if (narrowDip) {
        gain = 0; aman = false;
        teks = `Cekungan sempit di ${where} (≈ ${sgn(bv)} dB). Ini biasanya pembatalan suara karena pantulan/penempatan speaker; menaikkan EQ tidak akan memperbaikinya. Coba geser speaker atau posisi mic. ${kasus}`;
      } else {
        gain = Math.min(MAX_BOOST, -bv * 0.5);
        if (positions.length > 1 && spreadHere >= SPREAD_WARN) gain /= 2;
        gain = Math.round(gain * 2) / 2;
        teks = `Naikkan ${num(gain)} dB di ${where}: area ${describe(f0)} kurang (≈ ${sgn(bv)} dB dari seharusnya). Boost kecil saja, dengarkan hasilnya. ${kasus}`;
      }
      gain = Math.round(gain * 2) / 2;
      const q = roundQ(qFromOctaves(Math.max(width, 0.34)));
      const b = { hz: roundHz(f0), db: gain, q, aman, teks, spread: spreadHere, jenis: bv > 0 ? "kurangi" : narrowDip ? "cekungan" : "naikkan" };
      bands.push(b);
      if (aman && gain !== 0) {
        for (let i = 0; i < work.length; i++) work[i] += peakingDb(CENTERS[i], b.hz, gain, q);
      } else {
        for (let i = lo; i <= hi; i++) work[i] = 0;
      }
    }
    bands.sort((a, b) => a.hz - b.hz);
    if (!bands.length) notes.push("Dalam rentang yang bisa dipercaya dari mic ini, bentuk suara sudah dekat dengan target. Tidak ada koreksi EQ yang perlu.");
    notes.push(`Rentang yang dinilai: ${fmtHz(trust.lo)} – ${fmtHz(trust.hi)} (mic ini tidak datar di luar rentang itu).`);
    return { avg: comb.avg, spread: comb.spread, norm: comb.norm, target: tgt, dev, bands, notes, trusted: [trust.lo, trust.hi] };
  }

  // Kurva EQ gabungan dari daftar band (untuk digambar).
  function eqCurve(bands, freqs) {
    return freqs.map((f) => bands.reduce((s, b) => (b.aman && b.db ? s + peakingDb(f, b.hz, b.db, b.q) : s), 0));
  }

  function pinkNoise(n, seed) {
    let s = seed || 12345;
    const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 * 2 - 1; };
    const out = new Float32Array(n);
    let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, peak = 0;
    for (let i = 0; i < n; i++) {
      const w = rnd();
      b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759; b2 = 0.969 * b2 + w * 0.153852;
      b3 = 0.8665 * b3 + w * 0.3104856; b4 = 0.55 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.016898;
      out[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362; b6 = w * 0.115926;
      peak = Math.max(peak, Math.abs(out[i]));
    }
    for (let i = 0; i < n; i++) out[i] /= peak * 1.0;
    return out;
  }

  function wavBlob(samples, sr, gain) {
    const n = samples.length, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt "); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i] * gain)) * 32767, true);
    return new Blob([buf], { type: "audio/wav" });
  }

  /* ---------- EQ Main L/R (6 band) dan penggabungan dengan usulan ---------- */
  const EQ_TYPES = ["LCut", "LShv", "PEQ", "VEQ", "HShv", "HCut"];     // urutan jenis band X Air (PERKIRAAN, belum diverifikasi)
  function biquadDb(c, f, sr) {
    const w = 2 * Math.PI * f / sr, cw = Math.cos(w), sw = Math.sin(w), c2 = Math.cos(2 * w), s2 = Math.sin(2 * w);
    const nr = c.b0 + c.b1 * cw + c.b2 * c2, ni = -(c.b1 * sw + c.b2 * s2), dr = c.a0 + c.a1 * cw + c.a2 * c2, di = -(c.a1 * sw + c.a2 * s2);
    return 10 * Math.log10((nr * nr + ni * ni) / (dr * dr + di * di));
  }
  function rbj(type, f0, gain, q, sr) {
    const A = Math.pow(10, gain / 40), w0 = 2 * Math.PI * f0 / sr, cs = Math.cos(w0), sn = Math.sin(w0);
    if (type === 0 || type === 5) {                                     // low cut / high cut (orde 2)
      const al = sn / (2 * 0.7071);
      return type === 0 ? { b0: (1 + cs) / 2, b1: -(1 + cs), b2: (1 + cs) / 2, a0: 1 + al, a1: -2 * cs, a2: 1 - al }
        : { b0: (1 - cs) / 2, b1: 1 - cs, b2: (1 - cs) / 2, a0: 1 + al, a1: -2 * cs, a2: 1 - al };
    }
    if (type === 1 || type === 4) {                                     // shelf rendah / tinggi (kemiringan S=1)
      const al = sn / 2 * Math.sqrt(2), k = 2 * Math.sqrt(A) * al;
      return type === 1
        ? { b0: A * ((A + 1) - (A - 1) * cs + k), b1: 2 * A * ((A - 1) - (A + 1) * cs), b2: A * ((A + 1) - (A - 1) * cs - k), a0: (A + 1) + (A - 1) * cs + k, a1: -2 * ((A - 1) + (A + 1) * cs), a2: (A + 1) + (A - 1) * cs - k }
        : { b0: A * ((A + 1) + (A - 1) * cs + k), b1: -2 * A * ((A - 1) + (A + 1) * cs), b2: A * ((A + 1) + (A - 1) * cs - k), a0: (A + 1) - (A - 1) * cs + k, a1: 2 * ((A - 1) - (A + 1) * cs), a2: (A + 1) - (A - 1) * cs - k };
    }
    const al = sn / (2 * q);                                            // PEQ / VEQ (dan jenis tak dikenal)
    return { b0: 1 + al * A, b1: -2 * cs, b2: 1 - al * A, a0: 1 + al / A, a1: -2 * cs, a2: 1 - al / A };
  }
  function lrCurve(bands, freqs, sr) {
    sr = sr || 48000;
    return freqs.map((f) => bands.reduce((s, b) => {
      const cut = b.type === 0 || b.type === 5;
      if (!cut && Math.abs(b.db) < 0.05) return s;
      return s + biquadDb(rbj(b.type == null ? 2 : b.type, b.hz, cut ? 0 : b.db, b.q || 1, sr), f, sr);
    }, 0));
  }
  const clamp15 = (x) => Math.max(-15, Math.min(15, x));
  /* cur: band EQ L/R sekarang [{band,type,hz,db,q}]; recs: usulan ukur ruangan [{hz,db,q,aman}].
   * Setiap usulan digabung ke band PEQ yang berdekatan (<= 1/3 oktaf), atau ke band yang belum dipakai (gain ~0 dB). */
  function planLR(cur, recs) {
    const isPeq = (b) => b.type == null || b.type === 2 || b.type === 3;
    const plan = cur.map((b) => ({ band: b.band, now: b, next: { hz: b.hz, db: b.db, q: b.q }, aksi: "tetap", catatan: "" }));
    const used = new Set(), notes = [];
    const todo = recs.filter((r) => r.aman && r.db).slice().sort((a, b) => Math.abs(b.db) - Math.abs(a.db));
    for (const r of todo) {
      const near = plan.filter((p) => !used.has(p.band) && isPeq(p.now) && Math.abs(p.now.db) >= 0.3 && Math.abs(Math.log2(r.hz / p.now.hz)) <= 0.34)
        .sort((a, b) => Math.abs(Math.log2(r.hz / a.now.hz)) - Math.abs(Math.log2(r.hz / b.now.hz)))[0];
      const empty = near ? null : plan.find((p) => !used.has(p.band) && isPeq(p.now) && Math.abs(p.now.db) < 0.3);
      const t = near || empty;
      if (!t) { notes.push(`Usulan ${fmtHz(r.hz)} ${r.db > 0 ? "+" : "−"}${Math.abs(r.db).toFixed(1).replace(".", ",")} dB belum punya band yang bisa dipakai (semua band terpakai). Gabungkan manual atau pakai EQ di DSP.`); continue; }
      used.add(t.band);
      const want = near ? t.now.db + r.db : r.db;
      t.next = { hz: near ? t.now.hz : r.hz, db: clamp15(want), q: near ? t.now.q : r.q };
      t.aksi = near ? "gabung" : "band baru";
      t.catatan = near ? `digabung dengan usulan ${fmtHz(r.hz)} (${r.db > 0 ? "+" : "−"}${Math.abs(r.db).toFixed(1).replace(".", ",")} dB)` : `band kosong dipakai untuk usulan ${fmtHz(r.hz)}`;
      if (want !== t.next.db) t.catatan += `; dibatasi ${t.next.db > 0 ? "+" : "−"}15 dB (batas EQ)`;
    }
    return { plan, notes };
  }

  const api = { EQ_TYPES, lrCurve, planLR, NOMINAL, CENTERS, FFT_N, TRUST, TARGETS, REF_RANGE, Averager, fft, normalize, targetCurve, combine, analyze,
    eqCurve, peakingDb, pinkNoise, wavBlob, fmtHz, describe };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RoomCore = api;
})(typeof window !== "undefined" ? window : globalThis);
