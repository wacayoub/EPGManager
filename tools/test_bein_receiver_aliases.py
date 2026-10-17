#!/usr/bin/env python3
"""Synthetic beIN English-vs-Arabic receiver-ID regression; no network or receiver."""
import gzip
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from bein_receiver_id_normalize import normalize_file, read_root


class BeinDigitalEn2AliasTests(unittest.TestCase):
    def test_english_en2_is_canonicalized_without_merging_arabic_sports_2(self):
        raw_en2 = "beIN SPORTS2 ENGLISH Digital.qa"
        raw_ar2 = "beIN SPORTS2 DIGITAL.qa"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.xml.gz"
            tv = ET.Element("tv")
            for channel in (raw_en2, raw_ar2):
                node = ET.SubElement(tv, "channel", id=channel)
                ET.SubElement(node, "display-name").text = channel
                ET.SubElement(
                    tv, "programme",
                    channel=channel,
                    start="20261017000000 +0000",
                    stop="20261017010000 +0000",
                )
            path.write_bytes(gzip.compress(ET.tostring(tv, encoding="utf-8")))
            result = normalize_file(path)
            self.assertIsNotNone(result)
            self.assertEqual(result["channels"], 2)
            self.assertEqual(result["programmes"], 2)
            parsed = read_root(path)
            expected = {"beIN.Sports.EN2.qa", "beIN.Sports.2.qa"}
            self.assertEqual({x.get("id") for x in parsed.findall("channel")}, expected)
            self.assertEqual({x.get("channel") for x in parsed.findall("programme")}, expected)
            self.assertEqual(result["renamed"][raw_en2], "beIN.Sports.EN2.qa")
            self.assertEqual(result["renamed"][raw_ar2], "beIN.Sports.2.qa")


if __name__ == "__main__":
    unittest.main()
