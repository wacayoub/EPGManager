"""HD2/HD3 reported Grand Format every hour; never impersonate broadcaster."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import morocco_epg as base
import arryadia_hourly_grand_format as grand
import morocco_cloud_final as cloud
import morocco_cloud_runner as runner
import arryadia_fixture_candidates as fixture

TZ=ZoneInfo("Africa/Casablanca")
NOW=datetime(2026,10,17,10,15,tzinfo=TZ)


class ViewerReportedHourly(unittest.TestCase):
    def test_exact_24_one_hour_slots_on_each_channel_per_day(self):
        rows=grand.generate_hourly(days=2,now=NOW)
        self.assertEqual(len(rows),96)
        for cid in grand.IDS:
            channel=sorted([e for e in rows if e.channel==cid],key=lambda x:x.start)
            self.assertEqual(len(channel),48)
            self.assertEqual([e.start.hour for e in channel[:24]],list(range(24)))
            self.assertEqual([e.start.hour for e in channel[24:]],list(range(24)))
            for e in channel:
                self.assertEqual(e.title,"Grand Format")
                self.assertEqual(e.stop-e.start,timedelta(hours=1))
                self.assertTrue(grand.is_viewer_report(e,NOW))
                self.assertEqual((e.tl,e.dl),("fr","ar"))
                self.assertEqual(e.source,grand.SOURCE)

    def test_real_programme_overrides_intersecting_hours(self):
        real=base.Event(
            "Arryadia_HD2",NOW.replace(hour=11,minute=30),
            "لقاء رياضي", "مباشر",NOW.replace(hour=13,minute=20),
            "ar","ar","arryadia-current-official"
        )
        rows=grand.generate_hourly(days=1,now=NOW,verified=[real])
        hd2=[e for e in rows if e.channel=="Arryadia_HD2"]
        hd3=[e for e in rows if e.channel=="Arryadia_HD3"]
        self.assertEqual(len(hd2),21)
        self.assertEqual(len(hd3),24)
        self.assertNotIn(11,[e.start.hour for e in hd2])
        self.assertNotIn(12,[e.start.hour for e in hd2])
        self.assertNotIn(13,[e.start.hour for e in hd2])

    def test_provenance_prevents_generics_on_other_channels(self):
        rows=grand.generate_hourly(days=1,now=NOW)
        self.assertEqual(set(e.channel for e in rows),grand.IDS)
        bad=rows[0]
        bad.source="arryadia-current-official"
        self.assertFalse(grand.is_viewer_report(bad,NOW))

    def test_writer_keeps_reported_and_rejects_unauthorized(self):
        previous=runner.write_xml
        try:
            runner.write_xml=lambda rows,path:list(rows)
            cloud._install_strict_writer()
            now=datetime.now(TZ)
            reported=grand.generate_hourly(days=1,now=now)[0]
            core=[
                base.Event("2M",now,"أخبار","نشرة",now+timedelta(hours=1),"ar","ar","2m"),
                base.Event("MEDI1TV_AR.ma",now,"نشرة","أخبار",now+timedelta(hours=1),"ar","ar","medi1")
            ]
            self.assertIn(reported,runner.write_xml(core+[reported],"unused"))
            fake=base.Event(
                "Arryadia_HD1",reported.start,"Grand Format","اختبار",
                reported.stop,"fr","ar",grand.SOURCE
            )
            with self.assertRaisesRegex(RuntimeError,"Arryadia EPG provenance"):
                runner.write_xml(core+[fake],"unused")
        finally:
            runner.write_xml=previous

    def test_live_scanner_does_not_touch_hd2_hd3(self):
        self.assertEqual(set(fixture.SOURCES),{"Arryadia_HD1"})
        self.assertEqual(fixture.EXCLUDED_MATCH_CHANNELS,grand.IDS)


if __name__=="__main__":
    unittest.main()
