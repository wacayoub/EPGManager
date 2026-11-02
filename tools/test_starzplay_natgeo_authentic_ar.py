# -*- coding: utf-8 -*-
"""NatGeo Abu Dhabi real AR guide; no invented description/title by STARZPLAY."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import unittest
import xml.etree.ElementTree as ET

from tools import starzplay_hybrid as bridge

NATGEO="NationalGeographicAbuDhabi.ae@SD"
OTHER="starzplay.705949224430"
START="20261018090000 +0000"
STOP="20261018100000 +0000"

def programme(root,cid,title,desc="",lang="ar"):
    p=ET.SubElement(root,"programme",{"channel":cid,"start":START,"stop":STOP})
    ET.SubElement(p,"title",{"lang":lang}).text=title
    if desc:
        ET.SubElement(p,"desc",{"lang":lang}).text=desc
    return p

def fixtures(tmp, natgeo_title, natgeo_desc="", secondary_title="نقل مباشر"):
    ar=ET.Element("tv")
    en=ET.Element("tv")
    for r,nat_en,common in [
        (ar,"ناشيونال جيوغرافيك أبوظبي","ستارزبلاي سبورت 1"),
        (en,"National Geographic Abu Dhabi","STARZPLAY Sports 1"),
    ]:
        ET.SubElement(ET.SubElement(r,"channel",{"id":NATGEO}),
                      "display-name",{"lang":"ar" if r is ar else "en"}).text=nat_en
        ET.SubElement(ET.SubElement(r,"channel",{"id":OTHER}),
                      "display-name",{"lang":"ar" if r is ar else "en"}).text=common
    programme(ar,NATGEO,natgeo_title,natgeo_desc)
    programme(ar,OTHER,secondary_title,"وصف أصلي عربي")
    programme(en,NATGEO,"Growing up Animal","Original English synopsis",lang="en")
    programme(en,OTHER,"Live Sports Channel","English desc",lang="en")
    for lang,root in (("ar",ar),("en",en)):
        (tmp/(lang+".xml")).write_bytes(ET.tostring(root,encoding="utf-8"))
    policy={"default_profile":"arabic_native",
            "arabic_native_ids":[NATGEO],
            "hybrid_ids":[OTHER],
            "force_arabic_content_ids":[NATGEO],
            "arabic_display_name_ids":[NATGEO]}
    (tmp/"policy.json").write_text(json.dumps(policy))
    return (tmp/"ar.xml",tmp/"en.xml",tmp/"policy.json")

def run(tmp,natgeo_title,natgeo_desc="",translation=None):
    ar,en,policy=fixtures(tmp,natgeo_title,natgeo_desc)
    tr=translation if translation is not None else lambda text,cache:text
    cmd=["starzplay_hybrid.py","--arabic",str(ar),"--english",str(en),
         "--policy",str(policy),"--output",str(tmp/"out.xml"),
         "--report",str(tmp/"report.json")]
    with patch.object(sys,"argv",cmd),patch.object(bridge,"translate_ar",side_effect=tr):
        code=bridge.main()
    root=ET.parse(tmp/"out.xml").getroot()
    summary=json.loads((tmp/"report.json").read_text())
    return code,root,summary

def choose(root,id_):
    return next(x for x in root.findall("programme") if x.get("channel")==id_)

class TestSTARZPLAYAuthenticatedArabic(unittest.TestCase):
    def test_missing_source_synopsis_is_not_replaced_with_generic_arabic(self):
        with tempfile.TemporaryDirectory() as d:
            code,root,stats=run(Path(d),"تربية الحيوانات")
        nat=choose(root,NATGEO)
        self.assertEqual(code,0)
        self.assertEqual(nat.findtext("title"),"تربية الحيوانات")
        self.assertEqual(nat.find("title").get("lang"),"ar")
        self.assertEqual(nat.findall("desc"),[])
        self.assertEqual(stats["forced_arabic_description_missing_in_output"],1)
        self.assertEqual(stats["forced_arabic_description_untranslated"],0)
        self.assertEqual(stats["arabic_description_missing"],1)
        self.assertEqual(stats["forced_arabic_title_language_review"],0)

    def test_translation_failure_does_not_fabricate_arabic(self):
        with tempfile.TemporaryDirectory() as d:
            code,root,stats=run(Path(d),"Growing up Animal","English source description")
        nat=choose(root,NATGEO)
        self.assertEqual(code,0)
        self.assertEqual(nat.findtext("title"),"Growing up Animal")
        self.assertEqual(nat.find("title").get("lang"),"en")
        self.assertEqual(nat.findall("desc"),[])
        self.assertEqual(stats["forced_arabic_title_language_review"],1)
        self.assertEqual(stats["forced_arabic_description_untranslated"],1)
        self.assertEqual(stats["forced_arabic_description_missing_in_output"],1)
        self.assertIn("Growing up Animal",stats["forced_arabic_translation_failures"])

    def test_real_translated_provider_title_and_desc_kept(self):
        def translate(text, cache):
            if text=="Growing up Animal":return "تربية الحيوانات"
            if text=="English source description":return "وصف عربي حقيقي"
            return text
        with tempfile.TemporaryDirectory() as d:
            code,root,stats=run(Path(d),"Growing up Animal","English source description",translate)
        nat=choose(root,NATGEO)
        self.assertEqual(code,0)
        self.assertEqual(nat.findtext("title"),"تربية الحيوانات")
        self.assertEqual(nat.find("title").get("lang"),"ar")
        self.assertEqual(nat.findtext("desc"),"وصف عربي حقيقي")
        self.assertEqual(nat.find("desc").get("lang"),"ar")
        self.assertEqual(stats["forced_arabic_description_untranslated"],0)
        self.assertEqual(stats["forced_arabic_description_missing_in_output"],0)
        self.assertEqual(stats["forced_arabic_title_language_review"],0)

    def test_sports_hybrid_remains_en_title_and_ar_real_desc(self):
        with tempfile.TemporaryDirectory() as d:
            code,root,stats=run(Path(d),"تربية الحيوانات","تقرير أصلي")
        sport=choose(root,OTHER)
        self.assertEqual(code,0)
        self.assertEqual(sport.findtext("title"),"Live Sports Channel")
        self.assertEqual(sport.find("title").get("lang"),"en")
        self.assertEqual(sport.findtext("desc"),"وصف أصلي عربي")
        self.assertEqual(sport.find("desc").get("lang"),"ar")
        self.assertEqual(stats["english_title_applied"],1)
        self.assertEqual(stats["hybrid_programmes"],1)

    def test_no_sources_have_changed_identifiers(self):
        with tempfile.TemporaryDirectory() as d:
            code,root,stats=run(Path(d),"تربية الحيوانات")
        self.assertEqual({c.get("id") for c in root.findall("channel")},{NATGEO,OTHER})
        self.assertEqual(stats["channels"],2)
        self.assertEqual(stats["programmes"],2)

if __name__=="__main__":unittest.main()
