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

# Receiver reproduction: no OSN entry in epgmanager_srp_maps.json, but
# 24 automatic MappingStore rows and 2 manual rows for ext_epgscrapers_osn.
# The selected OSN source exists, and its generated channel map is EMPTY.
from types import SimpleNamespace

def canonical(ref):
    return str(ref or "").rstrip(":").upper()

def fake_logger(*args):
    return None

auto_fn = ast.parse(patcher.HELPER).body[0]
module = ast.fix_missing_locations(ast.Module(body=[auto_fn], type_ignores=[]))
ns = {
    "_canon_ref": canonical,
    "channel_mapper": SimpleNamespace(classify_service_ref=lambda x: "SAT"),
    "log": SimpleNamespace(exception=fake_logger),
}
exec(compile(module, "<osn-auto-integration>", "exec"), ns)
helper = ns["_merge_persisted_auto_overrides"]
source = "ext_epgscrapers_osn"
rows = {
    source + "::OSNMoviesAction.ae@SD": {"mode":"manual","refs":["1:0:1:ACTION:"]},
    source + "::OSNMoviesFamily.ae@SD": {"mode":"manual","refs":["1:0:1:FAMILY:"]},
}
for i in range(24):
    rows[source + "::OSNtest%02d.ae@SD" % i] = {
        "mode":"auto-repair","refs":["1:0:1:AUTO%02d:" % i],
        "confidence":1000,
    }
registry = ({canonical("1:0:1:ACTION:"):{"name":"OSN Movies Action"},
             canonical("1:0:1:FAMILY:"):{"name":"OSN Movies Family"},
             **{canonical("1:0:1:AUTO%02d:" % i):{"name":"OSN Test %d"%i} for i in range(24)}}, {})
manual_seed = {
    "osnmoviesaction.ae@sd": ["1:0:1:ACTION:"],
    "osnmoviesfamily.ae@sd": ["1:0:1:FAMILY:"],
}
restored = helper({source:manual_seed}, rows, SimpleNamespace(blocked_refs=lambda: []), False, registry)
assert len(restored[source]) == 26, "all 24 autos must be importable without SRP index"
assert sum(bool(k.startswith("osntest")) for k in restored[source]) == 24
assert restored[source]["osnmoviesaction.ae@sd"] == ["1:0:1:ACTION:"]
assert restored[source]["osnmoviesfamily.ae@sd"] == ["1:0:1:FAMILY:"]
assert helper({"other_source":{}}, rows, None, False, registry) == {"other_source":{}}, "no OFF source promotion"
print("OSN REPRODUCTION OK: 24 saved AUTO + 2 protected MANUAL, no SRP map")

# The receiver has also installed the GitHub-direct-stream compatibility
# patch. It must remain intact when AUTO is injected in the Native plan.
native_direct = legacy.replace(
    "            # rc36.8 direct-feed contract:",
    "            # EPG-Scrapers XMLTV: stream the HTTPS GitHub feed",
    1,
)
combined, changed = patcher.patch_source(native_direct)
assert changed
assert "EPG-Scrapers XMLTV: stream the HTTPS GitHub feed" in combined
assert "def _merge_persisted_auto_overrides(" in combined
compile(combined, "direct_native_import.py", "exec")
print("DIRECT GITHUB COMPAT OK: source streaming logic preserved")

print("REGRESSION OK: exact release patch, idempotent install, incompatible-layout protection")
