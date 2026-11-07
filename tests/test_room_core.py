import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = r"""
const C = require(process.argv[1]);
const sr = 48000;
function bq(x, f0, g, q) {
  const A = Math.pow(10, g / 40), w0 = 2 * Math.PI * f0 / sr, al = Math.sin(w0) / (2 * q), c = Math.cos(w0);
  const b0 = 1 + al * A, b1 = -2 * c, b2 = 1 - al * A, a0 = 1 + al / A, a1 = -2 * c, a2 = 1 - al / A;
  const y = new Float32Array(x.length); let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) { const v = (b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0; y[i] = v; x2 = x1; x1 = x[i]; y2 = y1; y1 = v; }
  return y;
}
function measure(x) { const a = new C.Averager(sr); for (let i = 0; i + C.FFT_N <= x.length; i += 8192) a.add(x.subarray(i, i + C.FFT_N)); return a.bands(); }
const assert = require("assert");
const pink = C.pinkNoise(sr * 12, 7);
const flat = measure(pink);
assert(Math.max(...C.normalize(flat).filter((v) => v != null).map(Math.abs)) < 1.5, "pink noise harus tampak datar");
assert.strictEqual(C.analyze([flat], { mic: "ukur", target: "datar" }).bands.length, 0);
const room = (g) => measure(bq(pink, 200, g, 2));
const r = C.analyze([room(8), room(7), room(9)], { mic: "ukur", target: "datar" });
assert.strictEqual(r.bands.length, 1);
assert(r.bands[0].hz >= 160 && r.bands[0].hz <= 250 && r.bands[0].db < -3 && r.bands[0].aman, JSON.stringify(r.bands));
const deep = measure(bq(pink, 3150, -14, 6));
const d = C.analyze([deep], { mic: "ukur", target: "datar" });
assert(d.bands.some((b) => !b.aman && b.db === 0), "cekungan sempit tidak boleh di-boost");
const narrowMic = C.analyze([room(8)], { mic: "genggam", target: "datar" });
assert(narrowMic.trusted[0] === 100 && narrowMic.trusted[1] === 4000);
assert.strictEqual(C.fmtHz(10000), "10 kHz");
assert.strictEqual(C.fmtHz(3150), "3,15 kHz");
console.log("ok");
"""


@unittest.skipUnless(shutil.which("node"), "node tidak terpasang")
class RoomCore(unittest.TestCase):
    def test_core(self):
        r = subprocess.run(["node", "-e", JS, str(ROOT / "viewer" / "room-core.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
