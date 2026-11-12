"""rc82 receiver fixes, exercised without Enigma2."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'source/receiver/rc82/EPGManager/core'

AJ_ENGLISH = '1:0:19:2778:3F2:E0:1040000:0:0:0:'
AJ_ARABIC = '1:0:19:2775:3F2:E0:1040000:0:0:0:'
IPTV = '4097:0:1:0:0:0:0:0:0:0:http%3a//example/stream'


class FakeDB:
    def __init__(self):
        self.flags = {}
        self.saves = 0

    def addFlag(self, ref, flag):
        self.flags[ref] = self.flags.get(ref, 0) | flag

    def removeFlag(self, ref, flag):
        self.flags[ref] = self.flags.get(ref, 0) & ~flag

    def saveServicelist(self):
        self.saves += 1


def load_core(module, db=None):
    package = types.ModuleType('rc82_core')
    package.__path__ = [str(CORE)]
    logger = types.ModuleType('rc82_core.logger')
    logger.get_logger = lambda name: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, exception=lambda *a, **k: None)
    mapper = types.ModuleType('rc82_core.channel_mapper')
    mapper.EPG_DIR = tempfile.gettempdir()
    mapper.classify_service_ref = lambda ref: 'IPTV' if ref.startswith('4097:') else 'DVB'

    def parse_dvb_ids(ref):
        p = ref.split(':')
        return {'sid': int(p[3], 16), 'tsid': int(p[4], 16), 'onid': int(p[5], 16)}
    mapper.parse_dvb_ids = parse_dvb_ids
    utils = types.ModuleType('rc82_core.utils')
    utils.indent_xml = lambda root: None
    utils.atomic_write = lambda path, text: Path(path).write_text(text, encoding='utf-8')
    enigma = types.ModuleType('enigma')
    enigma.eDVBDB = types.SimpleNamespace(getInstance=lambda: db)
    enigma.eServiceReference = str
    modules = {'rc82_core': package, 'rc82_core.logger': logger,
               'rc82_core.channel_mapper': mapper, 'rc82_core.utils': utils, 'enigma': enigma}
    with patch.dict(sys.modules, modules):
        sys.modules.pop('rc82_core.' + module, None)
        return importlib.import_module('rc82_core.' + module), modules


class PriorityGuardExpiry(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.guard, self.modules = load_core('epg_priority_guard', self.db)
        self.folder = tempfile.TemporaryDirectory()
        self.state = str(Path(self.folder.name) / 'priority.json')

    def tearDown(self):
        self.folder.cleanup()

    def protect(self, refs, now):
        with patch.dict(sys.modules, self.modules), patch.object(self.guard.time, 'time', return_value=now):
            return self.guard.protect_imported_services(refs, state_path=self.state)

    def journal(self):
        return json.loads(Path(self.state).read_text(encoding='utf-8'))

    def test_service_not_written_for_48h_gets_sat_eit_back(self):
        day = 24 * 3600
        self.protect([AJ_ENGLISH, AJ_ARABIC, IPTV], 1000)
        self.assertEqual(self.db.flags[AJ_ENGLISH], 0x1000)
        self.assertNotIn(IPTV, self.db.flags)
        self.protect([AJ_ARABIC], 1000 + day)  # feed stopped covering AJ English
        self.assertEqual(self.db.flags[AJ_ENGLISH], 0x1000, 'released too early')
        result = self.protect([AJ_ARABIC], 1000 + 2 * day + 1)
        self.assertEqual(result['stale_released'], 1)
        self.assertEqual(self.db.flags[AJ_ENGLISH], 0)
        self.assertEqual(self.db.flags[AJ_ARABIC], 0x1000)
        state = self.journal()
        self.assertEqual(state['schema'], 2)
        self.assertEqual(state['protected_refs'], [AJ_ARABIC])
        self.assertEqual(set(state['last_written']), {AJ_ARABIC})

    def test_rc81_journal_upgrade_releases_nothing_at_once(self):
        Path(self.state).write_text(json.dumps({'schema': 1, 'flag': 4096, 'updated_at': 1,
                                                'protected_refs': [AJ_ENGLISH]}), encoding='utf-8')
        result = self.protect([AJ_ARABIC], 10 ** 9)
        self.assertNotIn('stale_released', result)
        self.assertEqual(self.journal()['last_written'][AJ_ENGLISH], 10 ** 9)

    def test_import_without_dvb_services_releases_nothing(self):
        self.protect([AJ_ENGLISH], 1000)
        result = self.protect([IPTV], 1000 + 10 * 24 * 3600)
        self.assertNotIn('stale_released', result)
        self.assertEqual(self.db.flags[AJ_ENGLISH], 0x1000)
        self.assertEqual(self.journal()['protected_refs'], [AJ_ENGLISH])


class SourcesExport(unittest.TestCase):
    def test_volatile_paths_are_never_exported(self):
        export, modules = load_core('epgimport_export')
        # Receiver paths; existence is mocked (a real temp dir is under /tmp on Linux).
        xml = '/etc/epgmanager_epg/feed.xml'
        chans = '/etc/epgmanager_epg/epgmanager_channels/feed.channels.xml'
        rows = [{'source_id': 'good', 'xml_path': xml, 'map_path': chans},
                {'source_id': 'tmp', 'xml_path': '/tmp/x.xml', 'map_path': chans},
                {'source_id': 'volatile', 'xml_path': xml, 'map_path': '/var/volatile/x.xml'},
                {'source_id': 'run', 'xml_path': '/run/x/../x.xml', 'map_path': chans}]
        with patch.dict(sys.modules, modules), patch.object(export.os.path, 'exists', return_value=True):
            text, count = export.build_direct_sources(rows)
        self.assertEqual(count, 1)
        self.assertIn('EPG Manager - good', text)
        self.assertNotIn('/tmp/', text)


class PersistentLog(unittest.TestCase):
    def test_mounted_hdd_comes_first_otherwise_ram_log(self):
        logger = importlib.import_module('importlib').util
        spec = logger.spec_from_file_location('rc82_logger', CORE / 'logger.py')
        module = logger.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module.os.path, 'ismount', return_value=True):
            self.assertEqual(module._log_candidates()[0], '/media/hdd/epgmanager/logs')
        with patch.object(module.os.path, 'ismount', return_value=False):
            self.assertEqual(module._log_candidates()[0], '/var/log')


THMANYAH = '1:0:19:1:2:3:4:0:0:0:'
SPORT24 = 'ext_epgscrapers_sport24'
STCTV = 'ext_epgscrapers_stctv'


def load_importer():
    package = types.ModuleType('rc82_imp')
    package.__path__ = [str(CORE)]
    stub = lambda name, **attrs: types.SimpleNamespace(__name__='rc82_imp.' + name, **attrs)
    rank = {SPORT24: (5,), STCTV: (1,)}  # sport24 normally outranks stctv
    modules = {'rc82_imp': package}
    names = ('channel_mapper channel_registry source_catalog srp_channel_map source_priority '
             'source_readiness source_fast_status source_variant_policy external_sources '
             'source_channel_cache native_importer programme_feed_policy service_epg_sanitizer '
             'precision_match_engine deterministic_matcher openepg_arabic_overlay '
             'epg_priority_guard native_epgimport_bridge logger').split()
    for name in names:
        modules['rc82_imp.' + name] = stub(name)
    modules['rc82_imp.channel_registry'].canonical_service_ref = lambda ref: ref
    modules['rc82_imp.channel_mapper']._token_score = lambda a, b: 0
    modules['rc82_imp.channel_mapper'].EPG_DIR = tempfile.gettempdir()
    modules['rc82_imp.source_catalog'].mapping_source_id = lambda item: item['id']
    modules['rc82_imp.source_priority'].ownership_rank = lambda item, service: rank.get(item.get('id'), (0,))
    modules['rc82_imp.source_channel_cache'].get = lambda sid: []
    bridge = modules['rc82_imp.native_epgimport_bridge']
    for name in ('EPGImportLocalRunner', 'LocalEPGSource', '_source_aliases', 'CountingEPGCacheProxy',
                 '_load_epgimport_class', 'load_rytec_native_source'):
        setattr(bridge, name, type(name, (), {}))
    modules['rc82_imp.logger'].get_logger = lambda name: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, exception=lambda *a, **k: None)
    with patch.dict(sys.modules, modules):
        return importlib.import_module('rc82_imp.direct_native_import'), modules


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def all_readonly(self):
        return self.rows


class EmptyFeedOwnership(unittest.TestCase):
    items = [{'id': SPORT24}, {'id': STCTV}]
    parsed = {SPORT24: {'sport24.thmanyah.1': [THMANYAH]}, STCTV: {'sport24.thmanyah.1': [THMANYAH]}}

    def owners(self, dead, store=None):
        module, modules = load_importer()
        with patch.dict(sys.modules, modules):
            out, _stats = module._fast_single_owner_maps(
                self.items, self.parsed, ({}, {}), store=store,
                dead_claim=lambda mid, cid: (mid, cid) in dead)
        return sorted(out)

    def test_empty_feed_loses_to_feed_with_programmes(self):
        self.assertEqual(self.owners(set()), [SPORT24])  # unchanged when both are live
        self.assertEqual(self.owners({(SPORT24, 'sport24.thmanyah.1')}), [STCTV])

    def test_saved_automatic_owner_without_programmes_is_replaced(self):
        store = FakeStore({SPORT24 + '::sport24.thmanyah.1': {'mode': 'auto', 'confidence': 90, 'refs': [THMANYAH]}})
        self.assertEqual(self.owners({(SPORT24, 'sport24.thmanyah.1')}, store), [STCTV])

    def test_manual_owner_is_never_overridden(self):
        store = FakeStore({SPORT24 + '::sport24.thmanyah.1': {'mode': 'manual', 'confidence': 100, 'refs': [THMANYAH]}})
        self.assertEqual(self.owners({(SPORT24, 'sport24.thmanyah.1')}, store), [SPORT24])

    def test_all_empty_keeps_previous_choice(self):
        dead = {(SPORT24, 'sport24.thmanyah.1'), (STCTV, 'sport24.thmanyah.1')}
        self.assertEqual(self.owners(dead), [SPORT24])


class Rc82Package(unittest.TestCase):
    def test_python_compiles_and_versions_agree(self):
        base = ROOT / 'source/receiver/rc82'
        for path in sorted((base / 'EPGManager').rglob('*.py')):
            with self.subTest(path=path.name):
                compile(path.read_bytes(), str(path), 'exec')
        self.assertIn('"2026.11.12-rc82"', (base / 'EPGManager/version.py').read_text(encoding='utf-8'))
        self.assertIn('Version: 2026.11.12-rc82', (base / 'CONTROL/control').read_text(encoding='utf-8'))
        listed = set((base / 'EPGManager/PACKAGE_MANIFEST.txt').read_text(encoding='utf-8').split())
        self.assertIn('CHANGELOG-rc82.txt', listed)


if __name__ == '__main__':
    unittest.main()
