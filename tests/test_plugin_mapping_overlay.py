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
