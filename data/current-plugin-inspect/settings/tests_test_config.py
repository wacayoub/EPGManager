# -*- coding: utf-8 -*-
"""
tests/test_config.py

Tests core/config.py's JSON-fallback backend (used automatically outside
Enigma2). The Enigma2-native backend (ConfigClock/ConfigSubsection/etc.)
can't be exercised here since it requires the real `Components.config`
module, but the JSON backend shares the exact same public method
signatures, so these tests cover the actual contract every caller relies
on.

Run with:
    python3 -m unittest EPGManager.tests.test_config -v
"""

import os
import shutil
import tempfile
import unittest

from ..core import config as config_module


class TestConfigJSONBackend(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_path = config_module.JSON_FALLBACK_PATH
        config_module.JSON_FALLBACK_PATH = os.path.join(self.tmpdir, "epgmanager.conf")
        # Force the JSON-fallback path regardless of test environment.
        self._orig_have_enigma = config_module._HAVE_ENIGMA_CONFIG
        config_module._HAVE_ENIGMA_CONFIG = False
        self.config = config_module.Config()

    def tearDown(self):
        config_module.JSON_FALLBACK_PATH = self._orig_path
        config_module._HAVE_ENIGMA_CONFIG = self._orig_have_enigma
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_epg_output_dir_defaults_to_jedi_epg(self):
        self.assertEqual(self.config.get_epg_output_dir(),
                          config_module.DEFAULT_EPG_OUTPUT_DIR)

    def test_epg_output_dir_roundtrip(self):
        self.config.set_epg_output_dir("/media/hdd/epg")
        self.assertEqual(self.config.get_epg_output_dir(), "/media/hdd/epg")

    def test_epg_output_dir_blank_falls_back_to_default(self):
        self.config.set_epg_output_dir("   ")
        self.assertEqual(self.config.get_epg_output_dir(),
                          config_module.DEFAULT_EPG_OUTPUT_DIR)

    def test_epg_output_dir_persists_across_instances(self):
        self.config.set_epg_output_dir("/media/hdd/epg2")
        reloaded = config_module.Config()
        self.assertEqual(reloaded.get_epg_output_dir(), "/media/hdd/epg2")

    def test_daily_update_time_default(self):
        self.assertEqual(self.config.get_daily_update_time(), (6, 0))

    def test_daily_update_time_roundtrip(self):
        self.config.set_daily_update_time(14, 30)
        self.assertEqual(self.config.get_daily_update_time(), (14, 30))

    def test_daily_update_time_survives_corrupt_value(self):
        self.config._data["daily_update_time"] = "not-a-time"
        self.assertEqual(self.config.get_daily_update_time(), (6, 0))

    def test_schedule_mode_default_is_daily(self):
        self.assertEqual(self.config.get_schedule_mode(), "daily")

    def test_schedule_mode_accepts_weekly_and_monthly(self):
        self.config.set_schedule_mode("weekly")
        self.assertEqual(self.config.get_schedule_mode(), "weekly")
        self.config.set_schedule_mode("monthly")
        self.assertEqual(self.config.get_schedule_mode(), "monthly")

    def test_schedule_mode_rejects_invalid_value(self):
        with self.assertRaises(ValueError):
            self.config.set_schedule_mode("hourly")

    def test_weekly_update_day_default_and_roundtrip(self):
        self.assertEqual(self.config.get_weekly_update_day(), 0)
        self.config.set_weekly_update_day(4)
        self.assertEqual(self.config.get_weekly_update_day(), 4)

    def test_weekly_update_day_clamped_to_valid_range(self):
        self.config.set_weekly_update_day(99)
        self.assertEqual(self.config.get_weekly_update_day(), 6)
        self.config.set_weekly_update_day(-5)
        self.assertEqual(self.config.get_weekly_update_day(), 0)

    def test_monthly_update_day_default_and_roundtrip(self):
        self.assertEqual(self.config.get_monthly_update_day(), 1)
        self.config.set_monthly_update_day(15)
        self.assertEqual(self.config.get_monthly_update_day(), 15)

    def test_monthly_update_day_clamped_to_1_28(self):
        self.config.set_monthly_update_day(31)
        self.assertEqual(self.config.get_monthly_update_day(), 28)
        self.config.set_monthly_update_day(0)
        self.assertEqual(self.config.get_monthly_update_day(), 1)

    def test_last_update_roundtrip(self):
        self.assertIsNone(self.config.get_last_update())
        self.config.set_last_update(12345.0)
        self.assertEqual(self.config.get_last_update(), 12345.0)

    def test_source_enabled_defaults_true(self):
        self.assertTrue(self.config.is_source_enabled("medi1tv"))

    def test_source_enabled_roundtrip(self):
        self.config.set_source_enabled("snrt", False)
        self.assertFalse(self.config.is_source_enabled("snrt"))


if __name__ == "__main__":
    unittest.main()
