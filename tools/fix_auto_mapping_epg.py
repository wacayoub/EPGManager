#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe receiver-side hotfix: saved AUTO -> Native Import EPG (OpenATV/Enigma2).

Usage:
  python3 fix_auto_mapping_epg.py --check
  python3 fix_auto_mapping_epg.py --self-test
  python3 fix_auto_mapping_epg.py --install
  python3 fix_auto_mapping_epg.py --rollback

No changes to bouquets, lamedb, existing mapping JSON, Rytec or source XMLTV.
Installer updates just core/direct_native_import.py with a timestamped backup.
"""
import argparse
import ast
import os
import py_compile
import shutil
import tempfile
import time
from pathlib import Path

MODULE_PATHS = [
    "/usr/lib/enigma2/python/Plugins/Extensions/EPGManager/core/direct_native_import.py",
    "/usr/lib64/enigma2/python/Plugins/Extensions/EPGManager/core/direct_native_import.py",
]
BACKUP_DIR = Path("/etc/enigma2/epgmanager-auto-epg-backups")
ANCHOR = "def _prune_to_receiver_refs(mapping, registry_index):"
OLD_CALL = ("    parsed = _merge_manual_overrides(parsed, store, only_iptv=only_iptv, store_rows=mapping_rows)\n"
            "    parsed, seed_owner_stats = _fast_single_owner_maps(")
NEW_CALL = ("    parsed = _merge_manual_overrides(parsed, store, only_iptv=only_iptv, store_rows=mapping_rows)\n"
            "    parsed = _merge_persisted_auto_overrides(\n"
            "        parsed, mapping_rows, store=store, only_iptv=only_iptv, registry_index=registry_index)\n"
            "    parsed, seed_owner_stats = _fast_single_owner_maps(")
HELPER = "def _merge_persisted_auto_overrides(parsed, store_rows, store=None, only_iptv=False,\n                                    registry_index=None):\n    \"\"\"Project saved AUTO mappings into this import, even after SRP map rebuilds.\n\n    Manual mappings already have an import-time overlay. AUTO records also need\n    one: generated channels.xml files are disposable and can be rebuilt from the\n    receiver registry, losing otherwise-correct AUTO ownership. This overlay is\n    read-only (no changes to MappingStore or channels.xml).\n    \"\"\"\n    if not isinstance(store_rows, dict) or not parsed:\n        return parsed\n    selected_mids = set(str(mid or \"\").strip().lower() for mid in parsed)\n    receiver_by_ref = (registry_index or ({}, {}))[0] or {}\n    manually_owned = set()\n    for row in store_rows.values():\n        if not isinstance(row, dict) or str(row.get(\"mode\") or \"\").lower() != \"manual\":\n            continue\n        for ref in row.get(\"refs\") or []:\n            canon = _canon_ref(ref)\n            if canon:\n                manually_owned.add(canon)\n    blocked = set()\n    if store is not None:\n        try:\n            for ref in store.blocked_refs() or []:\n                canon = _canon_ref(ref)\n                if canon:\n                    blocked.add(canon)\n        except Exception:\n            log.exception(\"Could not read blocked refs for AUTO import overlay\")\n            return parsed  # fail closed rather than resurrect an ignored channel\n\n    for key, row in store_rows.items():\n        if \"::\" not in str(key) or not isinstance(row, dict):\n            continue\n        mode = str(row.get(\"mode\") or \"\").strip().lower()\n        if mode not in (\"auto\", \"auto-repair\", \"smartmatch\", \"smartmatch-iptv\"):\n            continue\n        mid, cid = str(key).split(\"::\", 1)\n        mid = mid.strip().lower()\n        cid = cid.strip().lower()\n        # An OFF/unselected feed must never be silently added to Import All.\n        if not mid or not cid or mid not in selected_mids:\n            continue\n        target = parsed[mid].setdefault(cid, [])\n        seen = set(_canon_ref(ref) for ref in target)\n        for ref in row.get(\"refs\") or []:\n            raw = str(ref or \"\").strip()\n            canon = _canon_ref(raw)\n            if not raw or not canon or canon in seen or canon in manually_owned or canon in blocked:\n                continue\n            if receiver_by_ref and canon not in receiver_by_ref:\n                continue  # don't import to foreign or removed receiver services\n            if only_iptv and channel_mapper.classify_service_ref(raw) != \"IPTV\":\n                continue\n            target.append(raw)\n            seen.add(canon)\n        if not target:\n            parsed[mid].pop(cid, None)\n    return parsed\n\n\n"


def patch_source(source):
    if "def _merge_persisted_auto_overrides(" in source and NEW_CALL in source:
        return source, False
    if source.count(ANCHOR) != 1 or source.count(OLD_CALL) != 1:
        raise RuntimeError("Unsupported EPGManager code layout; no files changed")
    if "def _merge_persisted_auto_overrides(" in source:
        raise RuntimeError("Partial patch detected; no files changed")
    updated = source.replace(ANCHOR, HELPER + ANCHOR, 1).replace(OLD_CALL, NEW_CALL, 1)
    compile(updated, "direct_native_import.py", "exec")
    return updated, True


def installed_path():
    for path in MODULE_PATHS:
        if os.path.isfile(path):
            return Path(path)
    raise RuntimeError("EPGManager/core/direct_native_import.py not found")


def atomic_write(path, content):
    fd, tmp = tempfile.mkstemp(prefix=".epgmanager-auto-", suffix=".py", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run_self_test():
    # Isolate the REAL helper source via AST: tests run without Enigma2 installed.
    block = ast.parse(HELPER).body[0]
    fn = compile(ast.Module(body=[block], type_ignores=[]), "<auto-epg>", "exec")
    class ChannelMapper:
        @staticmethod
        def classify_service_ref(ref):
            return "IPTV" if str(ref).startswith("4097:") else "SAT"
    class Store:
        def blocked_refs(self):
            return ["1:0:1:BLOCK:"]
    env = {
        "_canon_ref": lambda value: str(value or "").strip().rstrip(":").upper(),
        "channel_mapper": ChannelMapper,
        "log": type("Log", (), {"exception": staticmethod(lambda *args: None)})(),
    }
    exec(fn, env)
    merge = env["_merge_persisted_auto_overrides"]
    rows = {
        "source_a::ID-GOOD": {"mode": "auto-repair", "refs": ["1:0:1:A:"]},
        "source_a::ID-TWO": {"mode": "auto", "refs": ["1:0:1:B:"]},
        "source_a::ID-MANUAL": {"mode": "manual", "refs": ["1:0:1:M:"]},
        "source_a::ID-BLOCK": {"mode": "auto", "refs": ["1:0:1:BLOCK:"]},
        "source_a::ID-LOST": {"mode": "auto-repair", "refs": ["1:0:1:NOTONRECEIVER:"]},
        "source_off::ID-OFF": {"mode": "auto", "refs": ["1:0:1:A:"]},
        "source_a::ID-HIJACK": {"mode": "auto", "refs": ["1:0:1:M:"]},
    }
    registry = (dict((x.upper().rstrip(":"), {}) for x in
                ["1:0:1:A:", "1:0:1:B:", "1:0:1:M:"]), {})
    out = merge({"source_a": {"id-good": ["1:0:1:A:"]}}, rows, Store(), False, registry)
    assert out["source_a"]["id-good"] == ["1:0:1:A:"], "must not duplicate canonical ref"
    assert out["source_a"]["id-two"] == ["1:0:1:B:"], "AUTO survives stale channels.xml"
    assert "id-manual" not in out["source_a"], "manual is handled by the earlier manual overlay"
    assert "id-hijack" not in out["source_a"], "MANUAL lock must win"
    assert "id-block" not in out["source_a"], "ignored/unmapped must stay blocked"
    assert "id-lost" not in out["source_a"], "foreign ref must be ignored"
    assert "source_off" not in out, "OFF source must remain OFF"
    out2 = merge({"source_a": {}}, {
        "source_a::iptv": {"mode":"smartmatch-iptv", "refs":["4097:0:1:ABC:","1:0:1:A:"]}},
        None, True, ({}, {}))
    assert out2 == {"source_a": {"iptv": ["4097:0:1:ABC:"]}}, "IPTV-only import"
    print("SELF-TEST OK: AUTO recovery, de-duplication, manual precedence, blocked/foreign/OFF protection, IPTV-only")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for flag in ("check", "install", "rollback", "self-test"):
        group.add_argument("--" + flag, action="store_true")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return
    path = installed_path()
    if args.rollback:
        files = sorted(BACKUP_DIR.glob("direct_native_import.py.*.bak"))
        if not files:
            raise RuntimeError("No receiver backup found")
        backup = files[-1]
        restored = backup.read_text(encoding="utf-8")
        compile(restored, str(path), "exec")
        atomic_write(path, restored)
        print("ROLLED BACK:", backup)
        print("Restart Enigma2 GUI to load the restored module")
        return
    original = path.read_text(encoding="utf-8")
    updated, changed = patch_source(original)
    if not changed:
        print("ALREADY PATCHED:", path)
        return
    print("COMPATIBLE: Native Import AUTO EPG overlay can be installed")
    if args.check:
        print("CHECK ONLY: no receiver files modified")
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / ("direct_native_import.py." + time.strftime("%Y%m%dT%H%M%S") + ".bak")
    shutil.copy2(path, backup)
    atomic_write(path, updated)
    try:
        py_compile.compile(str(path), doraise=True)
    except Exception:
        atomic_write(path, original)
        raise
    print("INSTALLED: saved AUTO EPG mappings now included in Native Import")
    print("BACKUP:", backup)
    print("PROTECTED: MANUAL, NO EPG/IGNORE, lamedb, bouquets, XMLTV, Rytec, mappings")
    print("NEXT: Restart Enigma2 GUI, then run EPGManager -> Import All once")


if __name__ == "__main__":
    main()
