# -*- coding: utf-8 -*-
"""Dubai+ Arabic metadata: authentic only, never fabricate descriptions."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from tools import dubaiplus_api_source as api

class TestDubaiNativeMetadata(unittest.TestCase):
    def test_week_uses_bounded_windows_and_deduplicates_boundary_listings(self):
        def provider(lang, start, stop, timeout):
            self.assertLessEqual(stop - start, timedelta(hours=48))
            listings = [{"startTime": int(day.timestamp() * 1000)} for day in (start, stop)]
            return {"entries": [{"id": "dubai", "listings": listings}]}, api.API

        with patch.object(api, "fetch_window", side_effect=provider):
            data, windows = api.fetch("en-US", 168, 10)
        self.assertEqual(len(windows), 4)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(len(data["entries"][0]["listings"]), 5)

    def test_ar_ae_localized_description_for_both_profiles(self):
        en={"program":{"title":"Dubai Television","description":"English only",
                       "descriptionLocalized":{"en-US":"English text"}}}
        ar={"program":{"title":"تلفزيون دبي",
                       "shortDescriptionLocalized":{"ar-AE":"وصف عربي أصلي"}}}
        self.assertEqual(api.ar_desc(en,ar),"وصف عربي أصلي")

    def test_ar_localized_priority_over_english(self):
        en={"program":{"description":"Only English"}}
        ar={"program":{"descriptionLocalized":{"ar":"وصف عربي أصلي"},
                       "summary":"ملخص عربي ثانوي"}}
        self.assertEqual(api.ar_desc(en,ar),"وصف عربي أصلي")

    def test_no_invented_description_or_object_as_string(self):
        en={"program":{"title":"Actual title",
                       "description":{"ar":"Nested invalid in raw field"},
                       "summary":"English synopsis only"}}
        self.assertEqual(api.ar_desc(en,{}),"")
        self.assertFalse(api.has_ar(api.ar_desc(en,{})))

    def test_localized_titles_ar_ae_supported(self):
        en={"program":{"title":"English title"}}
        ar={"program":{"title":"English title","titleLocalized":{"ar-AE":"الاسم العربي"}}}
        self.assertEqual(api.title_ar(en,ar),"الاسم العربي")

    def test_preserve_hybrid_english_title(self):
        li={"program":{"title":"Dubai Sports Live"}}
        self.assertEqual(api.title_en(li),"Dubai Sports Live")

if __name__=="__main__":
    unittest.main()
