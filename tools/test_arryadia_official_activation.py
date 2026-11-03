from datetime import timedelta
import unittest
from tools import morocco_epg as m
from tools.arryadia_official_grid import parse_grid
from tools import test_arryadia_official_grid as fixtures
from tools.test_arryadia_official_grid import row,NOW


class OfficialActivation(unittest.TestCase):
    collect=fixtures.ArryadiaGrid.collect

    def test_french_only_tnt_is_checked_despite_a_current_arabic_satellite_grid(self):
        events,report,_=self.collect([row(marker='SAT',title='الرياضة'),row(marker='TNT',title='Local programme')])
        self.assertEqual({e.channel for e in events},{'Arryadia_HD','Arryadia_TNT'})
        state=report['official_service_activation']['Arryadia_TNT']
        self.assertEqual(state['status'],'OFFICIAL_EPG_AVAILABLE')
        self.assertFalse(state['estimated_schedule_allowed']);self.assertFalse(state['receiver_action'])
        self.assertEqual(len(report['selected_urls']),2)

    def test_french_hd1_is_independent_of_a_current_main_channel(self):
        events,report,_=self.collect([row(),row(marker='HD1',title='HD1 announcement')])
        self.assertEqual({e.channel for e in events},{'Arryadia_HD','Arryadia_HD1'})
        self.assertEqual(report['official_service_activation']['Arryadia_HD1']['status'],'OFFICIAL_EPG_AVAILABLE')
        self.assertEqual(report['official_service_activation']['Arryadia_TNT']['status'],'WAITING_FOR_OFFICIAL_EPG')

    def test_main_grid_does_not_wake_unannounced_services(self):
        events,report,_=self.collect([row(),row()])
        self.assertEqual({e.channel for e in events},{'Arryadia_HD'})
        self.assertTrue(all(r['status']=='WAITING_FOR_OFFICIAL_EPG' for r in report['official_service_activation'].values()))

    def test_arabic_and_french_terrestrial_markers_target_tnt_only(self):
        for marker in ('مباشر أرضي','البث الأرضي','القناة الأرضية','Diffusion terrestre'):
            with self.subTest(marker=marker):
                events,_=parse_grid(row(title=marker),m.morocco_wall_clock)
                self.assertEqual({e['channel'] for e in events},{'Arryadia_TNT'})

    def test_typed_local_metadata_wins_over_incidental_satellite_words(self):
        events,_=parse_grid(row(marker='local',title='Local service, unlike SAT'),m.morocco_wall_clock)
        self.assertEqual({e['channel'] for e in events},{'Arryadia_TNT'})

    def test_local_match_word_without_a_service_marker_does_not_assign_tnt(self):
        events,_=parse_grid(row(title='Championnat local'),m.morocco_wall_clock)
        self.assertEqual({e['channel'] for e in events},{'Arryadia_HD'})

    def test_exact_future_official_lkg_stays_available_until_expiry(self):
        event=m.Event('Arryadia_TNT',NOW,'Official local programme','SNRT local listing',NOW+timedelta(hours=1),source='old')
        events,report,_=self.collect([row('20260921'),row('20260921')],history=[event])
        self.assertEqual(len(events),1)
        self.assertEqual(report['official_service_activation']['Arryadia_TNT']['status'],'OFFICIAL_LKG_AVAILABLE')

    def test_seventh_day_hd1_and_tnt_announcements_are_usable(self):
        events,_=parse_grid(row('20261109',marker='HD1')+row('20261109',marker='TNT'),m.morocco_wall_clock)
        native=[m.Event(e['channel'],e['start'],e['title'],e['desc'],e['stop'],source='arryadia-snrt') for e in events]
        selected,_=m.select_groups({'arryadia':native},[],now=NOW,days=7)
        self.assertEqual({e.channel for e in selected},{'Arryadia_HD1','Arryadia_TNT'})

if __name__=='__main__':unittest.main()
