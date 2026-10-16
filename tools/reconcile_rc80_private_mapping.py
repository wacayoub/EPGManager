#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reconcile the private RC79.1 Mapping UI with reviewed RC80 preview changes.

Pure in-memory offline transformation. No Enigma2, opkg, network, mappings,
credentials or private receiver source content are read or published here.
"""
import ast
import hashlib

BASELINE_UI_SHA256 = "314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002"
RECONCILED_UI_SHA256 = "a528d9105d8cb32b952c7f759b1f2b223792c5d75c73fcc9749538791fcd6bb8"
PUBLIC_UI_GIT_BLOB = "26165496d9c530058dbdbc858c34794c0a4fb1a2"

def check(test, message):
    if not test:
        raise ValueError(message)

def one(src, old, new, name):
    check(src.count(old) == 1, "RC80 reviewed UI pattern mismatch: " + name)
    return src.replace(old, new, 1)

def _sha(blob):
    return hashlib.sha256(blob).hexdigest()

def _git_sha(blob):
    return hashlib.sha1(b"blob " + str(len(blob)).encode("ascii") + bytes([0]) + blob).hexdigest()

def reconcile_private_ui(working: bytes, public: bytes) -> tuple[bytes, list[str]]:
    """Keep all other RC79.1 methods and graft four reviewed preview methods."""
    check(_sha(working) == BASELINE_UI_SHA256, "unverified working RC79.1 UI preimage")
    check(_git_sha(public) == PUBLIC_UI_GIT_BLOB, "unverified public RC80 mapping overlay")
    old = working.decode("utf-8")
    new = public.decode("utf-8")
    def methods(src):
        tree = ast.parse(src)
        return {n.name:n for cl in tree.body
                if isinstance(cl, ast.ClassDef) and cl.name == "ChannelMappingScreen"
                for n in cl.body if isinstance(n, ast.FunctionDef)}
    own = methods(old)
    incoming = methods(new)
    allowed = ("_cross_source_suggestion_candidates", "_refresh_cross_source_suggestions",
               "_queue_github_best_match_health", "_poll_github_best_match_health")
    for name in allowed:
        check(name in own and name in incoming, "missing approved preview method " + name)
    for name in ("_cross_source_suggestion_candidates", "_refresh_cross_source_suggestions"):
        for n in ast.walk(incoming[name]):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                check(n.func.attr not in ("assign_exclusive", "save", "write"),
                      "unsafe preview writer " + name)
    lines = old.splitlines(keepends=True)
    newlines = new.splitlines(keepends=True)
    patches = {name: "".join(newlines[incoming[name].lineno-1:incoming[name].end_lineno])
               for name in allowed}
    cross = patches["_cross_source_suggestion_candidates"]
    cross = one(cross, '            0 if e.get("_suggestion_current") else 1,',
                '            0 if e.get("_suggestion_current") and (e.get("_suggestion_manual") or int(e.get("_verified_epg_rank", 3)) <= 2) else 2 if e.get("_suggestion_current") else 1,',
                "suggestion current/manual precedence")
    cross = one(cross, '                    "_suggestion_current": True,',
                '                    "_suggestion_current": True,\n                    "_suggestion_manual": str(info.get("mode") or "").lower() == "manual",\n                    "_github_health": github_match_health.health(sid, cid),',
                "mapping manual marker retention")
    patches["_cross_source_suggestion_candidates"] = cross
    refresh = patches["_refresh_cross_source_suggestions"]
    refresh = one(refresh, 'int(time.time() // 60))',
                  'int(time.time() // 60), github_match_health.generation())',
                  "preview cache key generation retention")
    marker = "        rows = []\n"
    oldcode = """        if suggestions:
            first = suggestions[0]
            if not first.get('_suggestion_current'):
                first['_best_review'] = not bool(first.get('_precision_auto')) or first.get('_github_health') not in ('EPG_OK', 'EPG_WARN')
"""
    refresh = one(refresh, marker, oldcode + marker, "review best marker retention")
    marker = "                rows.append(preview)\n"
    refresh = one(refresh, marker,
                  "                if epg.get('_best_review'):\n                    preview += '  •  CHECK'\n" + marker,
                  "review best label retention")
    patches["_refresh_cross_source_suggestions"] = refresh
    for name in sorted(allowed, key=lambda n: own[n].lineno, reverse=True):
        lines[own[name].lineno-1:own[name].end_lineno] = patches[name].splitlines(keepends=True)
    combined = "".join(lines)
    imports = """# RC80 verified public evidence is optional; retain RC79.1 functionality.
try:
    from ..core import verified_epg_match
except ImportError:
    verified_epg_match = None
try:
    from ..core import verified_epg_refresh
except ImportError:
    verified_epg_refresh = None
"""
    combined = one(combined, "from ..core.mapping_store import MappingStore",
                   imports + "from ..core.mapping_store import MappingStore",
                   "RC80 optional evidence imports")
    merged = methods(combined)
    check(set(merged) == set(own), "receiver method inventory mismatch")
    changed = sorted(name for name in own if ast.dump(own[name]) != ast.dump(merged[name]))
    check(changed == sorted(allowed), "unreviewed mapping method changes")
    check("CONFIG_SCHEMA_VERSION" not in combined, "mapping UI touched schema")
    resulting = combined.encode("utf-8")
    from audit_rc80_mapping_parity_readonly import require_safe_preview_parity
    require_safe_preview_parity(working, resulting)
    check(_sha(resulting) == RECONCILED_UI_SHA256,
          "private RC80 UI byte hash mismatch; do not assemble")
    return resulting, changed
