# -*- coding: utf-8 -*-
"""Fast diagnostics summary. No network access and no XML tree parsing."""
from __future__ import print_function

import hashlib
import os
import shutil

from . import activity_store, source_guard

GOLDEN = {
    "core/channel_mapper.py": "e8fdbc1928fcc38994a12a67205615907277d7023d675bffc64979eb83c5b3b9",
    "core/native_epgimport_bridge.py": "8dbe9a36f15565c916c1f3b904714da5c2cdc16ca5af586315fae721a5f80681",
    "core/srp_channel_map.py": "833d042cdd6c379f7fe5ba41f505c0cd8d689b7d55ee0feafb8cff436009fcdc",
}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(256 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def summary(plugin_dir, config=None):
    lines = ["QUICK DIAGNOSTICS"]
    good = 0
    for rel, expected in GOLDEN.items():
        path = os.path.join(plugin_dir, rel)
        try:
            current = _sha256(path)
            ok = current == expected
        except Exception:
            current = "missing"
            ok = False
        good += 1 if ok else 0
        lines.append("Golden %-27s %s" % (os.path.basename(rel), "OK" if ok else "MISMATCH"))

    epg_dir = "/etc/epgmanager_epg"
    try:
        if config is not None and hasattr(config, "get_epg_output_dir"):
            epg_dir = config.get_epg_output_dir()
    except Exception:
        pass
    try:
        usage = shutil.disk_usage(epg_dir if os.path.isdir(epg_dir) else "/etc")
        lines.append("EPG storage free              %.1f MB" % (float(usage.free) / 1048576.0))
    except Exception:
        lines.append("EPG storage free              unknown")

    state = source_guard.load_state().get("sources", {})
    restored = sum(1 for row in state.values() if row.get("restored"))
    failed = sum(1 for row in state.values() if not row.get("accepted"))
    unchanged = sum(1 for row in state.values() if row.get("unchanged"))
    lines.append("Safe-source guard             %d tracked • %d restored • %d failed • %d unchanged" % (
        len(state), restored, failed, unchanged))

    activity = activity_store.load()
    cycle = activity.get("cycle") or {}
    imp = activity.get("last_import") or {}
    lines.append("Last cycle                    %s • %.1fs" % (
        "OK" if cycle.get("ok", True) else "FAILED", float(cycle.get("elapsed") or 0.0)))
    lines.append("Last native import            %d events" % int(imp.get("imported_events") or 0))

    if config is not None:
        try:
            lines.append("Performance mode              %s" % str(config.get_performance_mode()).upper())
            lines.append("Safe EPG replacement          %s" % ("ON" if config.get_safe_source_replacement() else "OFF"))
        except Exception:
            pass
    lines.append("Overall                       %s" % ("GOOD" if good == len(GOLDEN) and failed == 0 else "CHECK"))
    return "\n".join(lines)
