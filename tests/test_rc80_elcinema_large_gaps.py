# -*- coding: utf-8 -*-
"""RC80 guards: a global WARN cannot turn a 24-hour guide hole green."""
from __future__ import annotations
import json
import unittest
from test_rc80_verified_epg_refresh import refresh, fixture, reader, NOW, bridge
from test_rc80_elcinema_partial_48h import inputs, observed, m, NOW as CLI_NOW

def source_xml_fixture(gap):
    docs,meta,audit,quality,raw=fixture("elcinema")
    audit["feeds"][0].update(
      status="WARN",channels=4,invalid_times=0,orphan_programmes=0,
      failed_channels=0,warning_channels=2,rows=[
         {"id":"A","status":"WARN","reason":"INTERNAL_GAP","max_gap_minutes":gap},
         {"id":"B","status":"WARN","reason":"SHORT_HORIZON","max_gap_minutes":0},
         {"id":"NONE","status":"PASS","reason":"","max_gap_minutes":0},
         {"id":"SYN","status":"PASS","reason":"","max_gap_minutes":0},
      ])
    docs["reports/elcinema-48h-audit.json"]=json.dumps(audit).encode("utf-8")
    return docs,audit

def cli_warn_fixture(gap):
    a,b,c,v=inputs()
    row=b["feeds"][0]
    row.update(status="WARN",failed_channels=0,warning_channels=3)
    for record in row["rows"]:
        if record["id"]=="short1":
            record.update(status="WARN",reason="INTERNAL_GAP",future_hours=40.0,
                          max_gap_minutes=gap)
        elif record["status"]=="WARN":
            record["max_gap_minutes"]=0
    return a,b,c,v

class ExtremeElCinema(unittest.TestCase):
    def test_runtime_warn_24h_gap_never_auto(self):
        docs,_=source_xml_fixture(1440)
        x=refresh.inspect_source("elcinema",now=NOW.timestamp(),reader=reader(docs))
        self.assertEqual(x["partial_48h_blocked_ids"],["A"])
        self.assertEqual(x["long_gap_review_epg_ids"],["A"])
        self.assertEqual(x["trusted_current_epg_ids"],[])
        result=bridge.quality_for_id("direct_elcinema","A",report={
            "audit":"EPGManager RC80 public feed provenance","evidence_schema":1,
            "checked_utc":NOW.isoformat(),
            "full_xmltv_integrity_checks_enabled":True,"sources":[x]
        },now=NOW.timestamp())
        self.assertEqual(result["status"],"LONG_INTERNAL_GAP_REVIEW")
        self.assertFalse(result["verified_now"])
    def test_runtime_warn_small_gap_can_be_candidate(self):
        docs,_=source_xml_fixture(719)
        x=refresh.inspect_source("elcinema",NOW.timestamp(),reader(docs))
        self.assertEqual(x["partial_48h_blocked_ids"],[])
        self.assertEqual(x["trusted_current_epg_ids"],["A"])
        self.assertTrue(x["ready_for_epg_ok"])
    def test_runtime_warn_unverified_rows_not_healthy(self):
        docs,audit=source_xml_fixture(1440)
        audit["feeds"][0]["rows"].pop()
        docs["reports/elcinema-48h-audit.json"]=json.dumps(audit).encode()
        x=refresh.inspect_source("elcinema",NOW.timestamp(),reader(docs))
        self.assertEqual(x["trusted_current_epg_ids"],[])
        self.assertFalse(x["ready_for_epg_ok"])
    def test_cli_warn_global_now_97_does_not_make_24h_gap_green(self):
        a,b,c,v=cli_warn_fixture(1440)
        x=observed(a,b,c,v)
        self.assertEqual(x["status"],"HASHED_XMLTV_PER_ID_PARTIAL_48H")
        self.assertEqual(x["partial_48h_large_gap_ids"],["short1"])
        self.assertEqual(x["review_partial_epg_ids"],["short1"])
        self.assertEqual(x["trusted_current_epg_ids"],["good1","good2","good3"])
        self.assertFalse(m.mapping_quality_for_id("short1",x)["auto_eligible"])
    def test_cli_metadata_alone_not_enough(self):
        a,b,c,v=cli_warn_fixture(1440)
        x=m.assess("elcinema",a,b,c,CLI_NOW.timestamp())
        self.assertEqual(x["partial_48h_blocked_ids"],["short1"])
        self.assertFalse(x["ready_for_epg_ok"])
    def test_cli_mismatched_warning_rows_closed(self):
        a,b,c,v=cli_warn_fixture(1440)
        b["feeds"][0]["rows"].pop()
        x=m.assess("elcinema",a,b,c,NOW.replace(hour=12,minute=10).timestamp())
        self.assertEqual(x["status"],"48H_AUDIT_INCONSISTENT_IDS")
        self.assertEqual(x["trusted_current_epg_ids"],[])
    def test_cli_719_gap_no_quarantine(self):
        a,b,c,v=cli_warn_fixture(719)
        x=observed(a,b,c,v)
        self.assertNotIn("short1",x.get("partial_48h_blocked_ids",[]))
        self.assertTrue(m.mapping_quality_for_id("short1",x)["auto_eligible"])
if __name__=="__main__":
    unittest.main()
