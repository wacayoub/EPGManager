# -*- coding: utf-8 -*-
"""Dubai+ exact Dubai TV OSN fallback: no replacement, fabrication or fuzzy ID."""
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import unittest
import xml.etree.ElementTree as ET

from tools import dubaiplus_verified_osn_gap as mod

NOW=datetime(2026,10,18,11,0,tzinfo=timezone.utc)
CID=mod.DUBAI_TV_ID

def date(hours):
    return (NOW+timedelta(hours=hours)).strftime("%Y%m%d%H%M%S +0000")

def programme(cid,start,stop,title):
    el=ET.Element("programme",{"channel":cid,"start":date(start),"stop":date(stop)})
    ET.SubElement(el,"title",{"lang":"ar"}).text=title
    ET.SubElement(el,"desc",{"lang":"ar"}).text="وصف المصدر"
    return el

def root():
    tv=ET.Element("tv")
    for id_ in (CID,"DubaiOne.ae@SD","DubaiZaman.ae@SD"):
        ET.SubElement(tv,"channel",{"id":id_})
    tv.append(programme(CID,-1,1,"عنوان دبي الأصلي"))
    tv.append(programme(CID,4,6,"برنامج دبي الأصلي"))
    tv.append(programme("DubaiOne.ae@SD",0,6,"دبي ون"))
    return tv

def authenticated():
    return [
       (NOW+timedelta(hours=0), NOW+timedelta(hours=2),
        programme(CID,0,2,"OSN overlap must NOT overwrite")),
       (NOW+timedelta(hours=2), NOW+timedelta(hours=3),
        programme(CID,2,3,"OSN authentic gap programme")),
       (NOW+timedelta(hours=3), NOW+timedelta(hours=5),
        programme(CID,3,5,"OSN partial overlap must be rejected")),
       (NOW+timedelta(hours=7), NOW+timedelta(hours=10),
        programme(CID,7,10,"OSN real future programme")),
    ],"DONOR_VERIFIED"

class TestDubaiPlusRealGapOnly(unittest.TestCase):
    @patch.object(mod,"audited_donor",return_value=authenticated())
    def test_own_authentic_schedule_wins_and_only_full_gaps_append(self,fetch):
        tv=root()
        before=list(tv.findall("programme"))
        res=mod.extend(tv,"feeds",NOW)
        self.assertEqual(res["status"],"VERIFIED_GAPS_FILLED")
        self.assertEqual(res["added"],2)
        self.assertEqual(res["overlap_skipped"],2)
        self.assertEqual(res["synthetic_programmes_added"],0)
        self.assertEqual([p.findtext("title") for p in tv.findall("programme") if
                          p.get("channel")==CID],[
                          "عنوان دبي الأصلي","برنامج دبي الأصلي",
                          "OSN authentic gap programme","OSN real future programme"])
        self.assertEqual(tv.findall("programme")[:3],before)
        self.assertEqual([p.findtext("title") for p in tv.findall("programme")
                          if p.get("channel")=="DubaiOne.ae@SD"],["دبي ون"])
        self.assertTrue(all(p.find("desc") is not None for p in tv.findall("programme")))
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[1:],("osn","DubaiTV.ae@SD",NOW))

    @patch.object(mod,"audited_donor",return_value=([],"DONOR_HASH_OR_FRESHNESS"))
    def test_unverified_osn_cannot_change_anything(self,fetch):
        tv=root()
        orig=ET.tostring(tv)
        res=mod.extend(tv,"feeds",NOW)
        self.assertEqual(res["added"],0)
        self.assertEqual(res["status"],"DONOR_HASH_OR_FRESHNESS")
        self.assertEqual(ET.tostring(tv),orig)

    @patch.object(mod,"audited_donor",return_value=authenticated())
    def test_empty_dubai_tv_fails_closed(self,fetch):
        tv=root()
        for p in list(tv.findall("programme")):
            if p.get("channel")==CID:tv.remove(p)
        res=mod.extend(tv,"feeds",NOW)
        self.assertEqual(res["status"],"PRIMARY_DUBAIPLUS_CHANNEL_EMPTY_MANUAL_REVIEW")
        self.assertEqual(res["added"],0)
        fetch.assert_not_called()

    @patch.object(mod,"audited_donor",return_value=authenticated())
    def test_duplicate_channel_id_fails_closed(self,fetch):
        tv=root()
        ET.SubElement(tv,"channel",{"id":CID})
        res=mod.extend(tv,"feeds",NOW)
        self.assertEqual(res["added"],0)
        fetch.assert_not_called()

    @patch.object(mod,"audited_donor",return_value=authenticated())
    def test_bad_dubai_source_time_fails_closed(self,fetch):
        tv=root()
        [p for p in tv.findall("programme") if p.get("channel")==CID][0].set("stop","invalid")
        res=mod.extend(tv,"feeds",NOW)
        self.assertEqual(res["status"],"INVALID_DUBAIPLUS_PRIMARY_PROGRAMME")
        self.assertEqual(res["added"],0)
        fetch.assert_not_called()

if __name__=="__main__":
    unittest.main()
