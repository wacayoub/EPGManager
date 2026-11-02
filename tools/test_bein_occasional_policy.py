# -*- coding: utf-8 -*-
"""An occasional sports channel is not a 24/7 EPG channel."""
import gzip
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from tools.bein_occasional_policy import is_occasional_bein
from tools import validate_48h, audit_source_quality

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)

class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz or timezone.utc)

def stamp(dt):
    return dt.strftime("%Y%m%d%H%M%S +0000")

def feed(where):
    tv=ET.Element("tv")
    channel_names = {
        "beINSportsMax1.qa@MENA": "beIN SPORTS MAX 1",
        "beINSportsXtra2.qa@SD": "beIN SPORTS XTRA 2",
        "beINMENA.beinsportsxtra7": "beIN SPORTS XTRA 7",
        "beINSportsAFC1.qa@MENA": "beIN SPORTS AFC 1",
        "beINSports1.qa@MENA": "beIN SPORTS 1",
        "beINSportsMax2.qa@MENA": "beIN SPORTS MAX 2",
    }
    for cid,name in channel_names.items():
        chan=ET.SubElement(tv,'channel',id=cid)
        ET.SubElement(chan,'display-name').text=name
    event=ET.SubElement(tv,'programme',channel='beINSportsXtra2.qa@SD',
                       start=stamp(NOW+timedelta(hours=3)),
                       stop=stamp(NOW+timedelta(hours=5)))
    ET.SubElement(event,'title').text="Real Fixture - Live"
    ET.SubElement(event,'desc').text="Match confirmé"
    # Deliberate full-length fake channel-name schedule: must still be flagged.
    for h in range(12):
        fake=ET.SubElement(tv,'programme',channel='beINSportsMax2.qa@MENA',
                           start=stamp(NOW+timedelta(hours=h)),
                           stop=stamp(NOW+timedelta(hours=h+1)))
        ET.SubElement(fake,'title').text="beIN SPORTS MAX 2"
        ET.SubElement(fake,'desc').text="دليل البرامج غير متوفر"
    f=Path(where)/'bein.xml.gz'
    f.write_bytes(gzip.compress(ET.tostring(tv,encoding='utf-8'),mtime=0))
    return f

class OccasionalBein(unittest.TestCase):
    def test_exact_event_ids_not_regular_sports(self):
        for cid in ("beINSportsMax1.qa@MENA","beINSportsXtra2.qa@SD",
                    "beINMENA.beinsportsxtra7","beINSportsAFC1.qa@MENA"):
            self.assertTrue(is_occasional_bein(cid),cid)
        for cid in ("beINSports1.qa@MENA","beINSportsNews.qa@SD",
                    "beINSports4KHDR.qa","AlkassOne.qa@SD","beINMovies1Premiere.qa@SD"):
            self.assertFalse(is_occasional_bein(cid),cid)
        self.assertTrue(is_occasional_bein(
            'beINMENA.newafcevent',('beIN SPORTS AFC 3',)))
        self.assertFalse(is_occasional_bein('SomethingElse.afc',('beIN SPORTS AFC 3',)))

    def test_no_event_info_not_failure(self):
        with tempfile.TemporaryDirectory() as d,patch.object(validate_48h,'datetime',FixedDatetime):
            f=feed(d)
            a=validate_48h.audit(f,48,90)
            by={r['id']:r for r in a['rows']}
            self.assertEqual(by['beINSportsMax1.qa@MENA']['status'],'INFO')
            self.assertEqual(by['beINSportsMax1.qa@MENA']['reason'],'OCCASIONAL_NO_EVENT')
            self.assertEqual(by['beINSportsAFC1.qa@MENA']['status'],'INFO')
            self.assertEqual(by['beINSportsXtra2.qa@SD']['status'],'INFO')
            self.assertEqual(by['beINSportsXtra2.qa@SD']['programmes'],1)
            self.assertEqual(by['beINSports1.qa@MENA']['status'],'FAIL')
            self.assertEqual(a['failed_channels'],1)
            self.assertEqual(a['informational_channels'],5)
            self.assertEqual(a['invalid_times'],0)

    def test_quality_retains_fake_programme_alarm(self):
        with tempfile.TemporaryDirectory() as d:
            f=feed(d)
            q=audit_source_quality.analyze(f,'bein',NOW)
            self.assertIn('beINSportsMax1.qa@MENA',q['occasional_event_channels_without_future_match'])
            self.assertIn('beINSportsAFC1.qa@MENA',q['occasional_event_channels_without_future_match'])
            self.assertNotIn('beINSportsMax1.qa@MENA',q['channels_with_shorter_than_24h'])
            self.assertNotIn('beINSportsXtra2.qa@SD',q['channels_with_shorter_than_24h'])
            self.assertIn('beINSports1.qa@MENA',q['channels_with_shorter_than_24h'])
            bad={r['id'] for r in q['suspected_placeholder_channels']}
            self.assertIn('beINSportsMax2.qa@MENA',bad)
            self.assertIn('beINSportsMax2.qa@MENA',q['occasional_event_channels_with_repeated_titles'])
            self.assertNotIn('beINSportsMax2.qa@MENA',q['suspected_repeated_title_channels'])
            self.assertEqual(q['future_programmes_by_channel']['beINSportsMax2.qa@MENA'],12)

    def test_other_source_never_gets_bein_exemption(self):
        with tempfile.TemporaryDirectory() as d:
            f=feed(d)
            q=audit_source_quality.analyze(f,'other',NOW)
            self.assertEqual(q['occasional_event_channel_ids'],[])
            self.assertIn('beINSportsMax1.qa@MENA',q['channels_with_shorter_than_24h'])

if __name__=='__main__':
    unittest.main()
