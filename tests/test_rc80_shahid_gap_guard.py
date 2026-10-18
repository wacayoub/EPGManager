# -*- coding: utf-8 -*-
"""Shahid AWSN 622-minute gap: reviewed ID only, not green EPG mapping."""
import gzip
import hashlib
import json
import unittest

from test_rc80_verified_epg_refresh import fixture, reader, refresh, bridge, NOW
from tools.audit_rc80_public_feed_provenance import assess

AWSN = "shahid.49923781913188"
MBC = "MBC1.ae@SD"

def example():
    docs, meta, audit, quality, xml = fixture("shahid")
    xml = (xml.replace('<channel id="NONE"><display-name>None</display-name></channel>', '')
              .replace('id="A"', 'id="' + AWSN + '"')
              .replace('channel="A"', 'channel="' + AWSN + '"')
              .replace('id="SYN"', 'id="' + MBC + '"')
              .replace('channel="SYN"', 'channel="' + MBC + '"')
              .replace("Channel name only", "برنامج حقيقي آخر"))
    raw = gzip.compress(xml.encode(), mtime=0)
    meta.update(channels=3,active_channels=3,programmes=3,
                currently_on_air_channels=2,
                sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
    audit["feeds"][0].update(
        status="WARN", channels=3, failed_channels=0,warning_channels=1,
        invalid_times=0,orphan_programmes=0,rows=[
            {"id":AWSN, "status":"WARN","reason":"SHORT_HORIZON+INTERNAL_GAP",
             "max_gap_minutes":622.0,"future_hours":46.07},
            {"id":MBC,"status":"PASS","reason":"",
             "max_gap_minutes":0,"future_hours":48.1},
            {"id":"B","status":"PASS","reason":"",
             "max_gap_minutes":0,"future_hours":48.1},
        ])
    quality["quality_status"]="WARN"
    quality["suspected_placeholder_channels"]=[]
    docs["feeds/shahid.xml.gz"]=raw
    docs["feeds/shahid.json"]=json.dumps(meta).encode()
    docs["reports/shahid-48h-audit.json"]=json.dumps(audit).encode()
    docs["reports/shahid-quality.json"]=json.dumps(quality).encode()
    return docs,meta,audit,quality

class TestShahidExactIdGapQuarantine(unittest.TestCase):
    def test_awsn_yellow_but_mbc_verified_now_green(self):
        docs,feed,audit,quality=example()
        preliminary=assess("shahid",feed,audit,quality,now=NOW.timestamp())
        self.assertEqual(preliminary["partial_48h_large_gap_ids"],[AWSN])
        self.assertEqual(preliminary["partial_48h_blocked_ids"],[AWSN])
        self.assertEqual(preliminary["status"],"48H_PARTIAL_IDS_NEED_XMLTV_PROOF")
        self.assertFalse(preliminary["ready_for_epg_ok"])
        result=refresh.inspect_source("shahid",now=NOW.timestamp(),reader=reader(docs))
        self.assertTrue(result["xmltv_sha256_verified"])
        self.assertTrue(result["ready_for_epg_ok"])
        self.assertEqual(result["trusted_current_epg_ids"],[MBC])
        self.assertEqual(result["partial_48h_blocked_ids"],[AWSN])
        self.assertEqual(result["long_gap_review_epg_ids"],[AWSN])
        self.assertIn(AWSN,result["xmltv_current_real_ids"])
        wrapped={"audit":"EPGManager RC80 public feed provenance",
                 "evidence_schema":1,"checked_utc":NOW.isoformat(),
                 "full_xmltv_integrity_checks_enabled":True,"sources":[result]}
        self.assertEqual(bridge.quality_for_id("direct_shahid",AWSN,
            report=wrapped,now=NOW.timestamp())["status"],"LONG_INTERNAL_GAP_REVIEW")
        self.assertEqual(bridge.quality_for_id("direct_shahid",MBC,
            report=wrapped,now=NOW.timestamp())["status"],"REAL_CURRENT_EPG")
        ranked=bridge.annotate_suggestions([
            {"source_id":"direct_shahid","channel_id":AWSN},
            {"source_id":"direct_shahid","channel_id":MBC},
        ],report=wrapped,now=NOW.timestamp())
        self.assertEqual(ranked[0]["channel_id"],MBC)
        # The verified ranking never changes an already saved owner.
        saved=bridge.annotate_suggestions([
            {"source_id":"direct_shahid","channel_id":AWSN,"_suggestion_current":True},
            {"source_id":"direct_shahid","channel_id":MBC},
        ],report=wrapped,now=NOW.timestamp())
        self.assertEqual(saved[0]["channel_id"],AWSN)
        self.assertEqual(saved[0]["_verified_epg_health"],"SAVED_OWNER")

    def test_invalid_shahid_per_id_cohort_fails_closed(self):
        for tamper in ("missing_id","duplicate_id","bad_gap","bad_failed_count"):
            with self.subTest(tamper=tamper):
                docs,feed,audit,quality=example()
                item=audit["feeds"][0]
                if tamper=="missing_id":
                    item["rows"].pop()
                if tamper=="duplicate_id":
                    item["rows"][1]["id"]=AWSN
                if tamper=="bad_gap":
                    item["rows"][0]["max_gap_minutes"]=-5
                if tamper=="bad_failed_count":
                    item["failed_channels"]=1
                docs["reports/shahid-48h-audit.json"]=json.dumps(audit).encode()
                pre=assess("shahid",feed,audit,quality,now=NOW.timestamp())
                self.assertEqual(pre["status"],"48H_AUDIT_INCONSISTENT_IDS")
                result=refresh.inspect_source("shahid",now=NOW.timestamp(),
                                              reader=reader(docs))
                self.assertFalse(result["ready_for_epg_ok"])
                self.assertEqual(result["trusted_current_epg_ids"],[])
                self.assertEqual(result["status"],"48H_AUDIT_INCONSISTENT_IDS")

if __name__=="__main__":
    unittest.main()
