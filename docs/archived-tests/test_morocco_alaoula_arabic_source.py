"""Guarantee Al Aoula Arabic broadcaster origin through the whole XMLTV pipeline."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import morocco_epg as base
import morocco_cloud_runner as runner
import morocco_cloud_final as cloud
import morocco_current_official as official

TZ = ZoneInfo("Africa/Casablanca")


def event(cid, title, source):
    start = datetime.now(TZ).replace(second=0, microsecond=0) + timedelta(hours=1)
    return base.Event(cid, start, title, title, start + timedelta(minutes=40),
                      "ar" if any("\u0600" <= c <= "\u06ff" for c in title) else "fr",
                      "ar" if any("\u0600" <= c <= "\u06ff" for c in title) else "fr",
                      source)


class ArabicAlAoula(unittest.TestCase):
    def test_official_page_url_is_arabic(self):
        self.assertEqual(official.SNRT_LIVE_URL,"https://www.snrt.ma/ar/al-aoula")

    def test_no_lkg_french_when_snrt_has_other_valid_programmes(self):
        original=runner.choose
        try:
            cloud._install_per_channel_lkg()
            verified=event("AlAoula","جماعتنا زينة","snrt-official-arabic")
            old_french=event("AlAoula","Jmaatna zina","snrt-official-live")
            other=event("Assadisa","القرآن الكريم","snrt")
            rows,mode=runner.choose(
                "snrt", [verified,other], [old_french,other],
                {"AlAoula","Assadisa"},1
            )
            self.assertIn(verified,rows)
            self.assertNotIn(old_french,rows)
            rows2,mode2=runner.choose(
                "snrt",[other],[old_french,other],{"AlAoula","Assadisa"},1
            )
            self.assertTrue(all(e.channel!="AlAoula" for e in rows2))
        finally:
            runner.choose=original

    def test_alaoula_refuses_french_xml_events(self):
        original=runner.write_xml
        try:
            runner.write_xml=lambda rows,path: {e.channel for e in rows}
            cloud._install_strict_writer()
            core=[event("2M","نشرة الأخبار","sudinfo"),
                  event("Arryadia_HD","برنامج رياضي","arryadia-current-official"),
                  event("MEDI1TV_AR.ma","نشرة الأخبار","medi1")]
            with self.assertRaisesRegex(RuntimeError,"Arabic official"):
                runner.write_xml(core+[event("AlAoula","Jmaatna zina","snrt-official-live")],"unused")
            self.assertIn("AlAoula",runner.write_xml(
                core+[event("AlAoula","جماعتنا زينة","snrt-official-arabic")],"unused"))
        finally:
            runner.write_xml=original


if __name__=="__main__":
    unittest.main()
