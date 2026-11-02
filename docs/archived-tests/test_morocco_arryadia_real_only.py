"""No synthetic Arryadia EPG; six-month history only matches actual programmes."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import arryadia_archive_patterns as policy
import morocco_cloud_final as cloud
import morocco_cloud_runner as runner
import morocco_cloud_legacy_logic as legacy

TZ=ZoneInfo("Africa/Casablanca")


def mk(cid,title,source,minutes=0):
    start=datetime.now(TZ).replace(second=0,microsecond=0)+timedelta(hours=2,minutes=minutes)
    return SimpleNamespace(channel=cid,title=title,source=source,start=start,
                           stop=start+timedelta(minutes=35),
                           desc="بطولة رياضية",tl="ar",dl="ar")


class ArryadiaRealOnly(unittest.TestCase):
    def test_no_three_hour_gap_fillers_in_historical_engine(self):
        today=datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        self.assertTrue(all(not rows for rows in
            legacy._arryadia_fill_gaps({},today,today+timedelta(days=3)).values()))

    def test_unavailable_official_grid_does_not_create_programmes(self):
        with patch.object(legacy.runner,"fetch",return_value=SimpleNamespace(text="<html></html>")):
            self.assertEqual(legacy.scrape_arryadia_historical(3),[])

    def test_six_month_archived_rows_do_not_become_current(self):
        html=('''<div class="grille-line 20260921">'''
              '''<div class="grille-time">09H00</div>'''
              '''<h2 class="program-title-sm">Arryadia Chabab - Football</h2>'''
              '''</div>''')
        with patch.object(legacy.runner,"fetch",return_value=SimpleNamespace(text=html)):
            self.assertEqual(legacy.scrape_arryadia_historical(3),[])

    def test_legacy_fillers_not_accepted_as_evidence(self):
        real=mk("Arryadia_HD","الرياضية شباب - كرة القدم","arryadia-current-official")
        filler=mk("Arryadia_HD","Programmes Arryadia HD","arryadia-current-official",45)
        old=mk("Arryadia_HD","برنامج قديم","arryadia",90)
        self.assertTrue(policy.actual_arr_event(real))
        self.assertFalse(policy.actual_arr_event(filler))
        self.assertFalse(policy.actual_arr_event(old))
        self.assertEqual(policy.validated_rows([real,filler,old]),[real])
        self.assertEqual(policy.family_hint(real.title),"arryadia_chabab_football")
        self.assertIsNone(policy.family_hint("Wydad vs Raja"))
        self.assertFalse(policy.RECURRING_FAMILIES["arryadia_chabab_football"]["reliable_future_clock"])

    def test_selection_discards_old_receiver_lkg(self):
        original=runner.choose
        try:
            cloud._install_per_channel_lkg()
            old=mk("Arryadia_HD","Programmes Arryadia HD","arryadia")
            rows,mode=runner.choose("arryadia",[],[old],{"Arryadia_HD"},10)
            self.assertEqual(rows,[])
            self.assertEqual(mode,"real-source-unavailable")
            real=mk("Arryadia_HD","الرياضية شباب - كرة القدم","arryadia-current-official")
            rows,mode=runner.choose("arryadia",[real,old],[old],{"Arryadia_HD"},10)
            self.assertEqual(rows,[real])
            self.assertEqual(mode,"real-only")
        finally:
            runner.choose=original

    def test_writer_rejects_synthetic_and_publishes_only_real(self):
        prev=runner.write_xml
        try:
            runner.write_xml=lambda rows,path:list(rows)
            cloud._install_strict_writer()
            core=[mk("2M","أخبار","sudinfo"),mk("MEDI1TV_AR.ma","نشرة الأخبار","medi1")]
            self.assertEqual(runner.write_xml(core,"unused"),core)
            fake=mk("Arryadia_HD","Programmes Arryadia HD","arryadia-current-official")
            with self.assertRaisesRegex(RuntimeError,"Arryadia EPG provenance"):
                runner.write_xml(core+[fake],"unused")
            verified=mk("Arryadia_HD","الرياضية شباب - كرة القدم","arryadia-current-official")
            self.assertIn(verified,runner.write_xml(core+[verified],"unused"))
        finally:
            runner.write_xml=prev


if __name__=="__main__":
    unittest.main()
