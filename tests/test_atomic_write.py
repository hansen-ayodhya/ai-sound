import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise.server import _write_atomic


class AtomicWrite(unittest.TestCase):
    def test_reader_never_sees_empty_or_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dump1.json"
            _write_atomic(p, json.dumps({"n": 0}))
            stop, bad = threading.Event(), []

            def reader():
                while not stop.is_set():
                    try:
                        json.loads(p.read_text())
                    except ValueError as exc:                    # file kosong atau terpotong
                        bad.append(str(exc))
                    except OSError:
                        pass                                      # Windows: sedang diganti; bukan data rusak

            t = threading.Thread(target=reader)
            t.start()
            try:
                for i in range(400):
                    _write_atomic(p, json.dumps({"n": i, "pad": "x" * 20000}))
            finally:
                stop.set()
                t.join()
            self.assertEqual(bad, [])
            self.assertEqual(json.loads(p.read_text())["n"], 399)
            self.assertFalse(list(Path(tmp).glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
