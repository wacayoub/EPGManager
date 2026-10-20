"""Read-only offline checks of the exact private Mapping workflow candidate.

Usage: python tools/audit_rc80_mapping_workflow.py <staging-directory>
"""
import ast
import copy
import json
from pathlib import Path
import sys
import types
from unittest.mock import patch

import prepare_rc80_mapping_workflow as fix


def audit(root):
    root = Path(root).resolve()
    before = {n: (root / "baseline" / n).read_bytes() for n in fix.ORIGINAL}
    after = fix.corrected(before)
    for name, raw in after.items():
        if (root / "candidate" / name).read_bytes() != raw:
            raise ValueError("candidate differs from pinned workflow patch")

    def methods(raw):
        return {c.name + "." + n.name: n for c in ast.parse(raw).body if isinstance(c, ast.ClassDef)
                for n in c.body if isinstance(n, ast.FunctionDef)}

    old, new = methods(before["ui/channel_mapping.py"]), methods(after["ui/channel_mapping.py"])
    for previous, renamed in (("assign_current", "_apply_current_mapping"),
                              ("unmap_current_channel", "_remove_current_mapping"),
                              ("ignore_current_channel", "_ignore_current_mapping"),
                              ("show_match_reason", "show_match_diagnostics")):
        a, b = old["ChannelMappingScreen." + previous], copy.deepcopy(new["ChannelMappingScreen." + renamed])
        b.name = a.name
        assert ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)
    old_health = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(before["core/github_match_health.py"]).body
                  if isinstance(n, ast.FunctionDef)}
    new_health = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(after["core/github_match_health.py"]).body
                  if isinstance(n, ast.FunctionDef)}
    assert all(new_health[k] == v for k, v in old_health.items())

    def harness(class_name, names, namespace):
        nodes = [copy.deepcopy(new[class_name + "." + n]) for n in names]
        cls = ast.ClassDef(name="Harness", bases=[], keywords=[], decorator_list=[], body=nodes)
        tree = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
        exec(compile(tree, "<actual-workflow-candidate>", "exec"), namespace)
        return namespace["Harness"]

    env = {"MultiContentEntryText": lambda **kw: kw, "RT_HALIGN_LEFT": 1, "RT_VALIGN_CENTER": 2,
           "theme": types.SimpleNamespace(STATUS_ORANGE_INT=1, STATUS_RED_INT=2, MUTED_TEXT_INT=3, WHITE_INT=4)}
    row = harness("ChannelPaneList", ["_build_row"], env)()
    row._row_color = lambda *a: 1
    service = {"ref": "example", "name": "Example", "_review_reason": "Guide information unknown"}
    assert row._build_row(service, True, "", "mapped", "Current programme")[-1]["text"] == "Guide information unknown"
    service["_review_reason"] = ""
    assert row._build_row(service, True, "", "mapped", "Current programme")[-1]["text"].startswith("NOW")

    box = types.SimpleNamespace(TYPE_INFO=0, TYPE_YESNO=1, TYPE_ERROR=2)
    screen_cls = harness("ChannelMappingScreen", ["_compact_epg_row", "_update_action_labels", "open_reset_ids_menu"],
                         {"MessageBox": box})
    class Label:
        def setText(self, value): self.text = value
    class Screen(screen_cls):
        def __getitem__(self, key): return self.labels.setdefault(key, Label())
    s = Screen(); s.labels = {}; s._auto_search_mode = False; s._remap_scope_refs = []
    s.bouquet_services = [{}]; s.selection_entries = [{}]; s._service_is_mapped = lambda *a: True
    s._current = lambda *a: {"ref": "example", "source_id": "source", "channel_id": "exact-id"}
    s._compact_source_name = lambda *a: "Source"
    text = s._compact_epg_row({"channel_id": "exact-id", "display_name": "Example"}, score=100)
    assert text.startswith("Match 100%") and "exact-id" in text
    for scope in ([], ["example", "other"]):
        s._remap_scope_refs = scope
        for focus, expected in ((1, "Auto Mapping"), (3, "Apply selected guide")):
            s.focus = focus; s._update_action_labels()
            assert s.labels["key_green"].text == expected
    messages = []
    s.session = types.SimpleNamespace(open=lambda *a: messages.append(a))
    s.reset_all_sids_and_rebuild = lambda: (_ for _ in ()).throw(AssertionError("reset fallback reached"))
    with patch.dict(sys.modules, {"Screens.ChoiceBox": None}):
        s.open_reset_ids_menu()
    assert messages[-1][2] == box.TYPE_ERROR

    return {"receiver_changed": False, "exact_candidate_verified": True,
            "original_mutation_bodies_unchanged": True, "technical_diagnostics_preserved": True,
            "all_existing_health_functions_unchanged": True,
            "actual_review_row_reason_and_normal_programme_render": "PASS",
            "actual_green_label_cases": 4, "actual_score_label": "PASS",
            "reset_menu_error_cannot_fall_through": True, "native_GUI": "NOT_TESTED"}


if __name__ == "__main__":
    print(json.dumps(audit(sys.argv[1]), indent=2))
