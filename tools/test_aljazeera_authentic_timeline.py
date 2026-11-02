# -*- coding: utf-8 -*-
"""Real-source safe Al Jazeera timing and no fictional Mubasher/Documentary."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import unittest
from zoneinfo import ZoneInfo

from tools import aljazeera_source as src


class TestAlJazeeraAuthenticity(unittest.TestCase):
    def test_utc_graphql_date_not_shifted_by_doha_at_2300(self):
        original=datetime(2026,10,18,23,0,tzinfo=timezone.utc)
        data={"data":{"schedule":{"items":[
           {"showTimeslot":"23:00","showName":"نشرة الأخبار",
            "startDate":str(int(original.timestamp())),"duration":"00:30:00",
            "showDescription":"الخبر"}
        ]}}}
        rows=src.parse_graphql_schedule(data,date(2026,10,18))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["start"],original)
        self.assertEqual(rows[0]["stop"],original+timedelta(minutes=30))

    def test_arabic_rendered_schedule_uses_doha_not_receiver_clock(self):
        text='<div>09:00</div><div>نشرة الأخبار</div><div>ملخص</div><div>10:00</div><div>حلقة أخرى</div>'
        entries=src.parse_schedule(text,date(2026,10,18))
        self.assertEqual(entries[0]["start"].hour,9)
        self.assertEqual(entries[0]["start"].utcoffset(),timedelta(hours=3))
        self.assertEqual(src.fmt_utc(entries[0]["start"]),"20261018060000 +0000")

    def test_do_not_extend_graphql_duration_to_next_show(self):
        base=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
        first=base
        second=base+timedelta(hours=3)
        def mock_graphql(session, wp_site, post_name, timeout, default_day):
            lang="en" if wp_site=="aje" else "ar"
            return [
                {"start":first,"stop":first+timedelta(minutes=25),
                 "title":"First "+lang,"desc":"Authentic 1"},
                {"start":second,"stop":second+timedelta(minutes=40),
                 "title":"Next "+lang,"desc":"Authentic 2"},
            ],200,"https://www.aljazeera.com/graphql"
        def mock_fetch(session,url,timeout):
            self.assertIn("/video/live",url)
            return "<html><body><h1>Official live</h1></body></html>",200
        with patch.object(src,"fetch",side_effect=mock_fetch) as ff,\
             patch.object(src,"fetch_graphql_schedule",side_effect=mock_graphql):
            root,stats=src.build_source(48,1)
        self.assertEqual(ff.call_count,5)
        self.assertEqual(stats["active_channels"],3)
        self.assertEqual(stats["programmes"],6)
        self.assertEqual(set(stats["zero_epg_ids"]),{
            "AlJazeeraMubasher.qa@SD","AlJazeeraDocumentary.qa@SD"})
        for id_ in ("AlJazeera.qa@Arabic","AlJazeera2.qa@HD","AlJazeera.qa@English"):
            programmes=[p for p in root.findall("programme") if p.get("channel")==id_]
            self.assertEqual(len(programmes),2)
            self.assertEqual(programmes[0].get("stop"),src.fmt_utc(first+timedelta(minutes=25)))
            self.assertEqual(programmes[1].get("start"),src.fmt_utc(second))
            self.assertEqual(programmes[1].get("stop"),src.fmt_utc(second+timedelta(minutes=40)))
        for id_ in ("AlJazeeraMubasher.qa@SD","AlJazeeraDocumentary.qa@SD"):
            report=stats["channel_reports"][id_]
            self.assertEqual(report["guide_status"],"NO_VERIFIED_OFFICIAL_SCHEDULE_ENDPOINT")
            self.assertEqual(report["programmes"],0)
            self.assertNotIn("schedule_errors",report)
        self.assertEqual(len(root.findall("channel")),5)

    def test_positive_overlap_only_clamps_not_overwrites_short_duration(self):
        base=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
        def mock_graphql(session, wp_site, post_name, timeout, default_day):
            return [
                {"start":base,"stop":base+timedelta(hours=2),"title":"Long"},
                {"start":base+timedelta(hours=1),"stop":base+timedelta(hours=2),"title":"Second"},
            ],200,"https://www.aljazeera.com/graphql"
        with patch.object(src,"fetch",return_value=("<html></html>",200)),\
             patch.object(src,"fetch_graphql_schedule",side_effect=mock_graphql):
            root,stats=src.build_source(48,1)
        programmes=[p for p in root.findall("programme") if p.get("channel")=="AlJazeera.qa@Arabic"]
        self.assertEqual(len(programmes),2)
        self.assertEqual(programmes[0].get("stop"),src.fmt_utc(base+timedelta(hours=1)))


if __name__=="__main__":
    unittest.main()
