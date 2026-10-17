# -*- coding: utf-8 -*-
"""RC80 private-compatible Best Match regressions. No Enigma2/receiver needed."""
import ast
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "source" / "overlay" / "ui" / "channel_mapping.py"

class FakeTimer:
    def __init__(self): self.calls = []
    def start(self, *args): self.calls.append(args)
    def stop(self): self.calls.append(("stop",))

class FakeHealth:
    def __init__(self):
        self.generation_value = 0
        self.busy = False
        self.requests = []
    def generation(self): return self.generation_value
    def is_busy(self): return self.busy
    def refresh_async(self, source_ids):
        self.requests.append(set(source_ids))
        return True
    def health(self, sid, cid): return "EPG_OK" if (sid, cid) == ("osn", "123") else "UNKNOWN"

class LiveParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = UI.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.code, filename=str(UI))
        cls.screen = next(x for x in cls.tree.body if isinstance(x, ast.ClassDef)
                          and x.name == "ChannelMappingScreen")
        cls.methods = {x.name:x for x in cls.screen.body
                       if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))}

    def harness(self):
        names = ("_queue_github_best_match_health", "_poll_github_best_match_health")
        nodes = [self.methods[n] for n in names]
        # Execute only these two syntactically reviewed methods with fake dependencies.
        cls_ast = ast.ClassDef(name="Harness", bases=[], keywords=[], body=nodes, decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[cls_ast], type_ignores=[]))
        health = FakeHealth()
        namespace = {"github_match_health":health}
        exec(compile(module,str(UI),"exec"),namespace)
        screen=namespace["Harness"]()
        screen._github_best_health_timer=FakeTimer()
        screen._github_health_generation=0
        screen.selection_cache={("__smart_suggestions__","demo"):[1],("other","keep"):[2]}
        screen.source_groups=[{"suggestion_all_sources":True}]
        screen.bouquet_services=[{"ref":"example"}]
        screen._current=lambda kind,values: values[0]
        screen.paint_count=0
        screen._refresh_cross_source_suggestions=lambda service: setattr(screen,"paint_count",screen.paint_count+1)
        return screen,health

    def test_complete_overlay_compiles(self):
        compile(self.code,str(UI),"exec")

    def test_both_callbacks_are_methods(self):
        self.assertIn("_queue_github_best_match_health",self.methods)
        self.assertIn("_poll_github_best_match_health",self.methods)

    def test_source_quality_runtime_import_present(self):
        self.assertIn("github_direct_sync, system_check, smart_source_cache_refresh, github_match_health",self.code)

    def test_timer_registration_and_lifecycle(self):
        self.assertIn("_ghcb.append(self._poll_github_best_match_health)",self.code)
        self.assertIn('getattr(self, "_github_best_health_timer", None)',self.code)

    def test_queue_accepts_only_valid_source_ids(self):
        screen,health=self.harness()
        screen._queue_github_best_match_health([{"source_id":"osn"},{},{"source_id":"bein"},{"source_id":"osn"}])
        self.assertEqual(health.requests,[{"osn","bein"}])
        self.assertEqual(screen._github_best_health_timer.calls,[(350,True)])

    def test_queue_empty_does_nothing(self):
        screen,health=self.harness()
        screen._queue_github_best_match_health([])
        self.assertEqual(health.requests,[])
        self.assertEqual(screen._github_best_health_timer.calls,[])

    def test_cache_invalidated_only_on_new_evidence(self):
        screen,health=self.harness()
        health.generation_value=3
        screen._poll_github_best_match_health()
        self.assertEqual(screen._github_health_generation,3)
        self.assertEqual(screen.selection_cache,{("other","keep"):[2]})
        self.assertEqual(screen.paint_count,1)
        screen._poll_github_best_match_health()
        self.assertEqual(screen.paint_count,1)

    def test_poll_busy_schedules_followup(self):
        screen,health=self.harness()
        health.busy=True
        screen._poll_github_best_match_health()
        self.assertEqual(screen._github_best_health_timer.calls,[(450,True)])

    def test_no_background_network_on_ui_tick(self):
        self.assertNotIn("refresh_async",ast.get_source_segment(self.code,self.methods["_poll_github_best_match_health"]))

    def test_quality_fallback_and_owner_preservation(self):
        text = ast.get_source_segment(self.code,self.methods["_cross_source_suggestion_candidates"])
        self.assertIn('"_github_health"',text)
        self.assertIn('"SYNTHETIC", "NO_EPG"',text)
        self.assertIn('"_verified_epg_rank"',text)
        self.assertIn('"_suggestion_current"',text)
        self.assertIn('info = self._primary_mapping_info_for_ref',text)

    def test_new_on_air_evidence_still_prioritized(self):
        text = ast.get_source_segment(self.code,self.methods["_cross_source_suggestion_candidates"])
        self.assertIn("verified_epg_match.quality_for_id",text)
        self.assertIn("verified_epg_match.annotate_suggestions",text)
        self.assertIn("live_rank.get(live_health, 3)",text)

    def test_live_queue_runs_after_nonblocking_suggestion_calculation(self):
        text=ast.get_source_segment(self.code,self.methods["_refresh_cross_source_suggestions"])
        self.assertLess(text.index("self._cross_source_suggestion_candidates"),text.index("self._queue_github_best_match_health"))
        self.assertIn("self.selection_cache",text)

if __name__ == "__main__":
    unittest.main()
