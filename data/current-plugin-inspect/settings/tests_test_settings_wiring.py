# -*- coding: utf-8 -*-
"""Static regression tests for OpenATV settings remote-key wiring."""
import ast
import os
import unittest

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "..", "ui", "settings.py")


class TestSettingsScreenWiring(unittest.TestCase):
    def setUp(self):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source)

    def test_does_not_depend_on_configlistscreen(self):
        self.assertNotIn("ConfigListScreen", self.source,
                         "Settings must not depend on ConfigListScreen key routing")

    def test_actionmap_explicitly_owns_navigation_and_edit_keys(self):
        for required in ('"up": self._up', '"down": self._down',
                         '"left": self._left', '"right": self._right',
                         '"ok": self._ok', '"green": self.save',
                         '"red": self.cancel'):
            self.assertIn(required, self.source)

    def test_cancel_restores_opening_snapshot(self):
        self.assertIn("self._original = self._take_snapshot()", self.source)
        self.assertIn("element.value = self._clone_value(original)", self.source)

    def test_save_persists_configfile(self):
        self.assertIn("configfile.save()", self.source)


if __name__ == "__main__":
    unittest.main()
