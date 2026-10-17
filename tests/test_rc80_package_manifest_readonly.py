# -*- coding: utf-8 -*-
"""Synthetic receiver plugin stubs only; no user plugin archive or private data."""
import hashlib
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/audit_rc80_package_manifest_readonly.py"
spec = spec_from_file_location("rc80_package_tree_audit", str(SCRIPT))
gate = module_from_spec(spec)
spec.loader.exec_module(gate)


def make_tree(folder):
    plugin = Path(folder) / "staged-receiver" / "EPGManager"
    plugin.mkdir(parents=True)
    original = {
        "version.py": b'__version__ = "2026.10.08-rc79.1"\nCONFIG_SCHEMA_VERSION = 15\n',
        "ui/channel_mapping.py": b"# private working UI baseline\n",
        "core/system_check.py": b"# original system check\n",
    }
    original.update({
        "core/other_%03d.py" % n: b"# unchanged original code\n"
        for n in range(gate.BASELINE_COUNT - len(original))
    })
    manifest = {"receiver_version": gate.BASELINE_VERSION, "config_schema": 15,
                "files": {p: {"sha256": gate.digest(value), "bytes": len(value)}
                          for p, value in original.items()}}
    for path, payload in original.items():
        target = plugin / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    reviewed = {}
    version = b'__version__ = "2026.10.15-rc80-private-offline"\nCONFIG_SCHEMA_VERSION = 15\n'
    (plugin / "version.py").write_bytes(version)
    reviewed["version.py"] = gate.digest(version)
    for name, public_path in (
        ("ui/channel_mapping.py", ROOT / "source/overlay/ui/channel_mapping.py"),
        ("core/verified_epg_match.py", ROOT / "source/overlay/core/verified_epg_match.py"),
        ("core/verified_epg_refresh.py", ROOT / "source/overlay/core/verified_epg_refresh.py"),
    ):
        target = plugin / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(public_path.read_bytes())
    return plugin, manifest, reviewed


class TestRC80PackageIntegrity(unittest.TestCase):
    def test_exact_138_plus_two_public_modules_structurally_verified_only(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            report = gate.audit(plugin, manifest, reviewed, ROOT)
            self.assertEqual(report["result_python_modules"], 140)
            self.assertEqual(report["original_file_paths_checked"], 138)
            self.assertEqual(len(report["added_public_modules"]), 2)
            self.assertEqual(report["approved_changed_paths"]["version.py"],
                             "private-reviewed-hash")
            self.assertEqual(report["approved_changed_paths"]["ui/channel_mapping.py"],
                             "public-review-exact")
            self.assertFalse(report["authorized_to_install"])
            self.assertFalse(report["hardware_tested"])
            self.assertFalse(report["package_built"])

    def test_missing_bridge_module_rejected_even_with_139_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            (plugin / "core/verified_epg_refresh.py").unlink()
            with self.assertRaisesRegex(ValueError, "138 baseline"):
                gate.audit(plugin, manifest, reviewed)

    def test_extra_unapproved_module_rejected_even_when_total_is_140(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            (plugin / "core/verified_epg_match.py").unlink()
            (plugin / "core/unapproved.py").write_text("# unapproved module\n")
            with self.assertRaisesRegex(ValueError, "source inventory"):
                gate.audit(plugin, manifest, reviewed)

    def test_modified_bridge_bytes_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            (plugin / "core/verified_epg_match.py").write_text("# fake verifier\n")
            with self.assertRaisesRegex(ValueError, "unverified new public overlay"):
                gate.audit(plugin, manifest, reviewed)

    def test_coordinated_staging_checkout_tamper_rejected(self):
        """Matching a modified checkout is not enough without frozen blob IDs."""
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            mirror = Path(temp) / "tampered-checkout"
            for rel in gate.PINNED_PUBLIC_GIT_BLOBS:
                src = ROOT / "source" / "overlay" / rel
                dest = mirror / "source" / "overlay" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
            changed = b"# Python-valid but NOT reviewed on GitHub\n"
            target = "core/verified_epg_refresh.py"
            (plugin / target).write_bytes(changed)
            (mirror / "source" / "overlay" / target).write_bytes(changed)
            with self.assertRaisesRegex(ValueError, "audited Git blob"):
                gate.audit(plugin, manifest, reviewed, mirror)

    def test_private_module_modification_without_digest_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            (plugin / "core/system_check.py").write_text("# unreviewed patch\n")
            with self.assertRaisesRegex(ValueError, "unapproved receiver runtime"):
                gate.audit(plugin, manifest, reviewed)

    def test_private_reviewed_patch_requires_exact_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            new = b"# private reviewed source quality fix\n"
            (plugin / "core/system_check.py").write_bytes(new)
            reviewed["core/system_check.py"] = hashlib.sha256(new).hexdigest()
            out = gate.audit(plugin, manifest, reviewed)
            self.assertEqual(out["approved_changed_paths"]["core/system_check.py"],
                             "private-reviewed-hash")
            reviewed["core/system_check.py"] = "0" * 64
            with self.assertRaises(ValueError):
                gate.audit(plugin, manifest, reviewed)

    def test_manifest_baseline_must_be_complete_and_sha_locked(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            manifest["files"].pop("core/other_000.py")
            with self.assertRaisesRegex(ValueError, "138 original"):
                gate.audit(plugin, manifest, reviewed)
            manifest["files"]["core/other_000.py"] = {"sha256": "0"*64, "bytes": 1}
            with self.assertRaises(ValueError):
                gate.audit(plugin, manifest, reviewed)

    def test_manifest_traversal_and_unauthorized_review_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            self.assertFalse(gate.valid_module_path("../private.py"))
            self.assertFalse(gate.valid_module_path("/tmp/other.py"))
            self.assertFalse(gate.valid_module_path("core\\bad.py"))
            self.assertFalse(gate.valid_module_path("core/__pycache__/bad.py"))
            reviewed["core/other_001.py"] = "a"*64
            with self.assertRaisesRegex(ValueError, "unapproved private"):
                gate.audit(plugin, manifest, reviewed)

    def test_non_reviewed_version_change_or_schema_downgrade_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            with self.assertRaises(ValueError):
                gate.audit(plugin, manifest, {})
            wrong = b'__version__ = "2026.10.15-rc80"\nCONFIG_SCHEMA_VERSION = 14\n'
            (plugin / "version.py").write_bytes(wrong)
            reviewed["version.py"] = gate.digest(wrong)
            with self.assertRaisesRegex(ValueError, "schema"):
                gate.audit(plugin, manifest, reviewed)

    def test_symlinked_runtime_module_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, manifest, reviewed = make_tree(temp)
            target = plugin / "core/verified_epg_match.py"
            target.unlink()
            target.symlink_to(ROOT / "source/overlay/core/verified_epg_match.py")
            with self.assertRaisesRegex(ValueError, "symlink"):
                gate.audit(plugin, manifest, reviewed)

    def test_live_receiver_directory_refusal_and_invalid_baseline(self):
        self.assertFalse(gate.valid_module_path("x/../../y.py"))
        with self.assertRaisesRegex(ValueError, "live receiver"):
            gate.audit("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager",
                       {"receiver_version": gate.BASELINE_VERSION, "config_schema":15,
                        "files":{}}, {})


if __name__ == "__main__":
    unittest.main()
