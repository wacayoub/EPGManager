# -*- coding: utf-8 -*-
"""Offline-only synthetic tests: rc79.1 recovery parity and hook safety."""
from importlib.util import spec_from_file_location, module_from_spec
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from test_original_rollback_ipk import make_ipk
import audit_original_rollback_ipk as old
import prepare_rc79_local_rescue_ipk as packaging

PATH = TOOLS / "audit_rc80_rollback_package_readonly.py"
spec = spec_from_file_location("read_only_recovery_audit", PATH)
checker = module_from_spec(spec)
spec.loader.exec_module(checker)


def fixture(folder, passive=True, include_asset=True):
    folder = Path(folder)
    ipk = folder / "candidate.ipk"
    overrides = ({old.REL + "skin/logo.svg": b"<svg/>"} if include_asset else {})
    digests, app = make_ipk(ipk, overrides=overrides)
    if include_asset:
        (app / "skin").mkdir(exist_ok=True)
        (app / "skin/logo.svg").write_bytes(b"<svg/>")
    original = old._read_ar(ipk.read_bytes())
    with old._data_tar(original, "data.tar") as archive:
        files = old._safe_file_members(archive)
    control = (
        "Package: %s\nVersion: %s\nArchitecture: all\n"
        % (checker.PACKAGE, checker.VERSION)
    ).encode()
    hooks = {
        "control": control,
        **{name: (checker.HOOK if passive else b"#!/bin/sh\nexit 1\n")
           for name in checker.HOOKS},
    }
    modes = {x: 0o644 for x in files}
    hook_modes = {x: 0o755 for x in checker.HOOKS}
    hook_modes["control"] = 0o644
    def package():
        ipk.write_bytes(packaging._make_ar([
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", packaging._make_tar(hooks, hook_modes)),
            ("data.tar.gz", packaging._make_tar(files, modes)),
        ]))
        return hashlib.sha256(ipk.read_bytes()).hexdigest()
    return ipk, app, files, hooks, package


class TestRc80RecoveryParity(unittest.TestCase):
    def test_exact_previous_payload_and_passive_hooks_without_opkg(self):
        with tempfile.TemporaryDirectory() as td:
            path, app, files, hooks, package = fixture(td)
            digest = package()
            result = checker.check_one("rescue_snapshot", path, digest, app)
            self.assertTrue(result["recognized_sha256"])
            self.assertTrue(result["python_content_identical"])
            self.assertTrue(result["assets_content_identical"])
            self.assertTrue(result["mode_parity"])
            self.assertTrue(result["rescue_hooks_passive"])
            self.assertTrue(result["complete_rescue_content"])
            self.assertFalse(result["opkg_recovery_tested"])
            self.assertFalse(result["authorized_to_install_rc80"])
            self.assertEqual(result["package_python_modules"], 138)
            self.assertEqual(result["matched_other_assets"], 1)

    def test_original_candidate_cannot_qualify_as_rescue(self):
        with tempfile.TemporaryDirectory() as td:
            path, app, files, hooks, package = fixture(td)
            digest = package()
            result = checker.check_one("original_candidate", path, digest, app)
            self.assertTrue(result["python_content_identical"])
            self.assertFalse(result["complete_rescue_content"])

    def test_executable_but_non_passive_hook_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            path, app, files, hooks, package = fixture(td, passive=False)
            digest = package()
            result = checker.check_one("rescue_snapshot", path, digest, app)
            self.assertFalse(result["rescue_hooks_passive"])
            self.assertFalse(result["complete_rescue_content"])

    def test_changed_app_asset_or_permissions_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            path, app, files, hooks, package = fixture(td)
            digest = package()
            logo = app / "skin/logo.svg"
            logo.write_bytes(b"changed")
            result = checker.check_one("rescue_snapshot", path, digest, app)
            self.assertFalse(result["assets_content_identical"])
            self.assertFalse(result["complete_rescue_content"])
            logo.write_bytes(b"<svg/>")
            logo.chmod(0o600)
            result = checker.check_one("rescue_snapshot", path, digest, app)
            self.assertTrue(result["assets_content_identical"])
            self.assertFalse(result["mode_parity"])
            self.assertFalse(result["complete_rescue_content"])

    def test_extra_application_file_and_bad_digest_block(self):
        with tempfile.TemporaryDirectory() as td:
            path, app, files, hooks, package = fixture(td)
            digest = package()
            (app / "secret-runtime.dat").write_bytes(b"unpackaged")
            result = checker.check_one("rescue_snapshot", path, digest, app)
            self.assertEqual(result["receiver_only_unmatched_file_count"], 1)
            self.assertFalse(result["complete_rescue_content"])
            # The contents of extra files must not appear in the report.
            self.assertNotIn("secret-runtime.dat", str(result))
            self.assertFalse(checker.check_one("rescue_snapshot", path, "0"*64, app)["recognized_sha256"])

    def test_symlinks_and_invalid_control_block(self):
        with tempfile.TemporaryDirectory() as td:
            path, app, files, hooks, package = fixture(td)
            digest = package()
            (app / "malicious.link").symlink_to("/etc/passwd")
            self.assertEqual(
                checker.check_one("rescue_snapshot", path, digest, app)["failure_type"],
                "UNEXPECTED_RECEIVER_SYMLINK",
            )
            (app / "malicious.link").unlink()
            hooks["control"] = b"Package: wrong\nVersion: 1\nArchitecture: all\n"
            digest = package()
            self.assertEqual(
                checker.check_one("rescue_snapshot", path, digest, app)["failure_type"],
                "INCORRECT_OPKG_CONTROL",
            )

    def test_audit_is_read_only_and_never_certifies_installation(self):
        source = PATH.read_text(encoding="utf-8")
        for forbidden in ("opkg install", "opkg upgrade", "opkg remove",
                          "subprocess.run(", "os.system(", ".write_bytes(",
                          ".write_text(", "shutil.copy", "tar.extractall("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        with tempfile.TemporaryDirectory() as td:
            path, app, files, hooks, package = fixture(td)
            digest = package()
            report = checker.audit(app, {"rescue_snapshot": (str(path), digest)})
            self.assertTrue(report["rescue_content_parity_found"])
            self.assertFalse(report["opkg_rollback_tested"])
            self.assertFalse(report["enigma2_restart_tested"])
            self.assertFalse(report["authorized_to_install_rc80"])


if __name__ == "__main__":
    unittest.main()
