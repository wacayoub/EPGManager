"""SHA-locked offline fix for active import timing and source refresh receipts."""
import ast
import hashlib

ORIGINAL = {
    'plugin.py': 'fe2901bbb3507c484cf22814693aa89161177e17fc9e47f7fb1b19edc34fe1b8',
    'core/scheduler.py': '29ac8ff2e95cd4ec5de34939881ecd789d66552be71c83c717976b11cba37594',
    'core/github_direct_sync.py': '14ae9f7c2486142ba60e13d63daf3f8060b7f7d1f5bd87addc6501e706e6fc6e',
    'ui/settings.py': 'ba2a989b772e768aeef4932bd24abdf966a5286a2edff931d12e36f5bf4474fa',
    'ui/native_sources.py': '138a4c384f7127ef7237f7c8994c1480aab382e3d798739b04f8317fa3506ca1',
}


def one(text, old, new):
    if text.count(old) != 1:
        raise ValueError('sync timing repair source pattern drift')
    return text.replace(old, new, 1)


def corrected(source):
    if set(source) != set(ORIGINAL):
        raise ValueError('sync timing repair inventory drift')
    for path, pin in ORIGINAL.items():
        if hashlib.sha256(source[path]).hexdigest() != pin:
            raise ValueError('sync timing repair preimage drift: ' + path)
    result = {}
    plugin = source['plugin.py'].decode('utf-8')
    plugin = one(plugin, 'def refresh_native_import_scheduler():',
                 'def refresh_native_import_scheduler(startup=False):')
    plugin = one(plugin,
        'NativeImportScheduler(config, _start_scheduled_native_import)',
        'NativeImportScheduler(config, _start_scheduled_native_import, startup=startup)')
    plugin = one(plugin, '            refresh_native_import_scheduler()\n',
                 '            refresh_native_import_scheduler(startup=True)\n')
    plugin = one(plugin,
        '        return getattr(manager, "_cycle_runner", None)',
        '        # Refresh control-plane metadata in the same scheduled cycle.\n'
        '        # This never downloads XML, prepares mappings or blocks eTimer.\n'
        '        try:\n'
        '            from .core import github_direct_sync\n'
        '            github_direct_sync.sync_async(force=False, min_age=0)\n'
        '        except Exception:\n'
        '            _log_exception("Scheduled source metadata refresh failed")\n'
        '        return getattr(manager, "_cycle_runner", None)')
    plugin = plugin.replace('%02d:%02d Casablanca', '%02d:%02d GMT+0')
    result['plugin.py'] = plugin

    scheduler = source['core/scheduler.py'].decode('utf-8')
    head, native = scheduler.split('class NativeImportScheduler(object):', 1)
    native = one(native, 'def __init__(self, config, on_import_due):',
                 'def __init__(self, config, on_import_due, startup=True):')
    native = one(native,
        'self._not_before = time.time() + NATIVE_IMPORT_BOOT_GRACE_SECONDS',
        'self._not_before = time.time() + (NATIVE_IMPORT_BOOT_GRACE_SECONDS if startup else 0)')
    native = one(native,
        '            from .timezone_manager import TimezoneManager\n            now = TimezoneManager().now()',
        '            from .sync_timing import utc_now\n            now = utc_now()')
    native = one(native,
        '        from .timezone_manager import TimezoneManager\n        tzm = TimezoneManager()\n        now = tzm.now()',
        '        from .sync_timing import utc_now\n        now = utc_now()')
    native = one(native, 'return tzm.from_timestamp(last) < slot',
                 'return float(last) < slot_stamp')
    native = native.replace('Casablanca)', 'GMT+0)')
    result['core/scheduler.py'] = head + 'class NativeImportScheduler(object):' + native

    direct = source['core/github_direct_sync.py'].decode('utf-8')
    direct = one(direct, '            _save(payload)\n',
        '            from .sync_timing import preserve_sync_receipts\n'
        '            with _LOCK:\n'
        '                payload["sources"] = preserve_sync_receipts(\n'
        '                    (load() or {}).get("sources"), payload["sources"])\n'
        '                _save(payload)\n')
    result['core/github_direct_sync.py'] = direct

    settings = source['ui/settings.py'].decode('utf-8')
    # The stored ConfigClock and every owner-selected value remain unchanged.
    settings = settings.replace('"EPG Import start time"', '"EPG Import start time (GMT+0)"')
    settings = settings.replace('Daily Casablanca local time for automatic EPG Import All.',
                                'Daily GMT+0 (UTC) time for automatic EPG Import All.')
    result['ui/settings.py'] = settings

    sources = source['ui/native_sources.py'].decode('utf-8')
    start = sources.index('    @staticmethod\n    def _direct_refresh_text(meta):')
    end = sources.index('    def _apply_direct_scraper_status(', start)
    sources = sources[:start] + (
        '    @staticmethod\n    def _direct_refresh_text(meta):\n'
        '        from ..core.sync_timing import source_refresh_text\n'
        '        return source_refresh_text(meta)\n\n') + sources[end:]
    sources = one(sources, 'Label("LAST REFRESH")', 'Label("PUBLISHED / CHECKED GMT+0")')
    sources = one(sources, '  •  Last refresh %s  •  ',
                  '  •  Published / checked GMT+0: %s  •  ')
    result['ui/native_sources.py'] = sources
    for path, text in result.items():
        ast.parse(text, filename=path)
    return {path: text.encode('utf-8') for path, text in result.items()}
