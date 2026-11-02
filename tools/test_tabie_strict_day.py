# -*- coding: utf-8 -*-
"""Offline, no HTTP: Tabie must not recycle previous day's programmes."""
from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo
from tabie_source import parse_local, belongs_to_requested_day

Q=ZoneInfo("Asia/Qatar")

class TestTabieStrictDay(unittest.TestCase):
    def test_real_program_of_requested_qatar_day_is_allowed(self):
        t=parse_local("2026-10-18T06:15:00")
        self.assertTrue(belongs_to_requested_day(t, "2026-10-18"))
    def test_recycled_yesterday_schedule_is_rejected(self):
        t=parse_local("2026-10-17T21:00:00")
        self.assertFalse(belongs_to_requested_day(t, "2026-10-18"))
        self.assertFalse(belongs_to_requested_day(t, "2026-10-19"))
    def test_utc_time_is_converted_to_qatar_broadcast_date(self):
        t=parse_local("2026-10-17T22:30:00Z")
        self.assertTrue(belongs_to_requested_day(t, "2026-10-18"))
        self.assertFalse(belongs_to_requested_day(t, "2026-10-17"))
    def test_bad_provider_date_never_passes(self):
        self.assertFalse(belongs_to_requested_day(None,"2026-10-18"))
        self.assertFalse(belongs_to_requested_day(parse_local("not-a-date"),"2026-10-18"))
    def test_midnight_rollover_is_day_following(self):
        t=parse_local("2026-10-18T00:10:00+03:00")
        self.assertFalse(belongs_to_requested_day(t,"2026-10-17"))
        self.assertTrue(belongs_to_requested_day(t,"2026-10-18"))
    def test_offset_conversion_is_not_casablanca_timezone(self):
        dt=datetime(2026,10,17,23,0,tzinfo=timezone.utc)
        self.assertTrue(belongs_to_requested_day(dt,"2026-10-18"))
        self.assertEqual(dt.astimezone(Q).hour,2)

if __name__=="__main__":
    unittest.main(verbosity=2)
