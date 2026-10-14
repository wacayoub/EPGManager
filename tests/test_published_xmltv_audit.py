# -*- coding: utf-8 -*-
"""Synthetic fixture regression tests for read-only XMLTV release audit."""
from datetime import datetime, timezone, timedelta
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import gzip
import hashlib
import unittest

path = Path(__file__).resolve().parents[1] / "tools" / "audit_published_xmltv.py"
spec = spec_from_file_location("xmltv_auditor", str(path))
auditor = module_from_spec(spec)
spec.loader.exec_module(auditor)


class TestPublishedXMLTVAudit(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    def fixture(self, title="Actual EPG programme", start="20261014110000 +0000",
                stop="20261014130000 +0000", age_hours=1, unknown=False):
        channel = "unknown" if unknown else "channel.one"
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<tv generator-info-name="unit-test">'
               '<channel id="channel.one"><display-name>Channel One</display-name></channel>'
               '<programme channel="' + channel + '" start="' + start
               + '" stop="' + stop + '"><title lang="en">' + title
               + '</title><desc lang="ar">وصف</desc></programme></tv>')
        packed = gzip.compress(xml.encode("utf-8"))
        meta = {
            "label": "morocco", "size_bytes": len(packed),
            "sha256": hashlib.sha256(packed).hexdigest(), "channels": 1,
            "programmes": 1, "active_channels": 1, "zero_epg_ids": [],
            "generated_utc": (self.now - timedelta(hours=age_hours)).isoformat(),
        }
        return packed, meta

    def test_actual_xml_matches_summary_and_samples_real_programmes(self):
        raw, meta = self.fixture()
        result = auditor.audit_xml(raw, meta, self.now)
        self.assertTrue(result["pass"], result["issues"])
        self.assertEqual(result["active_channels"], 1)
        self.assertEqual(result["channels_with_current_programme"], 1)
        self.assertEqual(result["sampled_real_programmes"][0]["title"],
                         "Actual EPG programme")
        self.assertTrue(result["sampled_real_programmes"][0]["description_present"])

    def test_detects_stale_feed_and_missing_current_programme(self):
        raw, meta = self.fixture(age_hours=200, start="20261013110000 +0000",
                                 stop="20261013130000 +0000")
        result = auditor.audit_xml(raw, meta, self.now)
        self.assertFalse(result["pass"])
        self.assertIn("stale_or_future_generated_feed", result["issues"])
        self.assertEqual(result["channels_with_current_programme"], 0)

    def test_detects_false_metadata_sha_and_counts(self):
        raw, meta = self.fixture()
        meta["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA256"):
            auditor.audit_xml(raw, meta, self.now)
        meta["sha256"] = hashlib.sha256(raw).hexdigest()
        meta["programmes"] = 2
        self.assertIn("declared_programme_count_mismatch",
                      auditor.audit_xml(raw, meta, self.now)["issues"])

    def test_detects_invalid_times_missing_titles_and_unknown_ids(self):
        raw, meta = self.fixture(title="", stop="20261014100000 +0000",
                                 unknown=True)
        result = auditor.audit_xml(raw, meta, self.now)
        self.assertFalse(result["pass"])
        self.assertIn("bad_xmltv_times", result["issues"])
        self.assertIn("empty_programme_titles", result["issues"])
        self.assertIn("programmes_reference_unknown_channels", result["issues"])

    def test_online_reader_receives_only_read_only_get_requests(self):
        raw, meta = self.fixture()
        calls = []
        def reader(url, budget):
            calls.append((url, budget))
            return (raw if url.endswith(".xml.gz") else
                    __import__("json").dumps(meta).encode("utf-8"))
        output = auditor.audit_source("morocco", self.now, reader=reader)
        self.assertTrue(output["pass"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(u.startswith(auditor.BASE) for u, _ in calls))


if __name__ == "__main__":
    unittest.main()
