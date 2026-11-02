# -*- coding: utf-8 -*-
"""Watania2's official 06:00-23:59 broadcast window is not missing EPG."""
from datetime import datetime,timedelta
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from tools.validate_48h import audit,wataniya2_verified_night_offair
from tools.alkass_source import parse_page,CHANNELS

TUNIS=ZoneInfo("Africa/Tunis")
QATAR=ZoneInfo("Asia/Qatar")

def dt(day,h,m):
    return datetime(day.year,day.month,day.day,h,m,tzinfo=TUNIS)

def source_file(path, midday_extra_gap=False):
    now=datetime.now(TUNIS)
    tv=ET.Element("tv")
    for cid in ("ElWatania1.tn@SD","ElWatania2.tn@SD"):
        ET.SubElement(ET.SubElement(tv,"channel",{"id":cid}),"display-name").text=cid
    for offset in range(-1,5):
        day=(now+timedelta(days=offset)).date()
        for cid,start,stop in [
            ("ElWatania1.tn@SD",dt(day,0,0),dt(day,23,59)),
            ("ElWatania2.tn@SD",dt(day,6,0),dt(day,23,59)),
        ]:
            periods=[(start,stop)]
            if midday_extra_gap and cid=="ElWatania2.tn@SD" and offset==1:
                periods=[(start,dt(day,10,0)),(dt(day,15,0),stop)]
            for a,b in periods:
                p=ET.SubElement(tv,"programme",{"channel":cid,
                    "start":a.strftime("%Y%m%d%H%M%S %z"),
                    "stop":b.strftime("%Y%m%d%H%M%S %z")})
                ET.SubElement(p,"title",{"lang":"ar"}).text="برنامج تلفزيوني حقيقي"
    path.write_bytes(ET.tostring(tv,encoding="utf-8"))

class FakePage:
    status_code=200
    def __init__(self,html):self.text=html
    def raise_for_status(self):pass
class FakeSession:
    def __init__(self,html):self.html=html
    def get(self,*a,**k):return FakePage(self.html)

class TestTunisNightAndAlKassRealSchedule(unittest.TestCase):
    def test_watania2_overnight_only_is_explicit_info_not_fake_programming(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"tunisiatv.xml.gz"
            # The fixture is XML even if path ends .gz; root() accepts gzip
            # only when suffix gz, so write valid compressed XMLTV.
            import gzip
            raw=f.with_suffix(".xml")
            source_file(raw)
            f.write_bytes(gzip.compress(raw.read_bytes(),mtime=0))
            rep=audit(f,48,90)
            rows={r["id"]:r for r in rep["rows"]}
            self.assertEqual(rows["ElWatania2.tn@SD"]["status"],"INFO",rows["ElWatania2.tn@SD"])
            self.assertEqual(rows["ElWatania2.tn@SD"]["reason"],
                             "OFF_AIR_OVERNIGHT_0000_0600_TUNIS")
            self.assertTrue(rows["ElWatania2.tn@SD"]["verified_regular_off_air"])
            self.assertGreater(rows["ElWatania2.tn@SD"]["max_gap_minutes"],350)
            self.assertEqual(rep["orphan_programmes"],0)
            self.assertEqual(rep["invalid_times"],0)
            self.assertEqual(rep["status"],"PASS")

    def test_unrelated_midday_gap_still_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"tunisiatv.xml.gz"
            import gzip
            f=p.with_suffix(".xml")
            source_file(f,midday_extra_gap=True)
            p.write_bytes(gzip.compress(f.read_bytes(),mtime=0))
            rep=audit(p,48,90)
            row=next(x for x in rep["rows"] if x["id"]=="ElWatania2.tn@SD")
            self.assertEqual(row["status"],"WARN")
            self.assertEqual(row["reason"],"INTERNAL_GAP")
            self.assertFalse(row["verified_regular_off_air"])

    def test_al_kass_parser_keeps_all_eight_channel_ids_and_real_only(self):
        html="".join(
            '<table class="team-result"><tr><td class="tv-prog-time">06:00</td>'
            f'<td>{i} مباريات اليوم</td></tr>'
            '<tr><td class="tv-prog-time">10:00</td>'
            '<td>News</td></tr></table>' for i in range(8)
        )
        day=datetime.now(QATAR).date()
        fetched=parse_page(FakeSession(html),"https://dirorigin.alkass.net/tvguide",day)
        self.assertEqual(set(fetched),{cid for cid,_ in CHANNELS})
        self.assertTrue(all(len(x)==2 for x in fetched.values()))
        self.assertTrue(all(x[0]["start"].hour==6 for x in fetched.values()))
        self.assertTrue(all(x[0]["start"].utcoffset()==timedelta(hours=3)
                            for x in fetched.values()))

if __name__=="__main__":
    unittest.main()
