"""Publication timestamps, receipt conservation and explicit GMT+0 formatting."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync_timing_tested', ROOT/'source/overlay/core/sync_timing.py')
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


class SyncTimingTests(unittest.TestCase):
    def test_offset_is_converted_before_utc_label(self):
        self.assertEqual(timing.stamp('2026-10-20T10:20:00+02:00'), '20/10 08:20')

    def test_equivalent_iso_and_epoch_instants(self):
        epoch = datetime(2026, 10, 20, 8, 20, tzinfo=timezone.utc).timestamp()
        self.assertEqual(timing.utc_time('2026-10-20T08:20:00Z'), timing.utc_time(epoch))
        self.assertEqual(timing.utc_time('2026-10-20 08:20:00'), timing.utc_time(epoch))

    def test_publication_is_not_presented_as_receiver_check(self):
        checked = datetime(2026, 10, 20, 8, 20, tzinfo=timezone.utc).timestamp()
        self.assertEqual(timing.source_refresh_text({'generated':'2026-10-19T14:36:00Z', 'checked':checked}),
                         '19/10 14:36 / 20/10 08:20')

    def test_missing_check_is_unknown_not_inferred_from_publication(self):
        self.assertEqual(timing.source_refresh_text({'generated':'2026-10-19T14:36:00Z'}),
                         '19/10 14:36 / Unknown')

    def test_invalid_timestamps_fail_closed(self):
        for value in (None, '', 'not a date', float('nan'), float('inf'), -1, True, 10**30):
            with self.subTest(value=value):
                self.assertEqual(timing.stamp(value), 'Unknown')

    def test_current_clock_is_explicitly_utc(self):
        now = timing.utc_now()
        self.assertEqual(now.utcoffset().total_seconds(), 0)
        self.assertLess(abs((now-datetime.now(timezone.utc)).total_seconds()), 5)

    def test_metadata_only_refresh_preserves_genuine_success(self):
        previous={'a':{'local_synced':100, 'local_path':'fixture.xml', 'generated':'old'}}
        current={'a':{'generated':'new', 'checked':200}}
        result=timing.preserve_sync_receipts(previous,current)
        self.assertEqual(result['a'],{'generated':'new','checked':200,'local_synced':100,'local_path':'fixture.xml'})
        self.assertNotIn('local_synced',current['a'])
        self.assertEqual(previous['a']['generated'],'old')

    def test_failed_sync_does_not_advance_success_or_hide_failure(self):
        result=timing.preserve_sync_receipts({'a':{'local_synced':100}},
            {'a':{'checked':200,'ok':False,'local_sync_error':'fixture unavailable'}})
        self.assertEqual(result['a']['local_synced'],100)
        self.assertFalse(result['a']['ok'])
        self.assertEqual(result['a']['local_sync_error'],'fixture unavailable')

    def test_success_clears_old_error_even_in_same_second(self):
        result=timing.preserve_sync_receipts({'a':{'local_synced':100,'local_sync_error':'old failure'}},
            {'a':{'local_synced':100,'ok':True}})
        self.assertNotIn('local_sync_error',result['a'])

    def test_metadata_refresh_does_not_invent_success_or_restore_deleted_sources(self):
        result=timing.preserve_sync_receipts({'removed':{'local_synced':100}}, {'a':{'ok':True}})
        self.assertEqual(result,{'a':{'ok':True}})


if __name__ == '__main__':
    unittest.main()
