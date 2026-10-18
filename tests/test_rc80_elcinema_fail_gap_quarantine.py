# -*- coding: utf-8 -*-
"""RC80 public-source guard: source-wide FAIL must preserve per-ID quarantine."""
from datetime import datetime, timedelta, timezone
import unittest

from tools.audit_rc80_public_feed_provenance import assess

NOW = datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)
STAMP = (NOW - timedelta(hours=1)).isoformat()


def published_fixture():
    feed = {
        "label": "elcinema", "channels": 4, "active_channels": 3,
        "currently_on_air_channels": 3, "generated_utc": STAMP,
        "sha256": "a" * 64, "size_bytes": 100,
    }
    audit = {"generated_utc": STAMP, "feeds": [{
        "feed": "elcinema.xml.gz", "status": "FAIL",
        "channels": 4, "invalid_times": 0, "orphan_programmes": 0,
        "failed_channels": 2, "rows": [
            {"id": "A", "status": "WARN", "reason": "SHORT_HORIZON+INTERNAL_GAP",
             "max_gap_minutes": 1440, "future_hours": 45.0},
            {"id": "B", "status": "FAIL", "reason": "TOO_SHORT",
             "max_gap_minutes": 0, "future_hours": 18.0},
            {"id": "C", "status": "FAIL", "reason": "NO_FUTURE_EPG",
             "max_gap_minutes": None, "future_hours": 0},
            {"id": "D", "status": "PASS", "reason": "",
             "max_gap_minutes": 0, "future_hours": 48.0},
        ],
    }]}
    quality = {"generated_utc": STAMP, "source": "elcinema",
               "source_file": "elcinema.xml.gz", "quality_status": "WARN"}
    return feed, audit, quality


class TestElCinemaSourceFailGapQuarantine(unittest.TestCase):
    def test_fail_cohort_keeps_real_gap_short_and_zero_ids_separate(self):
        feed, audit, quality = published_fixture()
        row = assess("elcinema", feed, audit, quality, now=NOW.timestamp())
        self.assertTrue(row["quality_cohort_verified"])
        self.assertEqual(row["status"], "48H_PARTIAL_IDS_NEED_XMLTV_PROOF")
        self.assertEqual(row["partial_48h_large_gap_ids"], ["A"])
        self.assertEqual(row["partial_48h_short_ids"], ["B"])
        self.assertEqual(row["partial_48h_zero_ids"], ["C"])
        self.assertEqual(row["partial_48h_blocked_ids"], ["A", "B", "C"])
        self.assertFalse(row["ready_for_epg_ok"])

    def test_false_fail_count_quarantines_entire_source(self):
        feed, audit, quality = published_fixture()
        audit["feeds"][0]["failed_channels"] = 1
        row = assess("elcinema", feed, audit, quality, now=NOW.timestamp())
        self.assertEqual(row["status"], "48H_AUDIT_INCONSISTENT_IDS")
        self.assertFalse(row["ready_for_epg_ok"])

    def test_zero_event_id_cannot_claim_a_real_future_programme(self):
        feed, audit, quality = published_fixture()
        audit["feeds"][0]["rows"][2]["future_hours"] = 1.0
        row = assess("elcinema", feed, audit, quality, now=NOW.timestamp())
        self.assertEqual(row["status"], "48H_AUDIT_FAIL")
        self.assertFalse(row["ready_for_epg_ok"])


if __name__ == "__main__":
    unittest.main()
