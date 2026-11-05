"""Regression coverage for receivers still using the archived source repository."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import enable_github_direct as installer


class InstalledSourceMigration(unittest.TestCase):
    def fixture(self, root):
        core = root / "core"
        core.mkdir()
        external = core / "external_sources.py"
        sync = core / "github_direct_sync.py"
        external.write_text(
            'EPG_SCRAPERS_BASE = "' + installer.OLD_SOURCE_ROOT + 'feeds"\n'
            'SOURCE_ID = "ext_epgscrapers_morocco"\n'
            'MENA_CLOUD_BASE = "https://raw.githubusercontent.com/wacayoub/EPGManager/mena-data"\n',
            encoding="utf-8")
        sync.write_text(
            'BASE = "' + installer.OLD_SOURCE_ROOT + 'feeds"\n'
            'REPORT_CSV = "' + installer.OLD_SOURCE_ROOT + 'reports/epg-source-id-monitoring.csv"\n',
            encoding="utf-8")
        return core, external, sync

    def test_urls_change_but_saved_ids_and_canonical_source_are_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core, external, sync = self.fixture(root)
            self.assertEqual(installer.migrate_source_urls(core, backup_base=root/"backup"), 0)
            data = {}
            exec(external.read_text(encoding="utf-8"), data)
            self.assertEqual(data["EPG_SCRAPERS_BASE"], installer.NEW_SOURCE_ROOT+"feeds")
            self.assertEqual(data["SOURCE_ID"], "ext_epgscrapers_morocco")
            self.assertTrue(data["MENA_CLOUD_BASE"].endswith("/mena-data"))
            status = {}
            exec(sync.read_text(encoding="utf-8"), status)
            self.assertEqual(status["REPORT_CSV"], installer.NEW_SOURCE_ROOT+"reports/epg-source-id-monitoring.csv")
            first = external.read_bytes(), sync.read_bytes()
            self.assertEqual(installer.migrate_source_urls(core, backup_base=root/"backup"), 0)
            self.assertEqual(first, (external.read_bytes(), sync.read_bytes()))
            self.assertEqual(len(list((root/"backup").iterdir())), 1)

    def test_check_mode_creates_no_backup_and_makes_no_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core, external, sync = self.fixture(root)
            first = external.read_bytes(), sync.read_bytes()
            installer.migrate_source_urls(core, check=True, backup_base=root/"backup")
            self.assertEqual(first, (external.read_bytes(), sync.read_bytes()))
            self.assertFalse((root/"backup").exists())

    def test_modern_compatibility_rewriter_keeps_its_legacy_input_prefix(self):
        current = ('SCRAPERS_BASE = "'+installer.NEW_SOURCE_ROOT+'feeds"\n'
                   'def canonical_source_url(url):\n'
                   '    old_base = "'+installer.OLD_SOURCE_ROOT+'feeds/"\n'
                   '    return url.replace(old_base, SCRAPERS_BASE+"/")\n')
        self.assertEqual(installer.patch_source_urls(current, "external_sources.py"), current)

    def test_rc80_report_stream_evidence_and_import_guard_urls_move_together(self):
        definitions = {
            "github_direct_stream.py": 'PREFIX = "'+installer.OLD_SOURCE_ROOT+'feeds/"\nAUDITS = "'+installer.OLD_SOURCE_ROOT+'reports/"\n',
            "github_match_health.py": 'REPORT_BASE = "'+installer.OLD_SOURCE_ROOT+'reports/"\n',
            "verified_epg_refresh.py": 'ROOT = "'+installer.OLD_SOURCE_ROOT+'"\n',
            "direct_native_import.py": 'def allowed(url):\n    return url.startswith("'+installer.OLD_SOURCE_ROOT+'feeds/") and url.endswith(".xml.gz")\n',
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core, _, _ = self.fixture(root)
            for name, source in definitions.items():
                (core/name).write_text(source, encoding="utf-8")
            installer.migrate_source_urls(core, backup_base=root/"backup")
            for name in definitions:
                updated = (core/name).read_text(encoding="utf-8")
                self.assertNotIn(installer.OLD_SOURCE_ROOT, updated)
                self.assertIn(installer.NEW_SOURCE_ROOT, updated)
            scope = {}
            exec((core/"direct_native_import.py").read_text(encoding="utf-8"), scope)
            self.assertTrue(scope["allowed"](installer.NEW_SOURCE_ROOT+"feeds/morocco.xml.gz"))
            self.assertFalse(scope["allowed"]("https://example.com/morocco.xml.gz"))

    def test_failed_second_write_restores_original_bytes_and_keeps_user_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core, external, sync = self.fixture(root)
            cached = root/"morocco.xml.lastgood"
            cached.write_bytes(b"saved EPG")
            first = external.read_bytes(), sync.read_bytes()
            real_write = installer.atomic_write
            def fail_second(path, text):
                if path == sync:
                    raise OSError("write failed")
                real_write(path, text)
            with patch.object(installer, "atomic_write", side_effect=fail_second):
                with self.assertRaises(OSError):
                    installer.migrate_source_urls(core, backup_base=root/"backup")
            self.assertEqual(first, (external.read_bytes(), sync.read_bytes()))
            self.assertEqual(cached.read_bytes(), b"saved EPG")

    def test_invalid_second_module_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core, external, sync = self.fixture(root)
            sync.write_text("invalid python !!!", encoding="utf-8")
            first = external.read_bytes()
            with self.assertRaises(SyntaxError):
                installer.migrate_source_urls(core, backup_base=root/"backup")
            self.assertEqual(external.read_bytes(), first)
            self.assertFalse((root/"backup").exists())


if __name__ == "__main__":
    unittest.main()
