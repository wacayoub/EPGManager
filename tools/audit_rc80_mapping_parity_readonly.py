#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RC80 read-only GUI/Auto Mapping parity gate; never loads receiver settings.

Compare actual baseline and staged UI source bytes, never merely method names.
The verified rc79.1 original stays PRIVATE; do not commit it or the staged UI.
"""
import ast

SCREEN = "ChannelMappingScreen"
REVIEWED_PREVIEW = frozenset((
    "_cross_source_suggestion_candidates",
    "_refresh_cross_source_suggestions",
    "_queue_github_best_match_health",
    "_poll_github_best_match_health",
))
PROTECTED_APIS = frozenset((
    "_auto_batch_match", "_merge_persisted_store_ownership",
    "auto_map_current_channel", "auto_map_all", "undo_last_mapping",
    "open_manual_source_override", "_start_auto_repair_batch",
    "_queue_manual_generated_sync",
))


def _parse(payload, label):
    if not isinstance(payload, bytes):
        raise ValueError(label + ": expected original source bytes")
    try:
        return ast.parse(payload.decode("utf-8"), filename=label)
    except (SyntaxError, UnicodeError) as exc:
        raise ValueError(label + ": cannot parse Python source") from exc


def _methods(tree):
    screens = [node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == SCREEN]
    if len(screens) != 1:
        raise ValueError("missing or duplicate receiver Mapping screen")
    body = screens[0].body
    methods = {node.name: node for node in body if isinstance(node, ast.FunctionDef)}
    if len(methods) != sum(isinstance(node, ast.FunctionDef) for node in body):
        raise ValueError("duplicate Mapping method")
    return screens[0], methods


def require_safe_preview_parity(original_bytes, staged_bytes):
    """Raise on changed/missing original non-preview methods or writer shortcuts."""
    original = _parse(original_bytes, "working rc79.1 Mapping")
    staged = _parse(staged_bytes, "staged rc80 Mapping")
    old_screen, before = _methods(original)
    new_screen, after = _methods(staged)
    if not REVIEWED_PREVIEW.issubset(before) or not REVIEWED_PREVIEW.issubset(after):
        raise ValueError("missing originally working preview callbacks")
    if not PROTECTED_APIS.issubset(before) or not PROTECTED_APIS.issubset(after):
        raise ValueError("working Auto Mapping/manual APIs are missing")
    if set(before) != set(after):
        raise ValueError("Mapping method inventory changed")
    unexpected = sorted(name for name in before
                        if name not in REVIEWED_PREVIEW
                        and ast.dump(before[name]) != ast.dump(after[name]))
    if unexpected:
        raise ValueError("unreviewed receiver Mapping behavior changed: " + ", ".join(unexpected[:12]))
    # Protect non-method class declarations (skin, actions and key mappings).
    before_other = [n for n in old_screen.body if not isinstance(n, ast.FunctionDef)]
    after_other = [n for n in new_screen.body if not isinstance(n, ast.FunctionDef)]
    if [ast.dump(n) for n in before_other] != [ast.dump(n) for n in after_other]:
        raise ValueError("Mapping class configuration changed")
    modified = sorted(name for name in REVIEWED_PREVIEW
                      if ast.dump(before[name]) != ast.dump(after[name]))
    if set(modified) != REVIEWED_PREVIEW:
        raise ValueError("missing one or more reviewed RC80 preview updates")
    for name in ("_cross_source_suggestion_candidates", "_refresh_cross_source_suggestions"):
        for node in ast.walk(after[name]):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("assign_exclusive", "save", "write")):
                raise ValueError("preview is performing unapproved mapping writes")
    # Avoid GUI polling that performs synchronous network requests.
    for node in ast.walk(after["_poll_github_best_match_health"]):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        fn = node.func
        if fn.attr in ("refresh_async", "urlopen", "urlretrieve"):
            raise ValueError("mapping GUI poll performs blocking or unapproved work")
        if (fn.attr in ("get", "post") and isinstance(fn.value, ast.Name)
                and fn.value.id in ("requests", "http", "urllib")):
            raise ValueError("mapping GUI poll performs blocking network requests")
    return {
        "status": "PRIVATE_RUNTIME_PARITY_VERIFIED_OFFLINE",
        "original_method_count": len(before),
        "protected_apis": sorted(PROTECTED_APIS),
        "changed_preview_methods": modified,
        "auto_manual_mapping_writers_ast_preserved": True,
        "authorized_to_install": False,
    }
