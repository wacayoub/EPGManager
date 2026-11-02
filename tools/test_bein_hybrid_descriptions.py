import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import bein_hybrid as mod

class StubTranslator:
    def translate_ar(self, text):
        return "وصف عربي للاختبار"


def programme(title, start="20261003120000 +0000", stop="20261003150000 +0000", channel="beINSports1.qa@MENA"):
    node = ET.Element("programme", {"channel": channel, "start": start, "stop": stop})
    ET.SubElement(node, "title", {"lang": "en"}).text = title
    return node

class DescriptionRegression(unittest.TestCase):
    def test_english_description_translated(self):
        node = programme("Match")
        ET.SubElement(node, "desc", {"lang": "en"}).text = "A live football match."
        changed, remaining = mod.ensure_arabic_desc(node, StubTranslator())
        self.assertEqual((changed, remaining), (1, 0))
        self.assertEqual(mod.text_of(node, "desc"), "وصف عربي للاختبار")

    def test_live_match_description_is_specific_and_marked(self):
        node = programme("France vs Italy - UEFA Nations League 2026/27")
        self.assertTrue(mod.ensure_arabic_desc_fallback(node, node.get("channel")))
        now = datetime(2026, 10, 3, 13, 0, tzinfo=timezone.utc)
        self.assertTrue(mod.add_live_description_marker(node, now))
        desc = mod.text_of(node, "desc")
        self.assertIn("France", desc)
        self.assertIn("Italy", desc)
        self.assertIn("دوري الأمم الأوروبية", desc)
        self.assertTrue(desc.startswith("Live | "))
        self.assertNotIn("بث مباشر", desc)

    def test_future_match_is_not_marked_live(self):
        node = programme("France vs Italy - UEFA Nations League", "20261003150000 +0000", "20261003180000 +0000")
        mod.ensure_arabic_desc_fallback(node, node.get("channel"))
        now = datetime(2026, 10, 3, 14, 59, tzinfo=timezone.utc)
        self.assertFalse(mod.add_live_description_marker(node, now))
        self.assertNotIn("مباشر", mod.text_of(node, "desc"))

    def test_short_match_slot_and_replay_not_marked_live(self):
        now = datetime(2026, 10, 3, 13, 0, tzinfo=timezone.utc)
        short = programme("France vs Italy", stop="20261003123000 +0000")
        replay = programme("France vs Italy - Replay")
        self.assertFalse(mod.is_current_live_match(short, now))
        self.assertFalse(mod.is_current_live_match(replay, now))

    def test_afc_champions_league_two_competition_is_arabic(self):
        node = programme("Al Wahda vs Kuwait - AFC Champions League Two 2026/27")
        mod.ensure_arabic_desc_fallback(node, node.get("channel"))
        desc = mod.text_of(node, "desc")
        self.assertIn("دوري أبطال آسيا 2", desc)
        self.assertNotIn(" ضمن دوري أبطال آسيا Two", desc)

    def test_news_channel_gets_arabic_news_description(self):
        node = programme("Al Jawla", channel="beINSportsNews.qa@SD")
        self.assertTrue(mod.ensure_arabic_desc_fallback(node, node.get("channel")))
        self.assertIn("أبرز نتائج المباريات", mod.text_of(node, "desc"))

    def test_news_title_mapping(self):
        title, changed = mod.arabize_sports_news_title("Al Jawla", StubTranslator())
        self.assertEqual(title, "الجولة")
        self.assertTrue(changed)

    def test_specific_arabic_description_is_preserved(self):
        node = programme("A programme")
        ET.SubElement(node, "desc", {"lang": "ar"}).text = "وصف عربي حقيقي للبرنامج."
        self.assertFalse(mod.ensure_arabic_desc_fallback(node, node.get("channel")))
        self.assertEqual(mod.text_of(node, "desc"), "وصف عربي حقيقي للبرنامج.")

if __name__ == "__main__":
    unittest.main(verbosity=2)
