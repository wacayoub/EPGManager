# -*- coding: utf-8 -*-
"""No receiver required: synthetic opkg original-IPK rollback eligibility."""
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import hashlib
import io
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools/audit_original_rollback_ipk.py"
spec = spec_from_file_location("rc80_original_ipk", str(MODULE))
ipk = module_from_spec(spec)
spec.loader.exec_module(ipk)


def make_tar(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, contents in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            tar.addfile(info, io.BytesIO(contents))
    return buffer.getvalue()


def make_ar(parts):
    output = bytearray(b"!<arch>\n")
    for name, data in parts:
        assert len(name) < 16
        head = ("%-16s%-12d%-6d%-6d%-8s%-10d" %
                (name + "/", 0, 0, 0, "100644", len(data))).encode("ascii") + b"\x60\n"
        assert len(head) == 60
        output.extend(head)
        output.extend(data)
        if len(data) % 2:
            output.extend(b"\n")
    return bytes(output)


def make_ipk(path, overrides=None, version=ipk.VERSION, path_attack=False):
    files = {}
    digest_expectations = {}
    for name in ipk.PROTECTED:
        content = ("# checked previous receiver file %s\n" % name).encode()
        files[ipk.REL + name] = content
        digest_expectations[name] = hashlib.sha256(content).hexdigest()
    for n in range(138-len(ipk.PROTECTED)):
        name = ipk.REL+"other_%03d.py" % n
        files[name] = b"# previous receiver code\n"
    if path_attack:
        files["../../outside.txt"] = b"never"
    files.update(overrides or {})
    control = ("Package: %s\nVersion: %s\nArchitecture: all\nDescription: test\n" %
               (ipk.PACKAGE, version)).encode()
    parts = [("debian-binary", b"2.0\n"),
             ("control.tar.gz", make_tar({"./control": control})),
             ("data.tar.gz", make_tar(files))]
    path.write_bytes(make_ar(parts))
    return digest_expectations


class TestPreviousIpkgEligibility(unittest.TestCase):
    def test_exact_original_package_is_eligible_but_not_authorized(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "old.ipk"
            protected = make_ipk(path)
            with patch.dict(ipk.PROTECTED, protected, clear=True):
                report = ipk.verify_old_ipk(path)
            self.assertTrue(report["rollback_package_identical_to_working_app"])
            self.assertEqual(report["python_modules"], 138)
            self.assertFalse(report["opkg_rollback_tested"])
            self.assertFalse(report["authorized_to_install_new_version"])

    def test_only_package_with_exact_previous_version_and_hashes_matches(self):
        with tempfile.TemporaryDirectory() as td:
            old = Path(td)/"old.ipk"
            digest = make_ipk(old, version="2026.10.14-rc80-beta1")
            with patch.dict(ipk.PROTECTED, digest, clear=True):
                with self.assertRaises(ValueError):
                    ipk.verify_old_ipk(old)
            old2 = Path(td)/"old2.ipk"
            make_ipk(old2)
            # All real receiver hashes differ from synthetic fixture.
            report = ipk.verify_old_ipk(old2)
            self.assertFalse(report["rollback_package_identical_to_working_app"])

    def test_refuses_missing_files_and_tar_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            old = Path(td)/"old.ipk"
            digest = make_ipk(old, path_attack=True)
            with patch.dict(ipk.PROTECTED, digest, clear=True):
                with self.assertRaises(ValueError):
                    ipk.verify_old_ipk(old)
            old.write_bytes(b"not a package")
            with self.assertRaises(ValueError):
                ipk.verify_old_ipk(old)

    def test_does_not_write_or_run_opkg(self):
        code=MODULE.read_text(encoding="utf-8")
        for operation in (".write_bytes(", ".write_text(", "opkg install", "opkg remove",
                          "opkg upgrade", "subprocess", "requests.", "urllib.request"):
            self.assertNotIn(operation, code)

if __name__ == "__main__":
    unittest.main()
