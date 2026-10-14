# -*- coding: utf-8 -*-
"""No receiver required: preinst backup / non-destructive postinst contracts."""
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRE = ROOT / 'release_overlay/safe_preinst.sh'
POST = ROOT / 'release_overlay/safe_postinst.sh'

class TestRc80PackageHooks(unittest.TestCase):
    def test_shell_syntax_and_no_destructive_hooks(self):
        for script in (PRE, POST):
            subprocess.run(['sh','-n',str(script)],check=True)
        post = POST.read_text(encoding='utf-8')
        for prohibited in ('rm -f ', 'rm -rf ', 'sed -i ', 'cp -f ', 'os.remove(', 'os.unlink(', 'selected_ids', 'source_time_offsets'):
            self.assertNotIn(prohibited, post)

    def test_preinst_backup_preserves_working_files_without_leaking_other_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plugin = root/'EPGManager'
            plugin.mkdir()
            (plugin/'plugin.py').write_text('working receiver source\n',encoding='utf-8')
            (plugin/'version.py').write_text('__version__ = "2026.10.08-rc79.1"\\n',encoding='utf-8')
            config = root/'config'
            config.mkdir()
            mapping = config/'epgmanager_mappings.json'
            mapping.write_text('{"KEEP":"manual"}\n',encoding='utf-8')
            (config/'settings').write_text('config.plugins.epgmanager.theme=neo\npassword.other=KEEP_SECRET\n',encoding='utf-8')
            env = dict(os.environ, EPGMGR_PLUGIN_DIR=str(plugin),
                       EPGMGR_CONFIG_DIR=str(config), EPGMGR_BACKUP_ROOT=str(root/'backups'))
            output=subprocess.run(['sh',str(PRE)],env=env,text=True,capture_output=True)
            self.assertEqual(output.returncode,0,output.stderr)
            backups=list((root/'backups').glob('pre-rc80-*'))
            self.assertEqual(len(backups),1)
            tar=backups[0]/'plugin.tar.gz'
            self.assertTrue(tar.is_file())
            with tarfile.open(tar,'r:gz') as t:
                self.assertEqual(t.extractfile('EPGManager/plugin.py').read(),b'working receiver source\n')
            self.assertEqual(mapping.read_text(),'{"KEEP":"manual"}\n')
            self.assertEqual((backups[0]/'config/epgmanager_mappings.json').read_text(),mapping.read_text())
            settings=(backups[0]/'config/epgmanager_settings.conf').read_text()
            self.assertIn('config.plugins.epgmanager.theme',settings)
            self.assertNotIn('KEEP_SECRET',settings)

    def test_postinst_on_synthetic_138_module_plugin_and_wrong_version(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            required=('plugin.py','version.py','ui/main.py','ui/channel_mapping.py',
                      'online_update.py','core/direct_native_import.py','core/epg_cycle.py',
                      'core/github_direct_stream.py','core/github_match_health.py')
            for rel in required:
                path=root/rel
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('# harmless source\n',encoding='utf-8')
            (root/'version.py').write_text('__version__ = "2026.10.14-rc80-beta1"\nCONFIG_SCHEMA_VERSION = 15\n',encoding='utf-8')
            for n in range(138-len(required)):
                (root/('extra_%03d.py'%n)).write_text('# harmless module\n',encoding='utf-8')
            state=root.parent/'receiver-state'
            state.mkdir()
            (state/'starzplay.xml').write_text('keep epg',encoding='utf-8')
            (state/'epgmanager_mappings.json').write_text('keep mapping',encoding='utf-8')
            env=dict(os.environ, EPGMGR_PLUGIN_DIR=str(root), EPGMGR_OFFLINE_TEST='1')
            run=subprocess.run(['sh',str(POST)],env=env,text=True,capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual((state/'starzplay.xml').read_text(),'keep epg')
            self.assertEqual((state/'epgmanager_mappings.json').read_text(),'keep mapping')
            (root/'version.py').write_text('__version__ = "old"\nCONFIG_SCHEMA_VERSION = 15\n',encoding='utf-8')
            failure=subprocess.run(['sh',str(POST)],env=env,text=True,capture_output=True)
            self.assertNotEqual(failure.returncode,0)

if __name__=='__main__':
    unittest.main()
