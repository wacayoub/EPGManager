#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guarded Best EPG Match GitHub-report + yellow REVIEW UI hotfix.

--check only checks compatibility. --install backs up the receiver UI module,
adds tiny core/github_match_health.py, and patches ONLY the Smart Mapping UI.
--rollback restores the saved channel_mapping.py and removes just the helper
installed by this tool. Existing user mappings, SAT bouquets, XML and Native
Import fixes are untouched.
"""
from __future__ import print_function

import argparse
import ast
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.request import Request, urlopen

BASES = [
    Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager"),
    Path("/usr/lib64/enigma2/python/Plugins/Extensions/EPGManager"),
]
BACKUPS = Path("/etc/enigma2/epgmanager-best-epg-github-backups")
HELPER_URL = ("https://raw.githubusercontent.com/wacayoub/EPGManager/"
              "fix/auto-mapping-native-epg-20261014/tools/github_match_health.py")
MARKER = "    # BEST_EPG_GITHUB_HEALTH_REVIEW_V1"
# One source-safe transformation per semantic site. No blind package overwrite.
REPLACES = [
    (
        "from ..core.mapping_store import MappingStore\n",
        "from ..core.mapping_store import MappingStore\nfrom ..core import github_match_health\n",
    ),
    (
        '''        mapped = bool(meta.get("mapped"))
        health = str(meta.get("health") or "EPG ?").upper()
        if health == "EPG OK":''',
        '''        mapped = bool(meta.get("mapped"))
        health = str(meta.get("health") or "EPG ?").upper()
        review = bool(meta.get("review")) and not mapped
        if review:
            health_color = theme.STATUS_YELLOW_INT
            health_text = "CHECK"
        elif health == "EPG OK":''',
    ),
    (
        '''                        meta.append({"mapped": bool(self._is_mapped(epg)),
                                     "health": self._epg_health_text(epg)})''',
        '''                        meta.append({"mapped": bool(self._is_mapped(epg)),
                                     "review": bool(epg.get("_best_review")),
                                     "health": self._epg_health_text(epg)})''',
    ),
    (
        '''        if not sid or not cid:
            return ""
        try:
            health = github_direct_sync.id_health(sid, cid)''',
        '''        if not sid or not cid:
            return ""
        # BEST_EPG_GITHUB_HEALTH_REVIEW_V1: remote 48h per-ID audit, cache ONLY.
        # Never trust stale local "NO EPG" once Native Import reads GitHub.
        remote = github_match_health.health(sid, cid)
        if remote is not None:
            return {"EPG_OK": "  •  ID OK", "EPG_WARN": "  •  EPG WARN",
                    "NO_EPG": "  •  NO EPG"}.get(remote, "")
        try:
            health = github_direct_sync.id_health(sid, cid)''',
    ),
    (
        '''            epg["_suggestion_active"] = bool(sid in (self.auto_selected_source_ids or set()))

            # One best row per source''',
        '''            epg["_suggestion_active"] = bool(sid in (self.auto_selected_source_ids or set()))
            epg["_github_health"] = github_match_health.health(sid, cid)

            # One best row per source''',
    ),
    (
        '''            rank = (
                0 if auto else 1,
                -int(confidence),''',
        '''            rank = (
                0 if epg["_github_health"] in ("EPG_OK", "EPG_WARN") else
                (2 if epg["_github_health"] == "NO_EPG" else 1),
                0 if auto else 1,
                -int(confidence),''',
    ),
    (
        '''        rows.sort(key=lambda e: (
            0 if e.get("_suggestion_current") else 1,
            0 if e.get("_precision_auto") else 1,''',
        '''        rows.sort(key=lambda e: (
            0 if e.get("_suggestion_current") else 1,
            0 if e.get("_github_health") in ("EPG_OK", "EPG_WARN") else
            (2 if e.get("_github_health") == "NO_EPG" else 1),
            0 if e.get("_precision_auto") else 1,''',
    ),
    (
        '''        cache_key = ("__smart_suggestions__", str((service or {}).get("ref") or ""),
                     self.mapping_revision, boot_generation, index_generation)''',
        '''        cache_key = ("__smart_suggestions__", str((service or {}).get("ref") or ""),
                     self.mapping_revision, boot_generation, index_generation,
                     github_match_health.generation())''',
    ),
    (
        '''        suggestions = self._cross_source_suggestion_candidates(service, limit=12)
        # rc46:''',
        '''        suggestions = self._cross_source_suggestion_candidates(service, limit=12)
        # Fetch tiny current GitHub health reports in a DAEMON; UI stays RAM-only.
        self._queue_github_best_match_health(suggestions)
        if suggestions:
            first = suggestions[0]
            if not first.get("_suggestion_current"):
                first["_best_review"] = (
                    not bool(first.get("_precision_auto")) or
                    first.get("_github_health") not in ("EPG_OK", "EPG_WARN"))
        # rc46:''',
    ),
    (
        '''            for epg in suggestions:
                confidence = int(epg.get("_precision_confidence") or 0)''',
        '''            for epg in suggestions:
                confidence = int(epg.get("_precision_confidence") or 0)''',
    ),
    (
        '''                rows.append(self._compact_epg_row(epg, score=pct, grade=grade,
                                                  mapped=bool(epg.get("_suggestion_current"))))
                ordered.append(epg)''',
        '''                label = self._compact_epg_row(epg, score=pct, grade=grade,
                                              mapped=bool(epg.get("_suggestion_current")))
                if epg.get("_best_review"):
                    label += "  •  CHECK"
                rows.append(label)
                ordered.append(epg)''',
    ),
    (
        '''            elif status in ("REVIEW", "AMBIGUOUS", "MANUAL"):
                color = theme.STATUS_YELLOW_INT
                tag = "MANUAL"''',
        '''            elif status in ("REVIEW", "AMBIGUOUS", "MANUAL"):
                color = theme.STATUS_YELLOW_INT
                tag = "CHECK"''',
    ),
    (
        '''            if target and status in ("MAPPED", "SAFE"):
                detail += "  →  " + target''',
        '''            if target and status in ("MAPPED", "SAFE", "REVIEW"):
                detail += "  →  " + target''',
    ),
    (
        '''            item["_precision_reason"] = str(check.get("reason") or "")
            try:''',
        '''            item["_precision_reason"] = str(check.get("reason") or "")
            item["_github_health"] = github_match_health.health(sid, item.get("channel_id"))
            try:''',
    ),
    (
        '''        proof_rows = [{"auto": True,
                       "confidence": int(epg.get("_precision_confidence") or 0),''',
        '''        # The live GitHub 48h report decides whether an EXACT auto candidate
        # can actually deliver EPG. Prefer a confirmed active feed before a
        # nominally stronger match with no programme evidence (STC vs OSN).
        # A proven NO_EPG ID is never auto-applied. Unknown legacy feeds keep
        # their existing conservative behaviour.
        autos = [row for row in autos if row.get("_github_health") != "NO_EPG"]
        online_ready = [row for row in autos
                        if row.get("_github_health") in ("EPG_OK", "EPG_WARN")]
        if online_ready:
            autos = online_ready
        proof_rows = [{"auto": True,
                       "confidence": int(epg.get("_precision_confidence") or 0),''',
    ),
    (
        '''        if allowed:
            return {"status": "review", "best": None, "matches": allowed}
        return {"status": "nomatch", "best": None, "matches": []}''',
        '''        if allowed:
            allowed.sort(key=lambda row: (
                0 if row.get("_github_health") in ("EPG_OK", "EPG_WARN") else
                (2 if row.get("_github_health") == "NO_EPG" else 1),
                -int(row.get("_precision_confidence") or 0),
                int(row.get("_auto_context_rank") or 9),
                str(row.get("source_id") or ""),
            ))
            return {"status": "review", "best": None,
                    "review_best": allowed[0], "matches": allowed}
        return {"status": "nomatch", "best": None, "matches": []}''',
    ),
    (
        '''                    elif status == "review" or matches:
                        _line = "REVIEW  %s  • manual mapping required" % row_name
                        self._auto_repair_live = {"channel": row_name,
                            "phase": "REVIEW", "match": _line, "index": idx + 1, "total": total}
                        row = {"ref": str(service.get("ref") or ""), "name": row_name, "status": "REVIEW", "target": ""}''',
        '''                    elif status == "review" or matches:
                        proposal = (verdict or {}).get("review_best") or (matches[0] if matches else {})
                        proposal_name = str(proposal.get("display_name") or proposal.get("channel_id") or "EPG")
                        proposal_source = str(proposal.get("source_name") or proposal.get("source_id") or "source")
                        target = "%s • %s" % (proposal_name, proposal_source)
                        _line = "CHECK  %s  →  %s" % (row_name, target)
                        self._auto_repair_live = {"channel": row_name,
                            "phase": "REVIEW", "match": _line, "index": idx + 1, "total": total}
                        row = {"ref": str(service.get("ref") or ""), "name": row_name,
                               "status": "REVIEW", "target": target}''',
    ),
    (
        '''            try:
                preview = source_quality.get_programme_preview_cached(sid, cid, max_future_hours=96) or {}
            except Exception:
                preview = {}
            # RC17:''',
        '''            try:
                # Direct GitHub streams must never inherit NOW/NEXT from stale
                # receiver XML quality scans. The remote preview shard is read
                # asynchronously and remains presentation-only.
                preview = ({} if github_match_health.source_key(sid) else
                           (source_quality.get_programme_preview_cached(
                               sid, cid, max_future_hours=96) or {}))
            except Exception:
                preview = {}
            # RC17:''',
    ),
    (
        '''                    if local_path and os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
                        with open(local_path, "rb") as fh:''',
        '''                    if github_match_health.source_key(sid):
                        local_path = ""  # Native Import uses GitHub, never stale local XML.
                    if local_path and os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
                        with open(local_path, "rb") as fh:''',
    ),
]
# Optional additive helpers bound to the Smart Mapping UI; do not compete with
# the existing NativeImport worker or other EPG timers.
POLL_METHOD = '''
    def _queue_github_best_match_health(self, suggestions):
        ids = set(str((x or {}).get("source_id") or "") for x in suggestions or [])
        if not ids:
            return
        try:
            began = github_match_health.refresh_async(ids)
            if began or github_match_health.is_busy():
                self._github_best_health_timer.start(350, True)
        except Exception:
            pass

    def _poll_github_best_match_health(self):
        try:
            gen = github_match_health.generation()
            if gen != self._github_health_generation:
                self._github_health_generation = gen
                # Only the Best Match pane is invalidated; no receiver rescans.
                self.selection_cache.clear()
                src = self._current("sources", self.source_groups) or {}
                service = self._current("channels", self.bouquet_services) or {}
                if src.get("suggestion_all_sources") and service:
                    self._refresh_cross_source_suggestions(service)
            if github_match_health.is_busy():
                self._github_best_health_timer.start(450, True)
        except Exception:
            pass

'''
TIMER_INIT_OLD = '''        _pcb.append(self._poll_safe_preview_warm)
        # 8.1.1: debounce Reception/Bouquet'''
TIMER_INIT_NEW = '''        _pcb.append(self._poll_safe_preview_warm)
        self._github_health_generation = github_match_health.generation()
        self._github_best_health_timer = eTimer()
        _hcb = self._github_best_health_timer.callback if hasattr(self._github_best_health_timer, "callback") else self._github_best_health_timer.timeout.get()
        _hcb.append(self._poll_github_best_match_health)
        # 8.1.1: debounce Reception/Bouquet'''
STOP_OLD = '''getattr(self, "_safe_preview_timer", None)):'''
STOP_NEW = '''getattr(self, "_safe_preview_timer", None), getattr(self, "_github_best_health_timer", None)):'''
INSERT_METHOD_BEFORE = "    def _refresh_cross_source_suggestions(self, service):"


def patch_source(src):
    if MARKER in src and "def _poll_github_best_match_health" in src:
        return src, False
    if MARKER in src or "from ..core import github_match_health" in src:
        raise RuntimeError("Partial Best Match GitHub patch; refusing to write")
    out = src
    for old, new in REPLACES:
        if old == new:
            continue
        if out.count(old) != 1:
            raise RuntimeError("Incompatible UI anchor: %s" % old.strip().split("\n")[0][:85])
        out = out.replace(old, new, 1)
    for old,new in ((TIMER_INIT_OLD,TIMER_INIT_NEW),(STOP_OLD,STOP_NEW)):
        if out.count(old) != 1:
            raise RuntimeError("Incompatible UI timer/cleanup anchor")
        out = out.replace(old,new,1)
    if out.count(INSERT_METHOD_BEFORE) != 1:
        raise RuntimeError("Best Match method missing")
    out = out.replace(INSERT_METHOD_BEFORE,POLL_METHOD+INSERT_METHOD_BEFORE,1)
    compile(out,"channel_mapping.py","exec")
    return out, True


def find_plugin():
    for root in BASES:
        path = root / "ui" / "channel_mapping.py"
        if path.is_file():
            return root, path
    raise RuntimeError("EPGManager/ui/channel_mapping.py not found")

def atomic_write(path, content):
    fd,temp = tempfile.mkstemp(prefix=".epg-best-match-",suffix=".tmp",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="") as fp:
            fp.write(content); fp.flush(); os.fsync(fp.fileno())
        if path.exists():
            os.chmod(temp,path.stat().st_mode & 0o777)
        os.replace(temp,str(path))
    finally:
        if os.path.exists(temp): os.unlink(temp)

def helper_source():
    request=Request(HELPER_URL,headers={"User-Agent":"EPGManager-BestMatch-Installer"})
    with urlopen(request,timeout=12) as response:
        raw=response.read(18000)
    data=raw.decode("utf-8")
    compile(data,"github_match_health.py","exec")
    if "def classify(" not in data or "def refresh_async(" not in data:
        raise RuntimeError("Unexpected GitHub health helper content")
    return data

def main():
    p=argparse.ArgumentParser(description=__doc__)
    options=p.add_mutually_exclusive_group(required=True)
    for flag in ("check","install","rollback"):
        options.add_argument("--"+flag,action="store_true")
    args=p.parse_args()
    root,ui=find_plugin()
    helper=root/"core"/"github_match_health.py"
    if args.rollback:
        backups=sorted(BACKUPS.glob("channel_mapping.py.*.bak"))
        if not backups:
            raise RuntimeError("No UI backup found")
        prev=backups[-1].read_text(encoding="utf-8")
        compile(prev,str(ui),"exec")
        atomic_write(ui,prev)
        # The helper has no side effects and may be used by later releases;
        # retaining it avoids breaking plugins upgraded since installation.
        print("UI ROLLED BACK:",backups[-1])
        print("GUI restart needed")
        return
    source=ui.read_text(encoding="utf-8")
    patched,changed=patch_source(source)
    print("COMPATIBLE: Best EPG Match GitHub per-ID reports / yellow review" if changed
          else "ALREADY PATCHED: Best EPG Match GitHub health")
    if args.check:
        print("CHECK ONLY: nothing changed")
        return
    downloaded=helper_source()
    BACKUPS.mkdir(parents=True,exist_ok=True)
    backup=BACKUPS/("channel_mapping.py."+time.strftime("%Y%m%dT%H%M%S")+".bak")
    if changed:
        shutil.copy2(ui,backup)
    old_helper=helper.read_text(encoding="utf-8") if helper.is_file() else None
    try:
        atomic_write(helper,downloaded)
        if changed:
            atomic_write(ui,patched)
    except Exception:
        if changed: atomic_write(ui,source)
        if old_helper is not None: atomic_write(helper,old_helper)
        else:
            try: helper.unlink()
            except OSError: pass
        raise
    print("INSTALLED: GitHub online per-ID Best Match, health-aware ranking, yellow CHECK")
    if changed: print("UI BACKUP:",backup)
    print("PROTECTED: mapping JSON, service references, native EPGImport, Stage 1/2 and channel bouquets")
    print("NEXT: restart GUI; open Smart Mapping > Best EPG Match; reports load in background")

if __name__=="__main__":
    main()
