# -*- coding: utf-8 -*-
"""Stage-2 read-only receiver audit regression tests."""
import ast
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / "tools/audit_rc80_runtime_readonly.py"
spec = spec_from_file_location("epg_rc80_runtime", TOOL)
target = module_from_spec(spec)
spec.loader.exec_module(target)


class TestRc80Stage2(unittest.TestCase):
    @staticmethod
    def environment(root):
        root = Path(root)
        plugin = root / "EPGManager"
        plugin.mkdir()
        (plugin / "plugin.py").write_text("x = 1\n")
        (plugin / "version.py").write_text('__version__ = "2026.10.08-rc79.1"\nCONFIG_SCHEMA_VERSION = 15\n')
        (plugin / "core").mkdir()
        (plugin / "core/epg_cycle.py").write_text("x = 1\n")
        extension = root / "Extensions"
        (extension / "EPGImport").mkdir(parents=True)
        config = root / "etc_epgimport"
        config.mkdir()
        proc = root / "proc"
        (proc / "100").mkdir(parents=True)
        (proc / "100/comm").write_text("enigma2\n")
        (proc / "101").mkdir()
        (proc / "101/comm").write_text("ssh\n")
        return plugin, extension, config, proc

    def test_no_installation_network_or_sensitive_reads(self):
        src = TOOL.read_text(encoding="utf-8")
        ast.parse(src)
        for forbidden in ("opkg install", "opkg upgrade", "opkg remove",
                          "rm -rf", "subprocess.run(", "os.system(",
                          ".write_bytes(", ".write_text(", ".unlink(",
                          "requests.get(", "urlopen(", "/etc/enigma2/settings",
                          "import enigma", "token.json", "sshpass"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)

    def test_gui_observation_not_confused_with_functional_test(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, extension, config, proc = self.environment(td)
            result = target.audit(plugin, extension, config, proc)
            self.assertTrue(result["gui_process"]["running"])
            self.assertEqual(result["gui_process"]["count"], 1)
            self.assertTrue(result["native_import_prerequisites"]["epgimport_plugin_present"])
            self.assertEqual(result["code"]["modules"], 3)
            self.assertEqual(result["code"]["schema"], 15)
            self.assertFalse(result["gui_functionally_tested"])
            self.assertFalse(result["epg_import_executed"])
            self.assertFalse(result["opkg_rollback_tested"])
            self.assertFalse(result["authorized_to_install"])

    def test_invalid_python_detected_without_receiver_import(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, extension, config, proc = self.environment(td)
            (plugin / "core/epg_cycle.py").write_text("def broken(:\n")
            result = target.audit(plugin, extension, config, proc)
            self.assertIn("MODULE_SYNTAX_OR_IO_ERROR", result["observations"])
            self.assertTrue(any("core/epg_cycle.py" in x for x in result["code"]["syntax_errors"]))

    def test_wrong_version_and_missing_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, extension, config, proc = self.environment(td)
            (plugin / "version.py").write_text('__version__ = "unknown"\n')
            result = target.audit(plugin, extension, config, proc)
            self.assertIn("UNEXPECTED_VERSION_OR_SCHEMA", result["observations"])
            missing = target.audit(Path(td) / "absent", extension, config, proc)
            self.assertFalse(missing["code"]["present"])
            self.assertFalse(missing["authorized_to_install"])

    def test_known_archive_checksum_not_a_rollback_certification(self):
        with tempfile.TemporaryDirectory() as td:
            ipk = Path(td) / "rescue.ipk"
            ipk.write_bytes(b"x" * 160)
            digest = hashlib.sha256(ipk.read_bytes()).hexdigest()
            original = target.RESCUE_SHA
            try:
                target.RESCUE_SHA = digest
                report = target.rescue_check(ipk)
                self.assertTrue(report["known_identity"])
                self.assertFalse(report["opkg_recovery_certified"])
            finally:
                target.RESCUE_SHA = original
            self.assertFalse(target.rescue_check(None)["opkg_recovery_certified"])
            self.assertFalse(target.rescue_check(Path(td) / "missing.ipk")["known_identity"])


if __name__ == "__main__":
    unittest.main()
