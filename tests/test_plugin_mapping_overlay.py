# -*- coding: utf-8 -*-
"""Guard against destroying the recently working Smart Mapping UI."""
import ast
import hashlib
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "source/overlay/ui/channel_mapping.py"
SNAPSHOT = ROOT / "data/release-inspect/channel_mapping.py"


class TestSmartMappingPackage(unittest.TestCase):
    def test_rc78_receiver_baseline_is_immutable(self):
        # Historical baseline used by assemble_rc78_staging.py MUST NOT be
        # rewritten to the new RC80 UI: otherwise that legacy stage no longer
        # authenticates the original rc78 release.
        raw = SNAPSHOT.read_bytes()
        blob = b"blob " + str(len(raw)).encode("ascii") + bytes([0]) + raw
        self.assertEqual(hashlib.sha1(blob).hexdigest(),
                         "d97aa4fed4b4619824efe16668cd9e82d4e1718d")
        self.assertNotEqual(OVERLAY.read_bytes(), raw)

    def test_rc80_only_changes_approved_preview_methods(self):
        baseline = ast.parse(SNAPSHOT.read_text(encoding="utf-8"))
        current = ast.parse(OVERLAY.read_text(encoding="utf-8"))
        def methods(tree):
            return {node.name: node for cls in tree.body
                    if isinstance(cls, ast.ClassDef) and cls.name == "ChannelMappingScreen"
                    for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        old, new = methods(baseline), methods(current)
        # RC80 intentionally restores two runtime callbacks already confirmed
        # present on the owner's working RC79.1 by a read-only receiver audit.
        restored = {"_queue_github_best_match_health",
                    "_poll_github_best_match_health"}
        self.assertTrue(restored.issubset(new),
                        "Live GitHub Best Match callbacks must be preserved")
        self.assertEqual(set(old), set(new) - restored,
                         "Lost/added methods beyond the reviewed live parity bridge")
        # __init__ registers the single eTimer, cleanup stops it; both
        # changes are required to preserve RC79.1's async UI behavior.
        approved = {"_cross_source_suggestion_candidates",
                    "_refresh_cross_source_suggestions", "__init__",
                    "_cleanup_fast_open"}
        changed = {key for key in old if ast.dump(old[key]) != ast.dump(new[key])}
        self.assertEqual(changed, approved,
                         "RC80 preview work modified unapproved receiver GUI methods")
        self.assertEqual(ast.dump(old["_auto_batch_match"]),
                         ast.dump(new["_auto_batch_match"]),
                         "Real Auto Repair writer must remain untouched")

    def test_verified_epg_evidence_only_touches_preview(self):
        src = OVERLAY.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(OVERLAY))
        self.assertIn("from ..core import verified_epg_match", src)
        self.assertIn("verified_epg_match.annotate_suggestions(rows)", src)
        self.assertIn("EPG NOW", src)
        self.assertIn("GENERATED?", src)
        method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef)
                      for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_cross_source_suggestion_candidates")
        self.assertFalse(any(isinstance(node, ast.Call) and
                             isinstance(node.func, ast.Attribute) and
                             node.func.attr in ("assign_exclusive", "save", "write")
                             for node in ast.walk(method)))
        # The receiver-facing automatic writer is deliberately NOT patched.
        self.assertIn("def _auto_batch_match(", src)
        self.assertIn("def _start_auto_repair_batch(", src)

    def test_mapping_behaviour_preserved(self):
        src = OVERLAY.read_text(encoding="utf-8")
        self.assertGreater(len(src.splitlines()), 9000)
        ast.parse(src, filename=str(OVERLAY))
        for symbol in (
            "def _auto_batch_match(",
            "def _refresh_cross_source_suggestions(",
            "def _merge_persisted_store_ownership(",
            "def _queue_manual_generated_sync(",
            "def open_manual_source_override(",
            "def auto_map_current_channel(",
            "def auto_map_all(",
            "def undo_last_mapping(",
            "class ChannelMappingScreen(",
        ):
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, src)


if __name__ == "__main__":
    unittest.main()
