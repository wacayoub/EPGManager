# -*- coding: utf-8 -*-
"""Background freshness manager for Smart Mapping source-channel catalogues.

rc73 goals:
- Smart Mapping always consumes the newest compact cache first.
- Opening Smart Mapping never forces a source refresh.
- Only missing/stale selected catalogues are refreshed in a daemon thread.
- A fresh cache is reused directly and re-published into process RAM.
- The UI thread never performs network or large JSON work.
"""
from __future__ import print_function

import threading
import time

from .logger import get_logger
from . import source_catalog, source_channel_cache, remote_channel_catalog, smart_catalog_boot, github_direct_sync
from .source_preferences import SourcePreferences

log = get_logger(__name__)

_LOCK = threading.RLock()
_BUSY = False
_LAST_START = 0.0
_LAST_FINISH = 0.0
_LAST_RESULT = {}
_ONE_BUSY = set()

# Channel-ID catalogues change far less often than programme EPG. One day keeps
# startup/network work tiny while still discovering newly-added IDs automatically.
DEFAULT_MAX_AGE = 24 * 3600
MIN_RECHECK = 30 * 60


def _mapping_items():
    try:
        selected = set(str(x) for x in (SourcePreferences().selected_mapping_ids() or []) if x)
    except Exception:
        selected = set()
    # Empty preferences must never fan out into every EPGShare/OpenEPG/Rytec
    # feed. Fall back to the compact 14 direct MENA feeds only.
    if not selected:
        try:
            selected = set(str(x) for x in (github_direct_sync.DIRECT_IDS or []) if x)
        except Exception:
            selected = set()
    out = []
    try:
        items = list(source_catalog.all_sources() or [])
    except Exception:
        items = []
    for item in items:
        sid = str(source_catalog.mapping_source_id(item) or '')
        if not sid or sid not in selected:
            continue
        row = dict(item or {})
        row['source_id'] = sid
        row.setdefault('source_name', row.get('name') or sid)
        out.append(row)
    return out


def _cache_info(item):
    sid = str((item or {}).get('source_id') or '')
    if not sid:
        return {}, 10**9
    try:
        info = dict(source_channel_cache.info_fast(sid) or {})
    except Exception:
        info = {}
    updated = int(info.get('updated_at') or 0)
    age = max(0, int(time.time()) - updated) if updated else 10**9
    return info, age


def _publish_cache(item, rows, method='cache-current'):
    sid = str((item or {}).get('source_id') or '')
    if not sid or not rows:
        return
    try:
        smart_catalog_boot.update_source(sid, item.get('source_name') or item.get('name') or sid,
                                         rows, method=method)
    except Exception:
        log.exception('Could not publish Smart Mapping cache for %s', sid)


def _refresh_item(item, force=False, max_age=DEFAULT_MAX_AGE, publish=False):
    sid = str((item or {}).get('source_id') or '')
    name = str((item or {}).get('source_name') or item.get('name') or sid)
    info, age = _cache_info(item)
    cached_count = int(info.get('channels') or 0)
    if cached_count and not force and age <= int(max_age or DEFAULT_MAX_AGE):
        # Normal startup does not decode the shard here. smart_catalog_boot owns
        # the one background cache load. Detect only the case where another
        # screen refreshed the shard *after* the resident boot catalogue loaded.
        cache_updated = int(info.get('updated_at') or 0)
        try:
            boot_meta = smart_catalog_boot.source_status(sid) or {}
            boot_updated = int(boot_meta.get('updated_at') or 0)
            boot_busy = bool((smart_catalog_boot.status() or {}).get('busy'))
            boot_has = bool(smart_catalog_boot.has(sid))
        except Exception:
            boot_updated = 0; boot_busy = False; boot_has = False
        needs_ram_reload = (not boot_busy) and ((not boot_has) or (cache_updated and cache_updated > boot_updated))
        if publish and needs_ram_reload:
            try:
                rows = list(source_channel_cache.get(sid) or [])
            except Exception:
                rows = []
            if rows:
                _publish_cache(item, rows, method='cache-current')
                needs_ram_reload = False
        return {'source_id': sid, 'name': name, 'state': 'cache', 'rows': cached_count, 'age': age,
                'changed': False, 'needs_ram_reload': needs_ram_reload}

    if str((item or {}).get('kind') or '') == 'local':
        try:
            fresh = list(source_catalog.local_channel_hints(sid) or [])
        except Exception:
            fresh = []
        if fresh:
            try:
                source_channel_cache.put(sid, name, fresh,
                    provider=item.get('provider') or '', region=item.get('region') or '',
                    language=item.get('language') or '', method='auto-local-cache')
            except Exception:
                pass
            if publish:
                _publish_cache(item, fresh, method='auto-local-cache')
            return {'source_id': sid, 'name': name, 'state': 'local', 'rows': len(fresh), 'age': 0, 'changed': True}
        return {'source_id': sid, 'name': name, 'state': 'empty', 'rows': 0, 'age': age, 'changed': False}

    try:
        fresh, meta = remote_channel_catalog.fetch(item, timeout=6, max_scan=8 * 1024 * 1024)
        fresh = list(fresh or [])
        if not fresh:
            return {'source_id': sid, 'name': name, 'state': 'kept-cache' if cached_count else 'empty',
                    'rows': cached_count, 'age': age, 'changed': False}
        source_channel_cache.put(sid, name, fresh,
            provider=item.get('provider') or '', region=item.get('region') or '',
            language=item.get('language') or '', method='auto-stale-refresh')
        if publish:
            _publish_cache(item, fresh, method='auto-stale-refresh')
        return {'source_id': sid, 'name': name, 'state': 'refreshed', 'rows': len(fresh), 'age': 0, 'changed': True}
    except Exception as exc:
        log.warning('Automatic Smart Mapping cache refresh failed for %s: %s', sid, exc)
        return {'source_id': sid, 'name': name, 'state': 'kept-cache' if cached_count else 'error',
                'rows': cached_count, 'age': age, 'error': str(exc), 'changed': False}


def ensure_async(force=False, max_age=DEFAULT_MAX_AGE):
    """Refresh stale/missing selected sources once, in background only."""
    global _BUSY, _LAST_START, _LAST_FINISH, _LAST_RESULT
    with _LOCK:
        now = time.time()
        if _BUSY:
            return False
        if not force and _LAST_START and (now - _LAST_START) < MIN_RECHECK:
            return True
        _BUSY = True
        _LAST_START = now

    def worker():
        global _BUSY, _LAST_FINISH, _LAST_RESULT
        result = {'checked': 0, 'cache': 0, 'refreshed': 0, 'errors': 0, 'rows': 0, 'started_at': int(time.time())}
        changed = False
        try:
            for item in _mapping_items():
                row = _refresh_item(item, force=force, max_age=max_age, publish=False)
                changed = bool(row.get('changed') or row.get('needs_ram_reload')) or changed
                result['checked'] += 1
                result['rows'] += int(row.get('rows') or 0)
                state = str(row.get('state') or '')
                if state in ('cache', 'local'):
                    result['cache'] += 1
                elif state == 'refreshed':
                    result['refreshed'] += 1
                elif state == 'error':
                    result['errors'] += 1
                time.sleep(0.03)
            if changed:
                # Exactly one RAM/index reload after all stale sources are written.
                smart_catalog_boot.ensure_async(force=True)
        except Exception as exc:
            result['errors'] += 1
            result['error'] = str(exc)
            log.exception('Automatic Smart Mapping cache refresh failed')
        finally:
            result['finished_at'] = int(time.time())
            with _LOCK:
                _LAST_RESULT = dict(result)
                _LAST_FINISH = time.time()
                _BUSY = False

    threading.Thread(target=worker, name='EPGM-SmartCacheRefresh', daemon=True).start()
    return True


def refresh_source_async(source_id, force=False, max_age=DEFAULT_MAX_AGE):
    """Ensure one source is current when selected from BLUE -> More.

    This is still automatic: a fresh cache causes no HTTP request; only a stale
    or missing cache is fetched. The caller never waits for the worker.
    """
    sid = str(source_id or '')
    if not sid:
        return False
    with _LOCK:
        if sid in _ONE_BUSY:
            return False
        _ONE_BUSY.add(sid)

    def worker():
        try:
            item = None
            for row in _mapping_items():
                if str(row.get('source_id') or '') == sid:
                    item = row; break
            if item:
                _refresh_item(item, force=force, max_age=max_age, publish=True)
        except Exception:
            log.exception('Automatic one-source cache refresh failed for %s', sid)
        finally:
            with _LOCK:
                _ONE_BUSY.discard(sid)

    threading.Thread(target=worker, name='EPGM-SmartCache-%s' % sid[:24], daemon=True).start()
    return True


def status():
    with _LOCK:
        out = dict(_LAST_RESULT or {})
        out['busy'] = bool(_BUSY)
        out['last_start'] = int(_LAST_START or 0)
        out['last_finish'] = int(_LAST_FINISH or 0)
        return out
