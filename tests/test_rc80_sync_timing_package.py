"""The timing package keeps installation consent and baseline verification."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import apply_rc80_sync_timing_fix as fix
import prepare_rc80_qualified_final as final
import prepare_rc80_sync_timing_package as package


class SyncTimingPackageTests(unittest.TestCase):
    def test_guard_keeps_141_baseline_backup_and_requires_142_after_install(self):
        original=final.final_guard_source((ROOT/'tools/rc80_supervised_guard.py').read_text(encoding='utf-8'))
        updated=package.updated_guard(original+"\nmain('backup', {'consent':'fixture'})\n")
        self.assertEqual(updated.count("manifest['before'], 141"),3)
        self.assertIn("verify_tree(PLUGIN, manifest['after'], 142)",updated)
        namespace={};exec(updated,namespace)
        with self.assertRaisesRegex(ValueError,'consent'):
            namespace['main']('backup',{'consent':'fixture'})

    def test_ambiguous_guard_is_rejected(self):
        with self.assertRaises(ValueError):
            package.updated_guard('def main(*args): pass\nmain("verify", {})\n')

    def test_original_ipk_identity_checked_before_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as tmp:
            p=Path(tmp)/'wrong.ipk';p.write_bytes(b'not the installed final package')
            with self.assertRaisesRegex(ValueError,'identity'):
                package.build(p,tmp)
            self.assertEqual(list(Path(tmp).iterdir()),[p])

    def test_patch_rejects_incomplete_inventory(self):
        with self.assertRaisesRegex(ValueError,'inventory'):
            fix.corrected({})

    def test_patch_rejects_every_unreviewed_preimage(self):
        with self.assertRaisesRegex(ValueError,'preimage'):
            fix.corrected({name:b'changed = True\n' for name in fix.ORIGINAL})


if __name__=='__main__':unittest.main()
