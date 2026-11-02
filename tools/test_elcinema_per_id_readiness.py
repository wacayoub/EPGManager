# -*- coding: utf-8 -*-
"""ElCinema source-quality labels must not turn placeholders green."""
import gzip, hashlib, json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tools import elcinema_per_id_readiness as gate

NOW=datetime(2026,10,17,13,30,tzinfo=timezone.utc)


def fixture(root,generated=NOW):
    root=Path(root)
    xml=b'<tv><channel id="A"/><channel id="B"/><channel id="C"/></tv>'
    data=gzip.compress(xml,mtime=0)
    (root/"elcinema.xml.gz").write_bytes(data)
    meta={"label":"elcinema","sha256":hashlib.sha256(data).hexdigest(),
          "size_bytes":len(data),"channels":3,"programmes":60,
          "generated_utc":generated.isoformat()}
    (root/"elcinema.json").write_text(json.dumps(meta))
    return root/"elcinema.xml.gz",root/"elcinema.json"


def audit():
    return {"channels":3,"invalid_times":0,"orphan_programmes":0,
            "rows":[
              {"id":"A","future_hours":48.4,"max_gap_minutes":0,"programmes":40,"reason":""},
              {"id":"B","future_hours":27.6,"max_gap_minutes":0,"programmes":35,"reason":"TOO_SHORT"},
              {"id":"C","future_hours":48.3,"max_gap_minutes":0,"programmes":46,"reason":""},
            ]}


def quality():
    return {"channels":3,"suspected_placeholder_channels":[{"id":"C","count":46}]}


class GenuineEPGOnly(unittest.TestCase):
    def test_green_yellow_and_placeholder_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed,meta=fixture(tmp)
            with patch.object(gate.validate_48h,"audit",return_value=audit()), \
                 patch.object(gate.audit_source_quality,"analyze",return_value=quality()):
                report=gate.verified_report(feed,meta,NOW)
            self.assertEqual(report["counts"],{"VERIFIED":1,"REVIEW":1,"QUARANTINE":1})
            by={x["xmltv_id"]:x for x in report["ids"]}
            self.assertTrue(by["A"]["auto_green_allowed"])
            self.assertFalse(by["B"]["auto_green_allowed"])
            self.assertTrue(by["B"]["manual_review_allowed"])
            self.assertFalse(by["C"]["auto_green_allowed"])
            self.assertEqual(by["C"]["reason"],"PLACEHOLDER_TITLES")
            self.assertEqual(report["synthetic_programmes_added"],0)

    def test_stale_source_no_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed,meta=fixture(tmp, NOW-timedelta(hours=26))
            with patch.object(gate.validate_48h,"audit",return_value=audit()), \
                 patch.object(gate.audit_source_quality,"analyze",return_value=quality()):
                r=gate.verified_report(feed,meta,NOW)
            self.assertEqual(r["counts"]["VERIFIED"],0)
            self.assertEqual(r["counts"]["REVIEW"],2)

    def test_bad_hash_refused_even_when_audit_would_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed,meta=fixture(tmp)
            x=json.loads(meta.read_text());x["sha256"]="0"*64;meta.write_text(json.dumps(x))
            with self.assertRaisesRegex(ValueError,"SOURCE_SHA256_MISMATCH"):
                gate.verified_report(feed,meta,NOW)

    def test_invalid_events_never_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed,meta=fixture(tmp)
            bad=audit();bad["invalid_times"]=1
            with patch.object(gate.validate_48h,"audit",return_value=bad):
                with self.assertRaisesRegex(ValueError,"BROKEN_XMLTV"):
                    gate.verified_report(feed,meta,NOW)

    def test_real_short_guide_stays_manual_yellow(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed,meta=fixture(tmp)
            a=audit();a["rows"][0]["future_hours"]=0.7
            a["rows"][1]["max_gap_minutes"]=180
            with patch.object(gate.validate_48h,"audit",return_value=a), \
                 patch.object(gate.audit_source_quality,"analyze",return_value=quality()):
                r=gate.verified_report(feed,meta,NOW)
            by={x["xmltv_id"]:x for x in r["ids"]}
            self.assertEqual(by["A"]["verdict"],"REVIEW")
            self.assertEqual(by["B"]["reason"],"INTERNAL_GAP")
            self.assertEqual(by["C"]["verdict"],"QUARANTINE")


if __name__=="__main__":
    unittest.main()
