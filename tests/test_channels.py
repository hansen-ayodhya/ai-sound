import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import channels, convert, report
from aise.channels import ProfileError
from aise.server import ROOT, App, serve


def prof(*rows):
    return channels.validate({"sources": [dict(r) for r in rows]})


def row(nama, jenis, ch=(), **kw):
    return {"nama": nama, "jenis": jenis, "ch": list(ch), **kw}


class Profile(unittest.TestCase):
    def test_default_has_operator_sources_without_channels(self):
        d = channels.default_profile()
        names = [s["nama"] for s in d["sources"]]
        self.assertEqual(len(names), 11)  # 10 jenis, singer ada 2
        self.assertIn("Mic pengkhotbah 2", names)
        self.assertTrue(all(s["ch"] == [] for s in d["sources"]))  # tidak mengarang nomor channel
        channels.validate(d)

    def test_validation(self):
        with self.assertRaises(ProfileError):
            prof(row("A", "bass", [3]), row("B", "keyboard", [3]))  # channel dobel
        with self.assertRaises(ProfileError):
            prof(row("A", "tidak-ada", [1]))
        with self.assertRaises(ProfileError):
            prof(row("A", "bass", [20]))
        with self.assertRaises(ProfileError):
            prof(row("", "bass", [1]))
        p = prof(row("USB", "playback", [17]), row("USB2", "playback", [17]))  # aux boleh berbagi
        self.assertEqual(p["sources"][0]["ch"], [17])

    def test_suggest_from_mixer_names(self):
        dump = {"values": {"/ch/01/config/name": ["WL"], "/ch/02/config/name": ["Singer 1"],
                           "/ch/03/config/name": ["Bass"], "/ch/05/config/name": ["Drum L"],
                           "/ch/06/config/name": ["Drum R"], "/ch/09/config/name": ["Pdt"]}}
        s = channels.suggest(channels.default_profile(), dump)
        by = {x["id"]: x for x in s["sources"]}
        self.assertEqual(by["wl"]["ch"], [1])
        self.assertEqual(by["singer1"]["ch"], [2])
        self.assertEqual(by["bass"]["ch"], [3])
        self.assertEqual(by["drum"]["ch"], [5, 6])
        self.assertEqual(by["khotbah1"]["ch"], [9])
        self.assertTrue(by["bass"]["otomatis"])
        self.assertEqual(by["gitar"]["ch"], [])  # tidak ada nama cocok: tetap kosong

    def test_acoustic_guitar_not_electric(self):
        dump = {"values": {"/ch/05/config/name": ["Gitar Ak"], "/ch/06/config/name": ["Gtr Elektrik"]}}
        by = {x["id"]: x for x in channels.suggest(channels.default_profile(), dump)["sources"]}
        self.assertEqual(by["gitar"]["ch"], [6])

    def test_mismatch(self):
        self.assertEqual(channels.mismatch({"jenis": "bass"}, "Vokal 3"), "vokal_singer")
        self.assertIsNone(channels.mismatch({"jenis": "vokal_utama"}, "Vokal 1"))
        self.assertIsNone(channels.mismatch({"jenis": "bass"}, "Ch 7"))


class RulesWithProfile(unittest.TestCase):
    def test_profile_drives_rules(self):
        dump = {"values": {
            "/ch/09/config/name": ["Mic 9"], "/ch/09/preamp/hpon": [0], "/ch/09/preamp/hpf": [0.2],
            "/ch/03/config/name": ["Bass"], "/headamp/03/phantom": [1],
            "/ch/07/config/name": ["Keys"], "/headamp/07/phantom": [1],
        }}
        p = prof(row("Mic pengkhotbah 1", "khotbah", [9]), row("Bass", "bass", [3], koneksi="DI aktif"),
                 row("Keyboard utama", "keyboard", [7]), row("Gitar listrik", "gitar_listrik"))
        f = {x.id: x for x in report.analyze(dump, p)}
        self.assertIn("hpf-mati-ch09", f)  # nama "Mic 9" tidak menunjukkan suara; Daftar Channel yang menentukan
        self.assertAlmostEqual(convert.hpf_hz(f["hpf-mati-ch09"].usulan[1]["usulan"]), 120, delta=1)
        self.assertIn("Mic pengkhotbah 1", f["hpf-mati-ch09"].channel)
        self.assertNotIn("phantom-ch03", f)  # DI aktif memang butuh 48V
        self.assertIn("phantom-ch07", f)     # keyboard line-level
        self.assertIn("daftar-belum-lengkap", f)
        self.assertIsNone(f["daftar-belum-lengkap"].ch)

    def test_without_profile_falls_back_to_names(self):
        dump = {"values": {"/ch/09/config/name": ["Mic 9"], "/ch/09/preamp/hpon": [0]}}
        self.assertNotIn("hpf-mati-ch09", [x.id for x in report.analyze(dump)])

    def test_name_mismatch_finding(self):
        dump = {"values": {"/ch/03/config/name": ["Vokal 3"]}}
        p = prof(row("Bass", "bass", [3]))
        self.assertIn("nama-beda-ch03", [x.id for x in report.analyze(dump, p)])


class ServerChannels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        folder = self.data / "audit-x"
        folder.mkdir(parents=True)
        dump = {"created": "t", "sha256": "x", "values": {"/ch/04/config/name": ["Mic 4"], "/ch/04/preamp/hpon": [0],
                                                          "/ch/04/preamp/hpf": [0.2], "/ch/01/config/name": ["WL"]}}
        (folder / "dump1.json").write_text(json.dumps(dump))
        (folder / "findings.json").write_text("[]")
        (folder / "report.md").write_text("")
        (self.data / "latest.json").write_text(json.dumps({"dump": "data/audit-x/dump1.json", "findings": "data/audit-x/findings.json",
                                                           "report": "data/audit-x/report.md"}))
        self.app = App(root=ROOT, data=self.data)
        probe = serve(App(root=ROOT, data=self.data), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        r.add_header("X-AISE-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_get_default_then_save_recomputes_findings(self):
        code, v = self.call("/api/channels")
        self.assertEqual(code, 200)
        self.assertFalse(v["saved"])
        self.assertEqual(v["mixer_names"]["1"], "WL")
        self.assertEqual([s["ch"] for s in v["suggested"]["sources"] if s["id"] == "wl"], [[1]])
        p = v["profile"]
        for s in p["sources"]:
            if s["id"] == "khotbah1":
                s["ch"] = [4]
        code, r = self.call("/api/channels", {"profile": p})
        self.assertEqual(code, 200, r)
        fnd = json.loads((self.data / "audit-x" / "findings.json").read_text())
        self.assertIn("hpf-mati-ch04", [f["id"] for f in fnd])
        self.assertIn("Mic pengkhotbah 1", (self.data / "audit-x" / "report.md").read_text())
        self.assertTrue(self.call("/api/channels")[1]["saved"])

    def test_bad_profile_rejected(self):
        code, r = self.call("/api/channels", {"profile": {"sources": [row("A", "bass", [2]), row("B", "bass", [2])]}})
        self.assertEqual(code, 400)
        self.assertIn("dua kali", r["error"])


if __name__ == "__main__":
    unittest.main()
