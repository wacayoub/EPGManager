# -*- coding: utf-8 -*-
"""Offline regression: no synthetic programmes inserted, no receiver writes."""
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from tools import prune_published_blank_titles as repair


class TestBlankProgrammeRepair(unittest.TestCase):
    def example(self, empty=1, bad_time=False):
        now = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
        root = ET.Element("tv", attrib={"generator-info-name": "official source"})
        for n in ("c1", "c2"):
            node = ET.SubElement(root, "channel", id=n)
            ET.SubElement(node, "display-name").text = "اسم القناة " + n
        for n, cid, title in ((0, "c1", "الخبر الأصلي"), (1, "c1", ""),
                              (2, "c2", "برنامج موثق")):
            if n == 1 and not empty:
                title = "برنامج آخر"
            start = (now - timedelta(minutes=10)).strftime("%Y%m%d%H%M%S +0000")
            stop = (now + timedelta(minutes=25)).strftime("%Y%m%d%H%M%S +0000")
            p = ET.SubElement(root, "programme",
                              channel=cid,
                              start=start if not bad_time or n != 0 else "invalid",
                              stop=stop)
            ET.SubElement(p, "title", {"lang": "ar"}).text = title
            ET.SubElement(p, "desc", {"lang": "ar"}).text = "وصف أصلي"
        raw = gzip.compress(ET.tostring(root, encoding="utf-8"), mtime=0)
        generated = "2026-10-14T13:00:00+00:00"
        meta = {
            "label": "shahid", "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw), "channels": 2, "active_channels": 2,
            "programmes": 3, "generated_utc": generated,
        }
        expected = {"shahid": {"sha256": meta["sha256"], "empty_titles": 1,
                                "programmes": 3, "channels": 2}}
        return raw, meta, expected, now

    def test_exact_repair_preserves_titles_ids_descriptions_and_generation(self):
        raw, metadata, expected, now = self.example()
        new_gz, new_meta, report = repair.create_repair(
            "shahid", raw, metadata, now, expected)
        new_xml = ET.fromstring(gzip.decompress(new_gz))
        self.assertEqual(len(new_xml.findall("channel")), 2)
        self.assertEqual(len(new_xml.findall("programme")), 2)
        self.assertEqual([x.findtext("title") for x in new_xml.findall("programme")],
                         ["الخبر الأصلي", "برنامج موثق"])
        self.assertEqual([x.findtext("desc") for x in new_xml.findall("programme")],
                         ["وصف أصلي", "وصف أصلي"])
        self.assertEqual(new_meta["generated_utc"], metadata["generated_utc"])
        self.assertEqual(new_meta["programmes"], 2)
        self.assertEqual(new_meta["active_channels"], 2)
        self.assertEqual(new_meta["currently_on_air_channels"], 2)
        self.assertEqual(new_meta["rejected_missing_title"], 1)
        self.assertEqual(new_meta["sha256"], hashlib.sha256(new_gz).hexdigest())
        self.assertEqual(report["fabricated_programmes"], 0)

    def test_tampered_source_sha_refused(self):
        raw, meta, expected, now = self.example()
        with self.assertRaisesRegex(ValueError, "Source has changed"):
            repair.create_repair("shahid", raw+b"extra", meta, now, expected)

    def test_wrong_blank_count_refused(self):
        raw, meta, expected, now = self.example(empty=0)
        with self.assertRaisesRegex(ValueError, "Expected exactly"):
            repair.create_repair("shahid", raw, meta, now, expected)

    def test_bad_timestamp_refused(self):
        raw, meta, expected, now = self.example(bad_time=True)
        with self.assertRaisesRegex(ValueError, "Invalid timestamp"):
            repair.create_repair("shahid", raw, meta, now, expected)

    def test_metadata_forgery_refused(self):
        raw, meta, expected, now = self.example()
        meta["programmes"] = 10
        with self.assertRaisesRegex(ValueError, "metadata"):
            repair.create_repair("shahid", raw, meta, now, expected)

    def test_unknown_source_never_repaired(self):
        raw, meta, expected, now = self.example()
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            repair.create_repair("tabie", raw, meta, now, expected)

    def test_dry_run_does_not_change_feed_or_meta(self):
        raw, meta, expected, now = self.example()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "feeds").mkdir()
            (base / "reports").mkdir()
            (base / "feeds/shahid.xml.gz").write_bytes(raw)
            (base / "feeds/shahid.json").write_text(json.dumps(meta))
            old_metadata = (base / "feeds/shahid.json").read_bytes()
            with patch.dict(repair.EXPECTED, expected, clear=True):
                report = repair.run("shahid", base, apply=False)
            self.assertEqual(report["removed_untitled_programmes"], 1)
            self.assertEqual(raw, (base / "feeds/shahid.xml.gz").read_bytes())
            self.assertEqual(old_metadata, (base / "feeds/shahid.json").read_bytes())
            self.assertFalse((base / "reports/shahid-title-repair.json").exists())

    def test_apply_writes_exact_three_public_files_only(self):
        raw, meta, expected, now = self.example()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "feeds").mkdir()
            (base / "reports").mkdir()
            (base / "feeds/shahid.xml.gz").write_bytes(raw)
            (base / "feeds/shahid.json").write_text(json.dumps(meta))
            with patch.dict(repair.EXPECTED, expected, clear=True):
                report = repair.run("shahid", base, apply=True)
            self.assertEqual(report["removed_untitled_programmes"], 1)
            self.assertEqual(len(list(base.rglob("*"))), 5)
            repaired = json.loads((base / "feeds/shahid.json").read_text())
            self.assertEqual(repaired["programmes"], 2)

    def test_no_receiver_touch_or_auto_apply(self):
        import inspect
        source = inspect.getsource(repair)
        for forbidden in ("opkg install", "os.system(", "subprocess.run(", "/etc/enigma2",
                          "/usr/lib/enigma2", "urllib.request", "requests.get", "rm -rf"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(sum(v["empty_titles"] for v in repair.EXPECTED.values()), 5)


if __name__ == "__main__":
    unittest.main()
