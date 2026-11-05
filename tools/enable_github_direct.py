#!/usr/bin/env python3
"""Guarded one-time installer: EPGManager GitHub -> OE-Alliance EPGImport.

Only changes two EPGManager Python modules and installs the small audit helper.
Preserves /etc/epgmanager_epg XML, *.lastgood, all ServiceRef/mapping files,
Rytec, source selection, and Enigma2 settings. Requires a GUI restart to load.
"""
from __future__ import print_function
import argparse
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

PLUGIN = Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
BACKUP_BASE = Path("/etc/enigma2/epgmanager-github-direct-backups")
HELPER_URL = ("https://raw.githubusercontent.com/wacayoub/EPGManager/"
              "main/tools/github_direct_stream.py")
OLD_SOURCE_ROOT = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/"
NEW_SOURCE_ROOT = "https://raw.githubusercontent.com/wacayoub/EPGManager/main/"
CYCLE_CALL = "direct_sync = self._sync_direct_xml_for_import(selected)"
NATIVE_START = "        else:\n            # rc36.8 direct-feed contract:"
NATIVE_END = "        overlay_meta = {}"

DIRECT_BLOCK = '''        else:
            # EPG-Scrapers XMLTV: stream the HTTPS GitHub feed through EPGImport.
            # Receiver-side *.xml / *.lastgood are never used for direct sources.
            if item.get("direct_feed"):
                url = str(item.get("url") or "")
                if not (url.startswith("https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds/")
                        and url.endswith(".xml.gz")):
                    stats["unreadable_files"] += 1
                    stats["skipped_source_names"].append(name)
                    continue
                stats["direct_remote_sources"] += 1
            else:
                # Keep the old grouped-provider and Rytec behaviour unchanged.
                local_direct = (external_sources.local_xml_path(item, epg_dir)
                                if item.get("grouped_feed") else "")
                if local_direct and os.path.isfile(local_direct) and os.path.getsize(local_direct) > 32:
                    url = local_direct
                    stats["local_sources"] += 1
                else:
                    fast = source_fast_status.get(item.get("id")) or {}
                    if fast.get("online") and fast.get("url"):
                        url = str(fast.get("url"))
                    else:
                        url = str(item.get("url") or "")
                    stats["direct_remote_sources"] += 1
            if not url:
                stats["unreadable_files"] += 1
                stats["skipped_source_names"].append(name)
                continue
'''


def patch(cycle, native, helper):
    if ("github_direct_stream.check_selected_sources(selected)" in cycle
            and "EPG-Scrapers XMLTV: stream the HTTPS GitHub feed" in native):
        return None, None
    if cycle.count(CYCLE_CALL) != 1:
        raise ValueError("EPG cycle direct-import anchor is missing or changed")
    if native.count(NATIVE_START) != 1 or NATIVE_END not in native:
        raise ValueError("EPGImport direct-source implementation has changed")
    first = native.index(NATIVE_START)
    last = native.index(NATIVE_END, first)
    cycle = cycle.replace(CYCLE_CALL,
                          "from . import github_direct_stream\n"
                          "            direct_sync = github_direct_stream.check_selected_sources(selected)", 1)
    native = native[:first] + DIRECT_BLOCK + native[last:]
    # Safety: avoid EPGCache deletion on a failed direct-source download.
    marker = "runner = DirectEPGImportRunner("
    start = cycle.find(marker)
    if start < 0:
        raise ValueError("Native EPGImport runner missing")
    end = cycle.find("sources, stats = runner.prepare()", start)
    if end < 0:
        raise ValueError("Native EPGImport preparation missing")
    segment = cycle[start:end]
    old = "clear_before_import=self.config.get_native_clear_before_import(),"
    if segment.count(old) != 1:
        raise ValueError("Native clear-before-import option has changed")
    cycle = cycle[:start] + segment.replace(old, "clear_before_import=False,", 1) + cycle[end:]
    compile(cycle, "epg_cycle.py", "exec")
    compile(native, "direct_native_import.py", "exec")
    compile(helper, "github_direct_stream.py", "exec")
    return cycle, native


def atomic_write(path, text):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         prefix=".github-direct-", suffix=".tmp",
                                         dir=str(path.parent), delete=False) as f:
            temp = f.name
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, str(path))
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def patch_source_urls(text, filename):
    """Migrate active URL constants while retaining saved source IDs."""
    pattern = re.compile(
        r"(?m)^(BASE|REPORT_CSV|EPG_SCRAPERS_BASE|SCRAPERS_BASE|PREFIX|AUDITS|REPORT_BASE|ROOT)(\s*=\s*)([\"'])"
        + re.escape(OLD_SOURCE_ROOT)
        + r"((?:(?:feeds|reports)(?:/[^\"'\r\n]*)?)?)\3")
    updated = pattern.sub(
        lambda match: match[1] + match[2] + match[3]
        + NEW_SOURCE_ROOT + match[4] + match[3], text)
    if Path(filename).name == "direct_native_import.py":
        for quote in ('"', "'"):
            old_guard = "url.startswith(" + quote + OLD_SOURCE_ROOT + "feeds/" + quote + ")"
            new_guard = "url.startswith(" + quote + NEW_SOURCE_ROOT + "feeds/" + quote + ")"
            updated = updated.replace(old_guard, new_guard)
    compile(updated, filename, "exec")
    return updated


def migrate_source_urls(core, check=False, backup_base=BACKUP_BASE):
    paths = [core / name for name in (
        "external_sources.py", "github_direct_sync.py", "github_direct_stream.py",
        "github_match_health.py", "verified_epg_refresh.py", "direct_native_import.py")]
    if not paths[0].is_file():
        raise ValueError("Installed external source catalogue unavailable")
    before, after = {}, {}
    for path in paths:
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated = patch_source_urls(original, str(path))
        if updated != original:
            before[path], after[path] = original, updated
    if not after:
        print("No legacy active URL constants found.")
        return 0
    for path in after:
        print("SOURCE URL MIGRATION", path)
    if check:
        print("CHECK ONLY")
        return 0
    backup = backup_base / ("source-urls-" + datetime.now().strftime("%Y%m%dT%H%M%S%f"))
    backup.mkdir(parents=True, exist_ok=False)
    for path in after:
        shutil.copy2(str(path), str(backup / path.name))
    try:
        for path, updated in after.items():
            atomic_write(path, updated)
    except Exception:
        for path in after:
            shutil.copy2(str(backup / path.name), str(path))
        raise
    print("SOURCE URL MIGRATION INSTALLED")
    print("BACKUP:", backup)
    print("Restart the Enigma2 GUI once to load the updated Python modules.")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", default=str(PLUGIN))
    parser.add_argument("--check", action="store_true", help="Only test compatibility")
    parser.add_argument("--source-urls-only", action="store_true",
                        help="Migrate the installed direct feed and dashboard URLs")
    args = parser.parse_args()
    core = Path(args.plugin_dir) / "core"
    if args.source_urls_only:
        try:
            return migrate_source_urls(core, check=args.check)
        except Exception as exc:
            print("SOURCE URL MIGRATION FAILED:", exc)
            return 2
    a = core / "epg_cycle.py"
    b = core / "direct_native_import.py"
    c = core / "github_direct_stream.py"
    if not (a.is_file() and b.is_file()):
        print("INCOMPATIBLE: installed EPGManager Python modules unavailable. No changes.")
        return 2
    before_a = a.read_text(encoding="utf-8")
    before_b = b.read_text(encoding="utf-8")
    if ("github_direct_stream.check_selected_sources(selected)" in before_a
            and "EPG-Scrapers XMLTV: stream the HTTPS GitHub feed" in before_b):
        print("ALREADY INSTALLED: native GitHub direct XMLTV mode")
        return 0
    try:
        req = Request(HELPER_URL, headers={"User-Agent": "EPGManager-Direct-Installer/1.0"})
        with urlopen(req, timeout=20) as response:
            helper = response.read(40000).decode("utf-8")
        next_a, next_b = patch(before_a, before_b, helper)
    except Exception as exc:
        print("INCOMPATIBLE: %s. No files changed." % exc)
        return 2
    print("COMPATIBLE: Enigma2 can use GitHub XMLTV without persistent receiver XML.")
    if args.check:
        print("CHECK ONLY: no changes")
        return 0
    backup = BACKUP_BASE / datetime.now().strftime("%Y%m%dT%H%M%S")
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(str(a), str(backup / a.name))
    shutil.copy2(str(b), str(backup / b.name))
    if c.exists():
        shutil.copy2(str(c), str(backup / c.name))
    try:
        atomic_write(a, next_a)
        atomic_write(b, next_b)
        atomic_write(c, helper)
    except Exception as exc:
        shutil.copy2(str(backup / a.name), str(a))
        shutil.copy2(str(backup / b.name), str(b))
        if (backup / c.name).exists():
            shutil.copy2(str(backup / c.name), str(c))
        elif c.exists():
            c.unlink()
        print("FAILED: restored original Python files: %s" % exc)
        return 3
    print("INSTALLED: direct GitHub .xml.gz URL -> Native EPGImport -> Enigma2")
    print("PROTECTED: mappings, user settings, Rytec, XMLs and .lastgood.")
    print("BACKUP:", backup)
    print("RESTART ENIGMA2 GUI once to activate the changed modules.")
    print("Daily automatic import still requires EPGManager's own scheduler enabled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
