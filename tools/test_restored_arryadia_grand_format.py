from datetime import datetime,timedelta,timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET
import requests
from tools import morocco_epg as m
from tools.arryadia_hourly_grand_format import generate_hourly,IDS,SOURCE
from tools.programme_authenticity import viewer_reported_schedule
from tools.audit_source_quality import analyze
from tools.test_mena_readiness_audit import fixture,check
from tools.test_mena_source_snapshot import rebuild

ROOT=Path(__file__).resolve().parents[1]

class RestoredGrandFormat(unittest.TestCase):
    def test_two_channels_have_24_hourly_slots_each_and_no_other_channel(self):
        now=datetime.now(m.TZ)
        rows=generate_hourly(2,now)
        self.assertEqual(len(rows),96)
        self.assertEqual({e.channel for e in rows},IDS)
        self.assertTrue(all(e.stop-e.start==timedelta(hours=1) and e.title=='Grand Format' and e.source==SOURCE for e in rows))

    def test_announced_programme_overrides_complete_intersecting_hours(self):
        now=datetime.now(m.TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        event=m.Event('Arryadia_HD2',now+timedelta(hours=11,minutes=30),'Official programme','Official description',now+timedelta(hours=13,minutes=20),source='arryadia-snrt')
        rows=generate_hourly(1,now,[event])
        self.assertEqual(sum(e.channel=='Arryadia_HD2' for e in rows),21)
        self.assertEqual(sum(e.channel=='Arryadia_HD3' for e in rows),24)
        self.assertFalse(any(e.channel==event.channel and e.start<event.stop and e.stop>event.start for e in rows))

    def test_default_collector_restores_user_schedule_during_official_outage(self):
        http=Mock();http.get.side_effect=requests.HTTPError()
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'Http',return_value=http):
            report=Path(tmp)/'report.json'
            rows=m.scrape_arryadia(3,report_path=report,include_smart=False)
            data=json.loads(report.read_text())
        self.assertTrue(rows);self.assertEqual({e.channel for e in rows},IDS)
        self.assertEqual(data['official_future_events'],0)
        self.assertGreater(data['viewer_reported_hourly_events'],0)
        self.assertEqual(data['retained_exact_lkg'],0)

    def test_provenance_survives_xml_lkg_pack_and_derived_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,_,now=fixture(root)
            raw=root/'morocco.xml'
            m.to_xml(generate_hourly(3,now),raw)
            saved=m.previous(raw)
            self.assertTrue(saved);self.assertTrue(all(e.source==SOURCE for e in saved))
            result=subprocess.run([sys.executable,str(ROOT/'tools/source_feed_pack.py'),
                '--input',str(raw),'--output-gz',str(root/'feeds/morocco.xml.gz'),
                '--output-txt',str(root/'feeds/morocco.txt'),'--stats',str(root/'feeds/morocco.json'),
                '--label','morocco'],capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            report=analyze(root/'feeds/morocco.xml.gz','morocco',now)
            self.assertEqual(report['viewer_reported_recurring_channels'],sorted(IDS))
            self.assertGreater(report['viewer_reported_recurring_programmes'],0)
            self.assertEqual(report['suspected_placeholder_channels'],[])
            self.assertEqual(report['suspected_repeated_title_channels'],[])
            _,xml=rebuild(root)
            for cid in IDS:
                events=[p for p in xml.findall('programme') if p.get('channel')==cid]
                self.assertTrue(events)
                self.assertTrue(all(viewer_reported_schedule(p) for p in events))
            ready=check(root,now)
            for cid in IDS:
                row=next(r for r in ready['ids'] if r['xmltv_id']==cid)
                self.assertEqual(row['status'],'DEGRADED_WITH_EPG')
                self.assertFalse(row['best_match_green_allowed'])

    def test_same_source_cannot_enable_hourly_programme_on_hd1(self):
        now=datetime.now(m.TZ)
        event=generate_hourly(1,now)[0];event.channel='Arryadia_HD1'
        with tempfile.TemporaryDirectory() as tmp,self.assertRaisesRegex(ValueError,'Unauthorized'):
            m.to_xml([event],Path(tmp)/'bad.xml')

    def test_old_generated_hours_do_not_block_new_official_rows(self):
        now=datetime.now(m.TZ).replace(minute=0,second=0,microsecond=0)
        old=m.Event('Arryadia_HD2',now,'Grand Format','User observation',now+timedelta(hours=1),'fr','ar',SOURCE)
        real=m.Event('Arryadia_HD2',now,'Official match','Current announcement',now+timedelta(hours=2),source='arryadia-snrt')
        kept=m._arryadia_fill([real],history=[old])
        self.assertEqual(kept,[real])

if __name__=='__main__':unittest.main()
