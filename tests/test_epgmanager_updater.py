# -*- coding: utf-8 -*-
"""Plugin-only Online Update safety tests (no Enigma2 or receiver needed)."""
import ast
import hashlib
import os
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "release_overlay" / "online_update.py"
TEXT = SOURCE.read_text(encoding="utf-8")
TREE = ast.parse(TEXT, filename=str(SOURCE))
PURE = {"_version_key", "_is_newer", "_validated_manifest", "_verify"}
FUNCTIONS = [node for node in TREE.body
             if isinstance(node, ast.FunctionDef) and node.name in PURE]
assert len(FUNCTIONS) == len(PURE), "Missing updater helper"
ns = {"hashlib": hashlib, "os": os, "re": re, "urlsplit": urlsplit}
exec(compile(ast.Module(body=FUNCTIONS, type_ignores=[]), str(SOURCE), "exec"), ns)
FAKE = {
    "version": "2026.10.15-rc79",
    "url": "https://github.com/wacayoub/EPGManager/releases/download/v2026.10.15-rc79/epgmanager_rc79_all.ipk",
    "sha256": "a" * 64,
    "size": 100,
}


class TestPluginUpdater(unittest.TestCase):
    def test_newer_version(self):
        self.assertTrue(ns["_is_newer"]("2026.10.15-rc79", "2026.10.04-rc78"))
        self.assertFalse(ns["_is_newer"]("2026.10.04-rc78", "2026.10.15-rc79"))

    def test_manifest_accepts_trusted_release(self):
        self.assertEqual(ns["_validated_manifest"](FAKE)["size"], 100)

    def test_rejects_missing_checksum(self):
        with self.assertRaises(ValueError):
            ns["_validated_manifest"](dict(FAKE, sha256=""))

    def test_rejects_untrusted_urls(self):
        for url in (
            "http://github.com/wacayoub/EPGManager/releases/download/v1/p.ipk",
            "https://evil.example.com/update.ipk",
            "https://github.com/other/repo/releases/download/v1/p.ipk",
            "https://github.com/wacayoub/EPGManager/releases/download/v1/a.sh",
            "https://github.com/wacayoub/EPGManager/releases/download/v1/a.ipk?unsafe=1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ns["_validated_manifest"](dict(FAKE, url=url))

    def test_rejects_bad_package_size(self):
        for size in (0, -1, 200 * 1024 * 1024):
            with self.subTest(size=size), self.assertRaises(ValueError):
                ns["_validated_manifest"](dict(FAKE, size=size))

    def test_package_verification(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.ipk"
            p.write_bytes(b"safe-test-ipk")
            correct = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertTrue(ns["_verify"](str(p), correct, p.stat().st_size))
            with self.assertRaises(ValueError):
                ns["_verify"](str(p), "b" * 64, p.stat().st_size)
            with self.assertRaises(ValueError):
                ns["_verify"](str(p), correct, 999)
            with self.assertRaises(ValueError):
                ns["_verify"](str(p), "", p.stat().st_size)

    def test_no_unsafe_restart_or_reinstall(self):
        self.assertNotIn("killall", TEXT)
        self.assertNotIn("opkg install --force-reinstall", TEXT)
        self.assertIn("Reinstall locked", TEXT)

    def test_gui_network_is_background(self):
        klass = next(n for n in TREE.body if isinstance(n, ast.ClassDef)
                     and n.name == "EPGManagerOnlineUpdate")
        names = {m.name: m for m in klass.body if isinstance(m, ast.FunctionDef)}
        self.assertIn("_start_job", names)
        self.assertIn("_poll_job", names)
        self.assertIn("_cleanup", names)
        self.assertIn("_make_backup", {n.name for n in TREE.body
                                      if isinstance(n, ast.FunctionDef)})
        for method in ("check_update", "install_update", "_confirmed"):
            direct = [n.func.id for n in ast.walk(names[method])
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
            self.assertNotIn("subprocess", direct)


if __name__ == "__main__":
    unittest.main()
