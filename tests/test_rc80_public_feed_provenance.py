# -*- coding: utf-8 -*-
"""Read-only published XMLTV/audit cohort regression tests."""
from datetime import datetime, timedelta, timezone
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
        self.assertTrue(x["ready_for_epg_ok"])

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

    def test_no_install_and_no_secrets(self):
        s = PATH.read_text(encoding="utf-8")
        for forbidden in ("opkg install", "opkg upgrade", "os.system(", "subprocess.run(",
                          "/etc/enigma2", ".write_text(", ".write_bytes(", "rm -rf"):
            self.assertNotIn(forbidden, s)
        self.assertEqual(len(module.SOURCES), 14)
        self.assertFalse(module.assess("morocco", *fixtures(), NOW.timestamp()).get("install_authorized", False))


if __name__ == "__main__":
    unittest.main()
