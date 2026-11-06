"""Source-clock date, duplicate and dispatch routing regressions."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from tools.vuplus_clock import collection_gate, due_tasks, target, validate_request
from tools.source_run_timing import observation

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 8, 45, tzinfo=timezone.utc)


class SourceClockTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT/"config/source-schedules.json").read_text())

    def test_all_sixteen_daily_targets_are_due_once(self):
        late = NOW.replace(hour=20)
        due = due_tasks(self.policy, {}, late)
        self.assertEqual(len(due), 16)
        sent = {r["task"]: r for r in due}
        self.assertEqual(due_tasks(self.policy, {"day": "2026-11-06", "sent": sent}, late), [])
        self.assertEqual(len(due_tasks(self.policy, {"day": "2026-11-06", "sent": sent},
                                      late+timedelta(days=1))), 16)

    def test_clock_follows_morocco_civil_time(self):
        schedule = self.policy["sources"]["elcinema"]
        for stamp, hour in (("2026-07-03T00:00:00+00:00", 3),
                            ("2026-11-06T00:00:00+00:00", 4)):
            day = datetime.fromisoformat(stamp).date()
            due = target(schedule, day).astimezone(timezone.utc)
            self.assertEqual((due.hour, due.minute), (hour, 20))

    def test_no_early_collection_and_next_due_is_not_lost(self):
        before = NOW.replace(hour=0, minute=16)
        self.assertEqual(due_tasks(self.policy, {}, before), [])
        due = due_tasks(self.policy, {}, before+timedelta(minutes=1))
        self.assertEqual([r["task"] for r in due], ["alkass"])
        state = {"day": "2026-11-06", "sent": {"alkass": due[0]}}
        due = due_tasks(self.policy, state, before.replace(minute=30))
        self.assertEqual([r["task"] for r in due], ["tabie"])

    def test_routing_requires_current_day_and_exact_target(self):
        requests = due_tasks(self.policy, {}, NOW)
        for request in requests:
            kind = validate_request(self.policy, request, NOW)
            self.assertIn(kind, ("source", "postprocess"))
        for change in ({"day": "2026-11-05"}, {"due": NOW.isoformat()}, {"task": "unknown"}):
            with self.assertRaises((ValueError, KeyError)):
                validate_request(self.policy, dict(requests[0], **change), NOW)
        with self.assertRaises(ValueError):
            validate_request(self.policy, requests[0], NOW.replace(hour=0, minute=0))

    def test_probe_only_routes_to_probe(self):
        request = {"task": "probe", "day": "2026-11-06", "due": NOW.isoformat()}
        self.assertEqual(validate_request(self.policy, request, NOW), "probe")

    def test_scheduled_fallback_skips_completed_attempt_but_manual_remains_available(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"reports").mkdir()
            report = root/"reports/osn-publish-lkg.json"
            for decision in ("PUBLISH", "KEEP_LKG"):
                report.write_text(json.dumps({
                    "generated_utc": NOW.isoformat(), "github_run_id": "123",
                    "sources": {"osn": {"decision": decision}}}))
                for event, requested in (("schedule", ""), ("push", "2026-11-06")):
                    run, reason = collection_gate(root, self.policy, "osn", event, requested, NOW)
                    self.assertFalse(run)
                    self.assertEqual(reason, "ALREADY_ATTEMPTED_ON_LOCAL_DAY")
                self.assertTrue(collection_gate(root, self.policy, "osn",
                                               "workflow_dispatch", "", NOW)[0])
            report.unlink()
            self.assertTrue(collection_gate(root, self.policy, "osn", "schedule", "", NOW)[0])

    def test_expired_clock_request_fails(self):
        with self.assertRaises(ValueError):
            collection_gate(ROOT, self.policy, "osn", "push", "2026-11-05", NOW)

    def test_clock_collections_record_real_delay(self):
        now = NOW.replace(hour=4, minute=25)
        row = observation("elcinema", "source-clock", now, self.policy)
        self.assertEqual(row["observed_delay_seconds"], 300)

    def test_clock_workflow_uses_main_and_only_verified_requests_choose_jobs(self):
        workflow = yaml.load((ROOT/".github/workflows/vuplus-clock.yml").read_text(),
                             Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"]["push"]["branches"], ["vuplus-clock"])
        self.assertEqual(workflow["jobs"]["route"]["steps"][0]["with"]["ref"], "main")
        self.assertEqual(workflow["jobs"]["source"]["if"],
                         "needs.route.outputs.kind == 'source'")
        self.assertIn("needs.source.outputs.collection_ran", workflow["jobs"]["verify_source"]["if"])


if __name__ == "__main__":
    unittest.main()
