# -*- coding: utf-8 -*-
"""RC80 OSN source-health evidence can ONLY restrict proven genuine EPG NOW."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[0]))
from test_rc80_verified_epg_refresh import refresh, bridge, fixture, reader, NOW

def with_osn(rows=None, wrong_hash=False, wrong_future=False):
    docs,meta,report,quality,_=fixture("osn")
    audit=report["feeds"][0]
    audit.update({
        "status":"FAIL","failed_channels":1,
        "invalid_times":0,"orphan_programmes":0,
        "rows":[{"id":cid,"status":"FAIL" if cid=="NONE" else "PASS"}
                for cid in ("A","B","NONE","SYN")]
    })
    docs["reports/osn-48h-audit.json"]=json.dumps(report).encode()
    when=datetime.fromisoformat(meta["generated_utc"])
    defaults=[
        ("A","VERIFIED",True),("B","REVIEW",False),
        ("NONE","NO_EPG",False),("SYN","REVIEW",False)]
    rows=rows or defaults
    h={
        "schema":1, "source":"osn",
        "source_sha256":"0"*64 if wrong_hash else meta["sha256"],
        "catalogue_channels":4,
        "programme_rows_unchanged":3,
        "synthetic_programmes_added":0,
        "source_generated_utc":meta["generated_utc"],
        "checked_utc":(when+timedelta(minutes=2)).isoformat(),
        "counts":{s:sum(x[1]==s for x in rows) for s in ("VERIFIED","REVIEW","NO_EPG")},
        "missing_ids":[x[0] for x in rows if x[1]=="NO_EPG"],
        "ids":[{"xmltv_id":cid,"status":s,"best_match_green_allowed":green,
                "manual_mapping_visible":True} for cid,s,green in rows],
    }
    if wrong_future:
        h["missing_ids"]=["A"]
        for x in h["ids"]:
            if x["xmltv_id"]=="A":
                x["status"]="NO_EPG"
                x["best_match_green_allowed"]=False
            if x["xmltv_id"]=="NONE":
                x["status"]="REVIEW"
        h["counts"]={"VERIFIED":0,"REVIEW":3,"NO_EPG":1}
    docs["reports/osn-per-id-health.json"]=json.dumps(h).encode()
    return docs,h

class VerifiedOSNHealth(unittest.TestCase):
    def inspect(self,docs):
        return refresh.inspect_source("osn",now=NOW.timestamp(),reader=reader(docs))

    def test_new_health_does_not_overrule_real_now_or_saved_owner(self):
        docs,_=with_osn()
        row=self.inspect(docs)
        self.assertTrue(row["osn_per_id_cohort_verified"])
        self.assertEqual(row["trusted_current_epg_ids"],["A"])
        report={"audit":"EPGManager RC80 public feed provenance",
                "evidence_schema":1,"checked_utc":NOW.isoformat(),
                "full_xmltv_integrity_checks_enabled":True,"sources":[row]}
        self.assertEqual(bridge.quality_for_id("direct_osn","NONE",report=report,
                                               now=NOW.timestamp())["status"],"NO_EPG")
        choices=[{"source_id":"direct_osn","channel_id":"NONE","_suggestion_current":True},
                 {"source_id":"direct_osn","channel_id":"A"}]
        out=bridge.annotate_suggestions(choices,report=report,now=NOW.timestamp())
        self.assertEqual(out[0]["channel_id"],"NONE")
        self.assertEqual(out[0]["_verified_epg_health"],"SAVED_OWNER")

    def test_source_verified_label_without_real_current_can_never_green(self):
        docs,_=with_osn([
            ("A","REVIEW",False),("B","VERIFIED",True),
            ("NONE","NO_EPG",False),("SYN","REVIEW",False)])
        row=self.inspect(docs)
        self.assertEqual(row["trusted_current_epg_ids"],[])
        self.assertFalse(row["ready_for_epg_ok"])
        self.assertEqual(row["osn_review_per_id_ids"],["A","SYN"])
        self.assertIn("B",row["review_future_epg_ids"])

    def test_bad_sha_or_fabricated_zero_current_fails_closed(self):
        for opts in ({"wrong_hash":True},{"wrong_future":True}):
            with self.subTest(opts=opts):
                docs,_=with_osn(**opts)
                row=self.inspect(docs)
                self.assertFalse(row["ready_for_epg_ok"])
                self.assertEqual(row["trusted_current_epg_ids"],[])
                self.assertFalse(row["xmltv_sha256_verified"])

    def test_duplicate_or_partial_id_catalogue_fails_closed(self):
        docs,h=with_osn()
        h["ids"][0]["xmltv_id"]="NONE"
        docs["reports/osn-per-id-health.json"]=json.dumps(h).encode()
        row=self.inspect(docs)
        self.assertFalse(row["ready_for_epg_ok"])

    def test_absent_new_report_keeps_existing_source_xmltv_evidence_policy(self):
        docs,_=with_osn()
        docs.pop("reports/osn-per-id-health.json")
        row=self.inspect(docs)
        self.assertFalse(row["osn_per_id_cohort_verified"])
        self.assertEqual(row["trusted_current_epg_ids"],["A"])
        self.assertIn("NONE",row["partial_48h_blocked_ids"])

    def test_source_report_never_invents_evidence_for_other_sources(self):
        docs,*_=fixture("morocco")
        row=refresh.inspect_source("morocco",now=NOW.timestamp(),reader=reader(docs))
        self.assertFalse(row["osn_per_id_cohort_verified"])
        self.assertEqual(row["trusted_current_epg_ids"],["A"])

if __name__=="__main__":
    unittest.main()
