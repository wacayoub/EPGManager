"""Exercise the real filesystem backup and conservation checks on a fixture."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
installer=load('stale_profile_installer',ROOT/'tools/install_rc80_stale_profile.py')
backup=load('retention_backup_helpers',ROOT/'tools/install_rc80_native_retention.py')
def sha(raw):return hashlib.sha256(raw).hexdigest()


class StaleProfileInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.plugin=self.root/'plugin';self.config=self.root/'config'
        (self.plugin/'core').mkdir(parents=True);self.config.mkdir()
        self.before={'core/source_quality.py':b'old_quality = True\n','core/github_direct_sync.py':b'old_sync = True\n'}
        self.after={'core/source_quality.py':b'new_quality = True\n','core/github_direct_sync.py':b'new_sync = True\n','core/quality_profile_freshness.py':b'helper = True\n'}
        for name,raw in self.before.items():(self.plugin/name).write_bytes(raw)
        (self.plugin/'version.py').write_bytes(b'version = "fixture"\n')
        for i in range(137):(self.plugin/('fixture%d.py'%i)).write_bytes(b'preserved = True\n')
        (self.config/'epgmanager_mappings.json').write_bytes(b'{"fixture":"preserved"}\n')
        (self.config/'settings').write_bytes(b'config.plugins.epgmanager.native_import_time=10:00\nother.private.setting=never-backup\n')

    def run_install(self,consent='APPLY_STALE_PROFILE_OWNER_AUTHORIZED'):
        return installer.install(consent,backup,self.after,self.plugin,self.config,self.root/'backups',
            before_pins={n:sha(v) for n,v in self.before.items()},
            after_pins={n:sha(v) for n,v in self.after.items()},version_pin=sha((self.plugin/'version.py').read_bytes()))

    def test_verified_backup_three_module_patch_and_config_conservation(self):
        config={p.name:p.read_bytes() for p in self.config.iterdir()}
        result=self.run_install()
        self.assertTrue(result['backup_readback_verified'])
        self.assertEqual(result['installed_python_modules'],141)
        self.assertEqual(config,{p.name:p.read_bytes() for p in self.config.iterdir()})
        for name,raw in self.after.items():self.assertEqual((self.plugin/name).read_bytes(),raw)
        self.assertFalse(result['restart_executed']);self.assertFalse(result['import_executed'])
        self.assertEqual(self.run_install()['status'],'ALREADY_INSTALLED')

    def test_consent_required_before_any_backup_or_change(self):
        with self.assertRaises(ValueError):self.run_install('NO_AUTHORIZATION')
        self.assertFalse((self.root/'backups').exists())
        self.assertEqual((self.plugin/'core/source_quality.py').read_bytes(),self.before['core/source_quality.py'])

    def test_preimage_drift_refused_before_backup(self):
        target=self.plugin/'core/source_quality.py';target.write_bytes(b'drift = True\n')
        with self.assertRaises(ValueError):self.run_install()
        self.assertEqual(target.read_bytes(),b'drift = True\n')
        self.assertFalse((self.root/'backups').exists())

    def test_backup_failure_prevents_all_module_changes(self):
        with patch.object(backup,'backup_verified',side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):self.run_install()
        for name,raw in self.before.items():self.assertEqual((self.plugin/name).read_bytes(),raw)
        self.assertFalse((self.plugin/'core/quality_profile_freshness.py').exists())


if __name__=='__main__':unittest.main()
