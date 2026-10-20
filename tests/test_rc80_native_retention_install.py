import hashlib
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import install_rc80_native_retention as deploy


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugin = self.root/'plugin'
        self.config = self.root/'config'
        (self.plugin/'core').mkdir(parents=True)
        self.config.mkdir()
        self.backups = self.root/'backups'
        self.old, self.new = b'VALUE = 1\n', b'VALUE = 2\n'
        (self.plugin/'core/direct_native_import.py').write_bytes(self.old)
        (self.plugin/'version.py').write_bytes(b'VERSION = "candidate"\n')
        for i in range(138):
            (self.plugin/('module%d.py' % i)).write_bytes(b'# unchanged\n')
        (self.plugin/'resource.txt').write_bytes(b'asset stays')
        (self.config/'epgmanager_mappings.json').write_bytes(b'{"saved":"owner"}')
        (self.config/'settings').write_bytes(b'config.plugins.epgmanager.test=true\nconfig.plugins.other.secret=private\n')
        self.epg = self.root/'existing-epg.dat'
        self.epg.write_bytes(b'existing programmes untouched')

    def run_install(self, consent='APPLY_NATIVE_RETENTION_OWNER_AUTHORIZED'):
        return deploy.install(consent, lambda text: self.new.decode(),
            self.plugin, self.config, self.backups, deploy.sha(self.old),
            deploy.sha(self.new), deploy.sha((self.plugin/'version.py').read_bytes()))

    def test_verified_backup_and_only_one_module_changed(self):
        mapping = (self.config/'epgmanager_mappings.json').read_bytes()
        result = self.run_install()
        self.assertEqual(result['changed_modules'], ['core/direct_native_import.py'])
        self.assertTrue(result['protected_config_hashes_unchanged'])
        self.assertEqual(self.epg.read_bytes(), b'existing programmes untouched')
        self.assertEqual((self.config/'epgmanager_mappings.json').read_bytes(), mapping)
        with tarfile.open(result['backup_path']) as archive:
            self.assertEqual(archive.extractfile('EPGManager/core/direct_native_import.py').read(), self.old)
            settings = archive.extractfile('config/epgmanager-settings.conf').read()
            self.assertNotIn(b'other.secret', settings)
            self.assertIn(b'epgmanager.test', settings)
        self.assertFalse(result['restart_executed'])
        self.assertFalse(result['import_executed'])

    def test_without_consent_no_backup_or_patch(self):
        with self.assertRaises(ValueError):
            self.run_install('')
        self.assertFalse(self.backups.exists())
        self.assertEqual((self.plugin/'core/direct_native_import.py').read_bytes(), self.old)

    def test_drift_rejected_before_backup(self):
        (self.plugin/'core/direct_native_import.py').write_bytes(b'# user edit\n')
        with self.assertRaisesRegex(ValueError, 'drift'):
            self.run_install()
        self.assertFalse(self.backups.exists())

    def test_backup_failure_cannot_replace_module(self):
        with patch.object(deploy, 'backup_verified', side_effect=ValueError('archive verify failed')):
            with self.assertRaisesRegex(ValueError, 'archive verify'):
                self.run_install()
        self.assertEqual((self.plugin/'core/direct_native_import.py').read_bytes(), self.old)

    def test_wrong_corrected_payload_rejected_before_write(self):
        self.new = b'UNAPPROVED = True\n'
        with self.assertRaisesRegex(ValueError, 'payload SHA'):
            deploy.install('APPLY_NATIVE_RETENTION_OWNER_AUTHORIZED', lambda text: 'OTHER = 1\n',
                self.plugin,self.config,self.backups, deploy.sha(self.old),deploy.sha(self.new),
                deploy.sha((self.plugin/'version.py').read_bytes()))
        self.assertFalse(self.backups.exists())

    def test_repeated_apply_is_idempotent(self):
        self.run_install()
        count = len(list(self.backups.iterdir()))
        self.assertEqual(self.run_install()['status'], 'ALREADY_INSTALLED')
        self.assertEqual(len(list(self.backups.iterdir())), count)


if __name__ == '__main__':
    unittest.main()
