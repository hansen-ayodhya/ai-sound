import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise.fake_mixer import FakeMixer
from aise.server import ROOT, App, serve

STATE = {
    "/info": ("V1.0", "MR18", "1.23"),
    "/ch/01/config/name": ("Vokal 1",),
    "/ch/01/preamp/hpon": (0,),
}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mixer = FakeMixer(STATE).start()
        self.app = App(root=ROOT, data=Path(self.tmp.name) / "data", addresses=list(STATE))
        self.httpd = serve(self.app, 0)
        self.port = self.httpd.server_address[1]
        # Host yang diizinkan memakai port sebenarnya; buat ulang handler dengan port itu.
        self.httpd.server_close()
        self.httpd = serve(self.app, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.mixer.stop()
        self.tmp.cleanup()

    def req(self, path, body=None, token=True, host=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        if token:
            r.add_header("X-AISE-Token", self.app.token)
        if body is not None:
            r.add_header("Content-Type", "application/json")
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw = resp.read()
                return resp.status, raw
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def target(self, **kw):
        return {"ip": "127.0.0.1", "port": self.mixer.port, **kw}

    def test_token_required(self):
        code, _ = self.req("/api/status", token=False)
        self.assertEqual(code, 403)
        code, _ = self.req("/api/probe", self.target(), token=False)
        self.assertEqual(code, 403)
        self.assertEqual(self.mixer.queries, [])

    def test_foreign_host_rejected(self):
        code, _ = self.req("/api/status", host="evil.example:80")
        self.assertEqual(code, 403)

    def test_html_gets_token_and_static_is_confined(self):
        code, raw = self.req("/viewer/home.html", token=False)
        self.assertEqual(code, 200)
        self.assertIn(self.app.token.encode(), raw)
        for bad in ("/viewer/../src/aise/server.py", "/viewer/%2e%2e/README.md", "/src/aise/server.py"):
            code, _ = self.req(bad, token=False)
            self.assertEqual(code, 404, bad)

    def test_probe(self):
        code, raw = self.req("/api/probe", self.target())
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["answered"], 2)
        self.assertEqual(self.mixer.write_attempts, [])

    def test_bad_input(self):
        code, _ = self.req("/api/probe", {"ip": "bukan-ip"})
        self.assertEqual(code, 400)
        code, _ = self.req("/api/meters", self.target(path="/ch/01/mix/fader"))
        self.assertEqual(code, 400)
        code, _ = self.req("/api/audit", self.target())  # tanpa centang
        self.assertEqual(code, 400)

    def test_audit_job(self):
        code, _ = self.req("/api/audit", self.target(no_service=True, scene_saved=True))
        self.assertEqual(code, 202)
        for _ in range(100):
            job = json.loads(self.req("/api/job")[1])
            if job["state"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job["state"], "done", job)
        self.assertTrue(job["result"]["identical"])
        status = json.loads(self.req("/api/status")[1])
        self.assertEqual(status["ip"], "127.0.0.1")
        self.assertTrue(status["latest"]["report"].startswith("data/audit-"))
        code, _ = self.req("/" + status["latest"]["report"], token=False)
        self.assertEqual(code, 200)
        self.assertEqual(self.mixer.write_attempts, [])


if __name__ == "__main__":
    unittest.main()
