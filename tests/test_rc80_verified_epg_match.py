# -*- coding: utf-8 -*-
"""Private-data-free, no-Enigma fixtures for RC80's optional preview bridge."""
from datetime import datetime, timezone, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "source/overlay/core/verified_epg_match.py"
spec = spec_from_file_location("verified_epg_match", str(PATH))
bridge = module_from_spec(spec)
spec.loader.exec_module(bridge)
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)


def stamp(dt):
    return dt.isoformat()


def fixture(now=NOW):
    return {
        "audit": "EPGManager RC80 public feed provenance",
        "evidence_schema": 1,
        "checked_utc": stamp(now - timedelta(minutes=2)),
        "full_xmltv_integrity_checks_enabled": True,
        "sources": [
            {"source": "morocco", "status": "HASHED_XMLTV_AND_AUDITS_HEALTHY",
             "xmltv_sha256_verified": True,
             "channel_specific_epg_evidence_verified": True,
             "real_now_coverage_verified": True,
             "per_id_evidence_observed_utc": stamp(now - timedelta(minutes=2)),
             "ready_for_epg_ok": True,
             "trusted_current_epg_ids": ["AlAoula.ma", "Arryadia.ma"],
             "review_future_epg_ids": ["Medi1.ma"],
             "suspected_placeholder_epg_ids": ["Fake.ma"],
             "xmltv_observed_zero_ids": ["Zero.ma"],
             "xmltv_current_real_ids": ["AlAoula.ma", "Arryadia.ma", "Untrusted.ma"]},
            {"source": "elcinema", "status": "XMLTV_CONTENT_QUALITY_ISSUES",
             "xmltv_sha256_verified": True,
             "channel_specific_epg_evidence_verified": True,
             "real_now_coverage_verified": True,
             "per_id_evidence_observed_utc": stamp(now - timedelta(minutes=2)),
             "ready_for_epg_ok": False,
             "trusted_current_epg_ids": [],
             "review_future_epg_ids": ["Future.eg"],
             "suspected_placeholder_epg_ids": ["BahrainTV.bh@SD"],
             "xmltv_observed_zero_ids": []},
        ]
    }


def candidate(sid, cid, confidence=950, saved=False):
    return {"source_id": sid, "channel_id": cid, "display_name": cid,
            "source_name": sid, "_precision_auto": True,
            "_precision_confidence": confidence, "_suggestion_current": saved}


class VerifiedEpgBridgeTests(unittest.TestCase):
    def check(self, sid, cid, report=None):
        return bridge.quality_for_id(sid, cid, report=fixture() if report is None else report, now=NOW.timestamp())

    def test_real_verified_now_is_highest_eligible_tier(self):
        result = self.check("direct_morocco", "AlAoula.ma")
        self.assertEqual(result["status"], "REAL_CURRENT_EPG")
        self.assertTrue(result["verified_now"])

    def test_direct_source_identity_must_match_namespace(self):
        self.assertEqual(self.check("direct_elcinema", "AlAoula.ma")["status"], "UNKNOWN")
        self.assertEqual(self.check("epgimport_morocco", "AlAoula.ma")["status"], "UNKNOWN")
        self.assertEqual(self.check("direct_nonexistent", "AlAoula.ma")["status"], "UNKNOWN")
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma.extra")["status"], "UNKNOWN")

    def test_partial_48h_report_certifies_only_nonfailed_real_now_ids(self):
        report = fixture()
        src = report["sources"][0]
        src["status"] = "HASHED_XMLTV_PER_ID_PARTIAL_48H"
        src["partial_48h_blocked_ids"] = ["Arryadia.ma"]
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma", report)["status"],
                         "REAL_CURRENT_EPG")
        self.assertEqual(self.check("direct_morocco", "Arryadia.ma", report)["status"],
                         "48H_FAILED_ID_REVIEW")
        self.assertFalse(self.check("direct_morocco", "Arryadia.ma", report)["verified_now"])

    def test_future_only_review_never_verified_now(self):
        status = self.check("direct_morocco", "Medi1.ma")
        self.assertEqual(status["status"], "FUTURE_ONLY_REVIEW")
        self.assertFalse(status["verified_now"])

    def test_unverified_current_event_does_not_count_as_real(self):
        self.assertEqual(self.check("direct_morocco", "Untrusted.ma")["status"], "CURRENT_UNVERIFIED")

    def test_placeholder_and_zero_ids_are_marked_but_not_promoted(self):
        self.assertEqual(self.check("direct_morocco", "Fake.ma")["status"], "SUSPECT_GENERATED_EPG")
        self.assertEqual(self.check("direct_morocco", "Zero.ma")["status"], "NO_EPG")
        self.assertEqual(self.check("direct_elcinema", "BahrainTV.bh@SD")["status"], "SUSPECT_GENERATED_EPG")

    def test_missing_wrong_or_old_report_fails_closed(self):
        for invalid in ({}, {"audit": "unexpected"}, {"full_xmltv_integrity_checks_enabled": False}):
            self.assertEqual(self.check("direct_morocco", "AlAoula.ma", invalid)["status"], "UNKNOWN")
        doc = fixture()
        doc["checked_utc"] = stamp(NOW - timedelta(hours=3))
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma", doc)["status"], "UNKNOWN")
        doc = fixture()
        doc["sources"][0]["per_id_evidence_observed_utc"] = stamp(NOW - timedelta(hours=3))
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma", doc)["status"], "UNKNOWN")
        doc = fixture()
        doc["checked_utc"] = stamp(NOW + timedelta(seconds=60))
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma", doc)["status"], "UNKNOWN")

    def test_failed_integrity_and_conflicting_source_rows_not_promoted(self):
        doc = fixture()
        doc["sources"][0]["xmltv_sha256_verified"] = False
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma", doc)["status"], "UNKNOWN")
        doc = fixture()
        doc["sources"].append(dict(doc["sources"][0]))
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma", doc)["status"], "UNKNOWN")
        doc = fixture()
        doc["sources"][0]["status"] = "XMLTV_CONTENT_QUALITY_ISSUES"
        # XML payload reports a current event, but failed global quality gate:
        # visible as unverified, NEVER marked REAL_CURRENT_EPG.
        result = self.check("direct_morocco", "AlAoula.ma", doc)
        self.assertEqual(result["status"], "CURRENT_UNVERIFIED")
        self.assertFalse(result["verified_now"])

    def test_preview_prefers_real_epg_to_fake_name_score(self):
        original = [
            candidate("direct_morocco", "Fake.ma", confidence=1000),
            candidate("direct_morocco", "AlAoula.ma", confidence=930),
            candidate("direct_morocco", "Medi1.ma", confidence=940),
            candidate("epgimport_morocco", "AlAoula.ma", confidence=990),
        ]
        ranked = bridge.annotate_suggestions(original, fixture(), NOW.timestamp())
        self.assertEqual([r["channel_id"] for r in ranked],
                         ["AlAoula.ma", "Medi1.ma", "AlAoula.ma", "Fake.ma"])
        self.assertEqual(original[0].get("_verified_epg_health"), None)

    def test_saved_manual_owner_stays_first_even_if_no_epg(self):
        rows = [candidate("direct_morocco", "AlAoula.ma"),
                candidate("direct_morocco", "Fake.ma", saved=True)]
        ranked = bridge.annotate_suggestions(rows, fixture(), NOW.timestamp())
        self.assertEqual(ranked[0]["channel_id"], "Fake.ma")
        self.assertEqual(ranked[0]["_verified_epg_health"], "SAVED_OWNER")

    def test_reviewer_can_still_see_all_suspect_and_zero_epg_options(self):
        rows = [candidate("direct_morocco", "Zero.ma"),
                candidate("direct_morocco", "Fake.ma"),
                candidate("direct_morocco", "AlAoula.ma")]
        ranked = bridge.annotate_suggestions(rows, fixture(), NOW.timestamp())
        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked[-1]["channel_id"], "Zero.ma")

    def test_producer_snapshot_is_compatible_with_preview_bridge(self):
        from unittest.mock import patch
        producer_path = PATH.parents[3] / "tools/audit_rc80_public_feed_provenance.py"
        spec = spec_from_file_location("rc80_public_evidence_producer", str(producer_path))
        producer = module_from_spec(spec)
        spec.loader.exec_module(producer)
        chosen = fixture()["sources"]
        def synthetic_result(source, _when, _verify):
            for row in chosen:
                if row["source"] == source:
                    valid = dict(row)
                    valid["pubcohort_verified"] = True
                    return valid
            return {"source": source, "status": "NETWORK_OR_FORMAT_UNVERIFIED",
                    "pubcohort_verified": False, "real_now_coverage_verified": False,
                    "xmltv_sha256_verified": False, "ready_for_epg_ok": False}
        with patch.object(producer, "fetch_one", side_effect=synthetic_result):
            output = producer.run_online(now=NOW.timestamp(), verify_xmltv=True)
        self.assertEqual(output["evidence_schema"], 1)
        self.assertEqual(output["checked_utc"], NOW.isoformat())
        self.assertEqual(len(output["sources"]), 14)
        self.assertEqual(bridge.quality_for_id("direct_morocco", "AlAoula.ma",
                          report=output, now=NOW.timestamp())["status"], "REAL_CURRENT_EPG")
        self.assertEqual(bridge.quality_for_id("direct_elcinema", "BahrainTV.bh@SD",
                          report=output, now=NOW.timestamp())["status"], "SUSPECT_GENERATED_EPG")

    def test_unknown_schema_never_confirms_epg(self):
        doc = fixture()
        doc.pop("evidence_schema")
        self.assertEqual(self.check("direct_morocco", "AlAoula.ma", doc)["status"], "UNKNOWN")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(bridge.load_local_report(str(path)))

    def test_local_loader_no_network_no_config_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "verified.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            before = path.read_bytes()
            report = bridge.load_local_report(str(path))
            self.assertEqual(report["audit"], fixture()["audit"])
            self.assertEqual(path.read_bytes(), before)
            path.write_text("not JSON", encoding="utf-8")
            self.assertIsNone(bridge.load_local_report(str(path)))
            path.write_bytes(b"1" * (bridge.MAX_REPORT_BYTES + 1))
            self.assertIsNone(bridge.load_local_report(str(path)))

    def test_unknown_direct_source_always_manual_review(self):
        rows = [candidate("direct_aljazeera", "AlJazeera.en"),
                candidate("direct_morocco", "AlAoula.ma")]
        output = bridge.annotate_suggestions(rows, fixture(), NOW.timestamp())
        self.assertEqual(output[0]["channel_id"], "AlAoula.ma")
        self.assertEqual(output[1]["_verified_epg_health"], "UNKNOWN")

    def test_module_never_mutates_mapping_or_starts_network(self):
        source = PATH.read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "opkg", "assign_exclusive", "save_sourcexml",
                          "socket", "subprocess", "http://", "https://", "write_text", "write_bytes"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
