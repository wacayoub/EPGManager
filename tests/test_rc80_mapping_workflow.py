"""Menu actions and confirmations are tested without touching owner mappings."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import prepare_rc80_mapping_workflow as fix


class WorkflowTests(unittest.TestCase):
    def screen(self):
        box = types.SimpleNamespace(TYPE_INFO=0, TYPE_YESNO=1, TYPE_ERROR=2)
        health = types.SimpleNamespace(health=lambda *a: "UNKNOWN", describe_cached=lambda *a: {
            "status": "UNKNOWN", "latest_stop": None})
        namespace = {"MessageBox": box, "github_match_health": health,
                     "time": __import__("time")}
        exec(compile(ast.parse("class Harness:\n" + fix.NEW_METHODS + fix.MAIN_MENU + fix.ADVANCED_MENU),
                     "<actual-workflow-methods>", "exec"), namespace)
        s = namespace["Harness"]()
        s.service = {"ref": "service", "name": "Example"}
        s.epg = {"source_id": "source", "channel_id": "exact-id", "display_name": "Guide"}
        s.bouquet_services = [s.service]; s.selection_entries = [s.epg]
        s._current = lambda key, records: s.service if key == "channels" else s.epg
        s._primary_mapping_info_for_ref = lambda ref: {"source_id": "old", "channel_id": "old-id"}
        s.mapping_revision = 7; s._remap_scope_refs = []
        s._source_by_id = lambda sid: {}
        s._service_is_mapped = lambda *a: True
        s._service_is_ignored = lambda *a: False
        s._service_has_conflict = lambda *a: False
        s._paint_is_mapped = lambda *a: True
        s._cached_primary_epg_health = lambda *a: "UNKNOWN"
        s.actions = []
        s._apply_current_mapping = lambda: s.actions.append("assign")
        s._remove_current_mapping = lambda: s.actions.append("unmap")
        s._ignore_current_mapping = lambda: s.actions.append("ignore")
        s._mapping_option_selected = lambda *a: None
        s._advanced_mapping_selected = lambda *a: None
        s.dialogs = []; s.messages = []
        s.session = types.SimpleNamespace(
            openWithCallback=lambda *a, **kw: s.dialogs.append((a, kw)),
            open=lambda *a, **kw: s.messages.append((a, kw)))
        return s

    def test_main_menu_has_five_safe_entries(self):
        s = self.screen(); fake = types.ModuleType("Screens.ChoiceBox"); fake.ChoiceBox = object
        with patch.dict(sys.modules, {"Screens.ChoiceBox": fake}): s.open_mapping_options()
        rows = s.dialogs[-1][1]["list"]
        self.assertEqual([key for title, key in rows], ["search", "channel_filters", "change_menu", "undo", "advanced"])
        self.assertFalse(any(title[0].isdigit() for title, key in rows))

    def test_pending_replacement_can_still_be_cancelled(self):
        s = self.screen(); s._remap_scope_refs = ["service"]
        fake = types.ModuleType("Screens.ChoiceBox"); fake.ChoiceBox = object
        with patch.dict(sys.modules, {"Screens.ChoiceBox": fake}): s.open_mapping_options()
        self.assertEqual(s.dialogs[-1][1]["list"][-1][1], "cancel_remap")

    def test_destructive_entries_are_advanced_and_reset_is_last(self):
        s = self.screen(); fake = types.ModuleType("Screens.ChoiceBox"); fake.ChoiceBox = object
        with patch.dict(sys.modules, {"Screens.ChoiceBox": fake}): s.open_advanced_mapping_tools()
        keys = [key for title, key in s.dialogs[-1][1]["list"]]
        self.assertIn("unmap", keys); self.assertEqual(keys[-1], "reset_ids")
        self.assertNotIn("search", keys)
        self.assertFalse(any(key.startswith("filter_") for key in keys))

    def test_assignment_is_a_preview_defaulting_to_no(self):
        s = self.screen(); s._remap_scope_refs = ["service", "other"]
        s.assign_current(); args, options = s.dialogs[-1]
        self.assertFalse(options["default"])
        self.assertIn("Services affected: 2", args[2])
        self.assertIn("old / old-id", args[2]); self.assertIn("source / exact-id", args[2])
        self.assertEqual(s.actions, [])
        args[0](False); self.assertEqual(s.actions, [])

    def test_remove_and_ignore_do_nothing_before_confirmation(self):
        for method in ("unmap_current_channel", "ignore_current_channel"):
            s = self.screen(); getattr(s, method)()
            args, options = s.dialogs[-1]
            self.assertFalse(options["default"]); self.assertEqual(s.actions, [])
            args[0](False); self.assertEqual(s.actions, [])

    def test_confirmed_unchanged_selection_dispatches_once(self):
        for action in ("assign", "unmap", "ignore"):
            s = self.screen(); s._confirm_mapping_action(True, s._mapping_action_context(), action)
            self.assertEqual(s.actions, [action])

    def test_ref_candidate_revision_and_scope_changes_refuse_confirmation(self):
        for change in ("ref", "candidate", "revision", "scope"):
            s = self.screen(); expected = s._mapping_action_context()
            if change == "ref": s.service = {"ref": "other"}
            elif change == "candidate": s.epg = dict(s.epg, channel_id="different")
            elif change == "revision": s.mapping_revision += 1
            else: s._remap_scope_refs = ["other"]
            s._confirm_mapping_action(True, expected, "assign")
            self.assertEqual(s.actions, []); self.assertTrue(s.messages)

    def test_unknown_action_cannot_call_arbitrary_method(self):
        s = self.screen(); s._confirm_mapping_action(True, s._mapping_action_context(), "reset_all")
        self.assertEqual(s.actions, [])

    def test_info_never_promises_continuous_coverage(self):
        s = self.screen(); s.epg["_precision_confidence"] = 1000
        s.show_match_reason(); text = s.messages[-1][0][1]
        self.assertIn("100% (not programme coverage)", text)
        self.assertIn("Last source event ends: Unknown", text)
        self.assertIn("gaps: not measured", text)
        self.assertEqual(s.actions, [])

    def test_review_reasons_distinguish_health_states(self):
        expected = {"UNKNOWN": "unknown", "NO_EPG": "No future", "SUSPECT": "repeated",
                    "SYNTHETIC": "generated", "EPG_WARN": "verification", "EPG_OK": ""}
        for status, fragment in expected.items():
            s = self.screen(); s._cached_primary_epg_health = lambda *a, value=status: value
            self.assertIn(fragment, s._review_reason_for_service(s.service))

    def test_source_drift_is_refused(self):
        with self.assertRaisesRegex(ValueError, "inventory drift"): fix.corrected({})
        with self.assertRaisesRegex(ValueError, "preimage drift"):
            fix.corrected({name: b"unreviewed" for name in fix.ORIGINAL})


class CachedHorizonTests(unittest.TestCase):
    NOW = 1792494000.0

    def epoch(self, value):
        try: return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception: return 0

    def stamp(self, value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def lookup(self, report, status="EPG_OK"):
        ns = {"time": types.SimpleNamespace(time=lambda: self.NOW),
              "source_key": lambda sid: "feed" if sid == "source" else "",
              "_lock": threading.RLock(), "_reports": {"feed": report},
              "classify": lambda *a: status, "_epoch": self.epoch, "FRESH_SECONDS": 72*3600}
        exec(fix.SNAPSHOT, ns)
        return ns["describe_cached"]("source", "Exact-ID")

    def report(self):
        return {"generated_utc": self.stamp(self.NOW - 60),
                "rows": {"exact-id": {"latest_stop_utc": self.stamp(self.NOW + 7200)}}}

    def test_fresh_report_exposes_last_event_horizon(self):
        result = self.lookup(self.report())
        self.assertEqual(result["horizon_hours"], 2)
        self.assertEqual(result["latest_stop"], self.NOW + 7200)

    def test_expired_or_future_report_does_not_expose_horizon(self):
        for delta in (-73*3600, 7200):
            report = self.report(); report["generated_utc"] = self.stamp(self.NOW + delta)
            self.assertIsNone(self.lookup(report)["horizon_hours"])

    def test_unknown_and_no_epg_never_expose_positive_horizon(self):
        for status in ("UNKNOWN", "NO_EPG"):
            self.assertIsNone(self.lookup(self.report(), status)["horizon_hours"])

    def test_missing_and_malformed_rows_are_safe(self):
        for report in (None, {}, {"generated_utc": self.stamp(self.NOW), "rows": []},
                       {"generated_utc": self.stamp(self.NOW), "rows": {"exact-id": None}}):
            self.assertIsNone(self.lookup(report)["latest_stop"])

    def test_past_event_is_not_a_future_guide(self):
        report = self.report(); report["rows"]["exact-id"]["latest_stop_utc"] = self.stamp(self.NOW - 1)
        self.assertIsNone(self.lookup(report)["horizon_hours"])

    def test_report_is_not_mutated(self):
        import copy
        report = self.report(); expected = copy.deepcopy(report)
        self.lookup(report)
        self.assertEqual(report, expected)
