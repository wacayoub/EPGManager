# -*- coding: utf-8 -*-
"""Guard against destroying the recently working Smart Mapping UI."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "source/overlay/ui/channel_mapping.py"
SNAPSHOT = ROOT / "data/release-inspect/channel_mapping.py"


class TestSmartMappingPackage(unittest.TestCase):
    def test_exact_recent_ui_restored(self):
        self.assertEqual(OVERLAY.read_bytes(), SNAPSHOT.read_bytes(),
                         "Current full UI and release overlay must not diverge")

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
