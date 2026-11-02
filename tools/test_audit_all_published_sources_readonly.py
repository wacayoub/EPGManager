# -*- coding: utf-8 -*-
"""Offline regression of aggregate published-source health flags."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from tools.audit_all_published_sources_readonly import assess, run, SOURCES

NOW = datetime(2026, 10, 17, 11, 0, tzinfo=timezone.utc)
def iso(when):
    return when.isoformat()

class TestAllSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ("feeds", "reports"):
            (self.root / folder).mkdir()
        self.source = "morocco"
        self.written = b"verified-published-gzip-placeholder"
        self.m = {
            "label":"morocco","generated_utc":iso(NOW),
            "sha256":hashlib.sha256(self.written).hexdigest(),
            "size_bytes":len(self.written),
            "channels":15, "active_channels":15,"programmes":500,
        }
        self.a={"generated_utc":iso(NOW),"feeds":[{"feed":"morocco.xml.gz","status":"PASS",
            "failed_channels":0,"warning_channels":0}]}
        self.q={"source":"morocco","source_file":"morocco.xml.gz",
            "generated_utc":iso(NOW),"quality_status":"PASS",
            "channels_without_future_epg":[],"suspected_placeholder_channels":[],
            "future_programmes_without_description":0}
        self.save()

    def save(self):
        folder = self.root
        (folder/"feeds"/(self.source+".xml.gz")).write_bytes(self.written)
        for name,doc in (("feeds/"+self.source+".json",self.m),
                         ("reports/"+self.source+"-48h-audit.json",self.a),
                         ("reports/"+self.source+"-quality.json",self.q)):
            (folder/name).write_text(json.dumps(doc),encoding="utf-8")

    def audit(self):
        return assess(self.source, self.root, NOW)

    def test_healthy_cohort_only_means_no_red_flags(self):
        a=self.audit()
        self.assertEqual(a["release_health"],"NO_RED_FLAGS_IN_SUMMARIES")
        self.assertFalse(a["programme_provenance_certified"])
        self.assertFalse(a["receiver_cache_validated"])
        self.assertTrue(a["feed_sha256_matches"])

    def test_checksum_change_is_blocked(self):
        self.m["sha256"]="f"*64
        self.save()
        self.assertIn("GZIP_SHA_MISMATCH",self.audit()["issues"])
        self.assertEqual(self.audit()["release_health"],"BLOCKED")

    def test_stale_feed_is_blocked(self):
        self.m["generated_utc"]=iso(NOW-timedelta(days=12))
        self.a["generated_utc"]=self.m["generated_utc"]
        self.q["generated_utc"]=self.m["generated_utc"]
        self.save()
        self.assertIn("STALE_PUBLISHED_DATA",self.audit()["issues"])

    def test_new_report_on_old_feed_is_flagged(self):
        self.m["generated_utc"]=iso(NOW-timedelta(hours=22))
        self.save()
        self.assertIn("OUT_OF_COHORT_REPORT",self.audit()["issues"])

    def test_missing_channel_and_suspect_programmes_flagged_individually(self):
        self.q["channels_without_future_epg"]=["2M"]
        self.q["suspected_placeholder_channels"]=[{"id":"Chada TV","count":10}]
        self.save()
        a=self.audit()
        self.assertEqual(a["release_health"],"REVIEW")
        self.assertEqual(a["no_future_epg_ids"],1)
        self.assertEqual(a["suspect_placeholder_ids"],1)

    def donor_cohort(self, age_hours=22):
        self.source = 'elcinema'
        self.m.update(label='elcinema', generated_utc=iso(NOW-timedelta(hours=age_hours)),
                      repacked_utc=iso(NOW), original_published_sha256='a'*64)
        self.a['feeds'][0]['feed']='elcinema.xml.gz'
        self.q.update(source='elcinema', source_file='elcinema.xml.gz')
        self.proof = {'output_sha256':self.m['sha256'],
                      'verified_provider_sha256':{'elcinema':'a'*64},
                      'provider_generated_utc':self.m['generated_utc'],
                      'preserved_original_programmes':True,
                      'generated_or_guessed_programmes':0}
        self.save_proof()

    def save_proof(self):
        self.save()
        (self.root/'reports/elcinema-daily-donor.json').write_text(json.dumps(self.proof))

    def test_verified_donor_repack_uses_new_report_cohort_without_refreshing_provider_age(self):
        self.donor_cohort()
        row=self.audit()
        self.assertNotIn('OUT_OF_COHORT_REPORT', row['issues'])
        self.assertEqual(row['published_age_hours'],22)

    def test_donor_proof_for_different_bytes_does_not_hide_cohort_failure(self):
        self.donor_cohort()
        self.proof['output_sha256']='b'*64
        self.save_proof()
        self.assertIn('OUT_OF_COHORT_REPORT', self.audit()['issues'])

    def test_repack_does_not_hide_expired_provider_data(self):
        self.donor_cohort(age_hours=100)
        self.assertIn('STALE_PUBLISHED_DATA', self.audit()['issues'])

    def cleanup_cohort(self, age_hours=22):
        self.m['generated_utc']=iso(NOW-timedelta(hours=age_hours))
        marker={'input_sha256':'a'*64, 'output_sha256':self.m['sha256'],
                'preserved_generated_utc':self.m['generated_utc'], 'repacked_utc':iso(NOW)}
        self.m['quality_cleanup']=marker
        self.a['source_sha256']=self.q['source_sha256']=self.m['sha256']
        self.cleanup_proof=dict(marker,source=self.source,remaining_programmes=self.m['programmes'],fabricated_programmes=0)
        self.save_cleanup_proof()

    def save_cleanup_proof(self):
        self.save()
        (self.root/'reports'/(self.source+'-quality-cleanup.json')).write_text(json.dumps(self.cleanup_proof))

    def test_cleanup_cohort_preserves_real_provider_age(self):
        self.cleanup_cohort()
        row=self.audit()
        self.assertNotIn('OUT_OF_COHORT_REPORT',row['issues'])
        self.assertEqual(row['published_age_hours'],22)

    def test_unverified_cleanup_cannot_hide_wrong_report_cohort(self):
        self.cleanup_cohort()
        self.cleanup_proof['output_sha256']='b'*64
        self.save_cleanup_proof()
        self.assertIn('OUT_OF_COHORT_REPORT',self.audit()['issues'])

    def test_cleanup_does_not_make_expired_source_fresh(self):
        self.cleanup_cohort(age_hours=100)
        self.assertIn('STALE_PUBLISHED_DATA',self.audit()['issues'])

    def test_48h_source_fail_does_not_certify_all_ids(self):
        self.a["feeds"][0].update(status="FAIL",failed_channels=2)
        self.save()
        self.assertEqual(self.audit()["48h_failed_ids"],2)
        self.assertFalse(self.audit()["programme_provenance_certified"])

    def test_complete_stc_catalogue_is_not_misreported_as_three_sports_only(self):
        self.source="stctv"
        self.m["label"]=self.source
        self.m["channels"]=149
        self.m["active_channels"]=149
        self.a["feeds"][0]["feed"]="stctv.xml.gz"
        self.q["source"]=self.source
        self.q["source_file"]="stctv.xml.gz"
        self.save()
        a=self.audit()
        self.assertNotIn("UNEXPECTED_SOURCE_SCOPE",a["issues"])
        self.assertEqual(a["channels"],149)

    def test_absent_sources_are_not_green(self):
        a=run(self.root,NOW)
        self.assertEqual(a["source_count"],len(SOURCES))
        self.assertGreaterEqual(a["source_release_blocked_count"],13)

if __name__ == "__main__":
    unittest.main()
