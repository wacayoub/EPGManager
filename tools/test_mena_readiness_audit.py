# -*- coding: utf-8 -*-
"""MENA 14 genuine source inputs, explicit quality warnings, structural release gate."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools.mena_readiness_audit import audit, render_markdown
from tools.test_mena_source_snapshot import feed, rebuild

ALL14 = (
    "morocco","alkass","tabie","tunisiatv","aljazeera","dubaiplus",
    "shahid","rotana","sport24","osn","bein","elcinema","starzplay","stctv",
)

def fixture(folder):
    folder = Path(folder)
    (folder/"config").mkdir(parents=True)
    (folder/"reports").mkdir(parents=True)
    (folder/"config/canonical-channel-policy.json").write_text(json.dumps({
        "default_source_order":list(ALL14),
        "alias_groups":[],
        "family_rules":[],
        "quality":{"min_future_programmes":1,"min_future_hours":4,
                   "min_description_pct":35,"min_language_fit_pct":50},
    }))
    now=datetime.now(timezone.utc).replace(microsecond=0)
    for source in ALL14:
        cid="2M" if source=="morocco" else (
            "TabieNow.qa" if source=="tabie" else source+".example@SD")
        feed(folder, source, cid, now, valid_future=True)
    meta,xml=rebuild(folder)
    return meta,xml,now

def check(folder,now):
    p=Path(folder)
    return audit(p/"feeds/mena.xml.gz",p/"feeds/mena.json",
                 p/"reports/canonical-source-routing.json",p/"feeds",now=now)

class TestMena517IDQualityReleaseGate(unittest.TestCase):
    def test_fourteen_trusted_sources_qualified_and_morocco_priority(self):
        with tempfile.TemporaryDirectory() as d:
            meta,root,now=fixture(d)
            report=check(d,now)
            self.assertEqual(report["status"],"READY",report["structural_errors"])
            self.assertEqual(report["verified_source_count"],14)
            self.assertTrue(report["source_manifest_sha256_match"])
            self.assertEqual(report["channels"],14)
            self.assertEqual(report["programmes"],42)
            self.assertEqual(report["selected_statuses"],{"QUALIFIED_AT_BUILD":14})
            self.assertEqual(report["invalid_programme_times"],0)
            self.assertEqual(report["exact_duplicate_programmes"],0)
            self.assertTrue(all(x["best_match_green_allowed"] for x in report["ids"]))
            self.assertEqual(next(x for x in report["ids"] if x["xmltv_id"]=="2M")["owner"],"morocco")
            self.assertEqual(next(x for x in report["ids"]
                                  if x["xmltv_id"]=="TabieNow.qa")["owner"],"tabie")
            self.assertIn("14 source fingerprints checked",render_markdown(report))

    def test_genuine_channels_can_become_expired_without_changing_ids(self):
        with tempfile.TemporaryDirectory() as d:
            meta,root,now=fixture(d)
            report=check(d,now+timedelta(days=4))
            self.assertEqual(report["status"],"READY_WITH_WARNINGS")
            self.assertEqual(report["selected_statuses"],{"NO_FUTURE_EPG":14})
            self.assertEqual(report["channels"],14)
            self.assertTrue(all(not x["best_match_green_allowed"] for x in report["ids"]))
            self.assertTrue(all(x["manual_mapping_visible"] for x in report["ids"]))
            self.assertEqual(report["structural_errors"],[])

    def test_modified_source_manifest_must_block_derived_publication(self):
        with tempfile.TemporaryDirectory() as d:
            meta,root,now=fixture(d)
            path=Path(d)/"feeds/morocco.json"
            data=json.loads(path.read_text())
            data["sha256"]="0"*64
            path.write_text(json.dumps(data))
            report=check(d,now)
            self.assertEqual(report["status"],"INVALID")
            self.assertTrue(any("SOURCE_MANIFEST_INTEGRITY_ERROR" in x
                                for x in report["structural_errors"]))

    def test_orphan_programmes_and_event_duplicate_are_structural_errors(self):
        with tempfile.TemporaryDirectory() as d:
            meta,root,now=fixture(d)
            p=Path(d)/"feeds/mena.xml.gz"
            reportfile=Path(d)/"feeds/mena.json"
            x=ET.fromstring(gzip.decompress(p.read_bytes()))
            cloned=ET.fromstring(ET.tostring(x.find("programme"),encoding="utf-8"))
            x.append(cloned)
            unknown=ET.fromstring(ET.tostring(cloned,encoding="utf-8"))
            unknown.set("channel","NOT_A_REAL_XMLTV_ID")
            x.append(unknown)
            raw=gzip.compress(ET.tostring(x,encoding="utf-8"),mtime=0)
            p.write_bytes(raw)
            data=json.loads(reportfile.read_text())
            data["sha256"]=hashlib.sha256(raw).hexdigest()
            data["programmes"]+=2
            reportfile.write_text(json.dumps(data))
            report=check(d,now)
            self.assertEqual(report["status"],"INVALID")
            self.assertEqual(report["exact_duplicate_programmes"],1)
            self.assertEqual(report["orphan_programmes"],1)
            self.assertIn("ORPHAN_PROGRAMMES:1",report["structural_errors"])
            self.assertIn("EXACT_DUPLICATE_PROGRAMMES:1",report["structural_errors"])

    def test_selected_main_and_no_future_guide_never_imply_good_epg(self):
        with tempfile.TemporaryDirectory() as d:
            meta,root,now=fixture(d)
            routefile=Path(d)/"reports/canonical-source-routing.json"
            data=json.loads(routefile.read_text())
            row=data["routing"]["2M"]
            self.assertEqual(row["selected_role"],"MAIN")
            row["candidates"][0]["qualified"]=False
            row["selection_reason"]="degraded_best_available"
            routefile.write_text(json.dumps(data))
            report=check(d,now)
            selected=next(x for x in report["ids"] if x["xmltv_id"]=="2M")
            self.assertEqual(selected["status"],"DEGRADED_WITH_EPG")
            self.assertFalse(selected["best_match_green_allowed"])
            self.assertEqual(selected["selected_role"],"MAIN")
            self.assertEqual(report["status"],"READY_WITH_WARNINGS")

if __name__ == "__main__":
    unittest.main()
