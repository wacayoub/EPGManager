from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import tempfile
import unittest

from tools.source_run_timing import observation
from tools.source_health_snapshot import inspect
from tools.test_all_source_recovery import sample

ROOT=Path(__file__).resolve().parents[1]


class SourceTiming(unittest.TestCase):
    def setUp(self):
        self.policy=json.loads((ROOT/'config/source-schedules.json').read_text())

    def test_all_source_targets_accept_late_runs(self):
        for source,row in self.policy['sources'].items():
            with self.subTest(source=source):
                now=datetime(2026,11,3,12,0,tzinfo=timezone.utc)
                result=observation(source,'schedule',now,self.policy)
                hour,minute=map(int,row['time'].split(':'))
                self.assertEqual(result['observed_delay_seconds'],round((now-now.astimezone(ZoneInfo(row['timezone'])).replace(hour=hour,minute=minute)).total_seconds()))
                self.assertTrue(result['delayed'])

    def test_manual_runs_have_no_false_delay(self):
        for source in self.policy['sources']:
            result=observation(source,'workflow_dispatch',datetime(2026,11,3,0,0,tzinfo=timezone.utc),self.policy)
            self.assertIsNone(result['observed_delay_seconds'])
            self.assertIsNone(result['scheduled_for_utc'])

    def test_elcinema_three_hours_later_on_morocco_clock(self):
        self.assertEqual(self.policy['sources']['elcinema'],
                         {'time':'04:20','timezone':'Africa/Casablanca'})
        # Five minutes after 04:20 civil time: UTC+1 in November,
        # UTC+0 during the March Ramadan suspension.
        for stamp in ('2026-11-03T03:25:00+00:00','2026-03-03T04:25:00+00:00'):
            result=observation('elcinema','schedule',datetime.fromisoformat(stamp),self.policy)
            self.assertEqual(result['observed_delay_seconds'],300)
            self.assertFalse(result['delayed'])

    def test_stc_finishes_its_allowance_before_its_consumers(self):
        def minutes(source):
            hour,minute=map(int,self.policy['sources'][source]['time'].split(':'))
            return 60*hour+minute
        for consumer in ('rotana','starzplay'):
            self.assertGreaterEqual(minutes(consumer)-minutes('stctv'),70)

    def test_local_target_follows_morocco_seasonal_clock(self):
        for month in (3,11):
            now=datetime(2026,month,3,7,12,tzinfo=timezone.utc)
            expected=round((now-now.astimezone(ZoneInfo('Africa/Casablanca')).replace(hour=7,minute=7)).total_seconds())
            self.assertEqual(observation('morocco','schedule',now,self.policy)['observed_delay_seconds'],expected)

    def test_current_index_corruption_is_not_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sample(root/'feeds/osn.xml.gz')
            self.assertEqual(inspect(root,'osn')['status'],'AVAILABLE')
            (root/'feeds/osn.txt').write_text('wrong.channel|Wrong\n')
            self.assertEqual(inspect(root,'osn')['status'],'INVALID_PUBLICATION')
