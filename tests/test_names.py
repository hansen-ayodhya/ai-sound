import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aise import ai, channels, live, report


class CleanName(unittest.TestCase):
    def test_strips_mixer_suffix_only(self):
        self.assertEqual(channels.clean_name("Lead Keyb_27"), "Lead Keyb")
        self.assertEqual(channels.clean_name("Bass_19"), "Bass")
        self.assertEqual(channels.clean_name("Vokal 1"), "Vokal 1")
        self.assertEqual(channels.clean_name("Mic_A"), "Mic_A")
        self.assertEqual(channels.clean_name(None), "")

    def test_used_in_labels_report_and_prompt(self):
        dump = {"values": {"/ch/01/config/name": ["Lead Keyb_27"], "/ch/01/mix/fader": [0.5]}}
        self.assertEqual(live.labels_from_dump(dump)[0], "CH 01 Lead Keyb")
        self.assertEqual(report._label(dump["values"], 1)[0], "Lead Keyb")
        self.assertIn('"nama_mixer": "Lead Keyb"', ai.build_prompt(dump, None, [], None, "umum", ""))


if __name__ == "__main__":
    unittest.main()


class RecommendationNames(unittest.TestCase):
    """Teks rekomendasi menyebut channel dengan nama, bukan nomor; 'CH nn' hanya bila tanpa nama."""

    def test_display_and_strip(self):
        from aise import channels
        prof = {"sources": [{"nama": "Khotbah", "jenis": "khotbah", "ch": [4]}]}
        self.assertEqual(channels.display_name(prof, "WL Pdt_27", 3), "WL Pdt")
        self.assertEqual(channels.display_name(prof, "", 4), "Khotbah")
        self.assertEqual(channels.display_name(prof, None, 5), "CH 05")
        self.assertEqual(channels.strip_ch("CH 03 WL Pdt (Vokal)"), "WL Pdt (Vokal)")
        self.assertEqual(channels.strip_ch("CH 03"), "CH 03")

    def test_rule_findings_use_names(self):
        from aise import convert
        from aise.report import findings_json
        dump = {"values": {"/ch/01/config/name": ["Vokal 1_01"], "/ch/01/preamp/hpon": [0], "/ch/01/preamp/hpf": [0.1],
                           "/ch/02/config/name": [""], "/ch/02/preamp/hpon": [0]}}
        prof = {"sources": [{"id": "a", "nama": "Khotbah", "jenis": "khotbah", "ch": [2], "stereo": False, "koneksi": ""}]}
        out = findings_json(dump, prof)
        text = " ".join(f["masalah"] for f in out)
        self.assertIn("Vokal 1", text)
        self.assertIn("Khotbah", text)
        self.assertNotRegex(text, r"\b[Cc][Hh] 0[12]\b")
        self.assertNotIn("'Vokal 1'", text)
