"""Regressions from the immutable 23 October production-feed audit."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from tools.test_mena_readiness_audit import fixture, check
from tools.test_mena_source_snapshot import rebuild
from tools import morocco_epg as morocco
from tools.audit_source_quality import analyze
from tools.source_health_snapshot import inspect


def replace_feed(root, source, cid, rows, name=None):
    tv=ET.Element('tv')
    ET.SubElement(ET.SubElement(tv,'channel',id=cid),'display-name').text=name or cid
    for start,stop,title,desc in rows:
        p=ET.SubElement(tv,'programme',channel=cid,
            start=start.strftime('%Y%m%d%H%M%S %z'),stop=stop.strftime('%Y%m%d%H%M%S %z'))
        ET.SubElement(p,'title').text=title
        if desc:ET.SubElement(p,'desc').text=desc
    data=gzip.compress(ET.tostring(tv,encoding='utf-8'),mtime=0)
    base=Path(root)/'feeds'
    (base/(source+'.xml.gz')).write_bytes(data)
    (base/(source+'.json')).write_text(json.dumps({'label':source,'channels':1,
        'programmes':len(rows),'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
        'generated_utc':datetime.now(timezone.utc).isoformat()}))


class MoroccoIsolation(unittest.TestCase):
    def event(self,cid='2M',title='عنوان عربي',offset=0):
        now=morocco.datetime.now(morocco.TZ)
        return morocco.Event(cid,now+timedelta(hours=offset),title,'وصف حقيقي',
            now+timedelta(hours=offset+1),'ar','ar','official')

    def test_genuine_french_2m_news_and_following_weather_accepted(self):
        rows=[self.event(offset=i) for i in range(18)]
        rows += [self.event(title='Info Soir',offset=18),self.event(title='Météo',offset=19)]
        self.assertTrue(morocco.valid('2m',rows)[0])

    def test_french_news_title_preserved_without_translation(self):
        self.assertEqual(morocco.tr2m_title(None,'Info Soir'),'Info Soir')

    def test_exhausted_http_budget_does_not_issue_more_requests(self):
        client=morocco.Http.__new__(morocco.Http)
        client.deadline=0
        with self.assertRaises(TimeoutError):
            client.get('https://example.invalid/')

    def test_two_unknown_latin_shows_still_require_review(self):
        rows=[self.event(offset=i) for i in range(18)]
        rows += [self.event(title='Unknown show',offset=18),self.event(title='Untranslated show',offset=19)]
        self.assertFalse(morocco.valid('2m',rows)[0])

    def test_failed_2m_does_not_freeze_snrt_and_exact_lkg_retained(self):
        fresh=self.event('AlAoula')
        old=self.event('Tamazight',offset=2)
        rows,status=morocco.select_groups({'snrt':[fresh]},[old])
        self.assertEqual({e.channel for e in rows},{'AlAoula','Tamazight'})
        self.assertEqual(status['2m']['mode'],'missing')
        self.assertEqual(old.start,next(e.start for e in rows if e.channel=='Tamazight'))

    def test_no_arryadia_gap_or_recurrence_created(self):
        e=self.event('Arryadia_HD')
        out=morocco._arryadia_fill([e],history=[])
        self.assertEqual(out,[e])
        self.assertEqual(morocco._arryadia_fill([],history=[]),[])

    def test_generated_old_lkg_cannot_reappear(self):
        fake=self.event('Arryadia_HD','Arryadia Programme')
        rows,_=morocco.select_groups({},[fake])
        self.assertEqual(rows,[])

    def test_xml_preserves_catalogue_without_fake_aflam(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'out.xml'
            morocco.to_xml([self.event('AFLAM.ma','برامج قناة السابعة AFLAM')],path)
            xml=ET.parse(path)
            self.assertEqual(len(xml.findall('channel')),len(morocco.CHANNELS))
            self.assertEqual(xml.findall('programme'),[])


class DerivedQuality(unittest.TestCase):
    def test_explicit_generated_rows_removed_but_id_visible(self):
        with tempfile.TemporaryDirectory() as d:
            _,_,now=fixture(d)
            replace_feed(d,'morocco','Arryadia_HD',[(now,now+timedelta(hours=24),'Arryadia Programme','وصف')])
            _,xml=rebuild(Path(d))
            report=check(d,now)
            row=next(x for x in report['ids'] if x['xmltv_id']=='Arryadia_HD')
            self.assertEqual(row['status'],'NO_FUTURE_EPG')
            self.assertFalse(row['best_match_green_allowed'])
            self.assertIn('Arryadia_HD',{c.get('id') for c in xml.findall('channel')})
            self.assertTrue(analyze(Path(d)/'feeds/morocco.xml.gz','morocco',now)['suspected_placeholder_channels'])

    def test_suspect_channel_name_loop_is_not_green(self):
        with tempfile.TemporaryDirectory() as d:
            _,_,now=fixture(d)
            replace_feed(d,'stctv','stctv.example@SD',[(now,now+timedelta(hours=24),'Channel Placeholder','وصف')],name='Channel Placeholder')
            rebuild(Path(d))
            row=next(x for x in check(d,now)['ids'] if x['xmltv_id']=='stctv.example@SD')
            self.assertEqual(row['status'],'DEGRADED_WITH_EPG')
            self.assertTrue(row['suspected_placeholder'])
            self.assertFalse(row['best_match_green_allowed'])

    def test_natgeo_english_title_cannot_enter_derived_feed(self):
        with tempfile.TemporaryDirectory() as d:
            _,_,now=fixture(d)
            cid='NationalGeographicAbuDhabi.ae@SD'
            replace_feed(d,'starzplay',cid,[(now,now+timedelta(hours=12),'English-only title','وصف عربي')])
            _,xml=rebuild(Path(d))
            self.assertFalse(any(p.get('channel')==cid for p in xml.findall('programme')))
            row=next(x for x in check(d,now)['ids'] if x['xmltv_id']==cid)
            self.assertFalse(row['best_match_green_allowed'])

    def test_genuine_degraded_backup_extends_preferred_without_green(self):
        with tempfile.TemporaryDirectory() as d:
            _,_,now=fixture(d)
            cid='AlkassThree.qa@SD'
            replace_feed(d,'bein',cid,[(now,now+timedelta(hours=5),'عنوان أصلي','وصف عربي')])
            replace_feed(d,'alkass',cid,[(now+timedelta(hours=5),now+timedelta(hours=30),'برنامج أصلي','')])
            _,xml=rebuild(Path(d))
            self.assertEqual(len([p for p in xml.findall('programme') if p.get('channel')==cid]),2)
            row=next(x for x in check(d,now)['ids'] if x['xmltv_id']==cid)
            self.assertEqual(row['status'],'DEGRADED_WITH_EPG')
            self.assertFalse(row['best_match_green_allowed'])

    def test_derived_times_are_utc_without_changing_instant(self):
        with tempfile.TemporaryDirectory() as d:
            _,_,now=fixture(d)
            _,xml=rebuild(Path(d))
            for p in xml.findall('programme'):
                self.assertTrue(p.get('start').endswith('+0000'))
                self.assertTrue(p.get('stop').endswith('+0000'))

    def test_fresh_lkg_decision_cannot_hide_expired_programmes(self):
        with tempfile.TemporaryDirectory() as d:
            _,_,now=fixture(d)
            replace_feed(d,'morocco','2M',[(now-timedelta(days=2),now-timedelta(days=1),'برنامج حقيقي','وصف')])
            (Path(d)/'reports/morocco-publish-lkg.json').write_text(json.dumps({
                'generated_utc':now.isoformat(),'sources':{'morocco':{'decision':'KEEP_LKG'}}}))
            state=inspect(d,'morocco',now)
            self.assertEqual(state['status'],'NO_FUTURE_EPG')
            self.assertTrue(state['last_attempt_kept_lkg'])
            self.assertEqual(state['channels_without_future_epg'],['2M'])


if __name__=='__main__':
    unittest.main()
