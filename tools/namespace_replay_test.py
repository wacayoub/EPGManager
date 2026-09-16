#!/usr/bin/env python3
"""Prove that replaying the complete canonical publication is byte-idempotent."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


TRACKED = {"manifest.json", "shards.json", "receiver-id-aliases.json"}


def snapshot(base: Path):
    paths = sorted(list(base.glob("*.xml.gz")) + [base / name for name in TRACKED])
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths if path.exists()
    }


def run(tool: Path, base: Path):
    subprocess.run([sys.executable, str(tool), "--dir", str(base)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    source = Path(args.dir).resolve()
    tool = Path(__file__).resolve().parent / "canonical_publish_finalize.py"
    with tempfile.TemporaryDirectory(prefix="epg-namespace-replay-") as tmp:
        work = Path(tmp) / "publish"
        shutil.copytree(source, work)
        run(tool, work)
        first = snapshot(work)
        run(tool, work)
        second = snapshot(work)
    changed = sorted(name for name in set(first) | set(second) if first.get(name) != second.get(name))
    if changed:
        print("NAMESPACE_REPLAY FAIL changed=%s" % ",".join(changed))
        return 1
    print("NAMESPACE_REPLAY PASS files=%d" % len(first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Rebuild marker 2026-09-16: trigger corrected MENA production pipeline without changing test behavior.
