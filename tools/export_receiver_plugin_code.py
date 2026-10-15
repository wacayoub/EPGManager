#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export selected EPGManager APPLICATION Python files for manual review.

Does not install, change EPGManager, restart the receiver, or upload the archive.
Never reads any /etc/enigma2 configuration, credentials, mappings or EPG data.
The resulting /tmp archive contains Python code and a SHA256 manifest only.
Review custom code before choosing to share: code can contain hard-coded data.
"""
from __future__ import print_function
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

BASE = Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
OUT = Path("/tmp/epgmanager-plugin-code-review.tar.gz")
FILES = (
    "ui/channel_mapping.py",
    "core/direct_native_import.py",
    "core/epg_cycle.py",
    "core/github_direct_stream.py",
    "core/github_match_health.py",
    "ui/settings.py",
    "ui/theme.py",
    "version.py",
)
MAX_BYTES = 2 * 1024 * 1024


def export(plugin_dir=BASE, target=OUT):
    plugin_dir, target = Path(plugin_dir), Path(target)
    if not plugin_dir.is_dir():
        raise RuntimeError("EPGManager plugin directory not found")
    records = {}
    content = {}
    for name in FILES:
        path = plugin_dir / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Required code file missing or symlink: " + name)
        payload = path.read_bytes()
        if len(payload) > MAX_BYTES:
            raise RuntimeError("Unexpectedly large Python file: " + name)
        records[name] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        content[name] = payload
    manifest = {
        "purpose": "EPGManager app-only source parity review",
        "content": "Python plugin modules only; no runtime configuration or XMLTV",
        "files": records,
    }
    content["MANIFEST.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    # All file names are fixed in FILES and cannot be influenced by runtime data.
    with tarfile.open(str(target), "w:gz") as tar:
        for name, payload in content.items():
            member = tarfile.TarInfo("EPGManager/" + name)
            member.size = len(payload)
            member.mode = 0o600
            tar.addfile(member, io.BytesIO(payload))
    target.chmod(0o600)
    return target, records


if __name__ == "__main__":
    try:
        destination, rows = export()
        print("EPGManager CODE EXPORT: OK")
        print("FILE:", destination)
        print("COUNT:", len(rows))
        print("SIZE:", destination.stat().st_size, "bytes")
        print("No configs, mappings, IPTV accounts, programme data or login files included.")
        print("You can retrieve this archive from /tmp through SFTP and attach it to the chat.")
    except Exception as exc:
        print("EXPORT FAILED:", str(exc))
        sys.exit(1)
