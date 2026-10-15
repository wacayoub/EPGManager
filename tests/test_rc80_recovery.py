# -*- coding: utf-8 -*-
"""Offline receiver code recovery rehearsal; no production files or opkg writes."""
from importlib.util import module_from_spec, spec_from_file_location
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/verify_rc80_recovery.py"
PREINST = ROOT / "release_overlay/safe_preinst.sh"
spec = spec_from_file_location("rc80_recovery", SCRIPT)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


class TestRc80Recovery(unittest.TestCase):
    def make_backup(self, root):
        root = Path(root)
        plugin = root / "EPGManager"
        plugin.mkdir()
        (plugin / "plugin.py").write_text("working plugin\n")
        (plugin / "version.py").write_text('__version__ = "2026.10.08-rc79.1"\n')
        (plugin / "ui").mkdir()
        (plugin / "ui/main.py").write_text("old dashboard\n")
        config = root / "etc"
        config.mkdir()
        (config / "epgmanager_mappings.json").write_text('{"manual":"KEEP"}\n')
        env = dict(os.environ, EPGMGR_PLUGIN_DIR=str(plugin),
                   EPGMGR_CONFIG_DIR=str(config), EPGMGR_BACKUP_ROOT=str(root / "backups"),
                   EPGMGR_RC80_SUPERVISED="1")
        proc = subprocess.run(["sh", str(PREINST)], env=env,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        archive = next((root / "backups").glob("pre-rc80-*/plugin.tar.gz"))
        return plugin, archive, config

    def test_preinstall_archive_validation_only_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, archive, config = self.make_backup(td)
            before = archive.read_bytes()
            report = recovery.inspect_backup(archive)
            self.assertTrue(report["backup_verified"])
            self.assertFalse(report["receiver_modified"])
            self.assertEqual(before, archive.read_bytes())
            self.assertIn("working plugin", (plugin / "plugin.py").read_text())
            self.assertIn("KEEP", (config / "epgmanager_mappings.json").read_text())

    def test_failure_recovery_keeps_current_version_and_user_data(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plugin, archive, config = self.make_backup(root)
            (plugin / "plugin.py").write_text("FAILED UPDATE\n")
            (plugin / "ui/main.py").write_text("BAD SCREEN\n")
            report = recovery.restore_code_only(archive, plugin)
            self.assertTrue(report["files_restored"])
            self.assertTrue(report["requires_opkg_reconciliation"])
            self.assertFalse(report["opkg_database_restored"])
            self.assertEqual((plugin / "plugin.py").read_text(), "working plugin\n")
            self.assertEqual((plugin / "ui/main.py").read_text(), "old dashboard\n")
            self.assertEqual((config / "epgmanager_mappings.json").read_text(),
                             '{"manual":"KEEP"}\n')
            retained = Path(report["failed_version_preserved_at"])
            self.assertEqual((retained / "plugin.py").read_text(), "FAILED UPDATE\n")

    def test_rejects_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            archive = td / "malicious.tar.gz"
            with tarfile.open(archive, "w:gz") as t:
                for n, data in [("EPGManager/plugin.py", b"test"),
                                ("EPGManager/version.py", b"__version__ = 'old'\n"),
                                ("EPGManager/../../escape", b"bad")]:
                    m = tarfile.TarInfo(n)
                    m.size = len(data)
                    t.addfile(m, io.BytesIO(data))
            with self.assertRaises(ValueError):
                recovery.inspect_backup(archive)
            with tarfile.open(archive, "w:gz") as t:
                for n in ("EPGManager/plugin.py", "EPGManager/version.py"):
                    m = tarfile.TarInfo(n)
                    m.type = tarfile.SYMTYPE
                    m.linkname = "/etc/passwd"
                    t.addfile(m)
            with self.assertRaises(ValueError):
                recovery.inspect_backup(archive)

    def test_restore_never_edits_opkg(self):
        code = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("opkg install", code)
        self.assertNotIn("opkg remove", code)
        self.assertNotIn("os.system(", code)
        self.assertIn("requires_opkg_reconciliation", code)


if __name__ == "__main__":
    unittest.main()
