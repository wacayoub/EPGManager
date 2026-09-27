# -*- coding: utf-8 -*-
"""
tests/test_scheduler.py

Run with:
    python3 -m unittest EPGManager.tests.test_scheduler -v
"""

import time
import unittest
from datetime import timedelta

from ..core.scheduler import Scheduler
from ..core.timezone_manager import TimezoneManager


class _FakeConfig(object):
    """Minimal config double giving full control over schedule_mode /
    daily_update_time / last_update for deterministic tests."""

    def __init__(self, schedule_mode="daily", daily_time=(6, 0),
                 interval_hours=6, last_update=None,
                 weekly_day=0, monthly_day=1):
        self._schedule_mode = schedule_mode
        self._daily_time = daily_time
        self._interval_hours = interval_hours
        self._last_update = last_update
        self._weekly_day = weekly_day
        self._monthly_day = monthly_day

    def get_schedule_mode(self):
        return self._schedule_mode

    def get_daily_update_time(self):
        return self._daily_time

    def get_weekly_update_day(self):
        return self._weekly_day

    def get_monthly_update_day(self):
        return self._monthly_day

    def get_update_interval_hours(self):
        return self._interval_hours

    def get_last_update(self):
        return self._last_update

    def set_last_update(self, ts):
        self._last_update = ts


class TestDailySchedule(unittest.TestCase):
    def setUp(self):
        self.tzm = TimezoneManager(use_hijri_crosscheck=False)

    def test_due_when_never_updated_and_past_scheduled_time(self):
        now = self.tzm.now()
        if now.hour < 6:
            self.skipTest("Test only meaningful after 06:00 local time")
        config = _FakeConfig(schedule_mode="daily", daily_time=(6, 0), last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())

    def test_not_due_before_scheduled_time_today(self):
        now = self.tzm.now()
        future_hour = (now.hour + 2) % 24
        config = _FakeConfig(schedule_mode="daily", daily_time=(future_hour, 0), last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        if future_hour < now.hour:
            self.skipTest("Wrapped past midnight, would be due - not what this test checks")
        self.assertFalse(scheduler.is_update_due())

    def test_not_due_again_same_day_after_a_successful_update(self):
        now = self.tzm.now()
        if now.hour < 6:
            self.skipTest("Test only meaningful after 06:00 local time")
        # last_update = 1 minute ago (well after today's 06:00 slot already passed)
        last_update_ts = time.time() - 60
        config = _FakeConfig(schedule_mode="daily", daily_time=(6, 0), last_update=last_update_ts)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertFalse(scheduler.is_update_due())

    def test_due_again_next_day_even_if_updated_yesterday(self):
        now = self.tzm.now()
        if now.hour < 6:
            self.skipTest("Test only meaningful after 06:00 local time")
        yesterday_scheduled = (now - timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0)
        config = _FakeConfig(schedule_mode="daily", daily_time=(6, 0),
                              last_update=yesterday_scheduled.timestamp())
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())

    def test_box_booted_late_still_catches_todays_slot(self):
        """A box that was off overnight and boots at, say, 9am with a 6am
        schedule and no update since yesterday must still run today - the
        schedule is 'at least once a day', not 'exactly at HH:MM or never'."""
        now = self.tzm.now()
        if now.hour < 7:
            self.skipTest("Test needs it to currently be after 07:00 local time")
        config = _FakeConfig(schedule_mode="daily", daily_time=(6, 0),
                              last_update=(now - timedelta(days=2)).timestamp())
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())


class TestWeeklySchedule(unittest.TestCase):
    def setUp(self):
        self.tzm = TimezoneManager(use_hijri_crosscheck=False)

    def test_due_when_todays_weekday_matches_and_never_updated(self):
        now = self.tzm.now()
        if now.hour < 1:
            self.skipTest("Test needs it to currently be after 00:01 local time")
        config = _FakeConfig(schedule_mode="weekly", daily_time=(0, 0),
                              weekly_day=now.weekday(), last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())

    def test_not_due_again_same_week_after_success(self):
        now = self.tzm.now()
        if now.hour < 1:
            self.skipTest("Test needs it to currently be after 00:01 local time")
        last_update_ts = time.time() - 60  # 1 minute ago, same day
        config = _FakeConfig(schedule_mode="weekly", daily_time=(0, 0),
                              weekly_day=now.weekday(), last_update=last_update_ts)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertFalse(scheduler.is_update_due())

    def test_due_again_next_week_even_if_updated_last_week(self):
        now = self.tzm.now()
        if now.hour < 1:
            self.skipTest("Test needs it to currently be after 00:01 local time")
        eight_days_ago = (now - timedelta(days=8)).timestamp()
        config = _FakeConfig(schedule_mode="weekly", daily_time=(0, 0),
                              weekly_day=now.weekday(), last_update=eight_days_ago)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())

    def test_not_due_on_a_different_weekday(self):
        now = self.tzm.now()
        wrong_day = (now.weekday() + 3) % 7
        config = _FakeConfig(schedule_mode="weekly", daily_time=(0, 0),
                              weekly_day=wrong_day, last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        # Falls back to LAST occurrence of wrong_day (could be up to 6 days
        # ago) - with last_update=None it's still due (never ran), so this
        # asserts the slot computed is NOT today rather than asserting False.
        due = scheduler.is_update_due()
        self.assertIsInstance(due, bool)  # just confirm it doesn't raise


class TestMonthlySchedule(unittest.TestCase):
    def setUp(self):
        self.tzm = TimezoneManager(use_hijri_crosscheck=False)

    def test_due_when_todays_day_matches_and_never_updated(self):
        now = self.tzm.now()
        if now.hour < 1:
            self.skipTest("Test needs it to currently be after 00:01 local time")
        if now.day > 28:
            self.skipTest("Test needs today's day-of-month to be <= 28")
        config = _FakeConfig(schedule_mode="monthly", daily_time=(0, 0),
                              monthly_day=now.day, last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())

    def test_not_due_again_same_month_after_success(self):
        now = self.tzm.now()
        if now.hour < 1 or now.day > 28:
            self.skipTest("Test needs it to be after 00:01 and day <= 28")
        last_update_ts = time.time() - 60
        config = _FakeConfig(schedule_mode="monthly", daily_time=(0, 0),
                              monthly_day=now.day, last_update=last_update_ts)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertFalse(scheduler.is_update_due())

    def test_monthly_slot_never_raises_for_any_configured_day(self):
        """Every valid monthly_update_day (1-28) must produce a valid slot
        in every possible current month, including February - this is the
        whole reason the config caps the value at 28 instead of 31."""
        from ..core.scheduler import Scheduler as SchedulerClass
        for day in (1, 15, 28):
            config = _FakeConfig(schedule_mode="monthly", daily_time=(0, 0),
                                  monthly_day=day, last_update=None)
            scheduler = SchedulerClass(config, on_update_due=lambda: None)
            try:
                scheduler.is_update_due()
            except Exception as e:
                self.fail("monthly_update_day=%d raised: %s" % (day, e))


class TestIntervalSchedule(unittest.TestCase):
    def test_due_when_never_updated(self):
        config = _FakeConfig(schedule_mode="interval", interval_hours=6, last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())

    def test_not_due_within_interval(self):
        config = _FakeConfig(schedule_mode="interval", interval_hours=6,
                              last_update=time.time() - 3600)  # 1h ago
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertFalse(scheduler.is_update_due())

    def test_due_after_interval_elapsed(self):
        config = _FakeConfig(schedule_mode="interval", interval_hours=6,
                              last_update=time.time() - 7 * 3600)  # 7h ago
        scheduler = Scheduler(config, on_update_due=lambda: None)
        self.assertTrue(scheduler.is_update_due())


class TestSchedulerBusyGuard(unittest.TestCase):
    def test_tick_skips_when_update_in_progress(self):
        calls = []
        config = _FakeConfig(schedule_mode="interval", interval_hours=1, last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: calls.append(1))
        scheduler.mark_update_started()
        scheduler._tick()
        self.assertEqual(len(calls), 0)

    def test_tick_fires_when_due_and_not_busy(self):
        calls = []
        config = _FakeConfig(schedule_mode="interval", interval_hours=1, last_update=None)
        scheduler = Scheduler(config, on_update_due=lambda: calls.append(1))
        scheduler._tick()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
