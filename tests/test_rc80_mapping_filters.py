"""Cached health filters must never turn unknown data into absent EPG."""
import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import prepare_rc80_mapping_filters as fix


class MappingFilterTests(unittest.TestCase):
    def harness(self, health="UNKNOWN", owner=None):
        tree = ast.parse("class Harness:\n" + fix.FILTER_METHODS)
        calls = []
        namespace = {"github_match_health": types.SimpleNamespace(
            health=lambda sid, cid: calls.append((sid, cid)) or health)}
        exec(compile(tree, "<actual-filter-methods>", "exec"), namespace)
        screen = namespace["Harness"]()
        screen._primary_mapping_info_for_ref = lambda ref: owner or {
            "source_id": "ext_epgscrapers_morocco", "channel_id": "2M"}
        screen._paint_is_mapped = lambda ref: True
        screen._service_has_conflict = lambda svc: False
        screen._service_is_ignored = lambda svc: False
        return screen, calls

    def test_only_confirmed_no_epg_enters_empty_filter(self):
        for status in (None, "UNKNOWN", "EPG_OK", "EPG_WARN", "SYNTHETIC", "SUSPECT", "NO_EPG"):
            with self.subTest(status=status):
                s, calls = self.harness(status)
                self.assertEqual(s._service_has_no_future_epg({"ref": "service"}), status == "NO_EPG")
                self.assertEqual(calls, [("ext_epgscrapers_morocco", "2M")])

    def test_review_includes_uncertain_and_degraded_guide(self):
        for status in (None, "UNKNOWN", "EPG_OK", "EPG_WARN", "SYNTHETIC", "SUSPECT", "NO_EPG"):
            s, _ = self.harness(status)
            self.assertEqual(s._service_needs_review({"ref": "service"}), status != "EPG_OK")

    def test_unmapped_conflicted_and_ignored_still_need_review(self):
        for method, result in (("_paint_is_mapped", False), ("_service_has_conflict", True), ("_service_is_ignored", True)):
            s, _ = self.harness("EPG_OK")
            setattr(s, method, lambda *a, result=result: result)
            self.assertTrue(s._service_needs_review({"ref": "service"}))

    def test_missing_service_or_owner_is_unknown(self):
        for service in (None, {}, {"name": "example"}):
            s, calls = self.harness("NO_EPG")
            self.assertFalse(s._service_has_no_future_epg(service))
            self.assertEqual(calls, [])
        s, calls = self.harness("NO_EPG", {"source_id": "source"})
        self.assertFalse(s._service_has_no_future_epg({"ref": "service"}))
        self.assertEqual(calls, [])

    def test_active_primary_owner_is_authoritative(self):
        s, calls = self.harness("EPG_OK", {"source_id": "manual-owner", "channel_id": "exact-id"})
        s._service_has_no_future_epg({"ref": "service"})
        self.assertEqual(calls, [("manual-owner", "exact-id")])

    def test_failed_health_lookup_does_not_infer_empty(self):
        s, _ = self.harness()
        def fail(ref): raise RuntimeError("unavailable")
        s._primary_mapping_info_for_ref = fail
        self.assertFalse(s._service_has_no_future_epg({"ref": "service"}))
        self.assertTrue(s._service_needs_review({"ref": "service"}))

    def test_cancel_and_invalid_answers_do_nothing(self):
        s, _ = self.harness(); calls = []
        s.set_channel_filter = calls.append
        for value in (None, [], "all", ("title",), ("title", "reset"), ("title", "all", "extra")):
            s._channel_filter_selected(value)
        self.assertEqual(calls, [])

    def test_only_four_readonly_modes_are_exposed(self):
        s, _ = self.harness(); calls = []
        s.set_channel_filter = calls.append
        fake = types.ModuleType("Screens.ChoiceBox"); fake.ChoiceBox = object
        opened = []
        s.session = types.SimpleNamespace(openWithCallback=lambda *args, **kw: opened.append((args, kw)))
        with patch.dict(sys.modules, {"Screens.ChoiceBox": fake}): s.open_channel_filters()
        choices = opened[0][1]["list"]
        self.assertEqual([x[1] for x in choices], ["all", "no_future", "review", "locked"])
        for choice in choices: s._channel_filter_selected(choice)
        self.assertEqual(calls, ["all", "no_future", "review", "locked"])

    def test_unreviewed_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, "inventory drift"): fix.corrected({})
        with self.assertRaisesRegex(ValueError, "preimage drift"):
            fix.corrected({"ui/channel_mapping.py": b"changed source"})
