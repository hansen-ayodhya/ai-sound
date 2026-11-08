import re
import shutil
import subprocess
import unittest
from pathlib import Path

VIEWER = Path(__file__).resolve().parent.parent / "viewer"
JS = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[1], "utf8");
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
scripts.forEach((s, i) => { try { new Function(s); } catch (e) { console.error("script " + i + ": " + e.message); process.exit(1); } });
console.log(scripts.length);
"""


@unittest.skipUnless(shutil.which("node"), "node tidak terpasang")
class ViewerScripts(unittest.TestCase):
    def test_every_page_script_parses(self):
        pages = sorted(VIEWER.glob("*.html"))
        self.assertGreaterEqual(len(pages), 8)
        for p in pages:
            r = subprocess.run(["node", "-e", JS, str(p)], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, f"{p.name}: {r.stderr}")

    def test_core_scripts_parse(self):
        for name in ("room-core.js",):
            r = subprocess.run(["node", "--check", str(VIEWER / name)], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
