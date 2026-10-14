# -*- coding: utf-8 -*-
"""No dependency on Enigma2; test fingerprint structure and privacy."""
import ast
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "tools/audit_receiver_plugin_readonly.py"
SOURCE = FILE.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(FILE))

class TestReceiverFingerprint(unittest.TestCase):
    def test_no_write_or_side_effect_operations(self):
        for forbidden in (".write_text(", ".write_bytes(", ".unlink(", ".rename(",
                          "subprocess", "os.system", "os.popen", "opkg ", "killall",
                          "requests.", "urlopen(", "shutil.", ".mkdir("):
            self.assertNotIn(forbidden, SOURCE)
        self.assertNotIn("import enigma", SOURCE)
        self.assertNotIn('open("/etc/enigma2/epgmanager_mappings.json"', SOURCE)

    def test_safe_import_and_fake_receiver(self):
        namespace = {"__name__": "_test_receiver_fingerprint"}
        exec(compile(TREE, str(FILE), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "EPGManager"
            plugin.mkdir()
            (plugin / "version.py").write_text(
                '__version__ = "2026.10.04-rc78"\nCONFIG_SCHEMA_VERSION = 15\n',
                encoding="utf-8")
            (plugin / "ui").mkdir()
            (plugin / "ui/channel_mapping.py").write_text(
                "def _auto_batch_match():\n    pass\n", encoding="utf-8")
            info = namespace["audit"](str(plugin), str(root / "config"))
            self.assertTrue(info["ok"])
            self.assertEqual(info["receiver_plugin_version"], "2026.10.04-rc78")
            self.assertEqual(info["config_schema_version"], 15)
            self.assertEqual(info["total_python_modules"], 2)
            self.assertTrue(info["features"]["ui/channel_mapping.py"]["full_auto_batch"])
            self.assertFalse(info["sensitive_data_included"])
            self.assertNotIn("secret", str(info).lower())

    def test_missing_install(self):
        namespace = {"__name__": "_test_receiver_fingerprint"}
        exec(compile(TREE, str(FILE), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(namespace["audit"](directory + "/missing", directory)["ok"])


if __name__ == "__main__":
    unittest.main()
