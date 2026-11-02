"""Offline HTTP, outage, scheduling and concurrent-publication regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import requests
import yaml
from tools import morocco_epg as m
from tools.morocco_run_timing import observation

ROOT=Path(__file__).resolve().parents[1]


class Response:
    def __init__(self,status,headers=None):
        self.status_code=status; self.headers=headers or {}; self.closed=False
    def raise_for_status(self):
        if self.status_code>=400: raise requests.HTTPError(response=self)
    def close(self): self.closed=True


class Clock:
    def __init__(self): self.now=100.; self.delays=[]
    def monotonic(self): return self.now
    def sleep(self,delay): self.delays.append(delay); self.now+=delay


class MoroccoRecovery(unittest.TestCase):
    def client(self,replies,budget=90):
        client=m.Http.__new__(m.Http); client.deadline=100+budget
        client.s=types.SimpleNamespace(get=Mock(side_effect=replies))
        return client

    def test_503_retries_use_backoff_then_recover(self):
        clock=Clock(); replies=[Response(503),Response(503),Response(200)]
        client=self.client(replies)
        with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.time,'sleep',clock.sleep):
            self.assertIs(client.get('https://www.snrt.ma/ar/node/1208'),replies[-1])
        self.assertEqual(clock.delays,[3.,6.]); self.assertTrue(replies[0].closed)

    def test_retry_after_honored_but_bounded(self):
        self.assertEqual(m._snrt_retry_delay(Response(503,{'Retry-After':'12'}),0),12)
        self.assertEqual(m._snrt_retry_delay(Response(503,{'Retry-After':'3600'}),0),3600)
        self.assertEqual(m._snrt_retry_delay(Response(503,{'Retry-After':'invalid'}),1),6)
        self.assertEqual(m._snrt_retry_delay(Response(503,{'Retry-After':'nan'}),0),3)

    def test_retry_after_http_date_supported(self):
        now=datetime.now(timezone.utc)
        stamp=now.timestamp()+12
        from email.utils import format_datetime
        raw=format_datetime(datetime.fromtimestamp(stamp,timezone.utc),usegmt=True)
        self.assertGreaterEqual(m._snrt_retry_delay(Response(503,{'Retry-After':raw}),0),10)

    def test_long_retry_after_defers_provider_without_retrying_early(self):
        clock=Clock(); client=self.client([Response(503,{'Retry-After':'3600'})])
        with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.time,'sleep',clock.sleep):
            with self.assertRaises(requests.HTTPError): client.get('https://www.snrt.ma/ar/node/1208')
        self.assertEqual(client.s.get.call_count,1); self.assertEqual(clock.delays,[])

    def test_access_denied_is_not_retried(self):
        clock=Clock(); reply=Response(403); client=self.client([reply])
        with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.time,'sleep',clock.sleep):
            with self.assertRaises(requests.HTTPError): client.get('https://www.snrt.ma/ar/node/1208')
        self.assertEqual(client.s.get.call_count,1); self.assertEqual(clock.delays,[]); self.assertTrue(reply.closed)

    def test_budget_exhaustion_stops_before_second_request(self):
        clock=Clock(); client=self.client([Response(503)],budget=2)
        with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.time,'sleep',clock.sleep):
            with self.assertRaises(TimeoutError): client.get('https://www.snrt.ma/ar/node/1208')
        self.assertEqual(client.s.get.call_count,1); self.assertEqual(clock.delays,[2.])

    def test_failure_is_bounded_to_three_attempts(self):
        clock=Clock(); client=self.client([Response(503) for _ in range(3)])
        with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.time,'sleep',clock.sleep):
            with self.assertRaises(requests.HTTPError): client.get('https://www.snrt.ma/ar/node/1208')
        self.assertEqual(client.s.get.call_count,3); self.assertEqual(clock.delays,[3.,6.])

    def test_other_provider_backoff_preserved(self):
        clock=Clock(); client=self.client([Response(503),Response(200)])
        with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.time,'sleep',clock.sleep):
            client.get('https://www.example.org/programmes')
        self.assertEqual(clock.delays,[.7])

    def test_snrt_host_matching_does_not_include_lookalikes(self):
        self.assertTrue(m._is_snrt_url('https://www.snrt.ma/ar/'))
        self.assertTrue(m._is_snrt_url('https://snrt.ma/ar/'))
        self.assertFalse(m._is_snrt_url('https://snrt.ma.example.org/ar/'))

    def test_snrt_concurrency_is_bounded_across_clients(self):
        state={'active':0,'maximum':0}; lock=threading.Lock()
        def get(*args,**kwargs):
            with lock:
                state['active']+=1; state['maximum']=max(state['active'],state['maximum'])
            try: time.sleep(.025); return Response(200)
            finally:
                with lock: state['active']-=1
        def run(_):
            client=m.Http.__new__(m.Http); client.deadline=time.monotonic()+3
            client.s=types.SimpleNamespace(get=get)
            return client.get('https://www.snrt.ma/ar/node/1208').status_code
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(list(pool.map(run,range(6))),[200]*6)
        self.assertLessEqual(state['maximum'],2); self.assertEqual(state['active'],0)

    def test_failed_arryadia_download_has_no_unbound_soup_error_or_fake_rows(self):
        http=Mock(); http.get.side_effect=requests.HTTPError(response=Response(503))
        with patch.object(m,'Http',return_value=http),patch.object(m,'log') as log:
            self.assertEqual(m.scrape_arryadia(2),[])
        self.assertFalse(any('DOM diagnostic failed' in str(call) for call in log.call_args_list))

    def test_outage_retains_only_exact_future_genuine_lkg(self):
        now=datetime.now(timezone.utc)
        good=m.Event('AlAoula',now+timedelta(hours=1),'نشرة الأخبار','وصف رسمي',now+timedelta(hours=2),source='snrt-ar')
        expired=m.Event('AlAoula',now-timedelta(days=2),'أخبار قديمة','وصف رسمي',now-timedelta(days=1),source='snrt-ar')
        fake=m.Event('Arryadia_HD',now,'Arryadia Programme','',now+timedelta(hours=1),source='arryadia-auto')
        rows,status=m.select_groups({},[good,expired,fake],now=now)
        self.assertEqual(rows,[good]); self.assertIs(rows[0],good)
        self.assertEqual(status['snrt']['mode'],'last-known-good'); self.assertFalse(status['snrt']['qualified'])

    def test_genuine_french_2m_news_remains_french(self):
        self.assertEqual(m.tr2m_title(None,'Info Soir'),'Info Soir')

    def test_0707_gmt_schedule_is_unchanged_and_single(self):
        doc=yaml.load((ROOT/'.github/workflows/refresh-morocco.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(doc['on']['schedule'],[{'cron':'7 7 * * *','timezone':'Etc/GMT'}])
        self.assertNotIn('push',doc['on']); self.assertIn('workflow_dispatch',doc['on'])

    def test_only_morocco_is_separated_from_shared_source_queue(self):
        core=yaml.load((ROOT/'.github/workflows/refresh-source-core.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(core['concurrency']['group'],"${{ inputs.source == 'morocco' && 'epg-morocco-source-publish' || 'epg-source-publish' }}")
        self.assertEqual(core['concurrency']['cancel-in-progress'],'false')

    def test_manual_retry_does_not_fabricate_schedule_lateness(self):
        result=observation('workflow_dispatch',datetime(2026,10,31,14,tzinfo=timezone.utc))
        self.assertIsNone(result['observed_delay_seconds']); self.assertFalse(result['delayed'])

    def test_scheduled_delay_measured_in_utc(self):
        result=observation('schedule',datetime(2026,10,31,14,tzinfo=timezone.utc))
        self.assertEqual(result['observed_delay_seconds'],24780); self.assertTrue(result['delayed'])

    def test_source_publish_rebase_preserves_other_source_and_unstaged_reports(self):
        if not shutil.which('git'): self.skipTest('git unavailable')
        def git(root,*args):
            r=subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid',*args],cwd=root,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            return r.stdout
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); seed=root/'seed'; seed.mkdir(); git(seed,'init','-b','main')
            for name in ('morocco.txt','bein.txt','shared-report.txt'): (seed/name).write_text('original\n')
            git(seed,'add','.'); git(seed,'commit','-m','initial')
            git(root,'clone','--bare',str(seed),str(root/'origin.git'))
            for name in ('left','right'): git(root,'clone',str(root/'origin.git'),name)
            left,right=root/'left',root/'right'
            (left/'bein.txt').write_text('new beIN\n'); git(left,'add','bein.txt'); git(left,'commit','-m','beIN')
            (right/'morocco.txt').write_text('new Morocco\n'); git(right,'add','morocco.txt'); git(right,'commit','-m','Morocco')
            (right/'shared-report.txt').write_text('local generated data\n')
            git(left,'push','origin','HEAD:main')
            git(right,'-c','rebase.autoStash=true','pull','--rebase','origin','main')
            git(right,'push','origin','HEAD:main')
            self.assertEqual((right/'bein.txt').read_text(),'new beIN\n')
            self.assertEqual((right/'shared-report.txt').read_text(),'local generated data\n')
            published=git(root/'origin.git','show','main:shared-report.txt')
            self.assertEqual(published,'original\n')


if __name__=='__main__': unittest.main()
