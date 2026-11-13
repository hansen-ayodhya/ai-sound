"""Pembaruan dari dalam aplikasi dan mulai otomatis. GitHub ditiru server lokal; bagian khusus Windows hanya jalan di Windows (CI)."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import autostart, pembaruan
from aise.pembaruan import BAT_NAME, EXE_NAME, NEW_NAME, OLD_NAME, RESULT_NAME, UpdateError, Updater, is_newer, make_bat, make_meta, parse_version
from aise.server import ROOT, App, serve

LOCAL = "aaa1111 01-11-2026 10:00"
REMOTE = "bbb2222 13-11-2026 13:40"
EXE = b"MZ" + bytes(range(256)) * 40


class FakeGitHub:
    """Meniru API rilis GitHub: daftar aset, unduhan lewat pengalihan (302), dan repo private (404 tanpa token)."""

    def __init__(self):
        self.exe, self.private, self.token = EXE, False, "github_pat_" + "x" * 30
        self.meta = {"versi": REMOTE, "sha256": hashlib.sha256(EXE).hexdigest(), "ukuran": len(EXE), "catatan": "uji"}
        self.seen: list[tuple[str, str | None]] = []
        self.assets = [EXE_NAME, "versi.json"]
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                auth = self.headers.get("Authorization")
                gh.seen.append((self.path, auth))
                if self.path.startswith("/blob/"):
                    return self._send(200, gh.exe if self.path == "/blob/1" else json.dumps(gh.meta).encode())
                if gh.private and auth != "Bearer " + gh.token:
                    return self._send(401 if auth else 404)
                if self.path == "/repos/o/r/releases/tags/terbaru":
                    base = f"http://127.0.0.1:{gh.port}"
                    rel = {"assets": [{"name": n, "url": f"{base}/assets/{i + 1}"} for i, n in enumerate(gh.assets)]}
                    return self._send(200, json.dumps(rel).encode())
                if self.path.startswith("/assets/"):
                    return self._send(302, headers={"Location": f"http://127.0.0.1:{gh.port}/blob/{self.path[-1]}"})
                self._send(404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "data").mkdir()
        self.gh = FakeGitHub()
        self.launched = []

    def tearDown(self):
        self.gh.stop()
        self.tmp.cleanup()

    def updater(self, version=LOCAL, frozen=True, windows=True):
        return Updater(self.dir / "data", self.dir, version, frozen, base=f"http://127.0.0.1:{self.gh.port}", repo="o/r",
                       windows=windows, launch=self.launched.append)


class Versions(unittest.TestCase):
    def test_parse_and_compare(self):
        sha, when = parse_version(REMOTE)
        self.assertEqual((sha, when.year, when.minute), ("bbb2222", 2026, 40))
        self.assertTrue(is_newer(LOCAL, REMOTE))
        self.assertFalse(is_newer(REMOTE, REMOTE))
        self.assertFalse(is_newer(REMOTE, LOCAL))                # rilis lebih tua dari yang terpasang
        self.assertTrue(is_newer("?", REMOTE))                    # versi lokal tidak terbaca: commit berbeda dianggap baru
        self.assertFalse(is_newer(LOCAL, ""))

    def test_meta(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "x.exe"
            p.write_bytes(EXE)
            m = make_meta(p, REMOTE + "\n", "judul commit")
            self.assertEqual(m, {"versi": REMOTE, "sha256": hashlib.sha256(EXE).hexdigest(), "ukuran": len(EXE), "catatan": "judul commit"})


class Check(Base):
    def test_available_and_cached(self):
        u = self.updater()
        v = u.check()
        self.assertTrue(v["tersedia"])
        self.assertEqual(v["rilis"], {"versi": REMOTE, "ukuran": len(EXE), "catatan": "uji"})
        n = len(self.gh.seen)
        u.check()
        self.assertEqual(len(self.gh.seen), n)                    # tidak menghubungi GitHub lagi sebelum waktunya
        u.check(force=True)
        self.assertGreater(len(self.gh.seen), n)
        self.assertFalse(self.updater(version=REMOTE).check()["tersedia"])

    def test_private_repo_needs_token_and_token_stays_with_api(self):
        self.gh.private = True
        u = self.updater()
        v = u.check()
        self.assertFalse(v["tersedia"])
        self.assertIn("private", v["galat"])
        with self.assertRaises(UpdateError):
            u.set_token("bukan token!")
        u.set_token("github_pat_" + "y" * 30)                     # token salah
        self.assertIn("ditolak", u.check()["galat"])
        u.set_token(self.gh.token)
        v = u.check()
        self.assertTrue(v["tersedia"] and v["token_ada"])
        self.assertNotIn(self.gh.token, json.dumps(v))            # token tidak pernah dikembalikan ke halaman
        blobs = [a for p, a in self.gh.seen if p.startswith("/blob/")]
        self.assertTrue(blobs and all(a is None for a in blobs))  # pengalihan diikuti tanpa token
        u.set_token(None)
        self.assertFalse(u.view()["token_ada"])

    def test_incomplete_release_and_bad_meta(self):
        self.gh.assets = [EXE_NAME]
        self.assertIn("belum lengkap", self.updater().check()["galat"])
        self.gh.assets = [EXE_NAME, "versi.json"]
        self.gh.meta = {"versi": REMOTE, "sha256": "zz", "ukuran": 5}
        self.assertIn("tidak wajar", self.updater().check()["galat"])

    def test_no_internet(self):
        u = Updater(self.dir / "data", self.dir, LOCAL, True, base="http://127.0.0.1:9", repo="o/r")
        self.assertIn("internet", u.check()["galat"])

    def test_previous_result_is_reported_once(self):
        (self.dir / "data" / RESULT_NAME).write_text("ok \r\n")
        self.assertEqual(self.updater().view()["hasil_terakhir"], "ok")
        self.assertEqual(self.updater().view()["hasil_terakhir"], "")


class DownloadInstall(Base):
    def test_download_verifies(self):
        u = self.updater()
        u.check()
        self.assertEqual(u.download(u._info).read_bytes(), EXE)
        for exe, why in [(EXE + b"x", "lebih besar"), (EXE[:-1] + b"?", "tidak cocok"), (EXE[:100], "tidak cocok")]:
            self.gh.exe = exe
            with self.assertRaises(UpdateError) as cm:
                u.download(u._info)
            self.assertIn(why, str(cm.exception))
            self.assertFalse((self.dir / NEW_NAME).exists())      # unduhan yang tidak cocok tidak ditinggalkan

    def test_rejects_non_program(self):
        self.gh.exe = b"<html>" + b"x" * 500
        self.gh.meta.update(sha256=hashlib.sha256(self.gh.exe).hexdigest(), ukuran=len(self.gh.exe))
        u = self.updater()
        u.check()
        with self.assertRaises(UpdateError):
            u.download(u._info)

    def wait(self, u, stages):
        end = time.monotonic() + 10
        while time.monotonic() < end and u.state["tahap"] not in stages:
            time.sleep(0.05)
        return u.state

    def test_install_writes_script_then_asks_app_to_close(self):
        u = self.updater()
        closed = threading.Event()
        u.install(["--lokal", "--port", "8765", "--no-browser", 'x" & del *'], 4321, closed.set)
        self.assertEqual(self.wait(u, ("mulai_ulang", "gagal"))["tahap"], "mulai_ulang")
        self.assertTrue(closed.wait(3))
        self.assertEqual(self.launched, [self.dir / BAT_NAME])
        self.assertEqual((self.dir / NEW_NAME).read_bytes(), EXE)
        bat = (self.dir / BAT_NAME).read_bytes().decode("ascii")
        self.assertIn('"PID eq 4321"', bat)
        self.assertIn(f'start "AI Sound Engineer" "{EXE_NAME}" --lokal --port 8765 --no-browser\r\n', bat)
        self.assertNotIn("del *", bat)                            # argumen aneh dibuang, tidak pernah masuk skrip

    def test_install_refusals(self):
        with self.assertRaises(UpdateError):
            self.updater(frozen=False).install([], 1, lambda: None)
        with self.assertRaises(UpdateError):
            self.updater(windows=False).install([], 1, lambda: None)
        u = self.updater(version=REMOTE)                           # sudah terbaru
        u.install([], 1, lambda: self.fail("tidak boleh menutup aplikasi"))
        self.assertIn("lebih baru", self.wait(u, ("gagal", "mulai_ulang"))["pesan"])
        self.assertEqual(self.launched, [])
        self.gh.exe = EXE[:50]                                     # unduhan rusak
        u2 = self.updater()
        u2.install([], 1, lambda: self.fail("tidak boleh menutup aplikasi"))
        self.assertEqual(self.wait(u2, ("gagal", "mulai_ulang"))["tahap"], "gagal")
        self.assertEqual(self.launched, [])
        self.assertFalse((self.dir / BAT_NAME).exists())


class Script(unittest.TestCase):
    def test_shape(self):
        bat = make_bat(77, ["--lokal"])
        self.assertTrue(bat.isascii() and bat.endswith("\r\n") and "\n" not in bat.replace("\r\n", ""))
        self.assertLess(bat.index(":tunggu_tutup"), bat.index(f'move /y "{EXE_NAME}" "{OLD_NAME}"'))      # tunggu aplikasi tertutup dulu
        self.assertLess(bat.index(f'move /y "{EXE_NAME}" "{OLD_NAME}"'), bat.index(f'move /y "{NEW_NAME}" "{EXE_NAME}"'))
        self.assertIn(f'move /y "{OLD_NAME}" "{EXE_NAME}"', bat)                                           # gagal memasang: exe lama dikembalikan

    @unittest.skipUnless(os.name == "nt", "skrip penukar hanya bisa dijalankan di Windows")
    def test_real_swap_on_windows(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "data").mkdir()
            new = Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe"       # program kecil yang langsung selesai
            (d / EXE_NAME).write_bytes(b"MZ lama")
            (d / NEW_NAME).write_bytes(new.read_bytes())
            proc = subprocess.Popen(["ping", "-n", "4", "127.0.0.1"], stdout=subprocess.DEVNULL)   # "aplikasi lama" yang masih berjalan ~3 dtk
            (d / BAT_NAME).write_bytes(make_bat(proc.pid, ["--no-browser"]).encode("ascii"))
            pembaruan.launch_bat(d / BAT_NAME)
            time.sleep(1.0)
            self.assertEqual((d / EXE_NAME).read_bytes(), b"MZ lama")              # belum ditukar selama proses lama masih hidup
            end = time.monotonic() + 40
            while time.monotonic() < end and not (d / "data" / RESULT_NAME).exists():
                time.sleep(0.2)
            proc.wait(timeout=10)
            self.assertEqual((d / "data" / RESULT_NAME).read_text().strip(), "ok")
            self.assertEqual((d / EXE_NAME).read_bytes(), new.read_bytes())
            self.assertEqual((d / OLD_NAME).read_bytes(), b"MZ lama")
            self.assertFalse((d / NEW_NAME).exists())
            end = time.monotonic() + 10
            while time.monotonic() < end and (d / BAT_NAME).exists():
                time.sleep(0.2)
            self.assertFalse((d / BAT_NAME).exists())                              # skrip menghapus dirinya
            time.sleep(1.0)                                                        # beri waktu whoami selesai sebelum folder dihapus


class Autostart(unittest.TestCase):
    def test_command(self):
        with mock.patch.object(sys, "executable", r"C:\Gereja Sound\AI-Sound-Engineer.exe"):
            self.assertEqual(autostart.command(True, Path("x"), True, 8765, 8765, True), r'"C:\Gereja Sound\AI-Sound-Engineer.exe"')
            self.assertEqual(autostart.command(True, Path("x"), False, 9000, 8765, False),
                             r'"C:\Gereja Sound\AI-Sound-Engineer.exe" --lokal --port 9000 --no-browser')
        self.assertTrue(autostart.command(False, Path("proj"), True, 8765, 8765, True).endswith('AI-Sound-Engineer-Jaringan.bat"'))

    def test_view(self):
        cmd = r'"C:\App\AI-Sound-Engineer.exe" --lokal'
        with mock.patch.object(autostart, "read", return_value=None):
            self.assertEqual(autostart.view(cmd)["aktif"], False)
        with mock.patch.object(autostart, "read", return_value=cmd + " --no-browser"):
            v = autostart.view(cmd)
            self.assertEqual((v["aktif"], v["cocok"], v["browser"]), (True, True, False))
        with mock.patch.object(autostart, "read", return_value=r'"D:\Lama\AI-Sound-Engineer.exe"'):
            v = autostart.view(cmd)
            self.assertEqual((v["aktif"], v["cocok"], v["browser"]), (True, False, True))

    @unittest.skipIf(os.name == "nt", "khusus selain Windows")
    def test_not_supported_elsewhere(self):
        self.assertIsNone(autostart.read())
        with self.assertRaises(ValueError):
            autostart.write("x")

    @unittest.skipUnless(os.name == "nt", "registry hanya ada di Windows")
    def test_real_registry_roundtrip(self):
        name = "AI-Sound-Engineer-UJI"
        try:
            autostart.write(r'"C:\uji\x.exe" --lokal', name)
            self.assertEqual(autostart.read(name), r'"C:\uji\x.exe" --lokal')
            autostart.write(None, name)
            self.assertIsNone(autostart.read(name))
            autostart.write(None, name)                                            # menghapus yang sudah tidak ada: tidak apa-apa
        finally:
            autostart.write(None, name)


class ServerEndpoints(Base):
    def setUp(self):
        super().setUp()
        self.app = App(root=ROOT, data=self.dir / "data", update_base=f"http://127.0.0.1:{self.gh.port}")
        self.app.updater.repo = "o/r"
        probe = serve(App(root=ROOT, data=self.dir / "data"), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        r.add_header("X-AISE-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flow_from_this_computer(self):
        code, v = self.call("/api/pembaruan")
        self.assertEqual(code, 200)
        self.assertEqual(v["rilis"]["versi"], REMOTE)
        self.assertFalse(v["bisa_pasang"])                                          # tes berjalan dari kode sumber, bukan exe
        self.assertEqual(self.call("/api/pembaruan/pasang", {})[0], 400)
        self.assertEqual(self.call("/api/pembaruan/token", {"token": "x"})[0], 400)
        self.assertEqual(self.call("/api/pembaruan/cek", {})[0], 200)
        self.assertIn("pembaruan_tersedia", self.call("/api/status")[1])
        code, a = self.call("/api/autostart")
        self.assertEqual((code, a["didukung"]), (200, os.name == "nt"))
        self.assertEqual(self.call("/api/autostart", {"aktif": "ya"})[0], 400)
        if os.name != "nt":
            self.assertEqual(self.call("/api/autostart", {"aktif": True})[0], 400)

    def test_other_devices_cannot_update_or_set_autostart(self):
        self.app.trust_loopback = False                                             # meniru HP di jaringan
        self.app.lan = True
        for path, body in [("/api/pembaruan", None), ("/api/autostart", None), ("/api/pembaruan/pasang", {}),
                           ("/api/pembaruan/token", {"token": "github_pat_" + "z" * 30}), ("/api/autostart", {"aktif": True})]:
            code, _ = self.call(path, body)
            self.assertIn(code, (401, 403), path)
        self.assertFalse(self.app.updater.view()["token_ada"])


if __name__ == "__main__":
    unittest.main()
