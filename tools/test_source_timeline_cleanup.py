"""Real schedule collisions: preserve provider data without inventing times."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch,Mock
import xml.etree.ElementTree as ET

from tools import morocco_epg as morocco
from tools.audit_source_quality import analyze
from tools.source_feed_pack import parse_xmltv_dt
from tools.source_timeline import same_start_duplicates
from tools.source_health_snapshot import inspect
from tools.test_bundle_fixture import write_bundle_metadata
from tools.clean_published_source import clean_payload
from tools.test_mena_readiness_audit import fixture,check
from tools.test_mena_source_snapshot import rebuild


def event(start='20261104000000 +0000', stop='20261104004400 +0000', title='Episode 7', desc='Official synopsis'):
    node=ET.Element('programme',channel='real.channel',start=start,stop=stop)
    ET.SubElement(node,'title',lang='ar').text=title
    ET.SubElement(node,'desc',lang='ar').text=desc
    return node


class SourceTimelineCleanup(unittest.TestCase):
    def test_duplicate_start_with_different_stop_keeps_an_observed_interval(self):
        short=event();long=event(stop='20261104004500 +0000')
        before=[ET.tostring(n) for n in (long,short)]
        kept,dropped=same_start_duplicates([long,short],parse_xmltv_dt)
        self.assertEqual(kept,[short]);self.assertEqual(dropped,[long])
        self.assertEqual([ET.tostring(n) for n in (long,short)],before)
        self.assertEqual(same_start_duplicates(kept,parse_xmltv_dt),(kept,[]))

    def test_distinct_content_or_start_is_never_silently_chosen(self):
        nodes=[event(),event(desc='A different provider synopsis'),event(title='Another programme'),
               event(start='20261105000000 +0000',stop='20261105004400 +0000')]
        self.assertEqual(same_start_duplicates(nodes,parse_xmltv_dt),(nodes,[]))

    def test_published_cleanup_preserves_ids_age_and_exact_second_pass_bytes(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        fmt=lambda d:d.strftime('%Y%m%d%H%M%S %z')
        tv=ET.Element('tv');ET.SubElement(ET.SubElement(tv,'channel',id='real.channel'),'display-name').text='Real Channel'
        tv.append(event(fmt(now),fmt(now+timedelta(minutes=44))))
        tv.append(event(fmt(now),fmt(now+timedelta(minutes=45))))
        raw=gzip.compress(ET.tostring(tv));digest=hashlib.sha256(raw).hexdigest()
        meta={'label':'shahid','sha256':digest,'size_bytes':len(raw),'programmes':2,'channels':1,'generated_utc':(now-timedelta(hours=3)).isoformat()}
        output,info,proof=clean_payload('shahid',raw,meta,digest,now)
        self.assertEqual(proof['removed_same_start_duplicates'],1)
        self.assertEqual(info['programmes'],1)
        self.assertEqual(info['generated_utc'],meta['generated_utc'])
        self.assertEqual(info['channels'],1)
        again,again_info,again_proof=clean_payload('shahid',output,info,info['sha256'],now)
        self.assertEqual(again,output);self.assertEqual(again_info,info)
        self.assertFalse(again_proof['changed'])
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);(folder/'input.xml').write_bytes(ET.tostring(tv))
            command=[sys.executable,str(Path(__file__).with_name('source_feed_pack.py')),
                     '--input',str(folder/'input.xml'),'--output-gz',str(folder/'feed.xml.gz'),
                     '--output-txt',str(folder/'feed.txt'),'--stats',str(folder/'feed.json'),'--label','shahid']
            result=subprocess.run(command,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            stats=json.loads((folder/'feed.json').read_text())
            self.assertEqual(stats['programmes'],1)
            self.assertEqual(stats['removed_same_start_duplicates'],1)

    def test_quality_and_live_health_flag_unresolved_overlap(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        tv=ET.Element('tv');ET.SubElement(ET.SubElement(tv,'channel',id='real.channel'),'display-name').text='Real Channel'
        fmt=lambda d:d.strftime('%Y%m%d%H%M%S %z')
        tv.append(event(fmt(now-timedelta(minutes=10)),fmt(now+timedelta(hours=30))))
        tv.append(event(fmt(now+timedelta(hours=24)),fmt(now+timedelta(hours=49)),title='Other real show'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'feeds/shahid.xml.gz';path.parent.mkdir()
            path.write_bytes(gzip.compress(ET.tostring(tv)));write_bundle_metadata(path)
            quality=analyze(path,'shahid',now);health=inspect(directory,'shahid',now)
        self.assertEqual(quality['channels_with_overlapping_programmes'],{'real.channel':1})
        self.assertEqual(quality['quality_status'],'WARN')
        self.assertEqual(health['status'],'DEGRADED')
        self.assertEqual(health['future_programmes_overlapping_previous'],1)

    def test_2m_leading_evening_belongs_to_previous_civil_day(self):
        def card(clock,title):
            return f'<a class="js-tv-grid-card" href="#">Magazine {title} {clock}</a>'
        html='<section class="tv-grid__card-container-left--channel">'+''.join((
            card('23h35','Repeated Show'),card('01h05','Night Show'),card('23h35','Repeated Show')))
        html+='</section><nav><a>Magazine Wrong Channel 12h00</a></nav>'
        day=datetime(2026,11,4).date()
        with patch.object(morocco,'tr2m_title',side_effect=lambda h,t:t),patch.object(morocco,'tr2m_desc',return_value='Synopsis'):
            rows=morocco.parse_2m(None,html,day)
        self.assertEqual([e.start.isoformat() for e in rows],[
            '2026-11-03T22:35:00+00:00','2026-11-04T00:05:00+00:00','2026-11-04T22:35:00+00:00'])
        self.assertEqual([e.title for e in rows],['Repeated Show','Night Show','Repeated Show'])

    def test_overlapping_candidate_stays_degraded_in_derived_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            _,_,now=fixture(directory)
            path=Path(directory)/'feeds/shahid.xml.gz'
            tv=ET.fromstring(gzip.decompress(path.read_bytes()))
            duplicate=ET.fromstring(ET.tostring(tv.find('programme')))
            duplicate.find('title').text='برنامج مختلف في التوقيت نفسه'
            tv.append(duplicate);path.write_bytes(gzip.compress(ET.tostring(tv)))
            write_bundle_metadata(path)
            rebuild(Path(directory));report=check(directory,now)
            selected=next(row for row in report['ids'] if row['xmltv_id']=='shahid.example@SD')
        self.assertEqual(selected['status'],'DEGRADED_WITH_EPG')
        self.assertFalse(selected['best_match_green_allowed'])
        self.assertEqual(selected['selected_source_overlap_rows'],1)

    def test_chada_intro_clock_is_not_an_extra_show(self):
        html='<p>La journee commence a 01:00 avec Chada Asks.</p><ul><li><div><strong>01:00</strong> - Chada Asks</div></li><li><div><strong>01:30</strong> - Zoom</div></li></ul>'
        rows=morocco._parse_chada_piisas(html,datetime(2026,11,4).date())
        self.assertEqual(len(rows),2)
        self.assertFalse(any(e.title.casefold()=='avec' for e in rows))
        self.assertEqual(rows[0].stop,rows[1].start)

    def test_morocco_scheduled_cli_does_not_skip_0707_or_delayed_jobs(self):
        collectors={name:Mock(return_value=[]) for name in ('scrape_snrt','scrape_arryadia','scrape_2m','scrape_chada','scrape_medi1')}
        clock=Mock(wraps=datetime);clock.now.return_value=datetime(2026,11,3,7,7,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory,patch.multiple(morocco,**collectors),patch.object(morocco,'datetime',clock),patch.object(morocco,'log'),patch.object(sys,'argv',['morocco_epg.py','--scheduled','--output-dir',directory]):
            self.assertEqual(morocco.main(),2)  # No EPG keeps LKG; collection was attempted.
        for collector in collectors.values():collector.assert_called_once()


if __name__=='__main__':unittest.main()
