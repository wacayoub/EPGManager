# -*- coding: utf-8 -*-
"""ElCinema RC80 incomplete 48h per-ID gate: no fake green mapping."""
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch
import unittest

MODULE=Path(__file__).resolve().parents[1]/"tools"/"audit_rc80_public_feed_provenance.py"
spec=spec_from_file_location("rc80_elcinema_partial_provenance",MODULE)
m=module_from_spec(spec)
spec.loader.exec_module(m)
NOW=datetime(2026,10,17,12,10,tzinfo=timezone.utc)
def date(t):return t.isoformat()
def inputs():
    t=date(NOW-timedelta(hours=1))
    feed={"generated_utc":t,"sha256":"a"*64,"size_bytes":206550,
          "channels":4,"active_channels":4,"currently_on_air_channels":4}
    rows=[
      {"id":"good1","status":"PASS","reason":""},
      {"id":"good2","status":"WARN","reason":"INTERNAL_GAP",
       "max_gap_minutes":60},
      {"id":"good3","status":"WARN","reason":"SHORT_HORIZON",
       "max_gap_minutes":0},
      {"id":"short1","status":"FAIL","reason":"TOO_SHORT+INTERNAL_GAP",
       "future_hours":35.0,"max_gap_minutes":360,"programmes":25},
    ]
    audit={"generated_utc":t,"feeds":[{"feed":"elcinema.xml.gz",
        "status":"FAIL","channels":4,"invalid_times":0,"orphan_programmes":0,
        "failed_channels":1,"warning_channels":2,"rows":rows}]}
    quality={"generated_utc":t,"source":"elcinema","source_file":"elcinema.xml.gz",
        "quality_status":"WARN","suspected_placeholder_channels":[]}
    result={"xmltv_sha256_verified":True,"xmltv_content_pass":True,
         "real_now_coverage_verified":True,
         "channel_specific_epg_evidence_verified":True,
         "xmltv_current_real_ids":["good1","good2","good3","short1"],
         "xmltv_future_real_ids":["good1","good2","good3","short1"],
         "xmltv_observed_zero_ids":[]}
    return feed,audit,quality,result

def observed(a,b,c,v):
    with patch.object(m,"_get_json",side_effect=[a,b,c]), \
         patch.object(m,"verify_payload",return_value=v):
        return m.fetch_one("elcinema",NOW.timestamp(),verify_xmltv=True)

class PartialElCinema(unittest.TestCase):
    def test_healthy_three_green_partial_one_yellow(self):
        a,b,c,v=inputs()
        x=observed(a,b,c,v)
        self.assertEqual(x["status"],"HASHED_XMLTV_PER_ID_PARTIAL_48H")
        self.assertEqual(x["trusted_current_epg_ids"],["good1","good2","good3"])
        self.assertEqual(x["review_partial_epg_ids"],["short1"])
        self.assertTrue(x["ready_for_epg_ok"])
        self.assertEqual(m.mapping_quality_for_id("short1",x),
            {"status":"PARTIAL_GUIDE_REVIEW","auto_eligible":False})
        self.assertTrue(m.mapping_quality_for_id("good2",x)["auto_eligible"])
    def test_metadata_only_must_never_certify_short_or_green(self):
        a,b,c,v=inputs()
        x=m.assess("elcinema",a,b,c,NOW.timestamp())
        self.assertEqual(x["status"],"48H_PARTIAL_IDS_NEED_XMLTV_PROOF")
        self.assertEqual(x["partial_48h_short_ids"],["short1"])
        self.assertFalse(x["ready_for_epg_ok"])
        self.assertEqual(x["trusted_current_epg_ids"],[])
    def test_suspect_candidate_excluded_even_if_warn_guide(self):
        a,b,c,v=inputs()
        c["suspected_placeholder_channels"]=[{"id":"good3","count":25}]
        x=observed(a,b,c,v)
        self.assertEqual(x["trusted_current_epg_ids"],["good1","good2"])
        self.assertEqual(m.mapping_quality_for_id("good3",x)["status"],"SUSPECT_GENERATED_EPG")
    def test_mismatched_source_zero_ids_invalidates_all_green(self):
        a,b,c,v=inputs()
        v["xmltv_observed_zero_ids"]=["short1"]
        x=observed(a,b,c,v)
        self.assertEqual(x["status"],"48H_PARTIAL_IDS_XMLTV_MISMATCH")
        self.assertEqual(x["trusted_current_epg_ids"],[])
        self.assertNotIn("review_partial_epg_ids",x)
    def test_short_report_without_real_future_programme_invalidates(self):
        a,b,c,v=inputs()
        v["xmltv_future_real_ids"].remove("short1")
        x=observed(a,b,c,v)
        self.assertEqual(x["trusted_current_epg_ids"],[])
        self.assertFalse(x["ready_for_epg_ok"])
    def test_only_known_reason_with_real_gap_allowed(self):
        for reason,gap in [("ZERO_EPG",360),("TOO_SHORT+INTERNAL_GAP",30),
                           ("TOO_SHORT",360),("INTERNAL_GAP",360)]:
            a,b,c,v=inputs()
            b["feeds"][0]["rows"][-1].update(reason=reason,max_gap_minutes=gap)
            with self.subTest(reason=reason,gap=gap):
                self.assertEqual(m.assess("elcinema",a,b,c,NOW.timestamp())["status"],
                                 "48H_AUDIT_FAIL")
    def test_incomplete_or_duplicate_rows_fail_closed(self):
        a,b,c,v=inputs()
        b["feeds"][0]["rows"][-1]=b["feeds"][0]["rows"][0].copy()
        self.assertEqual(m.assess("elcinema",a,b,c,NOW.timestamp())["status"],"48H_AUDIT_INCONSISTENT_IDS")
        a,b,c,v=inputs()
        b["feeds"][0]["rows"].pop()
        self.assertEqual(m.assess("elcinema",a,b,c,NOW.timestamp())["status"],"48H_AUDIT_INCONSISTENT_IDS")
    def test_stale_audit_or_published_data_never_green(self):
        a,b,c,v=inputs()
        a["generated_utc"]=date(NOW-timedelta(days=6))
        x=observed(a,b,c,v)
        self.assertFalse(x["ready_for_epg_ok"])
        self.assertEqual(x["trusted_current_epg_ids"],[])
        self.assertNotIn("review_partial_epg_ids",x)
    def test_xmltv_content_bad_never_auto(self):
        a,b,c,v=inputs()
        v["xmltv_content_pass"]=False
        x=observed(a,b,c,v)
        self.assertFalse(x["ready_for_epg_ok"])
        self.assertEqual(x["trusted_current_epg_ids"],[])
        self.assertNotIn("review_partial_epg_ids",x)
    def test_source_osn_cannot_reinterpret_short_failures(self):
        a,b,c,v=inputs()
        self.assertIsNone(m._safe_partial_48h_ids("osn",a,b["feeds"][0]))
    def test_without_current_short_event_not_yellow_green(self):
        a,b,c,v=inputs()
        v["xmltv_current_real_ids"].remove("short1")
        x=observed(a,b,c,v)
        self.assertNotIn("short1",x.get("review_partial_epg_ids",[]))
        self.assertFalse(m.mapping_quality_for_id("short1",x)["auto_eligible"])

if __name__=="__main__":
    unittest.main()
