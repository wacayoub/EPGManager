"""rc85: choose the best of several valid EPG IDs by guide strength (no Enigma2)."""
import ast
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'source/receiver/rc85/EPGManager'
CORE = PLUGIN / 'core'

NOW = 1_800_000_000
STCTV = 'ext_epgscrapers_stctv'
STARZ = 'ext_epgscrapers_starzplay'
RYTEC = 'ext_rytec_news_channels'
AD1 = '1:0:19:5:1FA9:2BE:1040000:0:0:0:'
STEMS = {STCTV: 'stctv', STARZ: 'starzplay'}


def monitor(rows, checked=NOW - 3600):
    """rows: {(stem, id): (future_programmes, future_hours)}"""
    out = {}
    for (stem, cid), (future, hours) in rows.items():
        out.setdefault(stem, []).append({'id': cid, 'future_programmes': future, 'future_hours': hours})
    return {'id_monitor_checked': checked, 'id_monitor': out}


def load_id_quality():
    package = types.ModuleType('rc85_q')
    package.__path__ = [str(CORE)]
    gds = types.ModuleType('rc85_q.github_direct_sync')
    gds.source_stem = lambda sid: STEMS.get(str(sid).lower(), '')
    with patch.dict(sys.modules, {'rc85_q': package, 'rc85_q.github_direct_sync': gds}):
        sys.modules.pop('rc85_q.id_quality', None)
        module = importlib.import_module('rc85_q.id_quality')
    return module, {'rc85_q': package, 'rc85_q.github_direct_sync': gds}


class GuideStrength(unittest.TestCase):
    def setUp(self):
        self.q, self.mods = load_id_quality()

    def strength(self, sid, cid, data):
        with patch.dict(sys.modules, self.mods):
            return self.q.strength(sid, cid, data=data, now=NOW)

    def test_classes_from_the_monitor(self):
        data = monitor({('stctv', 'Strong'): (90, 160.0), ('stctv', 'Good'): (20, 24.0),
                        ('stctv', 'Thin'): (3, 3.8), ('stctv', 'Empty'): (0, 0.0)})
        self.assertEqual(self.strength(STCTV, 'strong', data)[0], self.q.STRONG)
        self.assertEqual(self.strength(STCTV, 'GOOD', data)[0], self.q.GOOD)
        self.assertEqual(self.strength(STCTV, 'thin', data)[0], self.q.THIN)
        self.assertEqual(self.strength(STCTV, 'empty', data)[0], self.q.EMPTY)

    def test_no_data_counts_as_good_not_better_not_worse(self):
        data = monitor({('stctv', 'Strong'): (90, 160.0)})
        self.assertEqual(self.strength(RYTEC, 'bloomberg.nws', data), (self.q.GOOD, None, False))
        self.assertEqual(self.strength(STCTV, 'missing', data), (self.q.GOOD, None, False))

    def test_stale_monitor_is_ignored(self):
        data = monitor({('stctv', 'Thin'): (3, 3.8)}, checked=NOW - 3 * 86400)
        self.assertEqual(self.strength(STCTV, 'thin', data)[2], False)

    def test_labels(self):
        self.assertEqual(self.q.label(self.q.STRONG, 163.3), '7d guide')
        self.assertEqual(self.q.label(self.q.THIN, 3.8), '4h guide')
        self.assertEqual(self.q.label(self.q.EMPTY, 0.0), 'no guide')


def load_importer():
    package = types.ModuleType('rc85_imp')
    package.__path__ = [str(CORE)]
    stub = lambda name, **attrs: types.SimpleNamespace(__name__='rc85_imp.' + name, **attrs)
    rank = {STCTV: (5,), STARZ: (1,), RYTEC: (3,)}   # STC TV normally outranks StarzPlay
    modules = {'rc85_imp': package}
    names = ('channel_mapper channel_registry source_catalog srp_channel_map source_priority '
             'source_readiness source_fast_status source_variant_policy external_sources '
             'source_channel_cache native_importer programme_feed_policy service_epg_sanitizer '
             'precision_match_engine deterministic_matcher openepg_arabic_overlay '
             'epg_priority_guard native_epgimport_bridge logger').split()
    for name in names:
        modules['rc85_imp.' + name] = stub(name)
    modules['rc85_imp.channel_registry'].canonical_service_ref = lambda ref: ref
    modules['rc85_imp.channel_mapper']._token_score = lambda a, b: 0
    modules['rc85_imp.channel_mapper'].EPG_DIR = tempfile.gettempdir()
    modules['rc85_imp.source_catalog'].mapping_source_id = lambda item: item['id']
    modules['rc85_imp.source_priority'].ownership_rank = lambda item, service: rank.get(item.get('id'), (0,))
    modules['rc85_imp.source_channel_cache'].get = lambda sid: []
    bridge = modules['rc85_imp.native_epgimport_bridge']
    for name in ('EPGImportLocalRunner', 'LocalEPGSource', '_source_aliases', 'CountingEPGCacheProxy',
                 '_load_epgimport_class', 'load_rytec_native_source'):
        setattr(bridge, name, type(name, (), {}))
    modules['rc85_imp.logger'].get_logger = lambda name: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, exception=lambda *a, **k: None)
    with patch.dict(sys.modules, modules):
        return importlib.import_module('rc85_imp.direct_native_import'), modules


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def all_readonly(self):
        return self.rows


class BestOwner(unittest.TestCase):
    """AD Sport 1 on 13 Nov 2026: STC TV had 3.8 h, StarzPlay 163 h."""
    items = [{'id': STCTV}, {'id': STARZ}]
    parsed = {STCTV: {'stctv.cac5e490d765': [AD1]}, STARZ: {'abudhabisports1.ae@sd': [AD1]}}

    def owners(self, classes, store=None, dead=()):
        module, modules = load_importer()
        with patch.dict(sys.modules, modules):
            out, _ = module._fast_single_owner_maps(
                self.items, self.parsed, ({}, {}), store=store,
                dead_claim=lambda mid, cid: (mid, cid) in dead,
                strength_claim=lambda mid, cid: classes.get(mid, 1))
        return sorted(mid for mid, chans in out.items() if chans)

    def test_strong_guide_beats_higher_ranked_thin_guide(self):
        self.assertEqual(self.owners({STCTV: 2, STARZ: 0}), [STARZ])

    def test_same_strength_keeps_the_usual_ranking(self):
        self.assertEqual(self.owners({STCTV: 0, STARZ: 0}), [STCTV])
        self.assertEqual(self.owners({STCTV: 1, STARZ: 1}), [STCTV])

    def test_manual_owner_is_never_overridden(self):
        store = FakeStore({STCTV + '::stctv.cac5e490d765': {'mode': 'manual', 'confidence': 100, 'refs': [AD1]}})
        self.assertEqual(self.owners({STCTV: 2, STARZ: 0}, store), [STCTV])

    def test_saved_automatic_owner_competes_on_strength(self):
        store = FakeStore({STCTV + '::stctv.cac5e490d765': {'mode': 'auto', 'confidence': 90, 'refs': [AD1]}})
        self.assertEqual(self.owners({STCTV: 2, STARZ: 0}, store), [STARZ])

    def test_empty_feed_still_loses_first(self):
        self.assertEqual(self.owners({STCTV: 0, STARZ: 0}, dead={(STCTV, 'stctv.cac5e490d765')}), [STARZ])


def screen_method(name):
    tree = ast.parse((PLUGIN / 'ui/channel_mapping.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'ChannelMappingScreen':
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    item.decorator_list = []
                    q, _ = load_id_quality()
                    ns = {'id_quality': q}
                    exec(compile(ast.Module(body=[item], type_ignores=[]), 'channel_mapping', 'exec'), ns)
                    return ns[name]
    raise LookupError(name)


class SmartMappingBestGuide(unittest.TestCase):
    def setUp(self):
        self.best = screen_method('_best_guide_choice')

    def m(self, sid, conf, klass, hours, context=0, auto=True):
        return {'source_id': sid, '_precision_auto': auto, '_precision_confidence': conf,
                '_guide_class': klass, '_guide_hours': hours, '_suggestion_context_rank': context}

    def test_three_correct_ids_pick_the_strongest_guide(self):
        official = self.m('ext_epgscrapers_alkass', 100, 1, 28.3, context=0)
        osn = self.m('ext_epgscrapers_osn', 100, 0, 76.3, context=1)
        stc = self.m('ext_epgscrapers_stctv', 100, 0, 60.0, context=2)
        self.assertIs(self.best([official, osn, stc]), osn)

    def test_better_name_match_wins_over_longer_guide(self):
        exact = self.m('a', 100, 1, 20.0)
        doubtful = self.m('b', 70, 0, 160.0, auto=False)
        self.assertIs(self.best([doubtful, exact]), exact)

    def test_empty_guide_is_never_recommended(self):
        empty = self.m('a', 100, 3, 0.0)
        thin = self.m('b', 100, 2, 3.0, context=3)
        self.assertIs(self.best([empty, thin]), thin)
        self.assertIsNone(self.best([empty]))


class Rc85Package(unittest.TestCase):
    def test_version_strings(self):
        self.assertIn('__version__ = "2026.11.14-rc85"', (PLUGIN / 'version.py').read_text(encoding='utf-8'))
        self.assertIn('Version: 2026.11.14-rc85', (PLUGIN.parent / 'CONTROL/control').read_text(encoding='utf-8'))
        self.assertIn('CHANGELOG-rc85.txt', (PLUGIN / 'PACKAGE_MANIFEST.txt').read_text(encoding='utf-8'))
        self.assertIn('core/id_quality.py', (PLUGIN / 'PACKAGE_MANIFEST.txt').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
