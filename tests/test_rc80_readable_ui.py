"""Presentation candidate must retain the receiver write protection."""
import importlib.util
from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import prepare_rc80_readable_ui as patch
import prepare_rc80_readable_ui_package as package


class ReadableUiProtection(unittest.TestCase):
    def test_incomplete_inventory_refused(self):
        with self.assertRaisesRegex(ValueError, "inventory drift"):
            patch.corrected({})

    def test_unpinned_application_code_refused(self):
        with self.assertRaisesRegex(ValueError, "preimage drift"):
            patch.corrected({name: b"print('changed')" for name in patch.ORIGINAL})

    def test_guard_keeps_explicit_consent_and_only_changes_baseline_count(self):
        source = """def check(root, manifest):
    verify_tree(root, manifest['before'], 141)
    verify_tree(root, manifest['before'], 141)
    verify_tree(root, manifest['before'], 141)
def main(mode, manifest):
    if os.environ.get('EPGMGR_RC80_CONSENT') != manifest['consent']:
        raise ValueError('owner consent required')
    verify_tree(PLUGIN, manifest['after'], 142)
main('backup', {})
"""
        result = package.updated_guard(source)
        expected = source[:source.index("main('backup'")].replace(
            "manifest['before'], 141", "manifest['before'], 142")
        self.assertEqual(result, expected)
        with self.assertRaisesRegex(ValueError, "entrypoint drift"):
            package.updated_guard(source.replace("main('backup', {})", "main('verify', {})"))
        with self.assertRaisesRegex(ValueError, "consent guard missing"):
            package.updated_guard(source.replace("os.environ.get('EPGMGR_RC80_CONSENT')", "True"))
