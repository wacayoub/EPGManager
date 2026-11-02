# -*- coding: utf-8 -*-
"""Offline regression for report cohort publication decisions. No network or receiver."""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from report_cohort_gate import gate


class TestPublicationCohort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.feeds = self.root / "feeds"
        self.feeds.mkdir()
        self.decision = self.root / "publish.json"
        self.gz = gzip.compress(b'<tv><channel id="2M"/></tv>', mtime=0)
        (self.feeds / "morocco.xml.gz").write_bytes(self.gz)
        self.meta = {
            "label": "morocco",
            "generated_utc": "2026-10-17T09:00:00+00:00",
            "sha256": hashlib.sha256(self.gz).hexdigest(),
            "size_bytes": len(self.gz)
        }
        self._write_meta()
        self._write_decision("PUBLISH", True)

    def _write_meta(self):
        (self.feeds / "morocco.json").write_text(json.dumps(self.meta), encoding="utf-8")

    def _write_decision(self, decision, published):
        self.decision.write_text(json.dumps({
            "sources": {"morocco": {"decision": decision, "published": published}}
        }), encoding="utf-8")

    def check(self):
        return gate("morocco", self.decision, self.feeds)

    def test_accepted_published_bytes_rebuild(self):
        self.assertEqual(self.check(), "REBUILD")

    def test_keep_lkg_never_refreshes_quality_or_48h(self):
        self._write_decision("KEEP_LKG", False)
        (self.feeds / "morocco.xml.gz").unlink()
        (self.feeds / "morocco.json").unlink()
        self.assertEqual(self.check(), "KEEP")

    def test_keep_lkg_with_published_true_fails_closed(self):
        self._write_decision("KEEP_LKG", True)
        with self.assertRaises(ValueError):
            self.check()

    def test_unknown_publication_decision_fails_closed(self):
        self._write_decision("UNKNOWN", False)
        with self.assertRaises(ValueError):
            self.check()

    def test_changed_public_bytes_cannot_generate_reports(self):
        (self.feeds / "morocco.xml.gz").write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            self.check()

    def test_same_size_wrong_sha_cannot_generate_reports(self):
        self.meta["sha256"] = "0" * 64
        self._write_meta()
        with self.assertRaisesRegex(ValueError, "SHA256 differs"):
            self.check()

    def test_wrong_source_label_cannot_generate_reports(self):
        self.meta["label"] = "osn"
        self._write_meta()
        with self.assertRaises(ValueError):
            self.check()

    def test_missing_publication_time_cannot_generate_reports(self):
        self.meta["generated_utc"] = ""
        self._write_meta()
        with self.assertRaises(ValueError):
            self.check()


if __name__ == "__main__":
    unittest.main()
