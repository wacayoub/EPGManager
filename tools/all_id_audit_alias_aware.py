#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alias-aware wrapper for the exhaustive All-ID audit.

Compatibility aliases are intentionally duplicated from an audited canonical
schedule. They must not make the canonical ID look like an unrelated clone.
Aliases remain REVIEW/non-auto-lock so new mappings prefer the canonical ID.
Multiple aliases may legitimately point to the same canonical schedule.

When a release gate contains a FAIL, print the exact blocking ID(s), shard,
issues and warnings to the Actions log.  This keeps future production failures
diagnosable without weakening the zero-FAIL release policy.
"""
from __future__ import annotations

import json
import sys

import all_id_audit as base


def _arg_value(name):
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return ""


def _manifest_arg():
    return _arg_value("--manifest")


def _load_policy():
    path = _manifest_arg()
    if not path:
        return {}, set()
    try:
        manifest = json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        return {}, set()
    bein = ((manifest.get("shards") or {}).get("provider-bein") or {})
    compat = {}
    for alias, meta in (bein.get("compat_aliases") or {}).items():
        canonical = (meta or {}).get("canonical")
        if alias and canonical:
            compat[alias] = canonical
    nonrec = set(bein.get("non_recommended_ids") or [])
    return compat, nonrec


COMPAT_ALIAS_TO, NON_RECOMMENDED = _load_policy()


def _canonical_root(cid):
    seen = set()
    cur = cid
    while cur in COMPAT_ALIAS_TO and cur not in seen:
        seen.add(cur)
        cur = COMPAT_ALIAS_TO[cur]
    return cur


def alias_aware_duplicate_verdicts(rows):
    groups = base.defaultdict(list)
    for row in rows:
        if row["events"]:
            groups[row["fingerprint"]].append(row)

    duplicates = []
    for members in groups.values():
        if len(members) < 2:
            continue
        roots = {_canonical_root(x["id"]) for x in members}

        # One canonical timeline with one or many explicitly declared aliases.
        # The canonical remains eligible for PASS; only aliases are REVIEW.
        if len(roots) == 1 and any(x["id"] in COMPAT_ALIAS_TO for x in members):
            root = next(iter(roots))
            duplicates.append({
                "ids": [x["id"] for x in members],
                "unrelated": False,
                "compatibility_alias": True,
                "canonical_root": root,
            })
            for row in members:
                direct = COMPAT_ALIAS_TO.get(row["id"])
                if not direct:
                    continue
                tag = "COMPAT_ALIAS_TO=%s" % direct
                if tag not in row["warnings"]:
                    row["warnings"].append(tag)
                if row["verdict"] == "PASS":
                    row["verdict"] = "REVIEW"
                    row["score"] = min(row["score"], 92)
                row["auto_lock_safe"] = False
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
        for row in members:
            group_tag = "DUPLICATE_TIMELINE_GROUP=%d" % len(members)
            if group_tag not in row["warnings"]:
                row["warnings"].append(group_tag)
            if unrelated and len(members) >= 3:
                issue = "WRONG_PROGRAMME_ASSIGNMENT_CLONED_TIMELINE=%d" % len(members)
                if issue not in row["issues"]:
                    row["issues"].append(issue)
                row["verdict"] = "FAIL"
                row["score"] = min(row["score"], 40)
                row["auto_lock_safe"] = False
            elif unrelated:
                if "DUPLICATE_TIMELINE_UNRELATED" not in row["warnings"]:
                    row["warnings"].append("DUPLICATE_TIMELINE_UNRELATED")
                if row["verdict"] == "PASS":
                    row["verdict"] = "REVIEW"
                    row["score"] = min(row["score"], 92)
                    row["auto_lock_safe"] = False
            elif row["verdict"] == "PASS":
                row["verdict"] = "REVIEW"
                row["score"] = min(row["score"], 92)
                row["auto_lock_safe"] = False

    # Preserve explicit shard policy for legacy/suspicious IDs: never auto-lock.
    for row in rows:
        if row["id"] in NON_RECOMMENDED:
            tag = "NON_RECOMMENDED_LEGACY_ID"
            if tag not in row["warnings"]:
                row["warnings"].append(tag)
            if row["verdict"] == "PASS":
                row["verdict"] = "REVIEW"
                row["score"] = min(row["score"], 92)
            row["auto_lock_safe"] = False
    return duplicates


def _print_blocking_failures():
    path = _arg_value("--json")
    if not path:
        return
    try:
        report = json.load(open(path, "r", encoding="utf-8"))
    except Exception as exc:
        print("ALL-ID BLOCKER DETAIL unavailable: %s" % exc)
        return

    failures = [r for r in report.get("channels", []) if r.get("verdict") == "FAIL"]
    if not failures:
        print("ALL-ID BLOCKER DETAIL: none")
        return

    print("ALL-ID BLOCKER DETAIL: %d FAIL channel(s)" % len(failures))
    for row in failures:
        print(
            "  [FAIL] shard=%s id=%s name=%s events=%s coverage=%sh score=%s" % (
                row.get("shard", "?"), row.get("id", "?"), row.get("name", "?"),
                row.get("events", "?"), row.get("coverage_hours", "?"), row.get("score", "?"),
            )
        )
        print("    issues=%s" % (", ".join(row.get("issues") or []) or "NONE"))
        print("    warnings=%s" % (", ".join(row.get("warnings") or []) or "NONE"))
        for event in (row.get("preview") or [])[:3]:
            print("    preview=%s | %s" % (event.get("start", ""), event.get("title", "")))


base.apply_duplicate_verdicts = alias_aware_duplicate_verdicts


if __name__ == "__main__":
    rc = base.main()
    _print_blocking_failures()
    raise SystemExit(rc)
