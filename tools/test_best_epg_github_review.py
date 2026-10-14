#!/usr/bin/env python3
"""Isolated source and runtime regressions for GitHub Best EPG Match patch."""
import ast
import importlib.util
import pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(path, name):
    spec=importlib.util.spec_from_file_location(name, path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

patcher=load(ROOT/"tools"/"fix_best_epg_github_review.py", "best_match_patcher")
health=load(ROOT/"tools"/"github_match_health.py", "health")
snapshot=(ROOT/"data/release-inspect/channel_mapping.py").read_text(encoding="utf-8")
patched, changed=patcher.patch_source(snapshot)
assert changed
ast.parse(patched)
assert patcher.patch_source(patched)==(patched,False)
assert "from ..core import github_match_health" in patched
assert patched.count(patcher.MARKER)==1
assert "    # BEST_EPG_GITHUB_HEALTH_REVIEW_V1" in patched
assert '"review": bool(epg.get("_best_review"))' in patched
assert "theme.STATUS_YELLOW_INT" in patched
assert "github_match_health.generation()" in patched
assert "self._queue_github_best_match_health(suggestions)" in patched
assert "if epg.get(\"_best_review\")" in patched
assert 'not bool(first.get("_precision_auto"))' in patched
assert "if github_match_health.is_busy()" in patched
assert "getattr(self, \"_github_best_health_timer\", None)" in patched
try:
    patcher.patch_source("from ..core.mapping_store import MappingStore\n")
except RuntimeError:
    pass
else:
    raise AssertionError("unsupported release layout must refuse changes")

now=datetime(2026,10,14,9,0,tzinfo=timezone.utc).timestamp()
report={"generated_utc":"2026-10-13T14:19:54+00:00",
        "rows":{
         "abudhabitv.ae@sd":{"status":"PASS", "programmes":61,"latest_stop_utc":"2026-10-15T15:00:00+00:00"},
         "osncomedy.ae@sd":{"status":"WARN", "programmes":117,"latest_stop_utc":"2026-10-15T15:00:00+00:00"},
         "osn.216":{"status":"FAIL", "reason":"NO_FUTURE_EPG","programmes":0},
        }}
assert health.classify(report,"AbuDhabiTV.ae@SD",now)=="EPG_OK"
assert health.classify(report,"OSNComedy.ae@SD",now)=="EPG_WARN"
assert health.classify(report,"osn.216",now)=="NO_EPG"
assert health.classify(report,"missing-tv",now)=="UNKNOWN"
assert health.classify(report,"AbuDhabiTV.ae@SD",now+5*86400)=="UNKNOWN"
assert health.classify({"generated_utc":"2026-10-13T14:19:54+00:00","rows":{}}, "AbuDhabiTV.ae@SD",now)=="UNKNOWN"
assert health.source_key("ext_epgscrapers_osn")=="osn"
assert health.source_key("ext_epgscrapers_stctv")=="stctv"
assert health.source_key("ext_openepg_uae_6")==""
assert health.health("ext_openepg_uae_6","AbuDhabiTV.ae@SD",now) is None
health._reports["osn"]=report
health._reports["stctv"]={"generated_utc":"2026-10-13T14:23:45+00:00","rows":{}}
assert health.health("ext_epgscrapers_osn","AbuDhabiTV.ae@SD",now)=="EPG_OK"
assert health.health("ext_epgscrapers_stctv","AbuDhabiTV.ae@SD",now)=="UNKNOWN"
# Real regression reported on Vu+: STC DMC shows 50 schedules, 49 placeholders.
stc = {"generated_utc":"2026-10-13T14:23:45+00:00",
       "rows":{"dmc.eg@sd":{"status":"PASS","programmes":50,
                                "latest_stop_utc":"2026-10-15T15:00:00+00:00"}},
       "quality":{"generated_utc":"2026-10-13T14:23:45+00:00",
                  "placeholder_counts":{"dmc.eg@sd":49},"future_counts":{},
                  "low_diversity_ids":set()}}
osn = {"generated_utc":"2026-10-13T14:19:54+00:00",
       "rows":{"dmc.eg@sd":{"status":"WARN","programmes":35,
                                "latest_stop_utc":"2026-10-15T15:00:00+00:00"}}}
assert health.classify(stc,"DMC.eg@SD",now)=="SYNTHETIC", "50 generated events do NOT outrank 35 actual EPG"
assert health.classify(osn,"DMC.eg@SD",now)=="EPG_WARN"
stc["quality"]["placeholder_counts"]["dmc.eg@sd"] = 2
assert health.classify(stc,"DMC.eg@SD",now)=="EPG_OK"
stc["quality"]["low_diversity_ids"].add("dmc.eg@sd")
assert health.classify(stc,"DMC.eg@SD",now)=="SUSPECT"
# Rebuild a first-beta UI from the V2 transformation and replay its upgrade.
v1=patched
for old,new in patcher.UPGRADE_PAIRS:
    assert new in v1
    v1=v1.replace(new,old)
upgraded,changed=patcher.patch_source(v1)
assert changed and upgraded==patched, "upgrading V1 must preserve the exact V2 layout"
assert patcher.patch_source(upgraded)==(upgraded,False)
print("PASS: DMC STC 49/50 generated detected; OSN ahead, repeat detection, V1->V2 safe upgrade")
