# -*- coding: utf-8 -*-
"""Only synthetic RC79/RC80 bytes. Never requires a receiver or private backup."""
from pathlib import Path
import hashlib
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_rc80_offline_blocked_ipk as builder
import audit_rc80_rollback_package_readonly as rollback
from test_rc80_package_manifest_readonly import make_tree


class TestBlockedOfflinePackage(unittest.TestCase):
    def fixture(self, folder):
        plugin, manifest, reviewed = make_tree(folder)
        base = Path(folder)
        original = {}
        for rel in manifest["files"]:
            if rel == "version.py":
                original[rel] = b'__version__ = "2026.10.08-rc79.1"\nCONFIG_SCHEMA_VERSION = 15\n'
            elif rel == "ui/channel_mapping.py":
                original[rel] = b"# private working UI baseline\n"
            else:
                original[rel] = (plugin / rel).read_bytes()
        assets = {"assets/skin%03d.png" % i: ("image%03d" % i).encode()
                  for i in range(builder.EXPECTED_ASSETS)}
        for name, value in assets.items():
            target = plugin / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        payload = {rollback.PREFIX + rel: data for rel, data in {**original, **assets}.items()}
        data_modes = {rel: 0o644 for rel in payload}
        control = ("Package: %s\nVersion: %s\nArchitecture: all\nDescription: Private synthetic fixture\n"
                   % (rollback.PACKAGE, rollback.VERSION)).encode()
        ctrls = {"control": control, "preinst": b"#!/bin/sh\nexit 0\n",
                 "postinst": b"#!/bin/sh\nexit 0\n"}
        ctrls_modes = {"control": 0o644, "preinst": 0o755, "postinst": 0o755}
        package = builder._ar([
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", builder._tar_gzip(ctrls, ctrls_modes)),
            ("data.tar.gz", builder._tar_gzip(payload, data_modes)),
        ])
        rescue = base / "synthetic-rescue.ipk"
        rescue.write_bytes(package)
        digest = hashlib.sha256(package).hexdigest()
        return rescue, plugin, manifest, reviewed, digest

    def call(self, fx, **kw):
        rescue, plugin, manifest, reviewed, digest = fx
        return builder.prepare(rescue, plugin, manifest, reviewed,
                               expected_rescue_sha=digest, **kw)

    def test_check_only_verifies_and_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            fx = self.fixture(d)
            before = set(Path(d).rglob("*.ipk"))
            result = self.call(fx)
            self.assertTrue(result["read_only"])
            self.assertFalse(result["authorized_to_install"])
            self.assertEqual(result["source_modules"], 140)
            self.assertEqual(result["files_in_candidate"], 215)
            self.assertEqual(before, set(Path(d).rglob("*.ipk")))

    def test_build_is_reproducible_and_install_is_always_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            fx = self.fixture(d)
            private = Path(d) / "private"
            private.mkdir()
            output = private / builder.OUTPUT_NAME
            result = self.call(fx, build=True, output=output)
            raw = output.read_bytes()
            self.assertEqual(result["candidate_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertFalse(result["authorized_to_install"])
            archive = rollback.read_ar(raw)
            hooks, perms = rollback.read_tar(archive["control.tar.gz"])
            files, modes = rollback.read_tar(archive["data.tar.gz"])
            self.assertEqual(hooks["preinst"], builder.BLOCKED_PREINST)
            self.assertEqual(perms["preinst"], 0o755)
            self.assertEqual(len(files), 215)
            self.assertEqual(rollback.metadata(hooks["control"])["version"], builder.CANDIDATE_VERSION)
            self.assertEqual(modes[rollback.PREFIX + "core/verified_epg_match.py"], 0o644)
            self.assertNotIn(b"EPGMGR_RC80_SUPERVISED", hooks["preinst"])
            private2 = Path(d) / "private2"
            private2.mkdir()
            another = private2 / builder.OUTPUT_NAME
            self.call(fx, build=True, output=another)
            self.assertEqual(raw, another.read_bytes())
            with self.assertRaises(FileExistsError):
                self.call(fx, build=True, output=output)
            self.assertEqual(raw, output.read_bytes())

    def test_refuses_changed_rescue_checksum_or_baseline_hash(self):
        with tempfile.TemporaryDirectory() as d:
            fx = self.fixture(d)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                builder.prepare(fx[0], fx[1], fx[2], fx[3], expected_rescue_sha="a" * 64)
            fx[2]["files"]["core/other_001.py"]["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                self.call(fx)

    def test_staged_assets_or_file_modes_cannot_drift(self):
        with tempfile.TemporaryDirectory() as d:
            fx = self.fixture(d)
            target = fx[1] / "assets/skin003.png"
            target.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "asset differs"):
                self.call(fx)
            target.write_bytes(b"image003")
            target.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "mode differs"):
                self.call(fx)

    def test_refuses_extra_staging_files_and_unsafe_output(self):
        with tempfile.TemporaryDirectory() as d:
            fx = self.fixture(d)
            private = Path(d) / "private"
            private.mkdir()
            with self.assertRaisesRegex(ValueError, "fixed name"):
                self.call(fx, build=True, output=private / "epgmanager.ipk")
            (fx[1] / "core/unknown.py").write_bytes(b"# not authorized\n")
            with self.assertRaises(ValueError):
                self.call(fx)

    def test_missing_or_unreviewed_modules_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            fx = self.fixture(d)
            (fx[1] / "core/verified_epg_refresh.py").unlink()
            with self.assertRaises(ValueError):
                self.call(fx)

    def test_no_live_install_or_external_transfer_code(self):
        script = (ROOT / "tools/build_rc80_offline_blocked_ipk.py").read_text()
        for forbidden in ("subprocess", "requests.", "urlopen", "git push",
                          "opkg install", "opkg remove", "opkg upgrade"):
            self.assertNotIn(forbidden, script)
        self.assertIn("exit 90", script)


if __name__ == "__main__":
    unittest.main()
