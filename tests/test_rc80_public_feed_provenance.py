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
    return (
        {"generated_utc": gen, "sha256": "a"*64, "size_bytes": 10000,
         "currently_on_air_channels": 14, "active_channels": 14},
        {"generated_utc": gen, "feeds": [{"feed": source + ".xml.gz", "status": "WARN"}]},
        {"generated_utc": gen, "source": source, "source_file": source + ".xml.gz",
         "quality_status": "PASS"},
    )


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
                    "real_now_coverage_verified": True, "xmltv_issues": []}
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
        self.assertEqual(res["status"], "48H_AUDIT_FAIL")
        self.assertFalse(res["ready_for_epg_ok"])

    def test_verified_title_cleanup_keeps_original_feed_age(self):
        feed, audit, quality = fixtures("shahid")
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

    def test_no_install_and_no_secrets(self):
        s = PATH.read_text(encoding="utf-8")
        for forbidden in ("opkg install", "opkg upgrade", "os.system(", "subprocess.run(",
                          "/etc/enigma2", ".write_text(", ".write_bytes(", "rm -rf"):
            self.assertNotIn(forbidden, s)
        self.assertEqual(len(module.SOURCES), 14)
        self.assertFalse(module.assess("morocco", *fixtures(), NOW.timestamp()).get("install_authorized", False))


if __name__ == "__main__":
    unittest.main()
