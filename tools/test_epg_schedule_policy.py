# -*- coding: utf-8 -*-
"""Offline contract tests for Casablanca-aware EPG retry selection."""
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest
from zoneinfo import ZoneInfo
from tools import epg_schedule_policy as policy

MA = ZoneInfo("Africa/Casablanca")


def source(root, name, stamp, onair=80, active=100, decision="PUBLISH"):
    root = Path(root)
    (root / "feeds").mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    (root / "feeds" / (name+".json")).write_text(json.dumps({
        "generated_utc": stamp, "repacked_utc": "2026-10-15T15:30:00+00:00",
        "active_channels": active, "currently_on_air_channels": onair,
    }))
    (root / "reports" / (name+"-publish-lkg.json")).write_text(json.dumps({
        "generated_utc": stamp, "sources": {name: {"decision": decision}}
    }))


class TestAdaptiveSchedule(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 15, 15, 0, tzinfo=MA)

    def test_current_healthy_sources_skip_all_retries(self):
        with tempfile.TemporaryDirectory() as td:
            for name in policy.TARGETS:
                source(td, name, "2026-10-15T12:00:00Z")
            actual = policy.plan(td, self.now)
            self.assertEqual(actual["targets"], [])

    def test_stale_source_repacked_today_is_still_stale(self):
        with tempfile.TemporaryDirectory() as td:
            for name in policy.TARGETS:
                source(td, name, "2026-10-14T12:00:00Z")
            actual = policy.plan(td, self.now)
            self.assertEqual(actual["targets"], list(policy.TARGETS))
            self.assertEqual(actual["details"][0]["reason"], "PROVIDER_SOURCE_NOT_GENERATED_TODAY")

    def test_source_lkg_rejection_triggers_one_retry(self):
        with tempfile.TemporaryDirectory() as td:
            for name in policy.TARGETS:
                source(td, name, "2026-10-15T08:00:00Z")
            source(td, "shahid", "2026-10-15T08:00:00Z", decision="KEEP_LKG")
            actual = policy.plan(td, self.now)
            self.assertEqual(actual["targets"], ["shahid"])
            self.assertEqual(actual["details"][0]["reason"], "TODAY_SOURCE_CANDIDATE_REJECTED")

    def test_measured_low_publication_coverage_triggers_not_entire_strict_48h_failure(self):
        with tempfile.TemporaryDirectory() as td:
            for name in policy.TARGETS:
                source(td, name, "2026-10-15T08:00:00Z")
            source(td, "shahid", "2026-10-15T08:00:00Z", onair=45)
            self.assertEqual(policy.plan(td, self.now)["targets"], ["shahid"])

    def test_invalid_coverage_does_not_force_retry(self):
        with tempfile.TemporaryDirectory() as td:
            for name in policy.TARGETS:
                source(td, name, "2026-10-15T08:00:00Z", active=0, onair=0)
            self.assertEqual(policy.plan(td, self.now)["targets"], [])

    def test_active_main_chain_blocks_second_scraping(self):
        with tempfile.TemporaryDirectory() as td:
            got = policy.plan(td, self.now, main_chain_active=True)
            self.assertEqual(got["targets"], [])
            self.assertIn("MAIN_CHAIN_STILL_ACTIVE", got["skip_reason"])
            self.assertTrue(all(r["retry"] for r in got["details"]))

    def test_elcinema_not_selected_for_extra_15h_retry(self):
        with tempfile.TemporaryDirectory() as td:
            for name in policy.TARGETS:
                source(td, name, "2026-10-15T12:00:00Z")
            # No ElCinema source file at all: do not re-scrape it.
            self.assertEqual(policy.plan(td, self.now)["targets"], [])
            # Stale ElCinema source + LKG rejection still cannot requeue it.
            source(td, "elcinema", "2026-10-14T12:00:00Z", decision="KEEP_LKG")
            self.assertNotIn("elcinema", policy.plan(td, self.now)["targets"])
            self.assertEqual(policy.plan(td, self.now)["targets"], [])

    def test_bein_stale_or_rejected_never_gets_second_15h_scrape(self):
        with tempfile.TemporaryDirectory() as td:
            for name in policy.TARGETS:
                source(td, name, "2026-10-15T12:00:00Z")
            # Missing/stale/rejected beIN is not eligible for the 15h retry.
            self.assertEqual(policy.plan(td, self.now)["targets"], [])
            source(td, "bein", "2026-10-14T12:00:00Z", decision="KEEP_LKG")
            self.assertNotIn("bein", policy.plan(td, self.now)["targets"])
            self.assertEqual(policy.plan(td, self.now)["targets"], [])

    def test_casablanca_summer_and_ramadan_clock_boundaries(self):
        # Do not hardcode UTC+1. Runner tzdata may receive rule changes
        # from IANA while Morocco also suspends DST during Ramadan.
        for stamp in ("2026-10-14T23:30:00Z", "2026-03-01T23:30:00Z"):
            instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            expected = instant.astimezone(MA).date()
            self.assertEqual(policy.local_date(stamp), expected)
        self.assertIsNone(policy.local_date("bad timestamp"))

    def test_only_shahid_can_retry(self):
        self.assertEqual(policy.TARGETS, ("shahid",))
        self.assertEqual(policy.MAX_RETRIES, 1)

    def test_osn_cannot_retry_even_if_missing_stale_or_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            source(td, "shahid", "2026-10-15T12:00:00Z")
            self.assertEqual(policy.plan(td, self.now)["targets"], [])
            source(td, "osn", "2026-10-14T12:00:00Z", onair=0,
                   decision="KEEP_LKG")
            self.assertNotIn("osn", policy.plan(td, self.now)["targets"])
            self.assertEqual(policy.plan(td, self.now)["targets"], [])


if __name__ == "__main__":
    unittest.main()
