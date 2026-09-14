#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alias-aware wrapper for the exhaustive All-ID audit.

Compatibility aliases are intentionally duplicated from an audited canonical
schedule. They must not make the canonical ID look like an unrelated clone.
The alias itself remains REVIEW/non-auto-lock so new mappings still prefer the
canonical ID.
"""
from __future__ import annotations

import json
import sys

import all_id_audit as base


def _manifest_arg():
    for i, arg in enumerate(sys.argv):
        if arg == "--manifest" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith("--manifest="):
            return arg.split("=", 1)[1]
    return ""


def _load_policy():
    path = _manifest_arg()
    if not path:
        return {}, set()
    try:
        m = json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        return {}, set()
    bein = ((m.get("shards") or {}).get("provider-bein") or {})
    compat = {}
    for alias, meta in (bein.get("compat_aliases") or {}).items():
        canonical = (meta or {}).get("canonical")
        if alias and canonical:
            compat[alias] = canonical
    nonrec = set(bein.get("non_recommended_ids") or [])
    return compat, nonrec


COMPAT_ALIAS_TO, NON_RECOMMENDED = _load_policy()


def alias_aware_duplicate_verdicts(rows):
    groups = base.defaultdict(list)
    for r in rows:
        if r["events"]:
            groups[r["fingerprint"]].append(r)
    duplicates = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ids = {x["id"] for x in members}

        # Exact two-ID compatibility pair: canonical is not penalized; alias is
        # explicitly REVIEW/non-auto-lock so suggestions prefer the canonical.
        explained_alias = None
        explained_canonical = None
        if len(members) == 2:
            for alias, canonical in COMPAT_ALIAS_TO.items():
                if ids == {alias, canonical}:
                    explained_alias, explained_canonical = alias, canonical
                    break
        if explained_alias:
            duplicates.append({
                "ids": [x["id"] for x in members],
                "unrelated": False,
                "compatibility_alias": True,
                "alias": explained_alias,
                "canonical": explained_canonical,
            })
            for r in members:
                if r["id"] == explained_alias:
                    tag = "COMPAT_ALIAS_TO=%s" % explained_canonical
                    if tag not in r["warnings"]:
                        r["warnings"].append(tag)
                    if r["verdict"] == "PASS":
                        r["verdict"] = "REVIEW"
                        r["score"] = min(r["score"], 92)
                    r["auto_lock_safe"] = False
            continue

        unrelated = False
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if base.similar(members[i]["name"], members[j]["name"]) < 0.45:
                    unrelated = True
                    break
            if unrelated:
                break
        duplicates.append({"ids": [x["id"] for x in members], "unrelated": unrelated})
        for r in members:
            group_tag = "DUPLICATE_TIMELINE_GROUP=%d" % len(members)
            if group_tag not in r["warnings"]:
                r["warnings"].append(group_tag)
            if unrelated and len(members) >= 3:
                issue = "WRONG_PROGRAMME_ASSIGNMENT_CLONED_TIMELINE=%d" % len(members)
                if issue not in r["issues"]:
                    r["issues"].append(issue)
                r["verdict"] = "FAIL"
                r["score"] = min(r["score"], 40)
                r["auto_lock_safe"] = False
            elif unrelated:
                if "DUPLICATE_TIMELINE_UNRELATED" not in r["warnings"]:
                    r["warnings"].append("DUPLICATE_TIMELINE_UNRELATED")
                if r["verdict"] == "PASS":
                    r["verdict"] = "REVIEW"
                    r["score"] = min(r["score"], 92)
                    r["auto_lock_safe"] = False
            elif r["verdict"] == "PASS":
                r["verdict"] = "REVIEW"
                r["score"] = min(r["score"], 92)
                r["auto_lock_safe"] = False

    # Preserve the explicit shard policy for legacy/suspicious IDs: never auto-lock.
    for r in rows:
        if r["id"] in NON_RECOMMENDED:
            tag = "NON_RECOMMENDED_LEGACY_ID"
            if tag not in r["warnings"]:
                r["warnings"].append(tag)
            if r["verdict"] == "PASS":
                r["verdict"] = "REVIEW"
                r["score"] = min(r["score"], 92)
            r["auto_lock_safe"] = False
    return duplicates


base.apply_duplicate_verdicts = alias_aware_duplicate_verdicts


if __name__ == "__main__":
    raise SystemExit(base.main())
