from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
import requests
from tools import morocco_epg as m
from tools.arryadia_official_grid import parse_grid

NOW=datetime(2026,11,3,10,tzinfo=timezone.utc)

def row(day='20261103',clock='12H00',title='Official programme',marker=''):
    return f'<div class="grille-line {day}" data-channel="{marker}"><div class="grille-time">{clock}</div><div class="grille-content"><h2>{title}</h2><p>Official synopsis</p></div></div>'

def response(html):return types.SimpleNamespace(text=html,status_code=200)

class ArryadiaGrid(unittest.TestCase):
    def parse(self,html):return parse_grid(html,m.morocco_wall_clock)

    def collect(self,htmls,history=None):
        client=Mock();client.get.side_effect=[response(html) if isinstance(html,str) else html for html in htmls]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'
            with patch.object(m,'Http',return_value=client),patch.object(m,'datetime',wraps=datetime) as clock:
                clock.now.return_value=NOW
                rows=m.scrape_arryadia(3,history=history,report_path=path,include_viewer_schedule=False,include_smart=False)
            report=json.loads(path.read_text(encoding='utf-8'))
        return rows,report,client

    def test_next_same_service_start_ends_event_without_cross_service_clipping(self):
        events,_=self.parse(row(clock='12H00')+row(clock='12H30',marker='TNT')+row(clock='13H00',title='Next programme'))
        main=[e for e in events if e['channel']=='Arryadia_HD']
        self.assertEqual(main[0]['stop'].hour,13)
        self.assertEqual(main[-1]['stop']-main[-1]['start'],timedelta(hours=1))

    def test_explicit_midnight_calendar_date_is_not_shifted_again(self):
        events,_=self.parse(row('20261103','23H00')+row('20261104','00H20'))
        self.assertEqual(events[1]['start'],datetime(2026,11,4,0,20,tzinfo=timezone.utc))

    def test_variants_are_not_assumed_to_simulcast(self):
        for marker,expected in [('',{'Arryadia_HD'}),('TNT',{'Arryadia_TNT'}),('SAT',{'Arryadia_HD'}),('HD 1',{'Arryadia_HD1'}),('Arryadia_HD2',{'Arryadia_HD2'}),('HD3',{'Arryadia_HD3'})]:
            with self.subTest(marker=marker):
                events,_=self.parse(row(marker=marker))
                self.assertEqual({e['channel'] for e in events},expected)

    def test_same_clock_and_title_on_different_services_survive_dedup(self):
        events,_=self.parse(row(marker='SAT')+row(marker='TNT')+row(marker='TNT'))
        self.assertEqual(len(events),2)

    def test_iso_dated_card_is_read(self):
        events,_=self.parse('<article data-date="2026-11-03"><time>14:20</time><h3>Official match</h3></article>')
        self.assertEqual(events[0]['start'].hour,14)

    def test_undated_layout_footer_and_day_tabs_cannot_assign_current_date(self):
        html='<body data-date="20261103"><a>03/11</a>'+row(day='')+'<footer>20261103</footer></body>'
        events,report=self.parse(html)
        self.assertEqual(events,[]);self.assertEqual(report['undated_rows'],1)

    def test_real_stale_grid_is_not_redated_from_current_page_header(self):
        html='<header data-date="20261103">03/11/2026</header>'+row('20260921')
        events,report,client=self.collect([html,html])
        self.assertEqual(events,[])
        self.assertEqual(report['status'],'STALE_OFFICIAL_GRID')
        self.assertEqual(report['attempts'][0]['observed_programme_dates'],['2026-09-21'])
        self.assertEqual(client.get.call_count,2)

    def test_arabic_stale_falls_back_to_genuine_current_french_page(self):
        events,report,_=self.collect([row('20260921'),row()])
        self.assertEqual(len(events),1)
        self.assertEqual(report['status'],'CURRENT_GRID')
        self.assertEqual(report['selected_url'],'https://www.snrt.ma/fr/node/4070')
        self.assertEqual(events[0].tl,'fr')

    def test_current_arabic_needs_no_duplicate_french_request(self):
        events,report,client=self.collect([row(title='الأخبار الرياضية')])
        self.assertEqual(events[0].tl,'ar');self.assertEqual(client.get.call_count,1)

    def test_outage_keeps_only_exact_future_lkg(self):
        good=m.Event('Arryadia_HD2',NOW,'Real match','Official description',NOW+timedelta(hours=1),source='old')
        expired=m.Event('Arryadia_HD1',NOW-timedelta(days=2),'Old match','',NOW-timedelta(days=1),source='old')
        events,report,_=self.collect([requests.HTTPError(),requests.HTTPError()],history=[good,expired])
        self.assertEqual([(e.channel,e.start,e.stop) for e in events],[(good.channel,good.start,good.stop)])
        self.assertEqual(report['status'],'FUTURE_LKG_ONLY')

    def test_placeholder_and_invalid_clock_are_rejected(self):
        events,report=self.parse(row(clock='25H00')+row(title='Arryadia Programme'))
        self.assertEqual(events,[]);self.assertEqual(report['invalid_rows'],1)
        self.assertEqual(report['rejected_placeholder_rows'],1)

    def test_retained_arryadia_rows_are_not_reported_as_a_fresh_scrape(self):
        good=m.Event('Arryadia_HD2',NOW,'Real match','Official description',NOW+timedelta(hours=1),source='arryadia-lkg')
        with patch.object(m,'datetime',wraps=datetime) as clock:
            clock.now.return_value=NOW
            rows,status=m.select_groups({'arryadia':[good]},[],now=NOW)
        self.assertEqual(rows,[good])
        self.assertEqual(status['arryadia']['mode'],'last-known-good')
        self.assertEqual(status['arryadia']['retained_exact_lkg'],1)

if __name__=='__main__':unittest.main()
