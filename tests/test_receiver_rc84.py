"""rc84 Smart Mapping speed changes, exercised without Enigma2.

The changed functions are pulled out of the plugin source with ast and run
against small fakes, so the tests need neither Enigma2 nor the plugin package.
"""
import ast
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'source/receiver/rc84/EPGManager'

REFS = [
    '1:0:19:2778:3F2:E0:1040000:0:0:0:',
    '1:0:19:2778:3f2:e0:1040000:0:0:0',
    ' 1:0:1:C9:2:800:DCA0000:0:0:0::BLOOMBERG TV ',
    '4097:0:1:0:0:0:0:0:0:0:http%3a//example.invalid/1:Name',
    '1:0:1:2',
    '',
]


def functions(path, names, namespace):
    """Exec the named top-level functions (and simple assignments) of a module."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    body = [node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in names)
            or (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets))]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def method(path, cls, name):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    ns = {}
                    exec(compile(ast.Module(body=[item], type_ignores=[]), str(path), 'exec'), ns)
                    return ns[name]
    raise LookupError(name)


class CanonicalRefCache(unittest.TestCase):
    def setUp(self):
        mapper = functions(PLUGIN / 'core/channel_mapper.py', {'classify_service_ref'}, {})
        self.ns = functions(PLUGIN / 'core/channel_registry.py',
                            {'canonical_service_ref', '_canonical_service_ref_uncached',
                             '_CANON_CACHE', '_CANON_CACHE_MAX'},
                            {'channel_mapper': types.SimpleNamespace(**mapper)})

    def test_cached_result_equals_uncached(self):
        for ref in REFS + [None]:
            expected = self.ns['_canonical_service_ref_uncached'](ref)
            self.assertEqual(self.ns['canonical_service_ref'](ref), expected)
            self.assertEqual(self.ns['canonical_service_ref'](ref), expected)  # from cache
        self.assertEqual(self.ns['canonical_service_ref'](REFS[0]), '1:0:19:2778:3F2:E0:1040000:0:0:0')

    def test_cache_is_bounded(self):
        self.ns['_CANON_CACHE'].clear()
        self.ns['_CANON_CACHE_MAX'] = 3
        functions_ns = self.ns
        for sid in range(10):
            functions_ns['canonical_service_ref']('1:0:1:%X:1:1:C00000:0:0:0:' % sid)
        self.assertLessEqual(len(self.ns['_CANON_CACHE']), 3)


class ConflictCheck(unittest.TestCase):
    """rc84 _service_has_conflict must equal rc83's sort-then-count version."""

    def setUp(self):
        self.new = method(PLUGIN / 'ui/channel_mapping.py', 'ChannelMappingScreen', '_service_has_conflict')
        self.lookup = method(PLUGIN / 'ui/channel_mapping.py', 'ChannelMappingScreen', '_ref_lookup_keys')

    def screen(self, info_by_ref):
        mapper = functions(PLUGIN / 'core/channel_mapper.py', {'classify_service_ref'}, {})
        reg = functions(PLUGIN / 'core/channel_registry.py',
                        {'canonical_service_ref', '_canonical_service_ref_uncached',
                         '_CANON_CACHE', '_CANON_CACHE_MAX'},
                        {'channel_mapper': types.SimpleNamespace(**mapper)})
        self.lookup.__globals__['channel_registry'] = types.SimpleNamespace(**reg)
        s = types.SimpleNamespace(_mapped_info_by_ref=info_by_ref, _ref_keys_cache={})
        s._ref_lookup_keys = lambda ref: self.lookup(s, ref)
        return s

    @staticmethod
    def old(screen, ref):
        rows, seen = [], set()
        for key in screen._ref_lookup_keys(ref):
            for info in screen._mapped_info_by_ref.get(key) or []:
                sig = (str(info.get('source_id') or ''), str(info.get('channel_id') or ''),
                       str(info.get('mode') or ''))
                if sig not in seen:
                    seen.add(sig)
                    rows.append(info)
        return len([x for x in rows if str((x or {}).get('mode') or '').lower() == 'manual']) > 1

    def test_same_answer_as_rc83(self):
        ref = '1:0:19:9D72:FBE:E0:1040000:0:0:0:'
        canon = '1:0:19:9D72:FBE:E0:1040000:0:0:0'
        m = lambda sid, cid, mode='manual': {'source_id': sid, 'channel_id': cid, 'mode': mode}
        cases = [
            {},
            {ref: [m('a', 'x')]},
            {ref: [m('a', 'x')], canon: [m('a', 'x')]},                  # same owner twice
            {ref: [m('a', 'x')], canon: [m('b', 'y')]},                  # two manual owners
            {ref: [m('a', 'x'), m('b', 'y', 'auto-repair')]},           # one manual + auto
            {ref: [m('a', 'x', 'Manual'), m('a', 'x', 'manual')]},      # mode spelling differs
        ]
        for info in cases:
            screen = self.screen(info)
            self.assertEqual(self.new(screen, ref), self.old(screen, ref), info)


class ScaleCache(unittest.TestCase):
    def test_desktop_size_is_asked_once_per_window(self):
        ns = functions(PLUGIN / 'ui/compat.py', {'_scales', '_SCALES_CACHE', 'BASE_W', 'BASE_H'},
                       {'time': types.SimpleNamespace(time=lambda: 100.0)})
        calls = []
        ns['desktop_size'] = lambda: calls.append(1) or (1280, 720)
        for _ in range(50):
            self.assertEqual(ns['_scales'](), (1280 / 1920, 720 / 1080))
        self.assertEqual(len(calls), 1)


class Rc84Package(unittest.TestCase):
    def test_version_strings(self):
        version = (PLUGIN / 'version.py').read_text(encoding='utf-8')
        control = (PLUGIN.parent / 'CONTROL/control').read_text(encoding='utf-8')
        self.assertIn('__version__ = "2026.11.13-rc84"', version)
        self.assertIn('Version: 2026.11.13-rc84', control)
        self.assertIn('CHANGELOG-rc84.txt', (PLUGIN / 'PACKAGE_MANIFEST.txt').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
