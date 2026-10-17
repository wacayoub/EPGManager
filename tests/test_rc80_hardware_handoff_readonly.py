# -*- coding: utf-8 -*-
"""Synthetic regressions for the non-invasive RC80 hardware handoff gate."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import audit_rc80_hardware_handoff_readonly as gate

class HardwareHandoffTests(unittest.TestCase):
    def fixture(self, base, version='2026.10.08-rc79.1', bridges=False):
        plugin = base/'plugin'
        (plugin/'ui').mkdir(parents=True)
        (plugin/'core').mkdir()
        (plugin/'version.py').write_text(
            '__version__ = ' + repr(version) + '\nCONFIG_SCHEMA_VERSION = 15\n')
        methods = ''.join('    def %s(self):\n        return None\n' % m for m in gate.MAPPING)
        (plugin/'ui/channel_mapping.py').write_text('class ChannelMappingScreen:\n' + methods)
        for i in range(136):
            (plugin/'core'/('dummy%d.py' % i)).write_text('value = %d\n' % i)
        if bridges:
            for p in gate.BRIDGES:
                (plugin/p).write_text('placeholder = 1\n')
        (base/'extensions'/'EPGImport').mkdir(parents=True)
        return plugin

    def test_absent_plugin_is_blocked(self):
        with TemporaryDirectory() as d:
            p=Path(d)
            r=gate.audit(p/'missing',p/'extensions',p/'proc')
            self.assertEqual(r['status'],'NO_GO_PLUGIN_MISSING')
            self.assertFalse(r['authorized_to_install'])

    def test_rc79_baseline_does_not_certify_hardware(self):
        with TemporaryDirectory() as d:
            p=Path(d);root=self.fixture(p)
            r=gate.audit(root,p/'extensions',p/'proc')
            self.assertEqual(r['status'],'RC79_BASELINE_STATIC_PASS')
            self.assertFalse(r['hardware_certified'])
            self.assertFalse(r['mappings_read'])
            self.assertFalse(r['configuration_read'])
            self.assertEqual(r['functional_evidence']['github_native_epg_import'],'NOT_TESTED')

    def test_rc80_synthetic_fails_invalid_bridge(self):
        with TemporaryDirectory() as d:
            p=Path(d)
            root=self.fixture(p,'2026.10.15-rc80-offline-blocked',bridges=True)
            r=gate.audit(root,p/'extensions',p/'proc')
            self.assertEqual(r['status'],'NO_GO_STATIC_PREREQUISITES')
            self.assertTrue(any(x.startswith('UNVERIFIED_RC80_BRIDGE:') for x in r['observations']))

    def test_auto_mapping_removed_is_blocking(self):
        with TemporaryDirectory() as d:
            p=Path(d);root=self.fixture(p)
            f=root/'ui/channel_mapping.py'
            f.write_text(f.read_text().replace('def auto_map_all(', 'def missing_auto_map_all('))
            r=gate.audit(root,p/'extensions',p/'proc')
            self.assertIn('MAPPING_API_REGRESSION',r['observations'])

if __name__ == '__main__':
    unittest.main()
