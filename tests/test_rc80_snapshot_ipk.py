"""Snapshot package safety tests: no receiver, private files, or opkg."""
import base64
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import build_rc80_snapshot_ipk as builder

CONTROL = (b'Package: enigma2-plugin-extensions-epgmanager\n'
           b'Version: 2026.10.08-rc79.1\nArchitecture: all\nDescription: fixture\n')


class SnapshotIpkTests(unittest.TestCase):
    def fixtures(self):
        sources = {'module_%03d.py' % n: b'# synthetic\n' for n in range(140)}
        assets = {'icons/%03d.png' % n: b'unchanged resource' for n in range(75)}
        modes = {name: 0o644 for name in {**sources, **assets}}
        return sources, assets, modes

    def test_complete_package_keeps_exact_assets_modes_and_install_block(self):
        sources, assets, modes = self.fixtures()
        first = builder.pack(CONTROL, sources, assets, modes)
        self.assertEqual(first, builder.pack(CONTROL, sources, assets, modes))
        parts = builder.archive.read_ar(first)
        files, file_modes = builder.archive.read_tar(parts['data.tar.gz'])
        controls, control_modes = builder.archive.read_tar(parts['control.tar.gz'])
        self.assertEqual(len(files), 215)
        for rel, raw in assets.items():
            self.assertEqual(files[builder.archive.PREFIX + rel], raw)
            self.assertEqual(file_modes[builder.archive.PREFIX + rel], modes[rel])
        self.assertEqual(controls['preinst'], b'#!/bin/sh\n# RC80 staging only: installation forbidden\nexit 90\n')
        self.assertEqual(control_modes['preinst'], 0o755)
        self.assertEqual(len(controls['conffiles'].splitlines()), 75)
        self.assertTrue(all(p.startswith(builder.archive.PREFIX) for p in files))
        for hook in ('postinst', 'prerm', 'postrm'):
            self.assertEqual(controls[hook], builder.sealed.PASSIVE_HOOK)
        self.assertIn(b'Version: 2026.10.18-rc80-candidate\n', controls['control'])

    def test_foreign_duplicate_or_crlf_control_is_rejected(self):
        for bad in (CONTROL.replace(b'epgmanager', b'otherplugin'),
                    CONTROL + b'Version: ambiguous\n',
                    CONTROL.replace(b'\n', b'\r\n'),
                    CONTROL.replace(b'Architecture: all', b'Architecture: armv7')):
            with self.subTest(control=bad), self.assertRaises(ValueError):
                builder.control_for_candidate(bad)

    def test_missing_module_and_path_escape_rejected_before_packaging(self):
        sources, assets, modes = self.fixtures()
        sources.pop('module_000.py')
        with self.assertRaises(ValueError):
            builder.pack(CONTROL, sources, assets, modes)
        for rel in ('/etc/enigma2/settings', '../escape', 'icons/../../etc',
                    'icons\\file.png', 'icons/./file.png'):
            self.assertFalse(builder.application_path(rel), rel)

    def asset_fixture(self):
        data = b'original asset'
        rows = [{'path': 'icons/%03d.png' % n, 'mode': 0o644, 'bytes': len(data)}
                for n in range(75)]
        doc = {'receiver_modified': False, 'source_hashes_rechecked': 138,
               'assets': {r['path']: {'base64': base64.b64encode(data).decode(),
                                     'mode': r['mode'], 'sha256': hashlib.sha256(data).hexdigest()}
                          for r in rows}}
        return doc, {'assets': rows}

    def test_modified_resource_bytes_are_rejected(self):
        doc, inventory = self.asset_fixture()
        doc['assets']['icons/000.png']['base64'] = base64.b64encode(b'changed asset!').decode()
        with self.assertRaisesRegex(ValueError, 'asset hash'):
            builder.assets_from_snapshot(doc, inventory)

    def test_duplicate_resource_and_mode_drift_are_rejected(self):
        doc, inventory = self.asset_fixture()
        inventory['assets'][0]['mode'] = 0o755
        with self.assertRaisesRegex(ValueError, 'asset hash'):
            builder.assets_from_snapshot(doc, inventory)
        doc, inventory = self.asset_fixture()
        inventory['assets'][1] = inventory['assets'][0]
        with self.assertRaisesRegex(ValueError, 'duplicate asset'):
            builder.assets_from_snapshot(doc, inventory)


if __name__ == '__main__':
    unittest.main()
