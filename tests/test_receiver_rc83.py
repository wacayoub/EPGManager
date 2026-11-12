"""rc83 receiver fixes, exercised without Enigma2."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'source/receiver/rc83/EPGManager/core'

AD_SPORT = '1:0:19:5:1FA9:2BE:1040000:0:0:0:'
AJ_ENGLISH = '1:0:19:2778:3F2:E0:1040000:0:0:0:'
NOW = 1_800_000_000


class FakeDB:
    def __init__(self):
        self.flags = {}

    def addFlag(self, ref, flag):
        self.flags[ref] = self.flags.get(ref, 0) | flag

    def removeFlag(self, ref, flag):
        self.flags[ref] = self.flags.get(ref, 0) & ~flag

    def saveServicelist(self):
        pass


def load_core(module, db=None):
    package = types.ModuleType('rc83_core')
    package.__path__ = [str(CORE)]
    logger = types.ModuleType('rc83_core.logger')
    logger.get_logger = lambda name: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, exception=lambda *a, **k: None)
    mapper = types.ModuleType('rc83_core.channel_mapper')
    mapper.EPG_DIR = tempfile.gettempdir()
    mapper.classify_service_ref = lambda ref: 'IPTV' if ref.startswith('4097:') else 'DVB'

    def parse_dvb_ids(ref):
        p = ref.split(':')
        return {'sid': int(p[3], 16), 'tsid': int(p[4], 16), 'onid': int(p[5], 16)}
    mapper.parse_dvb_ids = parse_dvb_ids
    store = types.ModuleType('rc83_core.mapping_store')
    store.MappingStore = object
    srp = types.ModuleType('rc83_core.srp_channel_map')
    activity = types.ModuleType('rc83_core.activity_store')
    enigma = types.ModuleType('enigma')
    enigma.eDVBDB = types.SimpleNamespace(getInstance=lambda: db)
    enigma.eServiceReference = str
    modules = {'rc83_core': package, 'rc83_core.logger': logger, 'rc83_core.channel_mapper': mapper,
               'rc83_core.mapping_store': store, 'rc83_core.srp_channel_map': srp,
               'rc83_core.activity_store': activity, 'enigma': enigma}
    with patch.dict(sys.modules, modules):
        sys.modules.pop('rc83_core.' + module, None)
        return importlib.import_module('rc83_core.' + module), modules


class FakeCache:
    def importEvents(self, services, events):
        return None

    def importEvent(self, service, events):
        return None


class StaleFeedProxy(unittest.TestCase):
    def setUp(self):
        bridge, _ = load_core('native_epgimport_bridge')
        self.proxy = bridge.CountingEPGCacheProxy(FakeCache(), now=NOW)

    def test_only_future_programmes_mark_a_service_as_fed(self):
        # EPGImport hands over one (start, duration, title, ...) event per call.
        self.proxy.importEvents(AD_SPORT, ((NOW - 7200, 3600, 'old match', '', '', 0),))
        self.proxy.importEvents(AJ_ENGLISH, ((NOW - 600, 3600, 'news', '', '', 0),))
        self.assertEqual(self.proxy.mapped_services, {AJ_ENGLISH})
        self.assertEqual(self.proxy.past_only_services, {AD_SPORT})
        self.assertEqual(self.proxy.success_events, 2)

    def test_a_later_future_programme_clears_past_only(self):
        self.proxy.importEvent(AD_SPORT, ((NOW - 7200, 3600, 'old', '', '', 0),))
        self.proxy.importEvent(AD_SPORT, ((NOW + 60, 3600, 'next', '', '', 0),))
        self.proxy.importEvent(AD_SPORT, ((NOW - 9000, 3600, 'older', '', '', 0),))
        self.assertEqual(self.proxy.mapped_services, {AD_SPORT})
        self.assertEqual(self.proxy.past_only_services, set())

    def test_unknown_event_shape_still_counts_as_fed(self):
        self.proxy.importEvents([AD_SPORT], (('not-a-time', None),))
        self.assertEqual(self.proxy.mapped_services, {AD_SPORT})


class PastOnlyRelease(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.guard, self.modules = load_core('epg_priority_guard', self.db)
        self.folder = tempfile.TemporaryDirectory()
        self.state = str(Path(self.folder.name) / 'priority.json')

    def tearDown(self):
        self.folder.cleanup()

    def protect(self, refs, past_only=()):
        with patch.dict(sys.modules, self.modules), patch.object(self.guard.time, 'time', return_value=NOW):
            return self.guard.protect_imported_services(refs, state_path=self.state, past_only_refs=past_only)

    def test_stale_feed_service_gets_sat_eit_back_at_once(self):
        self.protect([AD_SPORT, AJ_ENGLISH])           # yesterday both were fed
        self.db.flags[AD_SPORT] = 0x1000 | 0x40         # keep unrelated flags intact
        result = self.protect([AJ_ENGLISH], past_only=[AD_SPORT])
        self.assertEqual(result['past_only_released'], 1)
        self.assertEqual(self.db.flags[AD_SPORT], 0x40)
        self.assertEqual(self.db.flags[AJ_ENGLISH], 0x1000)
        state = json.loads(Path(self.state).read_text(encoding='utf-8'))
        self.assertEqual(state['protected_refs'], [AJ_ENGLISH])

    def test_service_fed_by_another_source_is_not_released(self):
        result = self.protect([AJ_ENGLISH], past_only=[AJ_ENGLISH])
        self.assertNotIn('past_only_released', result)
        self.assertEqual(self.db.flags[AJ_ENGLISH], 0x1000)


class Rc83Package(unittest.TestCase):
    def test_version_strings(self):
        base = ROOT / 'source/receiver/rc83'
        self.assertIn('2026.11.12-rc83', (base / 'CONTROL/control').read_text(encoding='utf-8'))
        self.assertIn('2026.11.12-rc83', (base / 'EPGManager/version.py').read_text(encoding='utf-8'))
        self.assertTrue((base / 'EPGManager/CHANGELOG-rc83.txt').is_file())


if __name__ == '__main__':
    unittest.main()
