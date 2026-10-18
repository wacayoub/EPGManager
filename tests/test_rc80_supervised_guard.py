"""Synthetic installation-boundary tests. No real receiver or opkg access."""
import hashlib
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import rc80_supervised_guard as guard
import prepare_rc80_supervised_ipk as prepare


class SupervisedGuardTests(unittest.TestCase):
    def fixture(self, home):
        root = home/'plugin'
        root.mkdir()
        before = {}
        files = {'module_%03d.py' % n: b'# original working module\n' for n in range(138)}
        files.update({'icons/%03d.png' % n: b'keep resource' for n in range(75)})
        for rel, raw in files.items():
            p = root/rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            before[rel] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                           'mode': p.stat().st_mode & 0o777}
        return root, {'before': before, 'consent': 'fixture-consent'}

    def test_ordinary_install_cannot_write_or_start_backup(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(guard, 'check_before') as check, \
                patch.object(guard, 'make_backup') as backup:
            with self.assertRaisesRegex(ValueError, 'consent'):
                guard.main('backup', {'consent': 'fixture'})
            check.assert_not_called()
            backup.assert_not_called()

    def test_installed_removal_hooks_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder/'enigma2-plugin-extensions-epgmanager.prerm'
            path.write_text('#!/bin/sh\n# passive\nexit 0\n')
            self.assertTrue(guard.check_old_removal_hooks([folder]))
            path.write_text('#!/bin/sh\nrm -rf /etc/enigma2\nexit 0\n')
            with self.assertRaisesRegex(ValueError, 'requires review'):
                guard.check_old_removal_hooks([folder])

    def test_resource_drift_and_unexpected_module_stop_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self.fixture(Path(tmp))
            guard.verify_tree(root, manifest['before'], 138)
            resource = root/'icons/000.png'
            resource.write_bytes(b'new user resource')
            with self.assertRaisesRegex(ValueError, 'content drift'):
                guard.verify_tree(root, manifest['before'], 138)
            resource.write_bytes(b'keep resource')
            (root/'unexpected.py').write_bytes(b'# extra')
            with self.assertRaisesRegex(ValueError, 'inventory drift'):
                guard.verify_tree(root, manifest['before'], 138)

    def test_backup_preserves_originals_and_excludes_other_plugins_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root, manifest = self.fixture(home)
            config = home/'config'
            config.mkdir()
            mapping = config/'epgmanager_mappings.json'
            mapping.write_bytes(b'KEEP_MAPPING')
            settings = config/'settings'
            settings.write_bytes(b'config.plugins.epgmanager.theme=keep\nconfig.plugins.other.secret=PRIVATE\n')
            (config/'epg.dat').write_bytes(b'KEEP_EXISTING_EPG')
            result = guard.make_backup(root, config, home/'backup', manifest)
            archives = list((home/'backup').glob('*.tar.gz'))
            self.assertEqual(len(archives), 1)
            self.assertEqual(result, hashlib.sha256(archives[0].read_bytes()).hexdigest())
            with tarfile.open(archives[0], 'r:gz') as tar:
                self.assertEqual(tar.extractfile('config/epgmanager_mappings.json').read(), b'KEEP_MAPPING')
                self.assertEqual(tar.extractfile('config/epgmanager-settings.conf').read(),
                                 b'config.plugins.epgmanager.theme=keep\n')
                self.assertFalse(any('epg.dat' in n for n in tar.getnames()))
            self.assertEqual(mapping.read_bytes(), b'KEEP_MAPPING')
            self.assertEqual((config/'epg.dat').read_bytes(), b'KEEP_EXISTING_EPG')
            self.assertIn(b'PRIVATE', settings.read_bytes())
            guard.verify_tree(root, manifest['before'], 138)
            if os.name == 'posix':
                self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)

    def test_low_disk_space_stops_before_backup_directory_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root, manifest = self.fixture(home)
            with patch.object(guard.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()):
                with self.assertRaisesRegex(ValueError, 'space'):
                    guard.make_backup(root, home/'config', home/'backup', manifest)
            self.assertFalse((home/'backup').exists())

    def test_hooks_embed_independent_compilable_checks_without_restart(self):
        source = Path(guard.__file__).read_text()
        for mode in ('backup', 'verify'):
            hook = prepare.hook_source(source, {'consent': 'example'}, mode).decode()
            embedded = hook.split("<<'RC80_PYTHON'\n", 1)[1].rsplit('\nRC80_PYTHON', 1)[0]
            compile(embedded, '<synthetic-hook>', 'exec')
            for forbidden in ('systemctl', 'init 3', 'init 4', 'os.system(', 'subprocess', 'import enigma'):
                self.assertNotIn(forbidden, embedded)

    def test_unreviewed_old_hooks_stop_before_any_backup(self):
        manifest = {'consent': 'explicit-fixture'}
        with patch.dict(os.environ, {'EPGMGR_RC80_CONSENT': 'explicit-fixture'}), \
                patch.object(guard, 'check_before', side_effect=ValueError('requires review')), \
                patch.object(guard, 'make_backup') as backup:
            with self.assertRaisesRegex(ValueError, 'requires review'):
                guard.main('backup', manifest)
            backup.assert_not_called()

    def test_postinstall_verifies_140_modules_without_configuration_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self.fixture(Path(tmp))
            after = dict(manifest['before'])
            for name in ('verified_match.py', 'verified_refresh.py'):
                p = root/name
                raw = b'# newly reviewed module\n'
                p.write_bytes(raw)
                after[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                               'mode': p.stat().st_mode & 0o777}
            with patch.object(guard, 'PLUGIN', root), patch.object(guard, 'make_backup') as backup:
                guard.main('verify', {'after': after})
                backup.assert_not_called()
            (root/'verified_match.py').write_bytes(b'# unexpected modified code\n')
            with patch.object(guard, 'PLUGIN', root), self.assertRaisesRegex(ValueError, 'content drift'):
                guard.main('verify', {'after': after})


if __name__ == '__main__':
    unittest.main()
