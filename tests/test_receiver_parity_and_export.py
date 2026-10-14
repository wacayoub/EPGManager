# -*- coding: utf-8 -*-
"""Receiver parity and export tests; use fake files, never a real Vu+."""
import ast
import hashlib
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PARITY = ROOT / "tools/check_receiver_parity.py"
EXPORT = ROOT / "tools/export_receiver_plugin_code.py"


def isolated(path):
    ns = {"__name__": "_isolated_test", "__file__": str(path)}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns


class TestReceiverParity(unittest.TestCase):
    def test_guard_rejects_rc78_like_candidate(self):
        ns = isolated(PARITY)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / ns["REL"]
            directory.mkdir(parents=True)
            (directory / "ui").mkdir()
            (directory / "ui/channel_mapping.py").write_text(
                "old and incomplete", encoding="utf-8")
            report = ns["audit"](temp)
            self.assertFalse(report["verified_receiver_parity"])
            self.assertFalse(report["safe_to_install"])
            self.assertIn("ui/channel_mapping.py", report["mismatched_or_missing"])
            self.assertIn("core/direct_native_import.py", report["mismatched_or_missing"])

    def test_receiver_expected_digest_is_exact_and_private(self):
        ns = isolated(PARITY)
        for key, sha in ns["RECEIVER"]["protected"].items():
            self.assertEqual(len(sha), 64, key)
            int(sha, 16)
        self.assertNotIn("password", PARITY.read_text(encoding="utf-8").lower())
        self.assertIn("2026.10.08-rc79.1", PARITY.read_text(encoding="utf-8"))

    def test_export_contains_only_fixed_python_paths(self):
        ns = isolated(EXPORT)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "EPGManager"
            for name in ns["FILES"]:
                p = app / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("test Python code", encoding="utf-8")
            out = root / "review.tar.gz"
            result, rows = ns["export"](app, out)
            self.assertEqual(result, out)
            self.assertEqual(len(rows), len(ns["FILES"]))
            with tarfile.open(out, "r:gz") as tar:
                members = [m.name for m in tar.getmembers()]
                self.assertEqual(len(members), len(ns["FILES"]) + 1)
                self.assertTrue(all(m.startswith("EPGManager/") for m in members))
                self.assertFalse(any("mapping" in m and not m.endswith("channel_mapping.py") for m in members))
                self.assertNotIn("etc/enigma2", str(members))

    def test_export_refuses_missing_and_symlink_modules(self):
        ns = isolated(EXPORT)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaises(RuntimeError):
                ns["export"](directory, directory / "x.tar.gz")
            (directory / "ui").mkdir()
            (directory / "ui/channel_mapping.py").symlink_to(directory / "private.py")
            with self.assertRaises(RuntimeError):
                ns["export"](directory, directory / "x.tar.gz")


if __name__ == "__main__":
    unittest.main()
