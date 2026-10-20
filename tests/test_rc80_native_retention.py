"""Model partial-source updates and import failures without any Enigma2 writes."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from apply_rc80_private_functional_fixes import preserve_native_events

FIXTURE = '''
class FirstWriteFlushProxy:
    def __init__(self, cache, enabled=True):
        self.cache, self.enabled = cache, enabled
    def importEvents(self, services, events):
        if self.enabled:
            self.cache.flushEPG()
        self.cache.importEvents(services, events)
class Runner:
    clear_before_import = False
    sources = ['partial-live-feed']
    def start(self, cache):
        self.stale_cleanup = service_epg_sanitizer.sanitize_once(cache, self.sources)
        self.cache_proxy = FirstWriteFlushProxy(cache, enabled=not self.clear_before_import)
        return self.cache_proxy
    def fallback(self, cache):
        self.cache_proxy = FirstWriteFlushProxy(cache, enabled=not self.clear_before_import)
        return self.cache_proxy
'''


class Cache:
    def __init__(self, fail=False):
        self.events = {'existing-programme'}
        self.flushes = 0
        self.fail = fail
    def flushEPG(self):
        self.flushes += 1
        self.events.clear()
    def importEvents(self, services, events):
        if self.fail:
            raise RuntimeError('source read/write interrupted')
        self.events.update(events)


class NativeRetentionTests(unittest.TestCase):
    def namespace(self, patched=True):
        def reject_cleanup(*args):
            raise AssertionError('migration must not run during safe import')
        ns = {'service_epg_sanitizer': SimpleNamespace(sanitize_once=reject_cleanup)}
        exec(preserve_native_events(FIXTURE) if patched else FIXTURE, ns)
        return ns
    def test_partial_feed_preserves_existing_in_engine_and_fallback(self):
        for method in ('start', 'fallback'):
            with self.subTest(method=method):
                cache = Cache()
                runner = self.namespace()['Runner']()
                getattr(runner, method)(cache).importEvents(['saved-service'], ['fresh-programme'])
                self.assertEqual(cache.events, {'existing-programme', 'fresh-programme'})
                self.assertEqual(cache.flushes, 0)
    def test_failed_import_preserves_existing_in_both_paths(self):
        for method in ('start', 'fallback'):
            cache = Cache(fail=True)
            runner = self.namespace()['Runner']()
            with self.assertRaises(RuntimeError):
                getattr(runner, method)(cache).importEvents(['saved-service'], ['fresh-programme'])
            self.assertEqual(cache.events, {'existing-programme'})
            self.assertEqual(cache.flushes, 0)
    def test_proxy_default_is_non_destructive(self):
        cache = Cache()
        self.namespace()['FirstWriteFlushProxy'](cache).importEvents(['service'], [])
        self.assertEqual(cache.flushes, 0)
        self.assertEqual(cache.events, {'existing-programme'})
    def test_baseline_fallback_reproduces_loss(self):
        cache = Cache()
        self.namespace(False)['Runner']().fallback(cache).importEvents(['service'], ['fresh-programme'])
        self.assertEqual(cache.events, {'fresh-programme'})
        self.assertEqual(cache.flushes, 1)
    def test_drift_fails_closed(self):
        with self.assertRaises(ValueError):
            preserve_native_events(FIXTURE.replace('enabled=not self.clear_before_import', 'enabled=True', 1))


if __name__ == '__main__':
    unittest.main()
