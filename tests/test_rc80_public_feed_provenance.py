# -*- coding: utf-8 -*-
"""Read-only published XMLTV/audit cohort regression tests."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
from unittest.mock import patch
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "tools/audit_rc80_public_feed_provenance.py"
spec = spec_from_file_location("feed_provenance_gate", PATH)
module = module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 10, 14, 22, 45, tzinfo=timezone.utc)
ts = lambda dt: dt.isoformat()


def fixtures(source="morocco"):
    gen = ts(NOW - timedelta(hours=2))
    result = (
        {"generated_utc": gen, "sha256": "a"*64, "size_bytes": 10000,
         "currently_on_air_channels": 14, "active_channels": 14},
        {"generated_utc": gen, "feeds": [{"feed": source + ".xml.gz", "status": "WARN"}]},
        {"generated_utc": gen, "source": source, "source_file": source + ".xml.gz",
         "quality_status": "PASS", "suspected_placeholder_channels": []},
    )
    # ElCinema direct-source publication is eligible only with full per-ID
    # audit rows. Generic empty WARN metadata must never be considered green.
    if source == "elcinema":
        meta, report, _ = result
        meta.update(channels=2, active_channels=2, currently_on_air_channels=2)
        report["feeds"][0].update(
            status="WARN", channels=2, failed_channels=0,
            warning_channels=2, invalid_times=0, orphan_programmes=0,
            rows=[
                {"id": "BahrainTV.bh@SD", "status": "WARN",
                 "reason": "SHORT_HORIZON", "max_gap_minutes": 0},
                {"id": "RealChannel.eg@SD", "status": "WARN",
                 "reason": "SHORT_HORIZON", "max_gap_minutes": 0},
            ])
    return result


class TestPublicFeedProvenance(unittest.TestCase):
    def test_coherent_published_source(self):
        a, b, c = fixtures()
        x = module.assess("morocco", a, b, c, NOW.timestamp())
        self.assertEqual(x["status"], "PUBLICATION_METADATA_COHERENT")
        self.assertFalse(x["ready_for_epg_ok"])  # Metadata is not evidence of live programme.
        self.assertFalse(x["real_now_coverage_verified"])
        self.assertFalse(x["xmltv_sha256_verified"])

    def test_expired_tabie_feed_is_not_repaired_by_new_report(self):
        a, b, c = fixtures("tabie")
        a["generated_utc"] = ts(NOW - timedelta(days=9))
        r = module.assess("tabie", a, b, c, NOW.timestamp())
        self.assertEqual(r["status"], "STALE_OR_INVALID_PUBLISHED_FEED")
        self.assertFalse(r["pubcohort_verified"])

    def test_new_candidate_audit_of_old_lkg_rejected(self):
        a, b, c = fixtures("tabie")
        a["generated_utc"] = ts(NOW - timedelta(hours=20))
        r = module.assess("tabie", a, b, c, NOW.timestamp())
        self.assertEqual(r["status"], "AUDIT_FROM_DIFFERENT_PUBLICATION")

    def test_quality_from_unpublished_candidate_not_valid(self):
        a, b, c = fixtures()
        c["generated_utc"] = ts(NOW - timedelta(days=2))
        r = module.assess("morocco", a, b, c, NOW.timestamp())
        self.assertEqual(r["status"], "QUALITY_FROM_DIFFERENT_PUBLICATION")

    def test_absent_current_coverage_not_claimed(self):
        a, b, c = fixtures("elcinema")
        a.pop("currently_on_air_channels")
        r = module.assess("elcinema", a, b, c, NOW.timestamp())
        self.assertEqual(r["status"], "PUBLICATION_COHORT_OK_LIVE_NOW_UNKNOWN")
        self.assertFalse(r["real_now_coverage_verified"])
        self.assertFalse(r["ready_for_epg_ok"])

    def test_real_current_coverage_below_threshold(self):
        a, b, c = fixtures()
        a["currently_on_air_channels"] = 3
        r = module.assess("morocco", a, b, c, NOW.timestamp())
        self.assertEqual(r["status"], "PUBLISHED_NOW_COVERAGE_LOW")

    def test_invalid_sha_or_size_rejected(self):
        a, b, c = fixtures()
        a["sha256"] = "not-hash"
        self.assertEqual(module.assess("morocco", a, b, c, NOW.timestamp())["status"],
                         "INVALID_PUBLISHED_METADATA")
        a["sha256"] = "a"*64
        a["size_bytes"] = -100
        self.assertEqual(module.assess("morocco", a, b, c, NOW.timestamp())["status"],
                         "INVALID_PUBLISHED_METADATA")

    def test_failed_actual_48h_audit_not_mislabeled(self):
        a, b, c = fixtures()
        b["feeds"][0]["status"] = "FAIL"
        self.assertEqual(module.assess("morocco", a, b, c, NOW.timestamp())["status"],
                         "48H_AUDIT_FAIL")

    def test_invalid_source_and_future_publish_date_fail_closed(self):
        a, b, c = fixtures()
        c["source"] = "another"
        self.assertEqual(module.assess("morocco", a, b, c, NOW.timestamp())["status"],
                         "QUALITY_FROM_DIFFERENT_PUBLICATION")
        a["generated_utc"] = ts(NOW + timedelta(days=2))
        self.assertEqual(module.assess("morocco", a, b, c, NOW.timestamp())["status"],
                         "STALE_OR_INVALID_PUBLISHED_FEED")

    def test_verified_xmltv_sha_and_now_programmes(self):
        a, b, c = fixtures()
        start = (NOW - timedelta(minutes=30)).strftime("%Y%m%d%H%M%S +0000")
        stop = (NOW + timedelta(minutes=30)).strftime("%Y%m%d%H%M%S +0000")
        xml = ('<tv><channel id="c"><display-name>C</display-name></channel>'
               '<programme channel="c" start="%s" stop="%s">'
               '<title>Real programme</title></programme></tv>'
               % (start, stop)).encode()
        raw = gzip.compress(xml, mtime=0)
        a.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw),
                 channels=1, active_channels=1, programmes=1)
        valid = module.verify_payload("morocco", a, NOW.timestamp(),
                                      reader=lambda _u, _limit: raw)
        self.assertTrue(valid["xmltv_sha256_verified"])
        self.assertTrue(valid["xmltv_content_pass"])
        self.assertEqual(valid["xmltv_live_now_channels"], 1)
        a["sha256"] = "0"*64
        with self.assertRaises(ValueError):
            module.verify_payload("morocco", a, NOW.timestamp(),
                                  reader=lambda _u, _limit: raw)

    def test_only_hashed_live_xmltv_can_promote(self):
        a, b, c = fixtures()
        verified = {"xmltv_sha256_verified": True, "xmltv_content_pass": True,
                    "real_now_coverage_verified": True, "xmltv_issues": [],
                    "channel_specific_epg_evidence_verified": True,
                    "xmltv_current_real_ids": ["id.morocco.real"],
                    "xmltv_future_real_ids": ["id.morocco.real", "id.morocco.future"]}
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            actual = module.fetch_one("morocco", NOW.timestamp(), verify_xmltv=True)
        self.assertTrue(actual["ready_for_epg_ok"])
        self.assertEqual(actual["status"], "HASHED_XMLTV_AND_AUDITS_HEALTHY")
        b["feeds"][0]["status"] = "FAIL"
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            invalid = module.fetch_one("morocco", NOW.timestamp(), verify_xmltv=True)
        self.assertFalse(invalid["ready_for_epg_ok"])

    def test_hash_failure_is_nonpromotable(self):
        a, b, c = fixtures()
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", side_effect=ValueError("bad sha")):
            result = module.fetch_one("morocco", NOW.timestamp(), verify_xmltv=True)
        self.assertFalse(result["ready_for_epg_ok"])
        self.assertFalse(result["xmltv_sha256_verified"])
        self.assertFalse(result["real_now_coverage_verified"])
        self.assertEqual(result["status"], "XMLTV_DOWNLOAD_OR_INTEGRITY_UNVERIFIED")

    def test_json_only_mode_no_live_evidence(self):
        a, b, c = fixtures()
        with patch.object(module, "_get_json", side_effect=[a, b, c]):
            result = module.fetch_one("morocco", NOW.timestamp())
        self.assertFalse(result["ready_for_epg_ok"])
        self.assertFalse(result["real_now_coverage_verified"])

    def test_stale_published_xmltv_remains_primary_even_after_hash_verified(self):
        a, b, c = fixtures("tabie")
        a["generated_utc"] = ts(NOW - timedelta(days=9))
        audited = {
            "xmltv_sha256_verified": True, "xmltv_content_pass": False,
            "xmltv_issues": ["insufficient_current_time_coverage"],
            "real_now_coverage_verified": True,
        }
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=audited):
            res = module.fetch_one("tabie", NOW.timestamp(), verify_xmltv=True)
        self.assertEqual(res["status"], "STALE_OR_INVALID_PUBLISHED_FEED")
        self.assertEqual(res["xmltv_content_status"], "XMLTV_CONTENT_QUALITY_ISSUES")
        self.assertFalse(res["ready_for_epg_ok"])

    def test_48h_fail_remains_primary_even_if_xmltv_has_missing_titles(self):
        a, b, c = fixtures("elcinema")
        b["feeds"][0]["status"] = "FAIL"
        audited = {
            "xmltv_sha256_verified": True, "xmltv_content_pass": False,
            "xmltv_issues": ["empty_programme_titles"],
            "real_now_coverage_verified": True,
        }
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=audited):
            res = module.fetch_one("elcinema", NOW.timestamp(), verify_xmltv=True)
        # The mock FAIL report has no complete per-ID rows. Reject it
        # specifically as structurally inconsistent before XML title scoring.
        self.assertEqual(res["status"], "48H_AUDIT_INCONSISTENT_IDS")
        self.assertFalse(res["ready_for_epg_ok"])

    def test_verified_title_cleanup_keeps_original_feed_age(self):
        feed, audit, quality = fixtures("shahid")
        # This test is about immutable original age; a source-wide WARN now
        # requires complete per-ID rows to protect AWSN/other severe gaps.
        audit["feeds"][0]["status"] = "PASS"
        feed["repacked_utc"] = ts(NOW - timedelta(minutes=10))
        feed["original_published_sha256"] = "b" * 64
        audit["generated_utc"] = feed["repacked_utc"]
        quality["generated_utc"] = feed["repacked_utc"]
        result = module.assess("shahid", feed, audit, quality, NOW.timestamp())
        self.assertEqual(result["status"], "PUBLICATION_METADATA_COHERENT")
        self.assertTrue(result["pubcohort_verified"])
        self.assertTrue(result["quality_cohort_verified"])
        self.assertTrue(result["provider_data_reused_after_title_cleanup"])
        self.assertFalse(result["ready_for_epg_ok"])

    def test_repacking_cannot_launder_old_provider_data(self):
        feed, audit, quality = fixtures("shahid")
        feed["generated_utc"] = ts(NOW - timedelta(days=5))
        feed["repacked_utc"] = ts(NOW - timedelta(minutes=10))
        feed["original_published_sha256"] = "b" * 64
        result = module.assess("shahid", feed, audit, quality, NOW.timestamp())
        self.assertEqual(result["status"], "STALE_OR_INVALID_PUBLISHED_FEED")

    def test_invalid_cleanup_provenance_is_never_promoted(self):
        feed, audit, quality = fixtures("shahid")
        feed["repacked_utc"] = ts(NOW - timedelta(minutes=10))
        feed["original_published_sha256"] = feed["sha256"]
        result = module.assess("shahid", feed, audit, quality, NOW.timestamp())
        self.assertEqual(result["status"], "INVALID_REPACK_PROVENANCE")
        feed["original_published_sha256"] = "invalid"
        result = module.assess("shahid", feed, audit, quality, NOW.timestamp())
        self.assertEqual(result["status"], "INVALID_REPACK_PROVENANCE")
        feed["original_published_sha256"] = "b" * 64
        feed["repacked_utc"] = ts(NOW - timedelta(hours=3))
        result = module.assess("shahid", feed, audit, quality, NOW.timestamp())
        self.assertEqual(result["status"], "INVALID_REPACK_PROVENANCE")


    def test_verified_current_id_is_auto_eligible_but_future_is_review_only(self):
        a, b, c = fixtures()
        verified = {"xmltv_sha256_verified": True, "xmltv_content_pass": True,
                    "real_now_coverage_verified": True, "xmltv_issues": [],
                    "channel_specific_epg_evidence_verified": True,
                    "xmltv_current_real_ids": ["id.real"],
                    "xmltv_future_real_ids": ["id.real", "id.later"],
                    "xmltv_observed_zero_ids": ["id.empty"]}
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            result = module.fetch_one("morocco", NOW.timestamp(), verify_xmltv=True)
        self.assertEqual(result["trusted_current_epg_ids"], ["id.real"])
        self.assertEqual(result["review_future_epg_ids"], ["id.later"])
        self.assertTrue(result["ready_for_epg_ok"])
        self.assertEqual(module.mapping_quality_for_id("id.real", result),
                         {"status": "REAL_CURRENT_EPG", "auto_eligible": True})
        self.assertEqual(module.mapping_quality_for_id("id.later", result),
                         {"status": "FUTURE_ONLY_REVIEW", "auto_eligible": False})
        self.assertEqual(module.mapping_quality_for_id("id.empty", result),
                         {"status": "NO_EPG", "auto_eligible": False})

    def test_stale_but_parseable_feed_cannot_autolock_its_current_events(self):
        a, b, c = fixtures("tabie")
        a["generated_utc"] = ts(NOW - timedelta(days=9))
        verified = {"xmltv_sha256_verified": True, "xmltv_content_pass": True,
                    "real_now_coverage_verified": True, "xmltv_issues": [],
                    "channel_specific_epg_evidence_verified": True,
                    "xmltv_current_real_ids": ["id.stale"],
                    "xmltv_future_real_ids": ["id.stale"]}
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            result = module.fetch_one("tabie", NOW.timestamp(), verify_xmltv=True)
        self.assertEqual(result["status"], "STALE_OR_INVALID_PUBLISHED_FEED")
        self.assertFalse(result["ready_for_epg_ok"])
        self.assertEqual(result["trusted_current_epg_ids"], [])
        self.assertEqual(result["review_future_epg_ids"], [])
        self.assertEqual(module.mapping_quality_for_id("id.stale", result),
                         {"status": "CURRENT_UNVERIFIED", "auto_eligible": False})

    def test_unverified_or_missing_per_id_evidence_cannot_autolock(self):
        a, b, c = fixtures()
        verified = {"xmltv_sha256_verified": True, "xmltv_content_pass": True,
                    "real_now_coverage_verified": True, "xmltv_issues": [],
                    "xmltv_current_real_ids": ["id.unverified"]}
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            result = module.fetch_one("morocco", NOW.timestamp(), verify_xmltv=True)
        self.assertFalse(result["ready_for_epg_ok"])
        self.assertEqual(result["trusted_current_epg_ids"], [])
        self.assertEqual(module.mapping_quality_for_id("id.unverified", result),
                         {"status": "CURRENT_UNVERIFIED", "auto_eligible": False})
        self.assertEqual(module.mapping_quality_for_id("", result),
                         {"status": "UNKNOWN", "auto_eligible": False})

    def test_failure_in_another_channel_keeps_future_as_review_not_auto(self):
        a, b, c = fixtures()
        verified = {"xmltv_sha256_verified": True, "xmltv_content_pass": False,
                    "real_now_coverage_verified": True, "xmltv_issues": ["empty_programme_titles"],
                    "channel_specific_epg_evidence_verified": True,
                    "xmltv_current_real_ids": ["id.good"],
                    "xmltv_future_real_ids": ["id.good", "id.later"]}
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            result = module.fetch_one("morocco", NOW.timestamp(), verify_xmltv=True)
        self.assertFalse(result["ready_for_epg_ok"])
        self.assertEqual(result["trusted_current_epg_ids"], [])
        self.assertEqual(result["review_future_epg_ids"], ["id.later"])
        self.assertEqual(module.mapping_quality_for_id("id.good", result)["status"],
                         "CURRENT_UNVERIFIED")
        self.assertFalse(module.mapping_quality_for_id("id.good", result)["auto_eligible"])


    def test_suspected_generated_placeholder_never_becomes_real_epg(self):
        a, b, c = fixtures("elcinema")
        c["suspected_placeholder_channels"] = [{"id": "BahrainTV.bh@SD", "count": 18}]
        verified = {"xmltv_sha256_verified": True, "xmltv_content_pass": True,
                    "real_now_coverage_verified": True, "xmltv_issues": [],
                    "channel_specific_epg_evidence_verified": True,
                    "xmltv_current_real_ids": ["BahrainTV.bh@SD", "RealChannel.eg@SD"],
                    "xmltv_future_real_ids": ["BahrainTV.bh@SD", "RealChannel.eg@SD"]}
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            result = module.fetch_one("elcinema", NOW.timestamp(), verify_xmltv=True)
        self.assertEqual(result["trusted_current_epg_ids"], ["RealChannel.eg@SD"])
        self.assertEqual(result["suspected_placeholder_epg_ids"], ["BahrainTV.bh@SD"])
        self.assertEqual(module.mapping_quality_for_id("BahrainTV.bh@SD", result),
                         {"status": "SUSPECT_GENERATED_EPG", "auto_eligible": False})
        self.assertTrue(module.mapping_quality_for_id("RealChannel.eg@SD", result)["auto_eligible"])

    def test_missing_or_malformed_placeholder_quality_report_fails_closed(self):
        a, b, c = fixtures("morocco")
        verified = {"xmltv_sha256_verified": True, "xmltv_content_pass": True,
                    "real_now_coverage_verified": True, "xmltv_issues": [],
                    "channel_specific_epg_evidence_verified": True,
                    "xmltv_current_real_ids": ["id.real"],
                    "xmltv_future_real_ids": ["id.real"]}
        for bad in (None, "not-a-list", [{"count": 5}]):
            report = dict(c)
            if bad is None:
                report.pop("suspected_placeholder_channels", None)
            else:
                report["suspected_placeholder_channels"] = bad
            with self.subTest(report=bad), \
                    patch.object(module, "_get_json", side_effect=[a, b, report]), \
                    patch.object(module, "verify_payload", return_value=verified):
                result = module.fetch_one("morocco", NOW.timestamp(), verify_xmltv=True)
            self.assertFalse(result["ready_for_epg_ok"])
            self.assertEqual(result["trusted_current_epg_ids"], [])

    def _partial_osn(self):
        a, b, c = fixtures("osn")
        a.update(channels=4, active_channels=3, currently_on_air_channels=2)
        b["feeds"][0].update(
            status="FAIL", channels=4, failed_channels=1,
            invalid_times=0, orphan_programmes=0,
            rows=[
                {"id": "id.good", "status": "PASS"},
                {"id": "id.also_good", "status": "PASS"},
                {"id": "id.later", "status": "WARN"},
                {"id": "id.empty", "status": "FAIL", "reason": "NO_FUTURE_EPG"},
            ])
        verified = {
            "xmltv_sha256_verified": True, "xmltv_content_pass": True,
            "real_now_coverage_verified": True,
            "channel_specific_epg_evidence_verified": True,
            "xmltv_current_real_ids": ["id.good", "id.also_good"],
            "xmltv_future_real_ids": ["id.good", "id.also_good", "id.later"],
            "xmltv_observed_zero_ids": ["id.empty"],
        }
        return a, b, c, verified

    def test_osn_partial_48h_19_empty_ids_do_not_block_healthy_siblings(self):
        a, b, c, verified = self._partial_osn()
        pre = module.assess("osn", a, b, c, NOW.timestamp())
        self.assertEqual(pre["status"], "48H_PARTIAL_ZERO_IDS_NEED_XMLTV_PROOF")
        self.assertFalse(pre["ready_for_epg_ok"])
        self.assertEqual(pre["partial_48h_zero_ids"], ["id.empty"])
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            out = module.fetch_one("osn", NOW.timestamp(), verify_xmltv=True)
        self.assertEqual(out["status"], "HASHED_XMLTV_PER_ID_PARTIAL_48H")
        self.assertEqual(out["trusted_current_epg_ids"], ["id.also_good", "id.good"])
        self.assertEqual(out["review_future_epg_ids"], ["id.later"])
        self.assertTrue(out["ready_for_epg_ok"])
        self.assertEqual(module.mapping_quality_for_id("id.empty", out),
                         {"status": "NO_EPG", "auto_eligible": False})
        self.assertTrue(module.mapping_quality_for_id("id.good", out)["auto_eligible"])
        self.assertFalse(module.mapping_quality_for_id("id.later", out)["auto_eligible"])

    def test_osn_partial_not_allowed_on_metadata_only(self):
        a, b, c, _ = self._partial_osn()
        with patch.object(module, "_get_json", side_effect=[a, b, c]):
            out = module.fetch_one("osn", NOW.timestamp(), verify_xmltv=False)
        self.assertFalse(out["ready_for_epg_ok"])
        self.assertEqual(out["trusted_current_epg_ids"], [])

    def test_osn_partial_wrong_reason_fails_full_source_closed(self):
        a, b, c, _ = self._partial_osn()
        b["feeds"][0]["rows"][-1]["reason"] = "TOO_SHORT+INTERNAL_GAP"
        out = module.assess("osn", a, b, c, NOW.timestamp())
        self.assertEqual(out["status"], "48H_AUDIT_FAIL")
        self.assertEqual(out["trusted_current_epg_ids"], [])

    def test_osn_partial_missing_or_duplicate_rows_fail_closed(self):
        a, b, c, _ = self._partial_osn()
        for bad in (
            b["feeds"][0]["rows"][:3],
            [*b["feeds"][0]["rows"][:3], b["feeds"][0]["rows"][1]],
        ):
            with self.subTest(bad=bad):
                b["feeds"][0]["rows"] = bad
                out = module.assess("osn", a, b, c, NOW.timestamp())
                self.assertEqual(out["status"], "48H_AUDIT_FAIL")

    def test_osn_partial_bad_or_orphan_programmes_fail_closed(self):
        a, b, c, _ = self._partial_osn()
        for issue in ("invalid_times", "orphan_programmes"):
            with self.subTest(issue=issue):
                b["feeds"][0][issue] = 1
                self.assertEqual(module.assess("osn", a, b, c, NOW.timestamp())["status"],
                                 "48H_AUDIT_FAIL")
                b["feeds"][0][issue] = 0

    def test_osn_partial_xmltv_zero_ids_must_equal_report(self):
        a, b, c, verified = self._partial_osn()
        for mismatch in ([], ["id.unrelated"], ["id.empty", "id.missing"]):
            with self.subTest(mismatch=mismatch):
                wrong = dict(verified, xmltv_observed_zero_ids=mismatch)
                with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                        patch.object(module, "verify_payload", return_value=wrong):
                    out = module.fetch_one("osn", NOW.timestamp(), verify_xmltv=True)
                self.assertEqual(out["status"], "48H_PARTIAL_ZERO_IDS_XMLTV_MISMATCH")
                self.assertFalse(out["ready_for_epg_ok"])
                self.assertEqual(out["trusted_current_epg_ids"], [])

    def test_osn_partial_stale_metadata_never_autolocks(self):
        a, b, c, verified = self._partial_osn()
        a["generated_utc"] = ts(NOW - timedelta(days=9))
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            out = module.fetch_one("osn", NOW.timestamp(), verify_xmltv=True)
        self.assertFalse(out["ready_for_epg_ok"])
        self.assertEqual(out["status"], "STALE_OR_INVALID_PUBLISHED_FEED")

    def test_osn_partial_bad_quality_report_never_autolocks(self):
        a, b, c, verified = self._partial_osn()
        c["suspected_placeholder_channels"] = [{"count": 7}]
        with patch.object(module, "_get_json", side_effect=[a, b, c]), \
                patch.object(module, "verify_payload", return_value=verified):
            out = module.fetch_one("osn", NOW.timestamp(), verify_xmltv=True)
        self.assertFalse(out["ready_for_epg_ok"])
        self.assertEqual(out["trusted_current_epg_ids"], [])

    def test_no_install_and_no_secrets(self):
        s = PATH.read_text(encoding="utf-8")
        for forbidden in ("opkg install", "opkg upgrade", "os.system(", "subprocess.run(",
                          "/etc/enigma2", ".write_text(", ".write_bytes(", "rm -rf"):
            self.assertNotIn(forbidden, s)
        self.assertEqual(len(module.SOURCES), 14)
        self.assertFalse(module.assess("morocco", *fixtures(), NOW.timestamp()).get("install_authorized", False))


if __name__ == "__main__":
    unittest.main()
