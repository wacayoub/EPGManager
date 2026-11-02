# -*- coding: utf-8 -*-
"""Full OSN catalogue reporting, no fake EPG or remapping."""
import unittest
import xml.etree.ElementTree as ET
from osn_zero_epg_fallback import catalogue_future_health
from datetime import datetime,timedelta,timezone

NOW=datetime.now(timezone.utc)
def stamp(dt):
    return dt.strftime("%Y%m%d%H%M%S %z")

class OsnAllZeroTests(unittest.TestCase):
    def setUp(self):
        self.root=ET.Element("tv")
        for cid in ["OSNComedy","LegacyZero","LegacyZero2"]:
            ET.SubElement(self.root,"channel",id=cid)
        p=ET.SubElement(self.root,"programme",channel="OSNComedy",
            start=stamp(NOW-timedelta(minutes=5)),stop=stamp(NOW+timedelta(minutes=55)))
        ET.SubElement(p,"title").text="Programme réel"
    def test_detects_all_catalogue_missing_ids(self):
        s=catalogue_future_health(self.root)
        self.assertEqual(set(s),{"OSNComedy","LegacyZero","LegacyZero2"})
        self.assertEqual(s["OSNComedy"],1)
        self.assertEqual(s["LegacyZero"],0)
        self.assertEqual(s["LegacyZero2"],0)
    def test_no_fake_programmes_added(self):
        before=len(self.root.findall("programme"))
        catalogue_future_health(self.root)
        self.assertEqual(len(self.root.findall("programme")),before)
    def test_canonical_ids_not_changed(self):
        catalogue_future_health(self.root)
        self.assertEqual([c.get("id") for c in self.root.findall("channel")],
            ["OSNComedy","LegacyZero","LegacyZero2"])

if __name__=="__main__":
    unittest.main()
