# -*- coding: utf-8 -*-
"""Offline tests for the SSH stdin receiver-only read-only fingerprint."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from codex import receiver_readonly_probe as probe


class TestReceiverProbe(unittest.TestCase):
    def build(self, root):
        plugin = root / "installed"
        (plugin / "core").mkdir(parents=True)
        (plugin / "ui").mkdir()
        (root / "Extensions" / "EPGImport").mkdir(parents=True)
        (root / "enigma2").mkdir()
        (root / "proc" / "123").mkdir(parents=True)
        (root / "proc" / "123" / "comm").write_text("enigma2\n")
        (plugin / "version.py").write_text(
            "__version__ = '2026.10.08-rc79.1'\nCONFIG_SCHEMA_VERSION = 15\n")
        (plugin / "plugin.py").write_text("WHERE_AUTOSTART = 10\n")
        (plugin / "core" / "epg_cycle.py").write_text("clear_before_import=False,\n")
        (plugin / "ui" / "channel_mapping.py").write_text("def _auto_batch_match(self): pass\n")
        return plugin

    def test_receiver_metadata_read_only_with_private_files_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            plugin = self.build(root)
            private = root / "enigma2" / "epgmanager_mappings.json"
            secret = "PRIVATE_MAPPING_AND_KEY_MUST_NOT_APPEAR"
            private.write_text(secret, encoding="utf-8")
            prefs = root / "enigma2" / "epgmanager_source_preferences.json"
            prefs.write_text('{"token":"NEVER_LOG_THIS"}')
            before = hashlib.sha256(private.read_bytes()).hexdigest()
            result = probe.observe(plugin, root / "Extensions", root / "enigma2", root / "proc")
            content = json.dumps(result)
            self.assertEqual(result["mode"], "READ_ONLY")
            self.assertFalse(result["receiver_modified"])
            self.assertFalse(result["authorized_to_install_rc80"])
            self.assertFalse(result["mappings_read"])
            self.assertFalse(result["receiver_gui_functionally_tested"])
            self.assertEqual(result["installed_version"], "2026.10.08-rc79.1")
            self.assertEqual(result["installed_schema"], 15)
            self.assertEqual(result["installed_python_modules"], 4)
            self.assertTrue(result["epgimport_extension_present"])
            self.assertTrue(result["enigma_process_detected"])
            self.assertTrue(result["mappings_file_exists_only"])
            self.assertTrue(result["source_preferences_file_exists_only"])
            self.assertEqual(private.read_text(), secret)
            self.assertEqual(hashlib.sha256(private.read_bytes()).hexdigest(), before)
            self.assertNotIn(secret, content)
            self.assertNotIn("NEVER_LOG_THIS", content)
            self.assertFalse(result["protected_module_hashes"]["ui/theme.py"]["present"])
            self.assertTrue(result["functional_code_markers_not_hardware_proof"]
                            ["plugin.py"]["markers"]["WHERE_AUTOSTART"])

    def test_no_plugin_never_confused_with_installed_rc80(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "proc").mkdir()
            result = probe.observe(root / "absent",root,root,root / "proc")
            self.assertEqual(result["status"], "NO_EPGMANAGER_PLUGIN")
            self.assertEqual(result["installed_python_modules"], 0)
            self.assertIsNone(result["installed_version"])
            self.assertFalse(result["epgimport_extension_present"])
            self.assertFalse(result["receiver_modified"])

    def test_plugin_symlinks_never_traversed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            plugin=self.build(root)
            sensitive=root/"sensitive.py"
            sensitive.write_text("__version__='SECRET_VERSION'\n")
            version=plugin/"version.py"
            version.unlink()
            version.symlink_to(sensitive)
            result=probe.observe(plugin,root/"Extensions",root/"enigma2",root/"proc")
            self.assertIsNone(result["installed_version"])
            self.assertNotIn("SECRET_VERSION",json.dumps(result))

    def test_remote_probe_source_contains_no_write_or_external_exec(self):
        source=Path(probe.__file__).read_text()
        for forbidden in (
                "subprocess", "urllib", "requests", "socket.", "os.system",
                "write_text(", "write_bytes(", "mkdir(", "unlink(", "shutil.",
                "opkg install", "restart", "sshpass", "passwd"):
            self.assertNotIn(forbidden,source)


if __name__ == "__main__":
    unittest.main()
