#!/usr/bin/env python3
"""Regression test of the shipped Native Import patch against the release snapshot."""
from pathlib import Path
import ast
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
modfile = ROOT / "tools" / "fix_auto_mapping_epg.py"
spec = importlib.util.spec_from_file_location("fix_auto_mapping_epg", modfile)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
snapshot = (ROOT / "data" / "release-inspect" / "direct_native_import.py").read_text(encoding="utf-8")

# The release-inspect module is Enigma2-dependent, so test its AST and patch
# procedure without importing the full plugin on a workstation.
ast.parse(snapshot)
assert snapshot.count("def _merge_persisted_auto_overrides(") == 1
assert snapshot.count("parsed = _merge_persisted_auto_overrides(") == 1
assert patcher.patch_source(snapshot) == (snapshot, False), "patch is idempotent"

# Simulate an unpatched installed module from the current release snapshot.
legacy = snapshot.replace(patcher.HELPER, "", 1).replace(patcher.NEW_CALL, patcher.OLD_CALL, 1)
assert "def _merge_persisted_auto_overrides(" not in legacy
patched, did_change = patcher.patch_source(legacy)
assert did_change and patched == snapshot, "installer must generate the audited code exactly"
try:
    patcher.patch_source("def arbitrary_plugin(): pass")
except RuntimeError:
    pass
else:
    raise AssertionError("unsupported plugin version must fail closed")

patcher.run_self_test()
print("REGRESSION OK: exact release patch, idempotent install, incompatible-layout protection")
