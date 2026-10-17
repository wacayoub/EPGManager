# -*- coding: utf-8 -*-
"""After ElCinema midnight fix: 35 TOO_SHORT guides are real, but yellow."""
import unittest
from datetime import datetime,timedelta,timezone
from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1]/"tools"/"audit_rc80_public_feed_provenance.py"
spec=spec_from_file_location("rc80_elcinema_short_only_module",P)
m=module_from_spec(spec)
spec.loader.exec_module(m)
NOW=datetime(2026,10,17,13,28,tzinfo=timezone.utc)

def data():
    at=(NOW-timedelta(hours=1)).isoformat()
    feed={"label":"elcinema","channels":4,"active_channels":4,
          "currently_on_air_channels":4,"sha256":"a"*64,
          "size_bytes":85000,"generated_utc":at}
    rows=[
        {"id":"good1","status":"PASS","reason":"","future_hours":48,"max_gap_minutes":0},
        {"id":"good2","status":"WARN","reason":"SHORT_HORIZON","future_hours":42,"max_gap_minutes":0},
        {"id":"short","status":"FAIL","reason":"TOO_SHORT","future_hours":27.64,"max_gap_minutes":0},
        {"id":"good3","status":"PASS","reason":"","future_hours":48,"max_gap_minutes":0},
    ]
    audit={"generated_utc":at,"feeds":[{"feed":"elcinema.xml.gz",
           "status":"FAIL","channels":4,"failed_channels":1,
           "warning_channels":1,"invalid_times":0,"orphan_programmes":0,"rows":rows}]}
    quality={"source":"elcinema","source_file":"elcinema.xml.gz","generated_utc":at,
             "quality_status":"WARN","suspected_placeholder_channels":[]}
    verified={"xmltv_sha256_verified":True,"channel_specific_epg_evidence_verified":True,
              "real_now_coverage_verified":True,"xmltv_content_pass":True,
              "xmltv_current_real_ids":["good1","good2","good3","short"],
              "xmltv_future_real_ids":["good1","good2","good3","short"],
              "xmltv_observed_zero_ids":[]}
    return feed,audit,quality,verified

def online(v):
    f,a,q,data=v
    with patch.object(m,"_get_json",side_effect=[f,a,q]),patch.object(m,"verify_payload",return_value=data):
        return m.fetch_one("elcinema",NOW.timestamp(),verify_xmltv=True)

class ShortOnly(unittest.TestCase):
    def test_verified_short_only_is_yellow(self):
        x=online(data())
        self.assertEqual(x["status"],"HASHED_XMLTV_PER_ID_PARTIAL_48H")
        self.assertEqual(x["trusted_current_epg_ids"],["good1","good2","good3"])
        self.assertEqual(x["review_partial_epg_ids"],["short"])
        self.assertFalse(m.mapping_quality_for_id("short",x)["auto_eligible"])
    def test_metadata_only_not_a_green_pass(self):
        f,a,q,_=data()
        x=m.assess("elcinema",f,a,q,NOW.timestamp())
        self.assertEqual(x["status"],"48H_PARTIAL_IDS_NEED_XMLTV_PROOF")
        self.assertFalse(x["ready_for_epg_ok"])
    def test_short_with_severe_gap_requires_combined_reason(self):
        f,a,q,_=data()
        a["feeds"][0]["rows"][2]["max_gap_minutes"]=600
        self.assertEqual(m.assess("elcinema",f,a,q,NOW.timestamp())["status"],"48H_AUDIT_FAIL")
        a["feeds"][0]["rows"][2]["reason"]="TOO_SHORT+INTERNAL_GAP"
        self.assertEqual(m.assess("elcinema",f,a,q,NOW.timestamp())["status"],"48H_PARTIAL_IDS_NEED_XMLTV_PROOF")
    def test_36_hours_is_not_a_failure_in_elcinema_profile(self):
        f,a,q,_=data()
        a["feeds"][0]["rows"][2]["future_hours"]=36.0
        self.assertEqual(m.assess("elcinema",f,a,q,NOW.timestamp())["status"],"48H_AUDIT_FAIL")
    def test_zero_future_never_accepted_as_too_short(self):
        f,a,q,_=data()
        a["feeds"][0]["rows"][2]["future_hours"]=0
        self.assertEqual(m.assess("elcinema",f,a,q,NOW.timestamp())["status"],"48H_AUDIT_FAIL")
    def test_only_missing_expected_future_id_causes_quarantine(self):
        f,a,q,v=data()
        v["xmltv_future_real_ids"].remove("short")
        x=online((f,a,q,v))
        self.assertFalse(x["ready_for_epg_ok"])
        self.assertEqual(x["status"],"48H_PARTIAL_IDS_XMLTV_MISMATCH")
    def test_other_unexpected_reason_fail_closed(self):
        f,a,q,_=data()
        a["feeds"][0]["rows"][2]["reason"]="INTERNAL_GAP"
        self.assertEqual(m.assess("elcinema",f,a,q,NOW.timestamp())["status"],"48H_AUDIT_FAIL")
    def test_osn_is_not_given_short_exception(self):
        f,a,q,_=data()
        self.assertIsNone(m._safe_partial_48h_ids("osn",f,a["feeds"][0]))
    def test_source_staleness_blocks_current_programmes(self):
        f,a,q,v=data()
        f["generated_utc"]=(NOW-timedelta(days=5)).isoformat()
        self.assertFalse(online((f,a,q,v))["ready_for_epg_ok"])
    def test_suspected_placeholder_never_auto(self):
        f,a,q,v=data()
        q["suspected_placeholder_channels"]=[{"id":"good2","count":22}]
        x=online((f,a,q,v))
        self.assertNotIn("good2",x["trusted_current_epg_ids"])
        self.assertNotIn("short",x["trusted_current_epg_ids"])

if __name__=="__main__":
    unittest.main()
