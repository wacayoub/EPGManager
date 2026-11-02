# -*- coding: utf-8 -*-
"""Regression: XMLTV full 14-digit dates and GMT offsets must survive packing."""
from datetime import datetime,timedelta,timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools.test_bundle_fixture import write_bundle_metadata

from tools import source_feed_pack, validate_48h, audit_source_quality

ROOT=Path(__file__).resolve().parents[1]
FMT="%Y%m%d%H%M%S +0000"

def source(root, channels, now, offset_hours, count=1):
    tv=ET.Element("tv")
    tz=timezone(timedelta(hours=offset_hours))
    for cid in channels:
        c=ET.SubElement(tv,"channel",{"id":cid})
        ET.SubElement(c,"display-name").text=cid
        for i in range(count):
            start=now+timedelta(hours=i,minutes=-20)
            stop=start+timedelta(hours=1)
            p=ET.SubElement(tv,"programme",{
                "channel":cid,
                "start":start.astimezone(tz).strftime("%Y%m%d%H%M%S %z"),
                "stop":stop.astimezone(tz).strftime("%Y%m%d%H%M%S %z"),
            })
            ET.SubElement(p,"title",{"lang":"ar"}).text=f"برنامج حقيقي {i}"
    root.write_bytes(ET.tostring(tv,encoding="utf-8",xml_declaration=True))

class TestXMLTVAbsoluteTimestamps(unittest.TestCase):
    def test_full_seconds_and_provider_offsets_match_quality_parser(self):
        for text,expected in [
            ("20261018120030 +0300",datetime(2026,10,18,9,0,30,tzinfo=timezone.utc)),
            ("20261018120030 +0100",datetime(2026,10,18,11,0,30,tzinfo=timezone.utc)),
            ("20261018120030 -0500",datetime(2026,10,18,17,0,30,tzinfo=timezone.utc)),
            ("20261018120030 +0000",datetime(2026,10,18,12,0,30,tzinfo=timezone.utc)),
            ("20261018120030 Z",datetime(2026,10,18,12,0,30,tzinfo=timezone.utc)),
            ("202610181200 +0300",datetime(2026,10,18,9,0,tzinfo=timezone.utc)),
        ]:
            with self.subTest(source=text):
                self.assertEqual(source_feed_pack.parse_xmltv_dt(text),expected)
                self.assertEqual(validate_48h.dt(text),expected)
                self.assertEqual(audit_source_quality.parse_time(text),expected)
        for invalid in ("2026101812003000 +0300","20261018120030 +0300garbage",
                        "nonsense",""):
            with self.subTest(invalid=invalid):
                self.assertIsNone(source_feed_pack.parse_xmltv_dt(invalid))
                self.assertIsNone(validate_48h.dt(invalid))

    def test_source_feed_pack_converts_tunis_qatar_and_negative_offsets_to_gmt(self):
        now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
        absolute=now+timedelta(hours=2)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            tv=ET.Element("tv")
            for id_,offset in (("AlkassOne.qa@SD",3),
                               ("ElWatania2.tn@SD",1),("TestWest", -5)):
                ET.SubElement(ET.SubElement(tv,"channel",{"id":id_}),"display-name").text=id_
                tz=timezone(timedelta(hours=offset))
                program=ET.SubElement(tv,"programme",{"channel":id_,
                    "start":absolute.astimezone(tz).strftime("%Y%m%d%H%M%S %z"),
                    "stop":(absolute+timedelta(hours=1)).astimezone(tz).strftime("%Y%m%d%H%M%S %z")})
                ET.SubElement(program,"title").text="Authentic title"
            input_=p/"source.xml"
            input_.write_bytes(ET.tostring(tv,encoding="utf-8"))
            cmd=[sys.executable,str(ROOT/"tools/source_feed_pack.py"),
                 "--input",str(input_), "--output-gz",str(p/"feed.xml.gz"),
                 "--output-txt",str(p/"feed.txt"), "--stats",str(p/"feed.json"),
                 "--label","test","--window-hours","48"]
            x=subprocess.run(cmd,capture_output=True,text=True,timeout=20,cwd=p)
            self.assertEqual(x.returncode,0,(x.stdout,x.stderr))
            root=ET.fromstring(gzip.decompress((p/"feed.xml.gz").read_bytes()))
            self.assertEqual(len(root.findall("programme")),3)
            for event in root.findall("programme"):
                self.assertEqual(event.get("start"),absolute.strftime(FMT))
                self.assertEqual(event.get("stop"),(absolute+timedelta(hours=1)).strftime(FMT))
            meta=json.loads((p/"feed.json").read_text())
            self.assertEqual(meta["programmes"],3)
            self.assertEqual(meta["rejected_invalid_time"],0)

    def test_publish_lkg_current_coverage_uses_actual_plus3_absolute_clock(self):
        now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            target=p/"output/final/rotana.xml.gz"
            target.parent.mkdir(parents=True,exist_ok=True)
            local=p/"tmp_source.xml"
            source(local,("RotanaOne","RotanaTwo","RotanaThree","RotanaFour","RotanaFive"),
                   now,3,count=12)
            target.write_bytes(gzip.compress(local.read_bytes(),mtime=0))
            write_bundle_metadata(target)
            cmd=[sys.executable,str(ROOT/"tools/publish_lkg.py"),"--source","rotana",
                 "--report","reports/rotana-publish-lkg.json"]
            result=subprocess.run(cmd,capture_output=True,text=True,cwd=p,timeout=25)
            self.assertEqual(result.returncode,0,(result.stdout,result.stderr))
            report=json.loads((p/"reports/rotana-publish-lkg.json").read_text())
            row=report["sources"]["rotana"]
            self.assertEqual(row["decision"],"PUBLISH",row)
            self.assertEqual(row["candidate_stats"]["channels"],5)
            self.assertEqual(row["candidate_stats"]["current_channels"],5)
            self.assertGreater(row["candidate_stats"]["future_horizon_hours"],8)
            self.assertLess(row["candidate_stats"]["future_horizon_hours"],13)
            self.assertEqual(row["candidate_stats"]["invalid_rows"],0)

if __name__=="__main__":unittest.main()
