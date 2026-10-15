# -*- coding: utf-8 -*-
"""Offline, synthetic, no-receiver tests of experimental RC80 evidence refresh."""
from datetime import datetime, timedelta, timezone
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from unittest.mock import patch
import gzip
import hashlib
import json
import os
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "source/overlay/core/verified_epg_refresh.py"
BRIDGE = ROOT / "source/overlay/core/verified_epg_match.py"
import sys
import types
package = sys.modules.setdefault("rc80testcore", types.ModuleType("rc80testcore"))
package.__path__ = [str(BRIDGE.parent)]
spec = spec_from_file_location("rc80testcore.verified_epg_match", str(BRIDGE))
bridge = module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)
spec = spec_from_file_location("rc80testcore.verified_epg_refresh", str(MODULE))
refresh = module_from_spec(spec)
sys.modules[spec.name] = refresh
spec.loader.exec_module(refresh)
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)


def fixture(source="morocco", now=NOW):
    stamp = now - timedelta(hours=1)
    timestamp = stamp.isoformat()
    start_now = (now - timedelta(minutes=30)).strftime("%Y%m%d%H%M%S +0000")
    stop_now = (now + timedelta(minutes=30)).strftime("%Y%m%d%H%M%S +0000")
    start_later = (now + timedelta(minutes=30)).strftime("%Y%m%d%H%M%S +0000")
    stop_later = (now + timedelta(hours=3)).strftime("%Y%m%d%H%M%S +0000")
    xml = ('<tv>'
           '<channel id="A"><display-name>A</display-name></channel>'
           '<channel id="B"><display-name>B</display-name></channel>'
           '<channel id="NONE"><display-name>None</display-name></channel>'
           '<channel id="SYN"><display-name>Placeholder</display-name></channel>'
           '<programme channel="A" start="%s" stop="%s"><title lang="ar">برنامج حقيقي</title></programme>'
           '<programme channel="B" start="%s" stop="%s"><title>Tomorrow show</title></programme>'
           '<programme channel="SYN" start="%s" stop="%s"><title>Channel name only</title></programme>'
           '</tv>') % (start_now, stop_now, start_later, stop_later, start_now, stop_now)
    gz = gzip.compress(xml.encode("utf-8"), mtime=0)
    meta = {"label": source, "generated_utc": timestamp,
            "sha256": hashlib.sha256(gz).hexdigest(), "size_bytes": len(gz),
            "channels": 4, "active_channels": 3, "programmes": 3}
    report = {"generated_utc": timestamp,
              "feeds": [{"feed": source + ".xml.gz", "status": "PASS"}]}
    quality = {"generated_utc": timestamp, "source": source,
               "source_file": source + ".xml.gz", "quality_status": "PASS",
               "suspected_placeholder_channels": [{"id": "SYN", "count": 1}]}
    docs = {
        "feeds/" + source + ".json": json.dumps(meta).encode(),
        "reports/" + source + "-48h-audit.json": json.dumps(report).encode(),
        "reports/" + source + "-quality.json": json.dumps(quality).encode(),
        "feeds/" + source + ".xml.gz": gz,
    }
    return docs, meta, report, quality, xml


def reader(docs):
    def get(path, limit):
        assert path in docs, path
        assert len(docs[path]) <= limit
        return docs[path]
    return get


class TestRC80PublicEvidenceRefresh(unittest.TestCase):
    def test_hash_verified_current_programmes_beat_future_and_generated(self):
        docs, *_ = fixture()
        row = refresh.inspect_source("morocco", now=NOW.timestamp(), reader=reader(docs))
        self.assertEqual(row["status"], "HASHED_XMLTV_AND_AUDITS_HEALTHY")
        self.assertTrue(row["ready_for_epg_ok"])
        self.assertEqual(row["trusted_current_epg_ids"], ["A"])
        self.assertEqual(row["review_future_epg_ids"], ["B"])
        self.assertEqual(row["suspected_placeholder_epg_ids"], ["SYN"])
        self.assertEqual(row["xmltv_observed_zero_ids"], ["NONE"])
        self.assertEqual(row["xmltv_live_now_channels"], 2)
        self.assertTrue(row["xmltv_sha256_verified"])

    def test_wrong_hash_must_not_create_verified_programme_ids(self):
        docs, meta, *_ = fixture()
        docs["feeds/morocco.xml.gz"] += b"tampered"
        row = refresh.inspect_source("morocco", now=NOW.timestamp(), reader=reader(docs))
        self.assertFalse(row["ready_for_epg_ok"])
        self.assertFalse(row["xmltv_sha256_verified"])
        self.assertEqual(row["trusted_current_epg_ids"], [])

    def test_stale_original_publication_never_promotes_after_a_new_check(self):
        docs, meta, *_ = fixture()
        meta["generated_utc"] = (NOW - timedelta(days=8)).isoformat()
        docs["feeds/morocco.json"] = json.dumps(meta).encode()
        row = refresh.inspect_source("morocco", now=NOW.timestamp(), reader=reader(docs))
        self.assertEqual(row["status"], "STALE_OR_INVALID_PUBLISHED_FEED")
        self.assertFalse(row["ready_for_epg_ok"])

    def test_missing_quality_placeholder_field_cannot_grant_real_epg(self):
        docs, meta, report, quality, _ = fixture()
        quality.pop("suspected_placeholder_channels")
        docs["reports/morocco-quality.json"] = json.dumps(quality).encode()
        row = refresh.inspect_source("morocco", now=NOW.timestamp(), reader=reader(docs))
        self.assertFalse(row["ready_for_epg_ok"])
        self.assertEqual(row["status"], "XMLTV_CONTENT_QUALITY_ISSUES")

    def test_missing_title_invalid_time_or_incomplete_source_blocks_auto(self):
        docs, meta, _, _, original = fixture()
        xml = original.replace("برنامج حقيقي", "")
        packed = gzip.compress(xml.encode(), mtime=0)
        meta.update(sha256=hashlib.sha256(packed).hexdigest(), size_bytes=len(packed))
        docs["feeds/morocco.xml.gz"] = packed
        docs["feeds/morocco.json"] = json.dumps(meta).encode()
        row = refresh.inspect_source("morocco", now=NOW.timestamp(), reader=reader(docs))
        self.assertFalse(row["ready_for_epg_ok"])
        self.assertEqual(row["xmltv_missing_titles"], 1)

    def test_bad_48h_2m_does_not_disqualify_other_verified_channels(self):
        docs, _, audit48, _, _ = fixture()
        item = audit48["feeds"][0]
        item.update(status="FAIL", failed_channels=1, invalid_times=0,
                    orphan_programmes=0,
                    rows=[{"id": key, "status": "FAIL" if key == "B" else "PASS"}
                          for key in ("A", "B", "NONE", "SYN")])
        docs["reports/morocco-48h-audit.json"] = json.dumps(audit48).encode()
        row = refresh.inspect_source("morocco", NOW.timestamp(), reader(docs))
        self.assertEqual(row["status"], "HASHED_XMLTV_PER_ID_PARTIAL_48H")
        self.assertEqual(row["partial_48h_blocked_ids"], ["B"])
        self.assertEqual(row["trusted_current_epg_ids"], ["A"])
        self.assertEqual(row["review_future_epg_ids"], [])
        self.assertEqual(bridge.quality_for_id("direct_morocco", "B", report={
            "audit": "EPGManager RC80 public feed provenance",
            "evidence_schema": 1, "checked_utc": NOW.isoformat(),
            "full_xmltv_integrity_checks_enabled": True,
            "sources": [row]}, now=NOW.timestamp())["status"], "48H_FAILED_ID_REVIEW")

    def test_partial_48h_requires_complete_unambiguous_per_id_results(self):
        docs, _, audit48, _, _ = fixture()
        item = audit48["feeds"][0]
        item.update(status="FAIL", failed_channels=1, invalid_times=0,
                    orphan_programmes=0,
                    rows=[{"id": "B", "status": "FAIL"}])
        docs["reports/morocco-48h-audit.json"] = json.dumps(audit48).encode()
        row = refresh.inspect_source("morocco", NOW.timestamp(), reader(docs))
        self.assertFalse(row["ready_for_epg_ok"])
        self.assertEqual(row["status"], "48H_AUDIT_INCONSISTENT_IDS")

    def test_source_metadata_count_mismatch_does_not_auto_promote(self):
        docs, meta, *_ = fixture()
        meta["programmes"] = 9000
        docs["feeds/morocco.json"] = json.dumps(meta).encode()
        row = refresh.inspect_source("morocco", now=NOW.timestamp(), reader=reader(docs))
        self.assertFalse(row["ready_for_epg_ok"])

    def test_bounded_uncompressed_reader(self):
        stream = refresh._CappedRead(__import__("io").BytesIO(b"123456789"), maximum=6)
        self.assertEqual(stream.read(4), b"1234")
        with self.assertRaisesRegex(ValueError, "limit"):
            stream.read(4)

    def test_only_tmp_atomic_report_is_written_and_usable_by_ui(self):
        docs, *_ = fixture()
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            dest = str(Path(folder) / "verified.json")
            report = refresh.refresh_file(["morocco"], dest, NOW.timestamp(), reader(docs))
            self.assertEqual(os.stat(dest).st_mode & 0o777, 0o600)
            self.assertEqual(report["evidence_schema"], 1)
            loaded = bridge.load_local_report(dest)
            outcome = bridge.quality_for_id("direct_morocco", "A", report=loaded, now=NOW.timestamp())
            self.assertEqual(outcome["status"], "REAL_CURRENT_EPG")
            self.assertTrue(outcome["verified_now"])
            self.assertEqual(os.listdir(folder), ["verified.json"])
            self.assertEqual(json.loads(Path(dest).read_text())["checked_utc"], NOW.isoformat())

    def test_invalid_target_path_and_sources_are_rejected(self):
        with self.assertRaises(ValueError):
            refresh.refresh_file(["morocco"], "/etc/enigma2/data.json", NOW.timestamp())
        with self.assertRaises(ValueError):
            refresh.refresh_file(["wrong"], "/tmp/test.json", NOW.timestamp())
        with self.assertRaises(ValueError):
            refresh.refresh_file(["morocco", "osn", "bein"], "/tmp/test.json", NOW.timestamp())

    def test_missing_old_evidence_does_not_grant_false_ready(self):
        docs, *_ = fixture()
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            dest = str(Path(folder) / "verified.json")
            first = refresh.refresh_file(["morocco"], dest, NOW.timestamp(), reader(docs))
            self.assertEqual(len(first["sources"]), 1)
            stale = NOW + timedelta(hours=1)
            # The cache retains no old per-ID proof beyond the maximum age.
            docs2, *_ = fixture("osn", stale)
            second = refresh.refresh_file(["osn"], dest, stale.timestamp(), reader(docs2))
            self.assertEqual([x["source"] for x in second["sources"]], ["osn"])

    def test_disabled_ui_queue_never_starts_a_worker(self):
        with patch.object(refresh.threading, "Thread") as start:
            self.assertFalse(refresh.queue_background(["direct_morocco"], enabled=False))
            self.assertFalse(refresh.queue_background(["external_morocco"], enabled=True))
            start.assert_not_called()

    def test_symlinked_tmp_subdirectory_cannot_escape_to_receiver_config(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            with tempfile.TemporaryDirectory(dir=str(ROOT)) as outside:
                link = Path(directory) / "redirect"
                link.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    refresh.refresh_file(["morocco"], str(link / "evidence.json"),
                                         now=NOW.timestamp())
                self.assertEqual(os.listdir(outside), [])

    def test_wrong_root_and_invalid_gzip_are_rejected(self):
        docs, meta, *_ = fixture()
        raw = gzip.compress(b"<something></something>", mtime=0)
        meta.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        docs["feeds/morocco.json"] = json.dumps(meta).encode()
        docs["feeds/morocco.xml.gz"] = raw
        row = refresh.inspect_source("morocco", NOW.timestamp(), reader(docs))
        self.assertFalse(row["ready_for_epg_ok"])
        self.assertEqual(row["status"], "XMLTV_DOWNLOAD_OR_INTEGRITY_UNVERIFIED")

    def test_source_only_public_whitelist_no_receiver_secrets(self):
        code = MODULE.read_text()
        for bad in ("/etc/enigma2", "opkg install", "assign_exclusive",
                    "source_preferences.json", "subprocess", "os.system(", "shell=True"):
            self.assertNotIn(bad, code)


if __name__ == "__main__":
    unittest.main()
