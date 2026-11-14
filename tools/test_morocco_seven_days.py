from datetime import datetime,timedelta,timezone
import gzip,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from tools import morocco_epg as m
from tools.arryadia_hourly_grand_format import generate_hourly,is_viewer_report
from tools.arryadia_smart_epg import generate
from tools.morocco_seven_day_coverage import coverage

ROOT=Path(__file__).resolve().parents[1]

class MoroccoSevenDays(unittest.TestCase):
    def test_seventh_day_native_and_lkg_survive_selection_but_eighth_does_not(self):
        now=datetime.now(timezone.utc)
        seventh=m.Event('AlAoula',now+timedelta(days=6),'Actual provider show','Source description',now+timedelta(days=6,hours=1),source='snrt-ar')
        outside=m.Event('2M',now+timedelta(days=8),'Outside window','',now+timedelta(days=8,hours=1),source='old')
        rows,_=m.select_groups({'snrt':[seventh]},[outside],now=now,days=7)
        self.assertIn(seventh,rows);self.assertNotIn(outside,rows)
        rows,_=m.select_groups({},[seventh],now=now,days=7)
        self.assertIn(seventh,rows)

    def test_grand_format_covers_rolling_168_hours_and_passes_writer_scope(self):
        now=datetime.now(m.TZ)
        rows=generate_hourly(7,now,horizon_hours=168)
        for cid in ('Arryadia_HD2','Arryadia_HD3'):
            own=[r for r in rows if r.channel==cid]
            self.assertLessEqual(own[0].start,now)
            self.assertGreaterEqual(own[-1].stop,now+timedelta(hours=168))
            self.assertTrue(all(is_viewer_report(e,now) for e in own))
        with tempfile.TemporaryDirectory() as tmp:m.to_xml(rows,Path(tmp)/'seven.xml')

    def test_smart_keeps_a_published_sixth_day_botola_fixture(self):
        now=datetime.now(timezone.utc);start=now+timedelta(days=6)
        fixture={'id':'published','start':start,'stop':start+timedelta(hours=2),'home':'Home','away':'Away','title':'Home vs Away - Botola Pro','desc':'Published fixture'}
        rows,_=generate([],[],now,hours=168,fixture_rows=[fixture])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].start,start)

    def test_two_m_asks_seven_distinct_pages_and_retains_day_six(self):
        now=datetime.now(m.TZ);days=[]
        http=Mock();http.get.return_value.text='Source page'
        def parse(client,text,day,cache):
            days.append(day);start=m.morocco_wall_clock(day,datetime.min.time().replace(hour=13))
            return [m.Event('2M',start,'أخبار','وصف',start+timedelta(hours=1),source='2m-telerama')]
        with patch.object(m,'Http',return_value=http),patch.object(m,'parse_2m',side_effect=parse),patch.object(m,'log'):
            rows=m.scrape_2m(7)
        self.assertEqual(len(set(days)),7)
        self.assertEqual(days[-1],now.date()+timedelta(days=6))
        self.assertTrue(any(e.start.date()==days[-1] for e in rows))

    def test_production_branch_packs_168_hours_and_reports_every_id(self):
        bash=shutil.which('bash') or ('C:/Program Files/Git/bin/bash.exe' if Path('C:/Program Files/Git/bin/bash.exe').exists() else None)
        if not bash:self.skipTest('bash unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'tools').mkdir()
            for name in ('source_feed_pack.py','programme_authenticity.py','source_timeline.py'):shutil.copyfile(ROOT/'tools'/name,root/'tools'/name)
            now=datetime.now(m.TZ)
            rows=generate_hourly(7,now,horizon_hours=168)
            start=now+timedelta(days=6)
            rows.append(m.Event('2M',start,'أخبار منشورة','وصف',start+timedelta(hours=1),source='2m'))
            raw=root/'fixture.xml';m.to_xml(rows,raw)
            (root/'fixture.gz').write_bytes(gzip.compress(raw.read_bytes()))
            (root/'fake_collect.py').write_text("import sys,json,shutil\nfrom pathlib import Path\na=sys.argv[1:];assert a[a.index('--days')+1]=='7'\np=Path(a[a.index('--output-dir')+1]);p.mkdir(parents=True,exist_ok=True)\nshutil.copyfile('fixture.gz',p/'morocco.xml.gz')\n")
            source=(ROOT/'tools/refresh_one_source.sh').read_text()
            prefix=source.split('case "$SOURCE" in',1)[0]
            branch=source.split('  morocco)\n',1)[1].split('\n  tabie)',1)[0].rstrip().removesuffix(';;')
            wrapper='\npython() { if [ "$1" = tools/morocco_epg.py ]; then shift; "$TEST_PYTHON" fake_collect.py "$@"; else "$TEST_PYTHON" "$@"; fi; }\n'
            script=root/'run.sh';script.write_text(prefix+wrapper+branch)
            env=dict(os.environ,TEST_PYTHON=sys.executable)
            env.pop('GITHUB_WORKSPACE',None)
            result=subprocess.run([bash,str(script),'morocco','48'],cwd=root,env=env,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            feeds=root/'feeds';shutil.copytree(root/'output/final',feeds)
            meta=json.loads((feeds/'morocco.json').read_text())
            self.assertEqual(meta['window_hours'],168)
            report=coverage(feeds/'morocco.xml.gz',now)
            self.assertEqual(report['channels'],len(m.CHANNELS))
            self.assertEqual({r['id'] for r in report['ids']},set(m.CHANNELS))
            two=next(r for r in report['ids'] if r['id']=='2M')
            self.assertEqual(two['source_programmes'],1)
            for cid in ('Arryadia_HD2','Arryadia_HD3'):
                row=next(r for r in report['ids'] if r['id']==cid)
                self.assertEqual(row['status'],'FULL_REQUESTED_WINDOW')
                self.assertEqual(row['source_programmes'],0)
            empty=next(r for r in report['ids'] if r['id']=='AFLAM.ma')
            self.assertEqual(empty['status'],'NO_FUTURE_EPG')

if __name__=='__main__':unittest.main()
