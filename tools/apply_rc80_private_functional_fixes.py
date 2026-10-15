#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact-source RC80 repairs for the owner's private rescue. Offline only.

This publishes no private code: only narrowly scoped, preimage SHA-locked
replacements. Refuses any source drift, writes no receiver data or XMLTV.
"""
from pathlib import Path
import ast
import hashlib

ORIGINAL = {
    "core/system_check.py": "d086d5e7ac49439d1d95bac4ce9dc97c1fe445348591c6548c50f2457269b07a",
    "core/github_match_health.py": "941c89c9ace27f63a63a41486b3f012f9f96cd7ed6c0dca602bc8b3353f4571c",
}


def _one(s, old, new, rel):
    if s.count(old) != 1:
        raise ValueError("RC80 source pattern mismatch: " + rel)
    return s.replace(old, new, 1)


def corrected(source):
    """Return patched module bytes and hashes; does not change input tree."""
    if set(source) != set(ORIGINAL):
        raise ValueError("RC80 functional-fix inventory mismatch")
    for rel, expected in ORIGINAL.items():
        if hashlib.sha256(source[rel]).hexdigest() != expected:
            raise ValueError("RC80 private preimage not verified: " + rel)
    rel = "core/system_check.py"
    s = source[rel].decode("utf-8")
    s = _one(s, '''        if not xa.get("exists"):
            status = "FAIL"; notes.append("local XML missing")''', '''        if not xa.get("exists"):
            # GitHub-direct sources need not persist XML on the receiver.
            # Cached remote metadata is not proof of native EPG import:
            # report WARN/unknown, never a false local-file FAIL or PASS.
            status = "WARN"
            notes.append("GitHub direct: local XML optional; native EPG not verified")
            if meta.get("ok"):
                notes.append("remote feed metadata available")
            else:
                notes.append("remote feed metadata unavailable")''', rel)
    s = _one(s, '''        {"name": "LOCAL XML", "status": "PASS" if local_ok == len(github_direct_sync.DIRECT_SOURCES) else "FAIL",
         "detail": "%d/%d valid local feeds • %d XMLTV IDs" % (local_ok, len(github_direct_sync.DIRECT_SOURCES), total_ids)},''', '''        {"name": "LOCAL XML / GITHUB DIRECT", "status": "PASS" if local_ok == len(github_direct_sync.DIRECT_SOURCES) else "WARN",
         "detail": "%d/%d valid optional local feeds • %d local XMLTV IDs; remote-only feeds require native EPG verification" % (local_ok, len(github_direct_sync.DIRECT_SOURCES), total_ids)},''', rel)
    system_check = s.encode("utf-8")

    rel = "core/github_match_health.py"
    s = source[rel].decode("utf-8")
    s = _one(s, '''    quality = report.get("quality") or {}
    # Quality data is only trustworthy when fresh and for this same feed.
    qdate = _epoch(quality.get("generated_utc"))''', '''    quality = report.get("quality") or {}
    # Programme slots alone do not certify genuine EPG. Require a fresh
    # per-ID quality denominator before the green EPG_OK status.
    certified = False
    # Quality data is only trustworthy when fresh and for this same feed.
    qdate = _epoch(quality.get("generated_utc"))''', rel)
    s = _one(s, '''        future_by_id = quality.get("future_counts") or {}
        # Old quality reports have no per-ID denominator; their current audit''', '''        future_by_id = quality.get("future_counts") or {}
        certified = (cid in future_by_id and int(future_by_id.get(cid) or 0) > 0)
        # Old quality reports have no per-ID denominator; their current audit''', rel)
    s = _one(s, '''    return "EPG_WARN" if str(row.get("status") or "").upper() != "PASS" else "EPG_OK"''', '''    return "EPG_OK" if certified and str(row.get("status") or "").upper() == "PASS" else "EPG_WARN"''', rel)
    health = s.encode("utf-8")
    result = {"core/system_check.py":system_check, "core/github_match_health.py":health}
    for name, payload in result.items():
        ast.parse(payload.decode("utf-8"), filename=name)
    return result


def apply_to_staged(staged_plugin, original):
    """Apply in private *temporary* staging and return exact reviewed hashes."""
    root = Path(staged_plugin)
    safe = {rel: original[rel] for rel in ORIGINAL}
    fixed = corrected(safe)
    for rel, content in fixed.items():
        target = root / rel
        if target.is_symlink() or not target.is_file() or target.read_bytes() != original[rel]:
            raise ValueError("RC80 private staged source drift: " + rel)
        target.write_bytes(content)
    return {rel: hashlib.sha256(content).hexdigest() for rel, content in fixed.items()}