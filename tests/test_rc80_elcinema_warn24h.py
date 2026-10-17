# -*- coding: utf-8 -*-
"""Severe ElCinema WARN 1440-minute gaps are yellow not healthy Best EPG."""
import sys
from pathlib import Path
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"source"/"overlay"))
from core import verified_epg_refresh as receiver
from core import verified_epg_match as match
from tools import audit_rc80_public_feed_provenance as standalone

NOW=datetime(2026,10,17,13,0,tzinfo=timezone.utc)
def fixture():
    stamped=(NOW-timedelta(hours=1)).isoformat()
    feed={"label":"elcinema","generated_utc":stamped,"channels":4,
          "active_channels":4,"currently_on_air_channels":4,
          "sha256":"1"*64,"size_bytes":8304}
    rows=[
      {"id":"a.good","status":"PASS","reason":"",
       "future_hours":48,"max_gap_minutes":0},
      {"id":"b.gap","status":"WARN","reason":"INTERNAL_GAP",
       "future_hours":48,"max_gap_minutes":1440},
      {"id":"c.warn","status":"WARN","reason":"INTERNAL_GAP",
       "future_hours":48,"max_gap_minutes":180},
      {"id":"d.good","status":"PASS","reason":"",
       "future_hours":48,"max_gap_minutes":0},
    ]
    audit={"generated_utc":stamped,"feeds":[{
        "feed":"elcinema.xml.gz","channels":4,"status":"WARN",
        "invalid_times":0,"orphan_programmes":0,"failed_channels":0,
        "warning_channels":2,"rows":rows}]}
    quality={"generated_utc":stamped,"source":"elcinema","source_file":"elcinema.xml.gz",
             "quality_status":"WARN","suspected_placeholder_channels":[]}
    verified={"xmltv_sha256_verified":True,"xmltv_content_pass":True,
       "channel_specific_epg_evidence_verified":True,"real_now_coverage_verified":True,
       "xmltv_current_real_ids":["a.good","b.gap","c.warn","d.good"],
       "xmltv_future_real_ids":["a.good","b.gap","c.warn","d.good"],
       "xmltv_observed_zero_ids":[]}
    return feed,audit,quality,verified

def observed(f,a,q,v):
    with patch.object(standalone,"_get_json",side_effect=[f,a,q]), \
         patch.object(standalone,"verify_payload",return_value=v):
        return standalone.fetch_one("elcinema",NOW.timestamp(),verify_xmltv=True)

class WarnGapTests(unittest.TestCase):
    def test_severe_warn_detected_while_other_warn_remains_eligible(self):
        f,a,q,v=fixture()
        out=observed(f,a,q,v)
        self.assertEqual(out["severe_warn_gap_ids"],["b.gap"])
        self.assertEqual(out["trusted_current_epg_ids"],["a.good","c.warn","d.good"])
        self.assertEqual(out["review_partial_epg_ids"],["b.gap"])
        self.assertEqual(out["status"],"HASHED_XMLTV_PER_ID_PARTIAL_48H")
        self.assertEqual(standalone.mapping_quality_for_id("b.gap",out),
                         {"status":"PARTIAL_GUIDE_REVIEW","auto_eligible":False})
    def test_source_level_warn_is_not_an_automatic_green_approval(self):
        f,a,q,v=fixture()
        result=standalone.assess("elcinema",f,a,q,NOW.timestamp())
        self.assertEqual(result["status"],"48H_PARTIAL_IDS_NEED_XMLTV_PROOF")
        self.assertFalse(result["ready_for_epg_ok"])
    def test_runtime_gate_blocks_only_severe_gap(self):
        f,a,q,v=fixture()
        blocked=receiver._blocked_48h_ids("elcinema",a,{"a.good","b.gap","c.warn","d.good"})
        self.assertEqual(blocked,{"b.gap"})
        a["feeds"][0]["rows"][1]["max_gap_minutes"]=360
        self.assertEqual(receiver._blocked_48h_ids("elcinema",a,{"a.good","b.gap","c.warn","d.good"}),set())
    def test_runtime_does_not_require_other_sources_to_contain_all_warn_rows(self):
        _,a,_,_=fixture()
        self.assertEqual(receiver._blocked_48h_ids("osn",a,{"a.good","b.gap","c.warn","d.good"}),set())
    def test_report_missing_row_never_qualifies(self):
        f,a,q,v=fixture()
        a["feeds"][0]["rows"].pop()
        self.assertEqual(standalone.assess("elcinema",f,a,q,NOW.timestamp())["status"],
                         "48H_ELCINEMA_WARN_GAP_REPORT_INCOMPLETE")
        self.assertIsNone(receiver._blocked_48h_ids("elcinema",a,{"a.good","b.gap","c.warn","d.good"}))
    def test_wrong_gap_reason_is_not_trusted(self):
        f,a,q,v=fixture()
        a["feeds"][0]["rows"][1]["reason"]="SHORT_HORIZON"
        self.assertEqual(standalone.assess("elcinema",f,a,q,NOW.timestamp())["status"],
                         "48H_ELCINEMA_WARN_GAP_REPORT_INCOMPLETE")
        self.assertIsNone(receiver._blocked_48h_ids("elcinema",a,{"a.good","b.gap","c.warn","d.good"}))
    def test_missing_future_evidence_degrades_entire_source(self):
        f,a,q,v=fixture()
        v["xmltv_future_real_ids"].remove("b.gap")
        result=observed(f,a,q,v)
        self.assertFalse(result["ready_for_epg_ok"])
        self.assertEqual(result["status"],"48H_PARTIAL_IDS_XMLTV_MISMATCH")
    def test_no_gap_eligible_still_blocks_placeholder(self):
        f,a,q,v=fixture()
        q["suspected_placeholder_channels"]=[{"id":"c.warn","count":5}]
        result=observed(f,a,q,v)
        self.assertNotIn("c.warn",result["trusted_current_epg_ids"])
        self.assertNotIn("b.gap",result["trusted_current_epg_ids"])
        self.assertEqual(standalone.mapping_quality_for_id("c.warn",result)["status"],"SUSPECT_GENERATED_EPG")
    def test_preview_renders_yellow_for_24h_gap(self):
        time=NOW.timestamp()
        report={"audit":"EPGManager RC80 public feed provenance","evidence_schema":1,
                "full_xmltv_integrity_checks_enabled":True,
                "checked_utc":NOW.isoformat(),"sources":[{
                  "source":"elcinema","xmltv_sha256_verified":True,
                  "channel_specific_epg_evidence_verified":True,
                  "real_now_coverage_verified":True,
                  "per_id_evidence_observed_utc":NOW.isoformat(),
                  "ready_for_epg_ok":True,
                  "status":"HASHED_XMLTV_PER_ID_PARTIAL_48H",
                  "severe_warn_gap_ids":["b.gap"],"partial_48h_blocked_ids":["b.gap"],
                  "trusted_current_epg_ids":["a.good"],
                  "xmltv_current_real_ids":["a.good","b.gap"]}]}
        self.assertEqual(match.quality_for_id("direct_elcinema","b.gap",report,time)["status"],"PARTIAL_GUIDE_REVIEW")
        self.assertEqual(match.quality_for_id("direct_elcinema","a.good",report,time)["status"],"REAL_CURRENT_EPG")
    def test_stale_feed_never_green(self):
        f,a,q,v=fixture()
        f["generated_utc"]=(NOW-timedelta(days=5)).isoformat()
        self.assertFalse(observed(f,a,q,v)["ready_for_epg_ok"])
    def test_zero_gap_warn_data_is_structurally_checked(self):
        f,a,q,v=fixture()
        a["feeds"][0]["warning_channels"]=99
        self.assertIsNone(standalone._severe_elcinema_warn_gap_ids(f,a["feeds"][0]))
        self.assertIsNone(receiver._blocked_48h_ids("elcinema",a,{"a.good","b.gap","c.warn","d.good"}))

if __name__=="__main__":
    unittest.main()
