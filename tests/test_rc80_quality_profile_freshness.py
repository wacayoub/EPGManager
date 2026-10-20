"""Regressions for a measured five-day-old profile overruling fresh EPG."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1]/'source/overlay/core/quality_profile_freshness.py'
spec = importlib.util.spec_from_file_location('quality_profile_freshness', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QualityFreshnessTests(unittest.TestCase):
    def profile(self, stamp):
        return {'specific':True,'profile_complete':True,'future_last':0,'profile_checked':stamp}

    def test_measured_old_zero_cannot_overrule_new_monitor(self):
        self.assertFalse(module.authoritative_profile(self.profile(1791973623),1792458184,1792458500))

    def test_current_measured_empty_profile_can_prove_zero(self):
        self.assertTrue(module.authoritative_profile(self.profile(100000),99999,100010))

    def test_newer_monitor_invalidates_even_recent_local_verdict(self):
        self.assertFalse(module.authoritative_profile(self.profile(100000),100001,100010))

    def test_no_timestamp_or_incomplete_profile_is_not_authority(self):
        for change in ({'profile_checked':None},{'profile_complete':False},{'specific':False}):
            with self.subTest(change=change):
                self.assertFalse(module.authoritative_profile(dict(self.profile(100000),**change),0,100010))

    def test_expired_and_future_profiles_are_not_authority(self):
        for stamp in (1,100011):
            self.assertFalse(module.authoritative_profile(self.profile(stamp),0,100010))

    def test_malformed_and_nonfinite_timestamps_are_not_authority(self):
        for stamp in ('invalid',float('nan'),float('inf'),[],{}):
            with self.subTest(stamp=stamp):
                self.assertFalse(module.authoritative_profile(self.profile(stamp),0,100010))


if __name__ == '__main__':
    unittest.main()
