#!/usr/bin/env python3
"""EPGManager FINAL release preflight: inspect EXACT build-release source assembly.

Read-only: extracts the package archive in a temporary folder, overlays source/
exactly like build-release.yml, checks core runtime contracts and prints JSON.
It does not change update.json, make an IPK, install anything or create a release.
"""
from __future__ import print_function
import argparse, json, pathlib, py_compile, re, shutil, sys, tempfile, zipfile
ROOT = pathlib.Path(__file__).resolve().parent.parent

def _read(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""

def _same(path1, path2):
    return _read(path1) == _read(path2)

def analyze():
    result={"release_safe": False, "errors":[], "warnings":[], "info":{}, "checks":{}}
    fail=lambda key,detail: result["errors"].append({"check":key,"detail":detail})
    warn=lambda key,detail: result["warnings"].append({"check":key,"detail":detail})
    manifest=json.loads((ROOT/"update.json").read_text(encoding="utf-8"))
    result["info"]["manifest_version"]=manifest.get("version")
    archive_name=(ROOT/"source/current.txt").read_text(encoding="utf-8").strip()
    path=ROOT/"source"/archive_name
    result["info"]["build_source"]=archive_name
    if not path.is_file(): fail("archive","Current ZIP missing");return result
    with tempfile.TemporaryDirectory(prefix="epg-final-audit-") as tmpdir:
        temp=pathlib.Path(tmpdir)
        with zipfile.ZipFile(path) as z:
            names=z.namelist()
            result["info"]["source_zip_members"]=len(names)
            # Avoid zip-slip before extracting in the temporary build sandbox.
            for name in names:
                n=pathlib.PurePosixPath(name)
                if n.is_absolute() or ".." in n.parts:
                    fail("archive_path","Unsafe ZIP member "+str(name))
                    return result
            z.extractall(temp)
        candidates=sorted(temp.rglob("version.py"),key=lambda x:len(x.parts))
        src=next((p.parent for p in candidates
                  if (p.parent/"installer/build_ipk.sh").exists()),None)
        if not src:
            fail("archive","Cannot locate source root/build_ipk.sh")
            return result
        overlay=ROOT/"source/overlay"
        for f in overlay.rglob("*"):
            if f.is_file():
                dst=src/f.relative_to(overlay)
                dst.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(f,dst)
        version=_read(src/"version.py")
        result["info"]["assembled_version"]=re.search(r'__version__\s*=\s*[\"\x27]([^\"\x27]+)',version).group(1) if re.search(r'__version__\s*=\s*[\"\x27]([^\"\x27]+)',version) else "?"
        result["info"]["source_ui_lines"]=len(_read(src/"ui/channel_mapping.py").splitlines())
        result["info"]["inspected_ui_lines"]=len(_read(ROOT/"data/release-inspect/channel_mapping.py").splitlines())
        result["info"]["assembled_schema_version"]=next((x.strip() for x in version.splitlines() if "CONFIG_SCHEMA_VERSION" in x),"")
        def version_parts(v):
            return tuple(int(t) for t in re.findall(r"\d+", str(v or "")))
        incoming=result["info"]["assembled_version"]
        published=str(manifest.get("version"))
        if version_parts(incoming) <= version_parts(published):
            fail("version_mismatch","Assembled version %s must be newer than existing published release %s" %(incoming,published))
        if result["info"]["source_ui_lines"] < result["info"]["inspected_ui_lines"]*.6:
            fail("ui_code_loss","Assembled Smart Mapping UI is significantly smaller than current receiver snapshot (possible destructive downgrade)")
        ui=_read(src/"ui/channel_mapping.py")
        need_ui=["def _refresh_cross_source_suggestions(", "def _auto_batch_match(", "def _poll_github_best_match_health(", "github_match_health.health("]
        missing=[x for x in need_ui if x not in ui]
        result["checks"]["smart_mapping_contract_missing"]=missing
        if missing: fail("smart_mapping","Assembled UI missing: "+", ".join(missing))
        core=src/"core"
        cycle=_read(core/"epg_cycle.py")
        native=_read(core/"direct_native_import.py")
        if not (core/"github_match_health.py").is_file():
            fail("github_quality","core/github_match_health.py is not packaged")
        else:
            check=_read(core/"github_match_health.py")
            if '"SYNTHETIC"' not in check:
                fail("authenticity","Published quality helper missing generated-EPG detection")
        if "EPG-Scrapers XMLTV: stream the HTTPS GitHub feed" not in native:
            fail("github_direct","NativeImport is not assembled with verified GitHub streaming")
        if "def _merge_persisted_auto_overrides(" not in native:
            fail("auto_owner","Persisted AUTO Mapping overlay absent in assembled NativeImport")
        if "persisted_auto_keys = set()" not in native:
            fail("stage2","Second-pass AUTO ownership protection absent in assembled NativeImport")
        if "github_direct_stream.check_selected_sources(selected)" not in cycle:
            fail("epg_cycle","GitHub direct source health integration not assembled")
        if "clear_before_import=False," not in cycle:
            fail("safe_import","No explicit non-destructive clear_before_import override detected")
        # Presence of original beta hotfix scripts does not count as packaging!
        source_modules=[f for f in src.rglob("*.py") if "__pycache__" not in f.parts]
        result["info"]["assembled_python_modules"]=len(source_modules)
        bad_py=[]
        for file in source_modules:
            try:
                out=temp/"checks"/(str(file.relative_to(src))+".pyc")
                out.parent.mkdir(parents=True,exist_ok=True)
                py_compile.compile(str(file),cfile=str(out),doraise=True)
            except Exception as exc:
                bad_py.append([str(file.relative_to(src)),str(exc)[:180]])
        if bad_py: fail("syntax","Source compile errors: "+repr(bad_py[:12]))
        result["checks"]["compiled_python_modules"]=len(source_modules)-len(bad_py)
        data_version=_read(ROOT/"data/release-inspect/version.py")
        m=re.search(r'CONFIG_SCHEMA_VERSION\s*=\s*(\d+)',data_version)
        latest=int(m.group(1)) if m else -1
        m=re.search(r'CONFIG_SCHEMA_VERSION\s*=\s*(\d+)',version)
        effective=int(m.group(1)) if m else -1
        if latest>0 and effective>0 and effective<latest:
            fail("config_schema","Version schema %s in assembled package < known receiver schema %s" %(effective,latest))
        if "shlex.quote" not in _read(ROOT/"install.sh"):
            warn("installer","Installer uses dynamic command-line values without quoting")
        if "signatures" not in _read(ROOT/".github/workflows/build-release.yml"):
            warn("provenance","SHA-256 verifies downloaded artifact against mutable JSON; no independent signature found")
        if not any("pytest" in x or "unittest" in x for x in _read(ROOT/".github/workflows/build-release.yml").splitlines()):
            fail("release_gate","Release workflow can publish without a full regression suite")
        if ".github/workflows/build-release.yml" and 'workflow_dispatch:' in _read(ROOT/".github/workflows/build-release.yml"):
            warn("one_click_publish","Manual release publishes directly, without receiver smoke-test attestation")
    result["release_safe"]=not bool(result["errors"])
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--strict",action="store_true",help="exit nonzero when release blocked")
    args=p.parse_args()
    data=analyze()
    print("EPGMANAGER FINAL READINESS AUDIT")
    print(json.dumps(data,ensure_ascii=False,indent=2))
    print("RELEASE DECISION: "+("GO" if data["release_safe"] else "NO-GO"))
    return 0 if data["release_safe"] or not args.strict else 1
if __name__=="__main__":
    sys.exit(main())
