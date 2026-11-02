# -*- coding: utf-8 -*-
"""STARZPLAY display labels official EN; keep all 117 XMLTV programme IDs."""
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from tools import starzplay_hybrid as hybrid

ROOT=Path(__file__).resolve().parents[1]
EIGHT={
    "starzplay.460050472022":"Saudi Quran",
    "starzplay.460871719977":"Sky News Arabia - Live",
    "starzplay.559865896371":"Zee Alwan",
    "starzplay.720334376240":"Al Emarat TV",
    "starzplay.720335400130":"Majid TV",
    "starzplay.720335400133":"Abu Dhabi TV",
    "starzplay.770559016375":"Cartoon Network Arabic",
    "starzplay.770559016378":"Spacetoon",
}

def node(item,lang):
    c=ET.Element("channel",{"id":item["id"]})
    ET.SubElement(c,"display-name",{"lang":lang}).text=item["name"]
    ET.SubElement(c,"url",{"system":"starzplay-id"}).text=item["source_id"]
    return c

class TestSTARZPLAYOfficialENDisplayNames(unittest.TestCase):
    def test_real_two_117_channel_official_catalogues_resolve_all(self):
        # These are already-published source scrape diagnostics in the repo,
        # not live network or any receiver file.
        a=json.loads((ROOT/"reports/starzplay-ar-api-scrape.json").read_text())
        e=json.loads((ROOT/"reports/starzplay-en-api-scrape.json").read_text())
        ar=a["channel_catalogue"]
        en=e["channel_catalogue"]
        # Real provider catalogues change. Assert completeness and identity
        # conservation against this snapshot, not an obsolete fixed count.
        self.assertGreaterEqual(len(ar), 100)
        self.assertEqual(len(en), len(ar))
        ac={x["id"]:node(x,"ar") for x in ar}
        ec={x["id"]:node(x,"en") for x in en}
        original_ids=set(ac)
        original_source_ids={cid:hybrid.starzplay_provider_id(ch) for cid,ch in ac.items()}
        report=hybrid.english_display_names(ac,ec)
        self.assertEqual(report["applied"],len(ar))
        self.assertEqual(report["missing"],0)
        self.assertEqual(report["missing_ids"],[])
        self.assertEqual(report["by_provider_id"],8)
        self.assertEqual(set(report["by_provider_id_ids"]),set(EIGHT))
        self.assertEqual(set(ac),original_ids)
        self.assertEqual({cid:hybrid.starzplay_provider_id(ch) for cid,ch in ac.items()},original_source_ids)
        for cid,english in EIGHT.items():
            self.assertEqual(hybrid.text_of(ac[cid],"display-name"),english)
            self.assertEqual(ac[cid].find("display-name").get("lang"),"en")
            self.assertEqual(ac[cid].get("id"),cid)
        for channel in ac.values():
            label=hybrid.text_of(channel,"display-name")
            self.assertTrue(label)
            self.assertFalse(hybrid.is_ar(label), label)
            self.assertEqual(channel.find("display-name").get("lang"),"en")
        self.assertEqual(hybrid.text_of(ac["NationalGeographicAbuDhabi.ae@SD"],"display-name"),
                         "National Geographic Abu Dhabi")
        self.assertEqual(json.loads((ROOT/"config/starzplay-language-policy.json").read_text())
                         ["force_arabic_content_ids"],["NationalGeographicAbuDhabi.ae@SD"])

    def test_ambiguous_provider_id_does_not_guess_name(self):
        ar={"starzplay.123":node({"id":"starzplay.123","name":"قناة تجريبية",
                                   "source_id":"123"},"ar")}
        en={
            "en1":node({"id":"en1","name":"Channel One","source_id":"123"},"en"),
            "en2":node({"id":"en2","name":"Different Channel","source_id":"123"},"en"),
        }
        stats=hybrid.english_display_names(ar,en)
        self.assertEqual(stats["missing"],1)
        self.assertEqual(stats["by_provider_id"],0)
        self.assertEqual(stats["ambiguous_provider_ids"],["123"])
        self.assertEqual(hybrid.text_of(ar["starzplay.123"],"display-name"),
                         "قناة تجريبية")
        self.assertEqual(ar["starzplay.123"].get("id"),"starzplay.123")

    def test_missing_english_is_reported_not_fabricated(self):
        ar={"starzplay.987":node({"id":"starzplay.987","name":"القناة",
                                  "source_id":"987"},"ar")}
        stats=hybrid.english_display_names(ar,{})
        self.assertEqual(stats["missing_ids"],["starzplay.987"])
        self.assertEqual(stats["applied"],0)
        self.assertEqual(ar["starzplay.987"].find("display-name").get("lang"),"ar")

    def test_en_id_same_but_different_numeric_provider_is_rejected(self):
        ar={"stable":node({"id":"stable","name":"القناة","source_id":"111"},"ar")}
        en={"stable":node({"id":"stable","name":"Wrong name","source_id":"222"},"en")}
        stats=hybrid.english_display_names(ar,en)
        self.assertEqual(stats["missing"],1)
        self.assertEqual(hybrid.text_of(ar["stable"],"display-name"),"القناة")

if __name__=="__main__":
    unittest.main()
