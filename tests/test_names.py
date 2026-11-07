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
