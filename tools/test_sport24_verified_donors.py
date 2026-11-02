# -*- coding: utf-8 -*-
"""Genuine Sport24 three exact broadcast donor regression; no network/use of Vu+."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools import sport24_verified_donors as mod

NOW = datetime(2026,10,18,9,0,tzinfo=timezone.utc)
AD = "sport24.adsports.1"
DB1 = "sport24.dubaisports.1"
DB2 = "sport24.dubaisports.2"
T1 = "sport24.thmanyah.1"
T3 = "sport24.thmanyah.3"


def prog(cid, start_h, stop_h, title="لقاء رياضي حقيقي"):
    p=ET.Element("programme",{
        "channel":cid,
        "start":(NOW+timedelta(hours=start_h)).strftime("%Y%m%d%H%M%S +0000"),
        "stop":(NOW+timedelta(hours=stop_h)).strftime("%Y%m%d%H%M%S +0000"),
    })
    ET.SubElement(p,"title",{"lang":"ar"}).text=title
    ET.SubElement(p,"desc",{"lang":"ar"}).text="وصف رياضي أصلي"
    return p


def make(root,primary_extra=False):
    feeds=root/"feeds"; reports=root/"reports"
    feeds.mkdir();reports.mkdir()
    primary=ET.Element("tv")
    for cid in [AD,DB1,DB2,T1,T3]:
        ET.SubElement(primary,"channel",{"id":cid})
    primary.append(prog(AD,-1,2,"العنوان الأساسي Sport24"))
    primary.append(prog(T1,0,2,"برنامج ثمانية 1 الحقيقي"))
    if primary_extra:
        primary.append(prog(DB1,3,4,"أساسي دبي 1"))
    ids_by_source={
        "osn":[("AbuDhabiSports1.ae@SD", "AD Sports 1")],
        "dubaiplus":[("DubaiSports1.ae@SD","Dubai Sports 1"),
                     ("DubaiSports2.ae@SD","Dubai Sports 2")]
    }
    generated=NOW-timedelta(hours=1)
    for name,ids in ids_by_source.items():
        tv=ET.Element("tv")
        for cid,_ in ids:
            ET.SubElement(tv,"channel",{"id":cid})
            tv.append(prog(cid,1,3,"DONOR OVERLAP"))
            tv.append(prog(cid,3,5,"DONOR AUTHENTIC 1"))
            tv.append(prog(cid,5,25,"DONOR AUTHENTIC 2"))
            tv.append(prog(cid,25,45,"DONOR AUTHENTIC 3"))
        buf=gzip.compress(ET.tostring(tv,encoding="utf-8"),mtime=0)
        (feeds/(name+".xml.gz")).write_bytes(buf)
        (feeds/(name+".json")).write_text(json.dumps({
           "label":name,"generated_utc":generated.isoformat(),
           "sha256":hashlib.sha256(buf).hexdigest(),
           "size_bytes":len(buf),
           "channels":len(ids),"programmes":len(ids)*4
        }))
        (reports/(name+"-48h-audit.json")).write_text(json.dumps({
           "generated_utc":generated.isoformat(),
           "feeds":[{"feed":name+".xml.gz","status":"PASS",
             "channels":len(ids),"invalid_times":0,"orphan_programmes":0,
             "rows":[{"id":cid,"status":"PASS","future_hours":48.0,
                      "max_gap_minutes":0} for cid,_ in ids]}]
        }))
        (reports/(name+"-quality.json")).write_text(json.dumps({
            "source":name,"source_file":name+".xml.gz",
            "generated_utc":generated.isoformat(),"quality_status":"PASS",
            "suspected_placeholder_channels":[]
        }))
    return feeds,reports,primary


class TestSport24VerifiedDonors(unittest.TestCase):
    def test_real_three_channel_donor_complements_without_overwriting(self):
        with tempfile.TemporaryDirectory() as td:
            feeds,reports,tv=make(Path(td))
            result=mod.extend(tv,feeds,NOW)
            self.assertEqual(result["donor_channels"],3)
            self.assertEqual(result["synthetic_programmes_added"],0)
            self.assertEqual(result["added_programmes"],11)
            rows=tv.findall("programme")
            self.assertEqual([p.findtext("title") for p in rows if p.get("channel")==AD],[
                "العنوان الأساسي Sport24","DONOR AUTHENTIC 1","DONOR AUTHENTIC 2", "DONOR AUTHENTIC 3"])
            self.assertEqual(len([p for p in rows if p.get("channel")==T1]),1)
            self.assertEqual(len([p for p in rows if p.get("channel")==T3]),0)
            self.assertTrue(all(p.find("desc") is not None for p in rows))
            # Source description/language survive exact XMLTV record copying.
            for item in result["channels"]:
                self.assertTrue(item["status"] in ("VERIFIED_DONOR_APPLIED","VERIFIED_NO_OPEN_GAPS"))

    def test_current_sport24_programmes_always_win_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            feeds,reports,tv=make(Path(td),primary_extra=True)
            result=mod.extend(tv,feeds,NOW)
            db=[p for p in tv.findall("programme") if p.get("channel")==DB1]
            self.assertEqual([p.findtext("title") for p in db],[
                "أساسي دبي 1","DONOR OVERLAP","DONOR AUTHENTIC 2","DONOR AUTHENTIC 3"])
            # DONOR AUTHENTIC 1 at 3..5 is rejected; no clipping/invention.
            self.assertEqual(result["added_programmes"],10)

    def test_tampered_gzip_or_stale_cohort_never_added(self):
        for variant in ("wrong_hash","stale","audit_gap","wrong_id","source_quality"):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as td:
                feeds,reports,tv=make(Path(td))
                if variant=="wrong_hash":
                    (feeds/"osn.xml.gz").write_bytes(
                        (feeds/"osn.xml.gz").read_bytes()+b"bad")
                if variant=="stale":
                    meta=json.loads((feeds/"osn.json").read_text())
                    meta["generated_utc"]=(NOW-timedelta(days=4)).isoformat()
                    (feeds/"osn.json").write_text(json.dumps(meta))
                if variant in ("audit_gap","wrong_id"):
                    audit=json.loads((reports/"osn-48h-audit.json").read_text())
                    audit["feeds"][0]["rows"][0].update(
                        max_gap_minutes=200 if variant=="audit_gap" else 0,
                        id="WrongBroadcast" if variant=="wrong_id" else
                           "AbuDhabiSports1.ae@SD")
                    (reports/"osn-48h-audit.json").write_text(json.dumps(audit))
                if variant=="source_quality":
                    q=json.loads((reports/"osn-quality.json").read_text())
                    q["suspected_placeholder_channels"]=[
                        {"id":"AbuDhabiSports1.ae@SD","count":5}]
                    (reports/"osn-quality.json").write_text(json.dumps(q))
                result=mod.extend(tv,feeds,NOW)
                ad=[p for p in tv.findall("programme") if p.get("channel")==AD]
                self.assertEqual(len(ad),1)
                self.assertEqual(ad[0].findtext("title"),"العنوان الأساسي Sport24")
                self.assertEqual(result["donor_channels"],2)

    def test_unreviewed_similar_name_is_never_a_donor(self):
        with tempfile.TemporaryDirectory() as td:
            feeds,reports,tv=make(Path(td))
            ET.SubElement(tv,"channel",{"id":"sport24.adsports.4"})
            mod.extend(tv,feeds,NOW)
            self.assertFalse([p for p in tv.findall("programme")
                              if p.get("channel")=="sport24.adsports.4"])


if __name__ == "__main__":
    unittest.main()
