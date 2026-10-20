"""Validate final-package branding and real 141-module recovery protection."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import prepare_rc80_qualified_final as final


class QualifiedFinalTests(unittest.TestCase):
    def guard(self):
        source=final.final_guard_source((ROOT/'tools/rc80_supervised_guard.py').read_text(encoding='utf-8'))
        namespace={'__name__':'_final_guard_fixture'}
        exec(compile(source,'<final-guard>','exec'),namespace)
        return namespace

    def test_missing_final_consent_refuses_before_receiver_io(self):
        ns=self.guard()
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'consent'):
                ns['main']('backup',{'consent':'fixture-owner-consent'})

    def test_guard_141_module_backup_preserves_existing_data(self):
        ns=self.guard()
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);plugin=root/'plugin';config=root/'config';plugin.mkdir();config.mkdir()
            expected={}
            for index in range(216):
                name=('module%d.py'%index) if index<141 else ('resource%d.txt'%index)
                raw=b'fixture = True\n' if index<141 else b'fixture resource\n'
                p=plugin/name;p.write_bytes(raw)
                expected[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'mode':stat.S_IMODE(p.stat().st_mode)}
            mapping=config/'epgmanager_mappings.json';mapping.write_bytes(b'{"fixture":"keep"}\n')
            ns['make_backup'](plugin,config,root/'backups',{'before':expected})
            self.assertEqual(mapping.read_bytes(),b'{"fixture":"keep"}\n')
            self.assertEqual(ns['verify_tree'](plugin,expected,141),sum(r['bytes'] for r in expected.values()))
            self.assertEqual(len(list((root/'backups').glob('*.tar.gz'))),1)

    def test_final_version_keeps_schema_and_executable_behavior(self):
        original=b'__version__ = "old-candidate"\nCONFIG_SCHEMA_VERSION = 15\ndef behavior(): return 42\n'
        result=final.final_version(original);namespace={};exec(result,namespace)
        self.assertEqual(namespace['__version__'],'2026.10.20-rc80')
        self.assertEqual(namespace['CONFIG_SCHEMA_VERSION'],15)
        self.assertEqual(namespace['behavior'](),42)
        with self.assertRaises(ValueError):final.final_version(original.replace(b'= 15',b'= 16'))

    def test_openatv_core_dependency_preserves_other_dependencies(self):
        source=b'Package: fixture\nVersion: candidate\nArchitecture: all\nDepends: python3, python3-asyncio, python3-xml\nDescription: fixture\n'
        result=final.final_control(source)
        self.assertIn(b'Depends: python3-core, python3-asyncio, python3-xml\n',result)
        self.assertIn(b'Version: 2026.10.20-rc80\n',result)

    def test_unreviewed_guard_structure_is_rejected(self):
        with self.assertRaises(ValueError):final.final_guard_source('def main(): pass\n')


if __name__=='__main__':unittest.main()
