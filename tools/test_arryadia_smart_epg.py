from datetime import datetime,timedelta,timezone
import gzip,json
from pathlib import Path
import subprocess,sys,tempfile,types,unittest
from unittest.mock import Mock,patch
from tools import morocco_epg as m
from tools.arryadia_smart_epg import generate,learn,model
from tools.programme_authenticity import SMART_SOURCE,BOTOLA_SOURCE,smart_estimated_schedule
from tools.test_mena_readiness_audit import fixture,check
from tools.test_mena_source_snapshot import rebuild

NOW=datetime(2026,11,3,11,tzinfo=timezone.utc)
ROOT=Path(__file__).resolve().parents[1]

def observations():
    return [m.Event('Arryadia_HD',(NOW-timedelta(days=i)).replace(hour=7,minute=5),
             'Arryadia Chabab - Football','Old Home vs Old Away',
             (NOW-timedelta(days=i)).replace(hour=8,minute=5),'fr','ar','arryadia-snrt') for i in (1,2,3)]

def match(start=None,ident='one',home='FAR Rabat',away='Raja'):
    start=start or NOW+timedelta(days=1,hours=9)
    return {'id':ident,'start':start,'stop':start+timedelta(hours=2),'home':home,'away':away,
            'title':home+' vs '+away+' - Botola Pro','desc':'Published match schedule'}

class SmartEPG(unittest.TestCase):
    def test_recurrence_needs_three_official_days_and_never_reuses_old_opponents(self):
        rows,report=generate(observations(),[],NOW,fixture_rows=[])
        self.assertTrue(rows);self.assertEqual({e.channel for e in rows},{'Arryadia_HD'})
        self.assertTrue(all(e.source==SMART_SOURCE and '[Estimé]' in e.title for e in rows))
        self.assertTrue(all('Old Home' not in e.desc for e in rows))
        self.assertEqual(learn(observations()[:2],NOW),[])
        fake=observations()
        for event in fake:event.source=SMART_SOURCE
        self.assertEqual(learn(fake,NOW),[])

    def test_persisted_model_survives_provider_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.json'
            original,_=model(observations(),NOW,path)
            reused,mode=model([],NOW,path)
            self.assertEqual(original,reused)
            self.assertEqual(mode,'persisted_model_after_provider_outage')

    def test_published_fixture_becomes_estimated_broadcast_on_hd_only(self):
        event=match();rows,report=generate([],[],NOW,fixture_rows=[event])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].start,event['start'])
        self.assertEqual(rows[0].channel,'Arryadia_HD');self.assertEqual(rows[0].source,BOTOLA_SOURCE)
        self.assertIn('[Diffusion estimée]',rows[0].title)
        self.assertFalse(report['botola_matches_added'][0]['broadcast_channel_confirmed'])

    def test_simultaneous_matches_are_not_both_assigned_to_one_linear_channel(self):
        rows,report=generate([],[],NOW,fixture_rows=[match(),match(ident='two',home='Other Home')])
        self.assertEqual(rows,[]);self.assertEqual(len(report['simultaneous_matches_not_assigned']),2)

    def test_announced_event_has_priority_over_fixture_and_smart(self):
        fx=match();official=m.Event('Arryadia_HD',fx['start'],'Official show','Confirmed',fx['stop'],source='arryadia-snrt')
        rows,_=generate([], [official],NOW,fixture_rows=[fx])
        self.assertEqual(rows,[])
        real=m.Event('Arryadia_HD',NOW.replace(hour=7)+timedelta(days=1),'Official','Confirmed',NOW.replace(hour=9)+timedelta(days=1),source='arryadia-snrt')
        rows,_=generate(observations(),[real],NOW,fixture_rows=[])
        self.assertFalse(any(e.start<real.stop and e.stop>real.start for e in rows))

    def test_removed_match_is_not_replayed_from_previous_generated_guide(self):
        f=match();old=m.Event('Arryadia_HD',f['start'],f['title']+' [Diffusion estimée]','تقدير',f['stop'],'fr','ar',BOTOLA_SOURCE)
        _,status=m.select_groups({},[old],now=NOW)
        self.assertEqual(status['arryadia']['events'],0)

    def test_livescore_rejects_postponed_finished_and_second_division(self):
        stages=[]
        for league,phase in [('Botola Pro','NS'),('Botola Pro 2','NS'),('Botola Pro','Postponed'),('Botola Pro','FT')]:
            stages.append({'Cnm':'Morocco','Snm':league,'Events':[{'Esd':'20261104200000','Eid':league+phase,'Eps':phase,'T1':[{'Nm':'FAR Rabat'}],'T2':[{'Nm':'Raja'}]}]})
        client=Mock();client.get.return_value.json.return_value={'Stages':stages}
        with patch.object(m,'datetime',wraps=datetime) as time:
            time.now.return_value=NOW
            rows=m._livescore_botola_fixtures(client,48)
        self.assertEqual(len(rows),1)

    def test_sofascore_rejects_other_leagues_and_non_active_fixtures(self):
        events=[]
        for league,status in [(937,'notstarted'),(938,'notstarted'),(937,'postponed'),(937,'finished')]:
            events.append({'id':str(league)+status,'startTimestamp':int(match()['start'].timestamp()),'tournament':{'uniqueTournament':{'id':league}},'status':{'type':status},'homeTeam':{'name':'FAR Rabat'},'awayTeam':{'name':'Raja'}})
        client=Mock();client.get.return_value.json.return_value={'events':events}
        with patch.object(m,'datetime',wraps=datetime) as time:
            time.now.return_value=NOW
            rows=m._sofascore_botola_fixtures(client,48)
        self.assertEqual(len(rows),1)

    def test_smart_marker_survives_writer_lkg_pack_and_derived_feed(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            rows,_=generate([],[],now,fixture_rows=[match(start=now+timedelta(hours=2))])
            raw=root/'smart.xml';m.to_xml(rows,raw)
            self.assertEqual(m.previous(raw)[0].source,BOTOLA_SOURCE)
            args=[sys.executable,str(ROOT/'tools/source_feed_pack.py'),'--input',str(raw),'--output-gz',str(root/'feeds/morocco.xml.gz'),'--output-txt',str(root/'feeds/morocco.txt'),'--stats',str(root/'feeds/morocco.json'),'--label','morocco']
            result=subprocess.run(args,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            _,xml=rebuild(root)
            events=[e for e in xml.findall('programme') if e.get('channel')=='Arryadia_HD']
            self.assertTrue(events);self.assertTrue(all(smart_estimated_schedule(e) for e in events))
            row=next(r for r in check(root,now)['ids'] if r['xmltv_id']=='Arryadia_HD')
            self.assertFalse(row['best_match_green_allowed'])

    def test_collector_default_includes_smart_without_changing_hd2_hd3(self):
        fixture_row=match(start=datetime.now(timezone.utc)+timedelta(hours=2))
        http=Mock();http.get.side_effect=RuntimeError('Official outage')
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'Http',return_value=http),patch('tools.arryadia_smart_epg.fixtures',return_value=([fixture_row],{'provider':'fixture test'})):
            rows=m.scrape_arryadia(3,smart_model_path=Path(tmp)/'model.json')
        self.assertTrue(any(e.source==BOTOLA_SOURCE and e.channel=='Arryadia_HD' for e in rows))
        self.assertTrue(any(e.source==m.VIEWER_SOURCE and e.channel=='Arryadia_HD2' for e in rows))
        self.assertFalse(any(e.channel in ('Arryadia_HD1','Arryadia_TNT') for e in rows))
