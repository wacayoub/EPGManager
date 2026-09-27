# -*- coding: utf-8 -*-
"""Small persistent caches for large Enigma2 bouquet/XMLTV installations.

The cache is deliberately JSON-only and dependency free. It invalidates itself
when file size/mtime signatures change, so a 10k+ service receiver does not need
to rescan unchanged bouquet files every time Smart Mapping opens.
"""
import json
import os


def file_signature(paths):
    rows = []
    for path in sorted(set(paths or [])):
        try:
            st = os.stat(path)
            rows.append([path, int(st.st_mtime), int(st.st_size)])
        except OSError:
            rows.append([path, 0, 0])
    return rows


def load_cache(path, signature):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if data.get('signature') == signature and isinstance(data.get('payload'), list):
            return data.get('payload')
    except Exception:
        pass
    return None


def save_cache(path, signature, payload):
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({'signature': signature, 'payload': payload}, fh, separators=(',', ':'))
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def load_cache_fast(path, max_age=86400):
    """Load a cache without recomputing expensive source signatures.

    Smart Mapping uses this for instant startup on receivers with thousands of
    services. The cache is trusted for ``max_age`` seconds. A later explicit
    refresh can rebuild it.
    """
    try:
        st = os.stat(path)
        import time
        if max_age is not None and max_age >= 0 and (time.time() - st.st_mtime) > max_age:
            return None
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        payload = data.get('payload')
        return payload if isinstance(payload, list) else None
    except Exception:
        return None
