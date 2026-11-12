# -*- coding: utf-8 -*-
"""Lazy one-bouquet reader for Smart Mapping.

Smart Mapping is satellite-first and must not deserialize the receiver's complete
IPTV/bouquet service catalogue just to open the screen.  Real bouquet services
are therefore parsed only when that bouquet is actually selected.

This module intentionally does not modify the protected Golden channel_mapper.py.
"""
from __future__ import print_function
import os
from . import channel_mapper


def load_services(bouquet_file, bouquet_label="", parent_bouquet=None,
                  bouquet_dir=None):
    bouquet_dir = bouquet_dir or channel_mapper.BOUQUET_DIR
    filename = str(bouquet_file or "").strip()
    if not filename or filename.startswith("__EPGMANAGER_RECEPTION_"):
        return []
    path = filename if os.path.isabs(filename) else os.path.join(bouquet_dir, filename)
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except Exception:
        return []
    rows=[]
    pending_ref=None
    pending_name=None
    try:
        for raw in fh:
            line=raw.strip()
            if line.startswith("#SERVICE"):
                if "FROM BOUQUET" in line:
                    pending_ref=None; pending_name=None
                    continue
                pending_ref=line[len("#SERVICE"):].strip()
                pending_name=None
                if "http" in pending_ref.lower():
                    try:
                        tail=pending_ref.rsplit(":",1)[-1].strip()
                        if tail and not tail.lower().startswith(("http","https")):
                            pending_name=tail
                    except Exception:
                        pass
            elif line.startswith("#DESCRIPTION") and pending_ref:
                pending_name=line[len("#DESCRIPTION"):].strip()
                if pending_name and channel_mapper.is_tv_service_ref(pending_ref) and channel_mapper.is_probable_tv_name(pending_name):
                    rows.append({
                        "ref": pending_ref,
                        "name": pending_name,
                        "normalized": channel_mapper.normalize_name(pending_name),
                        "bouquet_label": bouquet_label or filename,
                        "bouquet_file": filename,
                        "parent_bouquet": parent_bouquet,
                        "service_type": channel_mapper.classify_service_ref(pending_ref),
                        "dvb": channel_mapper.parse_dvb_ids(pending_ref),
                    })
                pending_ref=None; pending_name=None
        if pending_ref and pending_name and channel_mapper.is_tv_service_ref(pending_ref) and channel_mapper.is_probable_tv_name(pending_name):
            rows.append({
                "ref": pending_ref,
                "name": pending_name,
                "normalized": channel_mapper.normalize_name(pending_name),
                "bouquet_label": bouquet_label or filename,
                "bouquet_file": filename,
                "parent_bouquet": parent_bouquet,
                "service_type": channel_mapper.classify_service_ref(pending_ref),
                "dvb": channel_mapper.parse_dvb_ids(pending_ref),
            })
    finally:
        try: fh.close()
        except Exception: pass
    return rows
