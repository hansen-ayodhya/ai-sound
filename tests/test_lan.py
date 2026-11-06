import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise.fake_mixer import FakeMixer
from aise.server import MAX_PIN_FAILS, ROOT, App, load_or_create_pin, serve


class LanMode(unittest.TestCase):
    """trust_loopback=False membuat semua permintaan diperlakukan seperti dari perangkat lain."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.mixer = FakeMixer({"/info": ("x",), "/ch/01/config/name": ("Vokal 1",)}).start()
        self.app = App(root=ROOT, data=self.data, lan=True, pin="123456", trust_loopback=False)
        self.data.mkdir(parents=True)
        (self.data / "pin.txt").write_text("123456")
        (self.data / "x.json").write_text("{}")
        (self.data / "gemini.json").write_text('{"key": "rahasia"}')
        (self.data / "audit-1").mkdir()
        (self.data / "audit-1" / "dump1.json").write_text("{}")
        probe = serve(App(root=ROOT, data=self.data), 0)
        self.port = probe.server_address[1]
        probe.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"192.168.1.20:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.mixer.stop()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or self.host, **(headers or {})}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        out = (r.status, dict(r.getheaders()), data)
        c.close()
        return out

    def login(self, pin):
        return self.req("POST", "/login", body=f"pin={pin}",
                        headers={"Content-Type": "application/x-www-form-urlencoded"})

    def cookie(self):
        code, h, _ = self.login("123456")
        self.assertEqual(code, 302)
        return h["Set-Cookie"].split(";")[0]

    def test_requires_pin(self):
        code, h, _ = self.req("GET", "/viewer/home.html")
        self.assertEqual(code, 302)
        self.assertEqual(h["Location"], "/login")
        code, _, _ = self.req("GET", "/api/status", headers={"X-AISE-Token": self.app.token})
        self.assertEqual(code, 401)
        code, _, body = self.req("GET", "/login")
        self.assertEqual(code, 200)
        self.assertNotIn(self.app.token.encode(), body)

    def test_wrong_pin_and_lockout(self):
        for _ in range(MAX_PIN_FAILS):
            code, _, _ = self.login("000000")
            self.assertEqual(code, 401)
        code, _, _ = self.login("123456")  # benar, tapi sudah terkunci
        self.assertEqual(code, 429)

    def test_login_then_use(self):
        ck = self.cookie()
        code, _, body = self.req("GET", "/viewer/home.html", headers={"Cookie": ck})
        self.assertEqual(code, 200)
        self.assertIn(self.app.token.encode(), body)
        hdr = {"Cookie": ck, "X-AISE-Token": self.app.token, "Content-Type": "application/json"}
        code, _, body = self.req("GET", "/api/status", headers=hdr)
        st = json.loads(body)
        self.assertFalse(st["is_local"])
        self.assertIsNone(st["pin"])  # PIN tidak pernah dikirim ke perangkat lain
        code, _, body = self.req("POST", "/api/probe", body=json.dumps({"ip": "127.0.0.1", "port": self.mixer.port}), headers=hdr)
        self.assertEqual(code, 200)
        for path in ("/api/update", "/api/tests", "/api/shutdown"):
            code, _, _ = self.req("POST", path, body="{}", headers=hdr)
            self.assertEqual(code, 403, path)
        self.assertEqual(self.mixer.write_attempts, [])

    def test_cookie_without_token_rejected(self):
        ck = self.cookie()
        code, _, _ = self.req("POST", "/api/probe", body=json.dumps({"ip": "127.0.0.1"}),
                              headers={"Cookie": ck, "Content-Type": "application/json"})
        self.assertEqual(code, 403)

    def test_data_folder_hides_secrets(self):
        ck = self.cookie()
        code, _, _ = self.req("GET", "/data/pin.txt", headers={"Cookie": ck})
        self.assertEqual(code, 404)
        for secret in ("/data/x.json", "/data/gemini.json", "/data/channels.json", "/data/mixer-ip.txt"):
            code, _, _ = self.req("GET", secret, headers={"Cookie": ck})
            self.assertEqual(code, 404, secret)
        code, _, _ = self.req("GET", "/data/audit-1/dump1.json", headers={"Cookie": ck})
        self.assertEqual(code, 200)

    def test_hostname_rejected_ip_accepted(self):
        code, _, _ = self.req("GET", "/login", host=f"evil.example:{self.port}")
        self.assertEqual(code, 403)
        code, _, _ = self.req("GET", "/login", host=f"8.8.8.8:{self.port}")  # bukan IP pribadi
        self.assertEqual(code, 403)
        code, _, _ = self.req("GET", "/login", host=f"10.0.0.5:{self.port}")
        self.assertEqual(code, 200)


class NotLan(unittest.TestCase):
    def test_lan_off_rejects_ip_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App(root=ROOT, data=Path(tmp) / "data")
            probe = serve(app, 0)
            port = probe.server_address[1]
            probe.server_close()
            httpd = serve(app, port)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                self.assertEqual(httpd.server_address[0], "127.0.0.1")
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("GET", "/viewer/home.html", headers={"Host": f"192.168.1.20:{port}"})
                self.assertEqual(c.getresponse().status, 403)
                c.close()
            finally:
                httpd.shutdown()
                httpd.server_close()


class Pin(unittest.TestCase):
    def test_pin_created_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d" / "pin.txt"
            a = load_or_create_pin(p)
            self.assertRegex(a, r"^\d{6}$")
            self.assertEqual(load_or_create_pin(p), a)


if __name__ == "__main__":
    unittest.main()
