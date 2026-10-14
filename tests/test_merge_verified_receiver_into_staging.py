# -*- coding: utf-8 -*-
"""Synthetic rc79.1 receiver export -> rc80 staging, no user code in GitHub."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from importlib.util import module_from_spec, spec_from_file_location

SCRIPT = Path(__file__).resolve().parents[1] / "tools/merge_verified_receiver_into_staging.py"
spec = spec_from_file_location("receiver_merge", str(SCRIPT))
merge = module_from_spec(spec)
spec.loader.exec_module(merge)


def fixture(tmp):
    top = Path(tmp)
    plugin = top / "staged" / merge.REL
    plugin.mkdir(parents=True)
    (plugin / "version.py").write_text(
        '__version__ = "2026.10.14-rc79-beta1"\nCONFIG_SCHEMA_VERSION = 15\n', encoding="utf-8")
    names = ["ui/main.py", "online_update.py"] + [
        x for x in merge.RECEIVER_SHA256
        if x not in ("core/github_direct_stream.py", "core/github_match_health.py", "version.py")
    ]
    for name in names:
        file = plugin / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("# base test module\n", encoding="utf-8")
    (plugin / "ui/main.py").write_text("# def open_diagnostics(\n", encoding="utf-8")
    (plugin / "online_update.py").write_text("# Reinstall locked\n", encoding="utf-8")
    for n in range(135 - len(names)):
        (plugin / ("other_%03d.py" % n)).write_text("# unchanged rc78 module\n", encoding="utf-8")
    assert len(list(plugin.rglob("*.py"))) == 136
    payloads = {}
    for name in merge.RECEIVER_SHA256:
        content = "# receiver fixture\n"
        if name == "version.py":
            content += '__version__ = "2026.10.08-rc79.1"\nCONFIG_SCHEMA_VERSION = 15\n'
        if name == "ui/channel_mapping.py":
            content += "# def review_next_suggestion(\n# def review_next_conflict(\n# github_match_health.health(\n"
        if name == "core/direct_native_import.py":
            content += "# def _merge_persisted_auto_overrides(\n# persisted_auto_keys = set()\n"
        if name == "core/epg_cycle.py":
            content += "# github_direct_stream.check_selected_sources(selected)\n# clear_before_import=False,\n"
        payloads[name] = content.encode("utf-8")
    expected = {k: hashlib.sha256(v).hexdigest() for k,v in payloads.items()}
    manifest = {"files": {k: {"sha256":expected[k], "bytes": len(v)} for k,v in payloads.items()}}
    archive = top / "receiver.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in list(payloads.items()) + [("MANIFEST.json", json.dumps(manifest).encode("utf-8"))]:
            entry = tarfile.TarInfo("EPGManager/" + name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    return top / "staged", archive, expected


class TestReceiverMerge(unittest.TestCase):
    def test_merge_reaches_138_modules_without_losing_gui(self):
        with tempfile.TemporaryDirectory() as d:
            unpacked, archive, hashes = fixture(d)
            report = merge.overlay_working_receiver(unpacked, archive, expected=hashes)
            self.assertEqual(report["python_modules"], 138)
            self.assertEqual(report["protected_receiver_modules_exact"], 7)
            self.assertFalse(report["safe_to_install"])
            self.assertIn("Reinstall locked", (unpacked / merge.REL / "online_update.py").read_text())
            self.assertIn("open_diagnostics", (unpacked / merge.REL / "ui/main.py").read_text())

    def test_archive_hash_rejects_unapproved_files(self):
        with tempfile.TemporaryDirectory() as d:
            unpacked, archive, hashes = fixture(d)
            hashes["ui/theme.py"] = "0" * 64
            with self.assertRaises(ValueError):
                merge.unpack_verified_receiver(archive, known=hashes)

    def test_missing_archive_member_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            unpacked, archive, hashes = fixture(d)
            hashes["core/unapproved.py"] = "0" * 64
            with self.assertRaises(ValueError):
                merge.unpack_verified_receiver(archive, known=hashes)


if __name__ == "__main__":
    unittest.main()
