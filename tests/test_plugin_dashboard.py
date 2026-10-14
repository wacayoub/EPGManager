# -*- coding: utf-8 -*-
"""Dashboard navigation topology regression tests — no Enigma2 runtime."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "source/overlay/ui/main.py"
TEXT = PATH.read_text(encoding="utf-8")
TREE = ast.parse(TEXT, filename=str(PATH))


class TestPluginDashboard(unittest.TestCase):
    def test_seven_working_main_tabs(self):
        screens = [n for n in TREE.body if isinstance(n, ast.ClassDef)
                   and n.name == "EPGManagerMainScreen"]
        self.assertEqual(len(screens), 1)
        names = {x.name for x in screens[0].body if isinstance(x, ast.FunctionDef)}
        for method in ("open_sources", "open_channel_mapping", "open_native_import",
                       "open_diagnostics", "open_settings", "open_online_update",
                       "_diagnostics_selected"):
            self.assertIn(method, names)
        self.assertIn('self.open_native_import, self.open_diagnostics, self.open_settings, self.open_online_update', TEXT)
        self.assertIn('size="292,53"', TEXT)
        self.assertNotIn('name="nav_logs"', TEXT)

    def test_legacy_diagnostics_are_reachable(self):
        for method in ("self.open_zero_epg", "self.open_duplicates",
                       "self.open_system_check", "self.open_logs"):
            self.assertIn(method, TEXT)
        self.assertIn('title="EPGManager Diagnostics"', TEXT)

    def test_remote_shortcuts_and_layout_preserved(self):
        self.assertIn('"green": self.open_channel_mapping', TEXT)
        self.assertIn('"yellow": self.run_cycle', TEXT)
        self.assertIn('"blue": self.open_sources', TEXT)
        self.assertIn('"menu": self.open_settings', TEXT)
        self.assertIn('"8": self.open_online_update', TEXT)
        self.assertIn('size="1920,1080"', TEXT)


if __name__ == "__main__":
    unittest.main()
