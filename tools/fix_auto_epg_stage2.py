#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore persisted AUTO EPG ownership across Native Import's second filter.

This guarded Stage 2 hotfix applies AFTER the existing AUTO overlay hotfix.
It modifies only core/direct_native_import.py; backs up the previous module.
It does NOT reset Enigma2 EPG, receiver bouquets, mapping JSON or source XMLTV.

python3 fix_auto_epg_stage2.py --self-test
python3 fix_auto_epg_stage2.py --check
python3 fix_auto_epg_stage2.py --install
python3 fix_auto_epg_stage2.py --rollback
"""
from __future__ import print_function

import argparse
import os
import shutil
import tempfile
import time
from pathlib import Path

MODULE_PATHS = [
    "/usr/lib/enigma2/python/Plugins/Extensions/EPGManager/core/direct_native_import.py",
    "/usr/lib64/enigma2/python/Plugins/Extensions/EPGManager/core/direct_native_import.py",
]
BACKUP_DIR = Path("/etc/enigma2/epgmanager-auto-epg-backups")
STAGE2_MARKER = "    persisted_auto_keys = set()"
ANCHOR = "    # Expand only the already-winning automatic targets."
OLD_LOOP = """        for cid, refs in (channels or {}).items():
            if (str(mid).lower(), str(cid).lower()) in manual_keys:
                dst[str(cid)] = list(refs or [])
            else:
                auto_rows[str(cid)] = list(refs or [])
"""
NEW_LOOP = """        for cid, refs in (channels or {}).items():
            exact_key = (str(mid).lower(), str(cid).lower())
            if exact_key in manual_keys or exact_key in persisted_auto_keys:
                # These are already persisted/exclusive user or vetted AUTO
                # decisions. Never reclassify them as new fuzzy candidates.
                dst[str(cid)] = list(refs or [])
            else:
                auto_rows[str(cid)] = list(refs or [])
"""
INSERT = """    # Persisted AUTO ownership has already passed the automatic mapping
    # engine and single-owner arbitration. Do not send those exact locks back
    # through the unrelated legacy fuzzy/semantic validator: manual locks
    # previously survived this second pass while equally valid AUTO locks
    # were silently lost (OSN Comedy/Crime/Premiere receiver reproduction).
    persisted_auto_keys = set()
    for mk, mrec in (mapping_rows or {}).items():
        if not isinstance(mrec, dict):
            continue
        if str(mrec.get("mode") or "").strip().lower() not in (
                "auto", "auto-repair", "smartmatch", "smartmatch-iptv"):
            continue
        if not (mrec.get("refs") or []):
            continue
        try:
            am, ac = str(mk).split("::", 1)
        except ValueError:
            continue
        persisted_auto_keys.add((am.strip().lower(), ac.strip().lower()))

"""

def patch_source(src):
    if STAGE2_MARKER in src and NEW_LOOP in src:
        return src, False
    if "def _merge_persisted_auto_overrides(" not in src:
        raise RuntimeError("Stage 1 AUTO overlay missing; install the previous patch first")
    if STAGE2_MARKER in src or NEW_LOOP in src:
        raise RuntimeError("Partial Stage 2 patch; aborting safely")
    if src.count(ANCHOR) != 1 or src.count(OLD_LOOP) != 1:
        raise RuntimeError("Unsupported Native Import release layout; receiver unchanged")
    out = src.replace(ANCHOR, INSERT + ANCHOR, 1).replace(OLD_LOOP, NEW_LOOP, 1)
    compile(out, "direct_native_import.py", "exec")
    return out, True

def installed_path():
    for p in MODULE_PATHS:
        if os.path.isfile(p):
            return Path(p)
    raise RuntimeError("EPGManager Native Import module not found")

def atomic_write(path, content):
    fd,tmp = tempfile.mkstemp(prefix=".auto-epg-stage2-",suffix=".py",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def self_test():
    stub = ('def _merge_persisted_auto_overrides(): pass\n' +
            'def test(mapping_rows, parsed, manual_keys, time, source_meta, registry_index):\n' +
            ANCHOR + '\n' +
            '    phase = time.time()\n' +
            '    expanded_parsed = {}\n' +
            '    for mid, channels in (parsed or {}).items():\n' +
            '        meta = source_meta.get(str(mid).lower()) or {"trusted": True}\n' +
            '        dst = {}\n' +
            '        auto_rows = {}\n' +
            OLD_LOOP +
            '        expanded_parsed[mid] = {"exact":dst,"legacy":auto_rows}\n' +
            '    return expanded_parsed\n')
    patched, ok = patch_source(stub)
    assert ok and patched != stub
    assert patch_source(patched) == (patched, False)
    import ast
    node = ast.parse(patched)
    assert len(node.body) == 2
    ns={}
    exec(compile(node, "<stage2-test>", "exec"), ns)
    rows={
       "ext_epgscrapers_osn::OSNComedy.ae@SD":{"mode":"auto-repair","refs":["R_COMEDY"],"confidence":1000},
       "ext_epgscrapers_osn::OSNCrime.ae@SD":{"mode":"auto-repair","refs":["R_CRIME"],"confidence":1000},
       "ext_epgscrapers_osn::OSNMoviesAction.ae@SD":{"mode":"manual","refs":["R_ACTION"]},
    }
    fake={
       "ext_epgscrapers_osn":{
         "osncomedy.ae@sd":["R_COMEDY"],
         "osncrime.ae@sd":["R_CRIME"],
         "osnmoviesaction.ae@sd":["R_ACTION"],
         "unknown.id":["R_UNTRUSTED"],
       }
    }
    result=ns["test"](rows,fake,{("ext_epgscrapers_osn","osnmoviesaction.ae@sd")},time,{},None)
    exact=result["ext_epgscrapers_osn"]["exact"]
    legacy=result["ext_epgscrapers_osn"]["legacy"]
    assert len(exact)==3 and "osncomedy.ae@sd" in exact and "osncrime.ae@sd" in exact
    assert list(legacy)==["unknown.id"]
    try:\n        patch_source("def _merge_persisted_auto_overrides():pass\\n")\n    except RuntimeError:\n        pass\n    else:\n        raise AssertionError("unsupported layout must abort")
    print("SELF-TEST OK: persisted OSN AUTO bypasses second semantic filter; MANUAL protected; other candidates still checked")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    for arg in ("self-test","check","install","rollback"):
        group.add_argument("--"+arg,action="store_true")
    args=parser.parse_args()
    if args.self_test:
        self_test()
        return
    path=installed_path()
    if args.rollback:
        backups=sorted(BACKUP_DIR.glob("direct_native_import.py.stage2.*.bak"))
        if not backups:
            raise RuntimeError("No Stage 2 backup available")
        prior=backups[-1].read_text(encoding="utf-8")
        compile(prior,str(path),"exec")
        atomic_write(path,prior)
        print("STAGE 2 ROLLED BACK:",backups[-1])
        print("Restart Enigma2 GUI")
        return
    original=path.read_text(encoding="utf-8")
    updated,changed=patch_source(original)
    if not changed:
        print("ALREADY INSTALLED: Stage 2")
        return
    print("COMPATIBLE: Stage 2 AUTO EPG protection")
    if args.check:
        print("CHECK ONLY: receiver unchanged")
        return
    BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    backup=BACKUP_DIR/("direct_native_import.py.stage2."+time.strftime("%Y%m%dT%H%M%S")+".bak")
    shutil.copy2(path,backup)
    try:
        atomic_write(path,updated)
    except Exception:
        atomic_write(path,original)
        raise
    print("INSTALLED: AUTO EPG Stage 2 filter protection")
    print("BACKUP:",backup)
    print("PRESERVED: MANUAL mappings, Enigma2 service refs, XMLTV and GitHub Direct")
    print("NEXT: restart GUI then EPGManager -> Import All")
if __name__=="__main__":
    main()
