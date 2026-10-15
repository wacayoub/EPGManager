# -*- coding: utf-8 -*-
"""Dry-run receiver preflight: no network, opkg install or filesystem changes."""
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "tools/audit_rc80_receiver_preflight_readonly.py"
spec = spec_from_file_location("rc80_preflight", str(PATH))
preflight = module_from_spec(spec)
spec.loader.exec_module(preflight)

class TestRc80ReceiverReadOnlyPreflight(unittest.TestCase):
    def test_does_not_perform_writes_or_install(self):
        code=PATH.read_text(encoding="utf-8")
        for prohibited in ('opkg install', 'opkg upgrade', 'rm -rf', '.write_text(',
                           '.write_bytes(', '.unlink(', 'os.remove(', 'wget ',
                           'urlopen(', 'requests.get(', 'os.system('):
            self.assertNotIn(prohibited,code)
        self.assertIn('[exe, "status", "enigma2-plugin-extensions-epgmanager"]',code)

    def test_version_and_schema_detected_without_reading_preferences(self):
        with tempfile.TemporaryDirectory() as td:
            plugin=Path(td)/"EPGManager"
            plugin.mkdir()
            (plugin/"version.py").write_text(
                '__version__ = "2026.10.08-rc79.1"\n'
                '__build__ = "2026.10.08.neo-dark-comfortable"\n'
                'CONFIG_SCHEMA_VERSION = 15\n',encoding="utf-8")
            outcome=preflight.audit(plugin=plugin,backup=Path(td)/"backup")
            self.assertEqual(outcome["installed_plugin_version"],"2026.10.08-rc79.1")
            self.assertEqual(outcome["installed_config_schema"],15)
            self.assertFalse(outcome["authorized_to_install"])
            self.assertFalse(outcome["environment_preflight_pass"])
            self.assertFalse(any(outcome["protected_receiver_files_identical"].values()))

    def test_missing_plugin_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            outcome=preflight.audit(plugin=Path(td)/"missing",backup=Path(td))
            self.assertFalse(outcome["authorized_to_install"])
            self.assertEqual(outcome["installed_plugin_version"],"unknown")

if __name__=="__main__":
    unittest.main()
