# -*- coding: utf-8 -*-
"""Tabie active-ID source exports: only genuine next/current programmes."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import unittest
import xml.etree.ElementTree as ET
from tabie_source import retain_live_ids,fmt

Q=ZoneInfo("Asia/Qatar")
NOW=datetime(2026,10,17,14,0,tzinfo=Q)
def build():
    raw=ET.Element("tv")
    cat=ET.Element("channels")
    for cid in ("QatarTelevision.qa@SD","QatarBusinessChannel.qa@SD","TabieNow.qa","TabieKids.qa","AlRayyanTV.qa@SD"):
        ET.SubElement(raw,"channel",id=cid)
        ET.SubElement(cat,"channel",xmltv_id=cid)
    return raw,cat
def add(root,cid,begin,end,title="Valid broadcast"):
    p=ET.SubElement(root,"programme",channel=cid,start=fmt(begin),stop=fmt(end))
    ET.SubElement(p,"title").text=title

class ActiveTabie(unittest.TestCase):
    def test_only_valid_current_or_future_ids(self):
        raw,cat=build()
        add(raw,"TabieNow.qa", NOW-timedelta(minutes=10),NOW+timedelta(minutes=20))
        add(raw,"QatarBusinessChannel.qa@SD",NOW+timedelta(hours=2),NOW+timedelta(hours=3))
        add(raw,"TabieKids.qa",NOW-timedelta(hours=12),NOW-timedelta(hours=11))
        out=retain_live_ids(raw,cat,NOW)
        self.assertEqual(out["retained_ids"],["QatarBusinessChannel.qa@SD","TabieNow.qa"])
        self.assertIn("TabieKids.qa",out["excluded_ids"])
        self.assertEqual(len(raw.findall("programme")),2)
        self.assertEqual(len(raw.findall("channel")),2)
        self.assertEqual(len(cat.findall("channel")),2)
    def test_future_beyond_48_hours_does_not_green_id(self):
        raw,cat=build()
        add(raw,"TabieKids.qa",NOW+timedelta(hours=54),NOW+timedelta(hours=55))
        self.assertEqual(retain_live_ids(raw,cat,NOW)["retained_ids"],[])
    def test_invalid_timestamp_is_never_used_to_retain(self):
        raw,cat=build()
        ET.SubElement(raw,"programme",channel="TabieNow.qa",start="garbage",stop=fmt(NOW+timedelta(hours=1)))
        self.assertEqual(retain_live_ids(raw,cat,NOW)["retained_ids"],[])
    def test_existing_identifiers_are_not_renamed(self):
        raw,cat=build()
        add(raw,"QatarBusinessChannel.qa@SD",NOW,NOW+timedelta(minutes=30))
        retain_live_ids(raw,cat,NOW)
        self.assertEqual(raw.find("channel").get("id"),"QatarBusinessChannel.qa@SD")
        self.assertEqual(cat.find("channel").get("xmltv_id"),"QatarBusinessChannel.qa@SD")
    def test_cross_midnight_real_guide_kept(self):
        raw,cat=build()
        begin=datetime(2026,10,17,23,45,tzinfo=Q)
        add(raw,"TabieKids.qa",begin,begin+timedelta(hours=1))
        self.assertIn("TabieKids.qa",retain_live_ids(raw,cat,NOW)["retained_ids"])
    def test_current_validity_not_inferred_from_gaps(self):
        raw,cat=build()
        add(raw,"TabieNow.qa",NOW-timedelta(hours=3),NOW-timedelta(hours=2))
        self.assertEqual(retain_live_ids(raw,cat,NOW)["retained_ids"],[])

if __name__=="__main__":
    unittest.main()
