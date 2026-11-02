# -*- coding: utf-8 -*-
"""Tabie genuine near-future replacement of an EXPIRED old QMC source only."""
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

REPO=Path(__file__).resolve().parents[1]
PUBLISH=REPO/"tools/publish_lkg.py"
REAL=("QatarBusinessChannel.qa@SD","TabieNow.qa","TabieKids.qa")

def feed(path, channels, now, start_lead_h, interval_minutes=20, programmes_each=30):
    root=ET.Element("tv")
    for cid in channels:
        ET.SubElement(ET.SubElement(root,"channel",{"id":cid}),"display-name").text=cid
        for i in range(programmes_each):
            a=now+timedelta(hours=start_lead_h,minutes=interval_minutes*i)
            b=a+timedelta(minutes=interval_minutes)
            p=ET.SubElement(root,"programme",{"channel":cid,
                 "start":a.strftime("%Y%m%d%H%M%S +0000"),
                 "stop":b.strftime("%Y%m%d%H%M%S +0000")})
            ET.SubElement(p,"title",{"lang":"ar"}).text=f"حلقات واقعية {i+1}"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(gzip.compress(ET.tostring(root,encoding="utf-8"),mtime=0))

def exercise(candidate_ids=REAL,lead=1,old_expired=True,programmes=30,other=None):
    now=datetime.now(timezone.utc).replace(microsecond=0)
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)
        current=root/"output/final/tabie.xml.gz"
        previous=root/"feeds/tabie.xml.gz"
        feed(current,candidate_ids,now,lead,programmes_each=programmes)
        write_bundle_metadata(current)
        old_names=list(REAL)+["QatarTelevision.qa@SD","QatarQuranTV.qa@SD",
             "AlkassOne.qa@SD","AlRayyanTV.qa@SD",
             "AlRayyanAlQadeem.qa@SD","QatarTelevision2.qa@SD"]
        feed(previous,old_names,now,-72 if old_expired else 0,
             programmes_each=55)
        before=previous.read_bytes()
        cmd=[sys.executable,str(PUBLISH),"--source","tabie",
             "--report","reports/tabie-publish-lkg.json"]
        p=subprocess.run(cmd,cwd=root,text=True,capture_output=True,timeout=25)
        assert p.returncode==0,(p.stdout,p.stderr)
        report=json.loads((root/"reports/tabie-publish-lkg.json").read_text())
        result=report["sources"]["tabie"]
        published=previous.read_bytes()
        return result,before,published

class TestQMCActiveOnlyExpiredLKG(unittest.TestCase):
    def test_three_genuine_near_future_ids_replace_expired_nine(self):
        result,before,after=exercise()
        self.assertEqual(result["decision"],"PUBLISH",result)
        self.assertNotEqual(before,after)
        self.assertEqual(result["candidate_stats"]["channels"],3)
        self.assertEqual(result["candidate_stats"]["active_channels"],3)
        self.assertTrue(result["candidate_stats"]["partial_recovery_from_expired_lkg"])
        self.assertEqual(result["candidate_stats"]["current_channels"],0)
        self.assertEqual(result["candidate_stats"]["future_channels"],3)
        self.assertGreaterEqual(result["candidate_stats"]["future_horizon_hours"],8)

    def test_valid_but_far_future_schedule_cannot_replace_old_archive(self):
        result,before,after=exercise(lead=9)
        self.assertEqual(result["decision"],"KEEP_LKG")
        self.assertEqual(after,before)
        self.assertIn("current_ratio<0.60",result["reasons"])

    def test_unknown_technical_channel_id_is_not_whitelisted(self):
        result,before,after=exercise(candidate_ids=("TabieNow.qa","TabieKids.qa","FAKE.channel"))
        self.assertEqual(result["decision"],"KEEP_LKG")
        self.assertEqual(after,before)

    def test_expired_guide_guard_does_not_override_healthy_old_publication(self):
        result,before,after=exercise(old_expired=False)
        self.assertEqual(result["decision"],"KEEP_LKG")
        self.assertEqual(after,before)

    def test_short_or_too_small_content_is_not_recovered(self):
        result,before,after=exercise(programmes=5)
        self.assertEqual(result["decision"],"KEEP_LKG")
        self.assertEqual(after,before)

if __name__=="__main__":unittest.main()
