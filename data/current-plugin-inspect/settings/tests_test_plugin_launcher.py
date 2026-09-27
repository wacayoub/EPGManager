# -*- coding: utf-8 -*-
"""
tests/test_plugin_launcher.py

Tests core.plugin_launcher.find_plugin()'s ranking logic entirely offline,
by injecting fake `Plugins.Plugin` / `Components.PluginComponent` modules
into sys.modules (the standard technique for testing Enigma2-adjacent code
without a real Enigma2 environment). No importlib.reload() is needed since
plugin_launcher.py imports Enigma2 modules lazily INSIDE each function call
rather than at module level, so injecting fake modules before each call is
enough for the fakes to be picked up.

Covers the real-world case found on 2026-08-09: a fork whose
WHERE_EXTENSIONSMENU entry is literally named "EPG-Importer Now" and runs
the import immediately, while its WHERE_PLUGINMENU entry ("EPG-Importer")
just opens the settings screen - find_plugin() must prefer the latter.

Run with:
    python3 -m unittest EPGManager.tests.test_plugin_launcher -v
"""

import sys
import types
import unittest

from ..core import plugin_launcher


class _FakePluginDescriptor(object):
    WHERE_PLUGINMENU = "pluginmenu"
    WHERE_EXTENSIONSMENU = "extensionsmenu"

    def __init__(self, name, description="", fnc=None):
        self.name = name
        self.description = description
        self.fnc = fnc

    def __call__(self, session=None, **kwargs):
        if self.fnc:
            self.fnc(session=session, **kwargs)


def _install_fake_enigma_modules(plugins_by_where):
    """plugins_by_where: {where: [FakePluginDescriptor, ...]}"""
    plugins_module = types.ModuleType("Plugins")
    plugin_module = types.ModuleType("Plugins.Plugin")
    plugin_module.PluginDescriptor = _FakePluginDescriptor
    plugins_module.Plugin = plugin_module

    components_module = types.ModuleType("Components")
    plugin_component_module = types.ModuleType("Components.PluginComponent")

    class _FakeRegistry(object):
        def getPlugins(self, where):
            return plugins_by_where.get(where, [])

    plugin_component_module.plugins = _FakeRegistry()
    components_module.PluginComponent = plugin_component_module

    sys.modules["Plugins"] = plugins_module
    sys.modules["Plugins.Plugin"] = plugin_module
    sys.modules["Components"] = components_module
    sys.modules["Components.PluginComponent"] = plugin_component_module


def _remove_fake_enigma_modules():
    for name in ("Plugins", "Plugins.Plugin", "Components", "Components.PluginComponent"):
        sys.modules.pop(name, None)


class TestFindPlugin(unittest.TestCase):
    def tearDown(self):
        _remove_fake_enigma_modules()

    def test_prefers_pluginmenu_over_extensionsmenu_action_entry(self):
        """The real-world case: WHERE_EXTENSIONSMENU is 'EPG-Importer Now'
        (runs the import immediately), WHERE_PLUGINMENU is the safe one."""
        safe = _FakePluginDescriptor("EPG-Importer", "Automated EPG Importer")
        unsafe = _FakePluginDescriptor("EPG-Importer Now", "Automated EPG Importer")

        _install_fake_enigma_modules({
            "pluginmenu": [safe],
            "extensionsmenu": [unsafe],
        })

        result = plugin_launcher.find_plugin("epgimport")
        self.assertIs(result, safe)

    def test_falls_back_to_extensionsmenu_when_no_pluginmenu_entry(self):
        safe_ext = _FakePluginDescriptor("EPG-Importer", "Automated EPG Importer")
        _install_fake_enigma_modules({
            "pluginmenu": [],
            "extensionsmenu": [safe_ext],
        })

        result = plugin_launcher.find_plugin("epgimport")
        self.assertIs(result, safe_ext)

    def test_returns_none_when_nothing_installed(self):
        _install_fake_enigma_modules({"pluginmenu": [], "extensionsmenu": []})
        self.assertIsNone(plugin_launcher.find_plugin("epgimport"))

    def test_openpli_style_extensionsmenu_entry_is_considered_safe(self):
        """OpenPLi's real fork: WHERE_EXTENSIONSMENU's fnc just calls main()
        - the descriptor name itself has no action words, so even without a
        WHERE_PLUGINMENU alternative it should be picked without warnings."""
        openpli_style = _FakePluginDescriptor("EPGImport", "Automated EPG Importer")
        _install_fake_enigma_modules({
            "pluginmenu": [],
            "extensionsmenu": [openpli_style],
        })

        result = plugin_launcher.find_plugin("epgimport")
        self.assertIs(result, openpli_style)

    def test_launch_calls_the_descriptor(self):
        calls = []
        safe = _FakePluginDescriptor("EPG-Importer", "Automated EPG Importer",
                                      fnc=lambda session=None: calls.append(session))
        _install_fake_enigma_modules({"pluginmenu": [safe], "extensionsmenu": []})

        ok = plugin_launcher.launch(session="FAKE_SESSION", target_key="epgimport",
                                     friendly_name="EPG-Importer")
        self.assertTrue(ok)
        self.assertEqual(calls, ["FAKE_SESSION"])


if __name__ == "__main__":
    unittest.main()
