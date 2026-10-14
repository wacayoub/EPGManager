# -*- coding: utf-8 -*-
"""Local rescue package tests: synthetic receiver only; never invoke opkg."""
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from test_original_rollback_ipk import make_ipk  # generated synthetic fixtures
import audit_original_rollback_ipk as old

SPEC = spec_from_file_location("rescue", str(TOOLS / "prepare_rc79_local_rescue_ipk.py"))
rescue = module_from_spec(SPEC)
SPEC.loader.exec_module(rescue)


class TestLocalRescueIPK(unittest.TestCase):
    def create(self, folder):
        root = Path(folder)
        original = root / "EPGManager_rc79_1_AUTOMAPPING_SAFE.ipk"
        hashes, live = make_ipk(original)
        return original, live, hashes, hashlib.sha256(original.read_bytes()).hexdigest()

    def test_readonly_reports_modified_and_missing_modules(self):
        with tempfile.TemporaryDirectory() as td:
            original, live, hashes, digest = self.create(td)
            path = live / "other_000.py"
            path.write_text("# local receiver-only patch\n", encoding="utf-8")
            (live / "receiver_new.py").write_text("# local additional module\n")
            (live / "other_001.py").unlink()
            with patch.dict(old.PROTECTED, hashes, clear=True):
                result = rescue.evaluate(candidate=original, receiver=live,
                                         expected_sha=digest, build=False)
            self.assertEqual(result["installed_python_modules"], 138)
            self.assertTrue(result["read_only"])
            self.assertGreater(result["python_modules_different_or_missing"], 0)
            self.assertFalse(result["authorized_to_install_rc80"])
            self.assertEqual(len(list(Path(td).glob("*LOCAL_RESCUE*.ipk"))), 0)

    def test_build_preserves_receiver_modules_and_original_assets(self):
        with tempfile.TemporaryDirectory() as td:
            original, live, hashes, digest = self.create(td)
            original_bytes = original.read_bytes()
            (live / "other_000.py").write_text("# receiver hotfix after packaging\n")
            (live / "receiver_extra.py").write_text("# another receiver-only module\n")
            (live / "other_001.py").unlink()
            target = Path(td) / rescue.RESCUE_BASENAME
            with patch.dict(old.PROTECTED, hashes, clear=True):
                outcome = rescue.evaluate(candidate=original, receiver=live,
                                          expected_sha=digest, build=True,
                                          output=target, _test_output_dir=td)
                report = old.verify_old_ipk(target, receiver_root=live)
            self.assertTrue(target.is_file())
            self.assertTrue(outcome["rescue_python_tree_identical"])
            self.assertTrue(report["rollback_python_tree_identical_to_working_app"])
            self.assertFalse(report["rollback_package_identical_to_working_app"])
            self.assertFalse(outcome["rescue_ipk_opkg_install_tested"])
            self.assertFalse(outcome["authorized_to_install_rc80"])
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual((live / "other_000.py").read_text(), "# receiver hotfix after packaging\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_rejects_changed_protected_file_and_original_digest(self):
        with tempfile.TemporaryDirectory() as td:
            original, live, hashes, digest = self.create(td)
            (live / "plugin.py").write_text("# fake edit\n")
            with patch.dict(old.PROTECTED, hashes, clear=True):
                with self.assertRaises(ValueError):
                    rescue.evaluate(candidate=original, receiver=live, expected_sha="0"*64)
            # Protected actual version.py changed: always refuse.
            (live / "version.py").write_text("modified version\n")
            with patch.dict(old.PROTECTED, hashes, clear=True):
                with self.assertRaises(ValueError):
                    rescue.evaluate(candidate=original, receiver=live, expected_sha=digest)

    def test_build_wont_write_outside_tmp_or_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            original, live, hashes, digest = self.create(td)
            with patch.dict(old.PROTECTED, hashes, clear=True):
                with self.assertRaises(ValueError):
                    rescue.evaluate(candidate=original, receiver=live,
                                    expected_sha=digest, build=True,
                                    output=Path(td)/"wrong-name.ipk", _test_output_dir=td)
                output = Path(td) / rescue.RESCUE_BASENAME
                output.write_bytes(b"pre-existing package remains untouched")
                with self.assertRaises(FileExistsError):
                    rescue.evaluate(candidate=original, receiver=live,
                                    expected_sha=digest, build=True, output=output,
                                    _test_output_dir=td)
                self.assertEqual(output.read_bytes(), b"pre-existing package remains untouched")

    def test_package_only_asset_blocks_rescue_build(self):
        with tempfile.TemporaryDirectory() as td:
            original = Path(td) / "EPGManager_rc79_1_AUTOMAPPING_SAFE.ipk"
            hashes, live = make_ipk(
                original, overrides={old.REL + "legacy-logo.png": b"old graphic"})
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            target = Path(td) / rescue.RESCUE_BASENAME
            with patch.dict(old.PROTECTED, hashes, clear=True):
                report = rescue.evaluate(candidate=original, receiver=live,
                                         expected_sha=digest, build=False)
                self.assertEqual(report["extra_original_package_assets_count"], 1)
                self.assertIn("legacy-logo.png", report["extra_original_package_assets_sample"])
                with self.assertRaisesRegex(ValueError, "assets do not match"):
                    rescue.evaluate(candidate=original, receiver=live,
                                    expected_sha=digest, build=True,
                                    output=target, _test_output_dir=td)
            self.assertFalse(target.exists())

    def test_asset_mode_drift_blocks_rescue_build(self):
        with tempfile.TemporaryDirectory() as td:
            original = Path(td) / "EPGManager_rc79_1_AUTOMAPPING_SAFE.ipk"
            hashes, live = make_ipk(
                original, overrides={old.REL + "skin/icon.png": b"same graphic"})
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            icon = live / "skin/icon.png"
            icon.parent.mkdir(parents=True, exist_ok=True)
            icon.write_bytes(b"same graphic")
            icon.chmod(0o600)
            with patch.dict(old.PROTECTED, hashes, clear=True):
                report = rescue.evaluate(candidate=original, receiver=live,
                                         expected_sha=digest, build=False)
                self.assertEqual(report["changed_receiver_asset_modes_count"], 1)
                with self.assertRaisesRegex(ValueError, "assets do not match"):
                    rescue.evaluate(candidate=original, receiver=live,
                                    expected_sha=digest, build=True,
                                    output=Path(td) / rescue.RESCUE_BASENAME,
                                    _test_output_dir=td)

    def test_rejects_small_compressed_bomb_before_unpacking(self):
        with tempfile.TemporaryDirectory() as td:
            original = Path(td) / "EPGManager_rc79_1_AUTOMAPPING_SAFE.ipk"
            hashes, live = make_ipk(
                original,
                overrides={old.REL + "huge.dat": b"X" * (rescue.MAX_RECEIVER_FILE + 1)})
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            with patch.dict(old.PROTECTED, hashes, clear=True):
                with self.assertRaisesRegex(ValueError, "Oversized member"):
                    rescue.evaluate(candidate=original, receiver=live,
                                    expected_sha=digest, build=False)

    def test_no_receiver_install_or_publication(self):
        code=(TOOLS/"prepare_rc79_local_rescue_ipk.py").read_text()
        for forbidden in ("opkg install", "opkg upgrade", "opkg remove",
                          "gh release", "git push", "requests.post", "urllib.request"):
            self.assertNotIn(forbidden, code)
        self.assertTrue(rescue.RESCUE_BASENAME.endswith("_UNTESTED.ipk"))


if __name__ == "__main__":
    unittest.main()
