"""Exercise source migration and publication failure paths without a receiver."""
from datetime import datetime, timedelta, timezone
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from tools.test_bundle_fixture import write_bundle_metadata

ROOT = Path(__file__).resolve().parents[1]


class PublicationGuard(unittest.TestCase):
    def publish(self, change):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            path = base / 'output/final/rotana.xml.gz'
            path.parent.mkdir(parents=True)
            tv = ET.Element('tv')
            now = datetime.now(timezone.utc)
            for n in range(5):
                cid = 'channel.' + str(n)
                ET.SubElement(tv, 'channel', id=cid)
                for i in range(12):
                    start = now + timedelta(hours=i, minutes=-5)
                    p = ET.SubElement(tv, 'programme', channel=cid,
                                      start=start.strftime('%Y%m%d%H%M%S %z'),
                                      stop=(start + timedelta(hours=1)).strftime('%Y%m%d%H%M%S %z'))
                    ET.SubElement(p, 'title').text = 'Official programme ' + str(i)
            change(tv)
            path.write_bytes(gzip.compress(ET.tostring(tv), mtime=0))
            write_bundle_metadata(path)
            if getattr(self, 'break_sidecar', '') == 'sha':
                meta = path.with_name('rotana.json')
                data = json.loads(meta.read_text()); data['sha256'] = '0' * 64
                meta.write_text(json.dumps(data))
            elif getattr(self, 'break_sidecar', '') == 'index':
                path.with_name('rotana.txt').write_text('wrong.id|Wrong\n')
            elif getattr(self, 'break_sidecar', '') == 'missing':
                path.with_name('rotana.json').unlink()
            feed = base / 'feeds/rotana.xml.gz'; feed.parent.mkdir()
            # Even a broken previous publication must remain untouched on refusal.
            feed.write_bytes(b'previous publication')
            for ext in ('json', 'txt'):
                feed.with_name('rotana.' + ext).write_bytes(b'previous sidecar')
            result = subprocess.run([sys.executable, str(ROOT / 'tools/publish_lkg.py'),
                                     '--source', 'rotana'], cwd=base, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads((base / 'reports/publish-lkg.json').read_text())['sources']['rotana']
            if not decision['published']:
                self.assertEqual(feed.read_bytes(), b'previous publication')
                for ext in ('json', 'txt'):
                    self.assertEqual(feed.with_name('rotana.' + ext).read_bytes(), b'previous sidecar')
            return decision

    def test_valid_complete_bundle_publishes(self):
        self.assertTrue(self.publish(lambda tv: None)['published'])

    def test_duplicate_channel_ids_keep_last_known_good(self):
        row = self.publish(lambda tv: tv.append(copy.deepcopy(tv.find('channel'))))
        self.assertFalse(row['published'])
        self.assertIn('duplicate_channel_id', str(row['reasons']))

    def test_duplicate_programmes_keep_last_known_good(self):
        row = self.publish(lambda tv: tv.append(copy.deepcopy(tv.find('programme'))))
        self.assertFalse(row['published'])
        self.assertIn('invalid_rows=1', row['reasons'])

    def test_unknown_programme_channel_keeps_last_known_good(self):
        self.assertFalse(self.publish(lambda tv: tv.find('programme').set('channel', 'unknown'))['published'])

    def test_bad_or_missing_sidecars_cannot_partially_replace_last_good(self):
        for value in ('sha', 'index', 'missing'):
            with self.subTest(value=value):
                self.break_sidecar = value
                self.assertFalse(self.publish(lambda tv: None)['published'])


class UnifiedCatalogue(unittest.TestCase):
    def test_catalogue_and_installed_legacy_urls_share_fourteen_unique_feeds(self):
        package = types.ModuleType('unified_core')
        package.__path__ = [str(ROOT / 'source/overlay/core')]
        downloader = types.ModuleType('unified_core.downloader'); downloader.Downloader = object
        logger = types.ModuleType('unified_core.logger'); logger.get_logger = lambda name: None
        with patch.dict(sys.modules, {'unified_core': package,
                                     'unified_core.downloader': downloader,
                                     'unified_core.logger': logger}):
            import importlib
            external = importlib.import_module('unified_core.external_sources')
            catalog = importlib.import_module('unified_core.source_catalog')
            expected = set(json.loads((ROOT / 'config/canonical-channel-policy.json').read_text())['default_source_order'])
            direct = external._DIRECT
            self.assertEqual({row['id'].removeprefix('direct_') for row in direct}, expected)
            for row in direct:
                self.assertTrue(row['url'].startswith('https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds/'))
            old = {'id': 'saved_legacy_morocco', 'url': 'https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/feeds/morocco.xml.gz'}
            with patch.object(catalog, 'discover_epgimport_sources', return_value=[old]):
                rows = catalog.all_sources()
            self.assertEqual(sum(r['url'].endswith('/feeds/morocco.xml.gz') for r in rows), 1)
            self.assertEqual(old['id'], 'saved_legacy_morocco')
            self.assertIn('EPG-Scrapers', old['url'])  # caller's settings never mutated


if __name__ == '__main__':
    unittest.main()
