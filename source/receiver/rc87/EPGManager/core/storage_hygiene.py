# -*- coding: utf-8 -*-
"""EPGManager install/update storage hygiene.

Authoritative user configuration is preserved. Rebuildable indexes, caches,
legacy migration markers and stale external XML files are removed during a
plugin upgrade so the receiver does not accumulate old release artifacts.

This module is deliberately independent of Enigma2 UI APIs so postinst can run
it with ordinary Python 3 before the GUI is restarted.
"""
from __future__ import print_function

import glob
import json
import os
import shutil
import time

# User choices / state that must survive upgrades and are eligible for safety backups.
SENSITIVE_RELATIVE = (
    "etc/enigma2/epgmanager.conf",
    "etc/enigma2/epgmanager_mappings.json",
    "etc/enigma2/epgmanager_source_preferences.json",
    "etc/enigma2/epgmanager_mapping_sources.json",
    "etc/enigma2/epgmanager_native_settings.json",
    "etc/enigma2/epgmanager_native_sources.json",
    "etc/enigma2/epgmanager_online_update.json",
    "etc/enigma2/epgmanager_smartmatch_learning.json",
    "etc/epgmanager_epg/custom_dictionary.json",
)

# Receiver-side JSON/indexes that are derived and safe to rebuild after an upgrade.
DERIVED_ENIGMA2_NAMES = (
    "epgmanager_activity.json",
    "epgmanager_auto_repair_state.json",
    "epgmanager_balanced_exact_state.json",
    "epgmanager_channel_registry.json",
    "epgmanager_first_use_remote_mapping.json",
    "epgmanager_id_audit.json",
    "epgmanager_id_harvest_state.json",
    "epgmanager_iptv_registry.json",
    "epgmanager_mapping_view_cache.json",
    "epgmanager_mena_overlay.json",
    "epgmanager_sat_coverage_state.json",
    "epgmanager_smart_name_index.dirty",
    "epgmanager_smart_name_index.json.gz",
    "epgmanager_source_channels.json",  # obsolete monolithic cache; sharded .d survives
    "epgmanager_source_guard.json",
    "epgmanager_source_readiness.json",
    "epgmanager_xmltv_index.json",
)

# RC10: small/bounded performance caches. They are not authoritative user
# settings and are never backup material. Keep them across compatible upgrades
# so a 50+ source receiver does not re-read/re-map the world after each IPK.
# Their own version/signature checks invalidate stale content.
PRESERVED_PERFORMANCE_NAMES = (
    "epgmanager_http_validators.json",
    "epgmanager_sat_registry.json",
    "epgmanager_source_fast_status.json",
    "epgmanager_source_index.json",
    "epgmanager_srp_maps.json",
    "epgmanager_srp_master.json",
)

# Full XMLTV is local only for EPGManager-owned generators. Provider map files are
# mapping metadata, not programme XMLTV, and are retained as last-known-good maps.
EPG_ROOT_FILES = set((
    "custom_dictionary.json",
    "translation_cache_ar_v4.json",
    "unknown_shows.json",
    "dependency_status.json",
    "medi1tv_ar.xml",
    "2M_Chada.xml",
    "snrt.xml",
    "arryadia_snrt.xml",
    "bein.xml",
    "rytec.channels.xml",
    "azman.channels.xml",
    ".beta96_bein_routing_done",
))
EPG_ROOT_DIRS = set(("backups", "epgmanager_channels"))


def _rooted(root, rel):
    return os.path.join(os.path.abspath(root), rel.lstrip("/"))


def _remove(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.unlink(path)
        return True
    except Exception:
        return False


def _safe_source_ids():
    try:
        from . import source_catalog
        return set(str(source_catalog.mapping_source_id(x) or "").lower()
                   for x in source_catalog.all_sources()
                   if source_catalog.mapping_source_id(x))
    except Exception:
        return set()




def _safe_catalogue_ids():
    try:
        from . import source_catalog
        return set(str((x or {}).get("id") or "") for x in source_catalog.all_sources()
                   if str((x or {}).get("id") or ""))
    except Exception:
        return set()


def _clean_source_channel_shards(shard_dir):
    """Keep compact channel caches only for sources still in the catalogue."""
    if not os.path.isdir(shard_dir):
        return 0
    valid = _safe_catalogue_ids()
    if not valid:
        return 0
    removed = 0
    for name in list(os.listdir(shard_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(shard_dir, name)
        sid = ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                row = json.load(fh)
            sid = str((row or {}).get("source_id") or "")
        except Exception:
            sid = ""
        if sid and sid in valid:
            continue
        removed += int(_remove(path))
        _remove(path + ".meta")
    # Remove sidecars whose shard no longer exists.
    for name in list(os.listdir(shard_dir)):
        if name.endswith(".json.meta"):
            base = os.path.join(shard_dir, name[:-5])
            if not os.path.exists(base):
                removed += int(_remove(os.path.join(shard_dir, name)))
    return removed

def _clean_channel_maps(map_dir):
    """Drop channel-map files for source IDs no longer present in the catalogue."""
    if not os.path.isdir(map_dir):
        return 0
    valid = _safe_source_ids()
    if not valid:
        return 0
    removed = 0
    suffixes = (".coverage.channels.xml", ".provider.channels.xml", ".channels.xml")
    for name in list(os.listdir(map_dir)):
        path = os.path.join(map_dir, name)
        if os.path.isdir(path):
            removed += int(_remove(path))
            continue
        low = name.lower()
        source_id = ""
        for suffix in suffixes:
            if low.endswith(suffix):
                source_id = low[:-len(suffix)]
                break
        if not source_id or source_id not in valid:
            removed += int(_remove(path))
    return removed


def rotate_sensitive_backups(root="/", keep=3):
    backup_root = _rooted(root, "etc/epgmanager_epg/backups")
    if not os.path.isdir(backup_root):
        return 0
    rows = []
    for name in os.listdir(backup_root):
        path = os.path.join(backup_root, name)
        if not os.path.isdir(path):
            continue
        try:
            rows.append((os.path.getmtime(path), path))
        except Exception:
            rows.append((0, path))
    rows.sort(reverse=True)
    removed = 0
    for _mtime, path in rows[max(1, int(keep)):]:
        removed += int(_remove(path))
    # latest is a pointer, not a backup. Keep it only when its target survives.
    latest = os.path.join(backup_root, "latest")
    if os.path.isfile(latest):
        try:
            target = open(latest, "r").read().strip()
            target_rooted = target if root == "/" else _rooted(root, target)
            if not target or not os.path.isdir(target_rooted):
                os.unlink(latest)
        except Exception:
            _remove(latest)
    return removed


def cleanup_runtime(root="/", backup_keep=3):
    """Clean only rebuildable/stale EPGManager state.

    Never removes authoritative user mappings, source selections, native settings,
    updater settings, local learning or custom dictionary.
    """
    stats = {"derived": 0, "epg_root": 0, "channel_maps": 0, "source_shards": 0,
             "markers": 0, "backups": 0}
    enigma = _rooted(root, "etc/enigma2")
    for name in DERIVED_ENIGMA2_NAMES:
        stats["derived"] += int(_remove(os.path.join(enigma, name)))
    # Compact per-source Channel-ID shards are programme-free and bounded.
    # Keep them across upgrades; a source refresh overwrites them in place.
    stats["source_shards"] = _clean_source_channel_shards(
        os.path.join(enigma, "epgmanager_source_channels.d"))

    # Historical one-shot beta migration flags are not authoritative state. A
    # clean RC install rebuilds the derived indexes from current mappings/sources.
    for path in glob.glob(os.path.join(enigma, ".epgmanager_beta*_pending")):
        stats["markers"] += int(_remove(path))

    epg_dir = _rooted(root, "etc/epgmanager_epg")
    try:
        if not os.path.isdir(epg_dir):
            os.makedirs(epg_dir)
        for name in list(os.listdir(epg_dir)):
            if name in EPG_ROOT_FILES or name in EPG_ROOT_DIRS:
                continue
            stats["epg_root"] += int(_remove(os.path.join(epg_dir, name)))
    except Exception:
        pass
    stats["channel_maps"] = _clean_channel_maps(os.path.join(epg_dir, "epgmanager_channels"))
    stats["backups"] = rotate_sensitive_backups(root=root, keep=backup_keep)
    return stats


def write_report(stats, path):
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "timestamp": int(time.time()), "cleanup": stats}, fh,
                      separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="/")
    p.add_argument("--report", default="/tmp/epgmanager-storage-cleanup.json")
    p.add_argument("--backup-keep", type=int, default=3)
    args = p.parse_args()
    stats = cleanup_runtime(root=args.root, backup_keep=args.backup_keep)
    report = args.report
    if args.root != "/" and report.startswith("/"):
        report = _rooted(args.root, report)
    write_report(stats, report)
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
