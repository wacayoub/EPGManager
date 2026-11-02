"""Filler removal preserves real programmes, stable IDs and provider age."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools.clean_published_source import clean_payload
from tools.programme_authenticity import clean_programme

ROOT = Path(__file__).resolve().parents[1]


class CleanupTests(unittest.TestCase):
    def example(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        root = ET.Element('tv')
        channel = ET.SubElement(root, 'channel', id='stable.channel')
        ET.SubElement(channel, 'display-name', lang='en').text = 'Channel Example'
        ET.SubElement(channel, 'display-name', lang='ar').text = 'قناة الاختبار'
        for i, title in enumerate(('Channel Example', 'قناة الاختبار', 'Daily News', 'Daily News', 'A real movie')):
            start = now+timedelta(hours=i)
            event = ET.SubElement(root, 'programme', channel='stable.channel',
                                 start=start.strftime('%Y%m%d%H%M%S %z'),
                                 stop=(start+timedelta(hours=1)).strftime('%Y%m%d%H%M%S %z'))
            ET.SubElement(event, 'title', lang='en').text = title
            ET.SubElement(event, 'desc', lang='ar').text = 'وصف أصلي' if i != 3 else 'Program information unavailable'
        raw = gzip.compress(ET.tostring(root, encoding='utf-8'), mtime=0)
        sha = hashlib.sha256(raw).hexdigest()
        meta = {'label':'stctv', 'channels':1, 'programmes':5, 'sha256':sha,
                'size_bytes':len(raw), 'generated_utc':(now-timedelta(hours=12)).isoformat()}
        return root, raw, meta, now

    def test_drop_only_channel_labels_and_remove_unavailable_description(self):
        original, raw, meta, now = self.example()
        output, info, proof = clean_payload('stctv', raw, meta, meta['sha256'], now)
        root = ET.fromstring(gzip.decompress(output))
        self.assertEqual(proof['removed_programmes'], 2)
        self.assertEqual(proof['removed_unavailable_descriptions'], 1)
        self.assertEqual([p.findtext('title') for p in root.findall('programme')], ['Daily News','Daily News','A real movie'])
        self.assertEqual([c.get('id') for c in root.findall('channel')], ['stable.channel'])
        self.assertEqual(info['generated_utc'], meta['generated_utc'])
        self.assertEqual(meta['programmes'], 5)
        self.assertEqual(info['programmes'], 3)
        for before, after in zip(original.findall('programme')[2:], root.findall('programme')):
            self.assertEqual(before.attrib, after.attrib)
            self.assertEqual(ET.tostring(before.find('title')), ET.tostring(after.find('title')))

    def test_second_cleanup_is_exact_byte_noop(self):
        _, raw, meta, now = self.example()
        first, info, _ = clean_payload('stctv', raw, meta, meta['sha256'], now)
        second, second_info, proof = clean_payload('stctv', first, info, info['sha256'], now)
        self.assertEqual(first, second)
        self.assertEqual(info, second_info)
        self.assertFalse(proof['changed'])

    def test_changed_publication_or_forged_metadata_refused(self):
        _, raw, meta, now = self.example()
        with self.assertRaisesRegex(ValueError, 'changed'):
            clean_payload('stctv', raw, meta, '0'*64, now)
        forged = dict(meta, programmes=100)
        with self.assertRaisesRegex(ValueError, 'count'):
            clean_payload('stctv', raw, forged, meta['sha256'], now)

    def test_real_bilingual_title_survives_with_correct_language(self):
        event = ET.Element('programme')
        ET.SubElement(event, 'title', lang='en').text = 'Channel Example'
        ET.SubElement(event, 'title', lang='ar').text = 'الأخبار المسائية'
        result = clean_programme(event, ['Channel Example'])
        self.assertEqual([(n.get('lang'), n.text) for n in result.findall('title')], [('ar','الأخبار المسائية')])
        self.assertEqual(len(event.findall('title')), 2)

    def test_real_french_morocco_titles_and_repeating_shows_survive(self):
        for title in ('Info Soir','Météo','Daily News','برامج اليوم','Al Jazeera investigation','Program Guide to Wildlife'):
            event = ET.Element('programme')
            ET.SubElement(event,'title').text = title
            self.assertIsNotNone(clean_programme(event, ['2M', 'Al Jazeera']))

    def test_packer_cannot_reintroduce_channel_name_filler(self):
        original, _, _, _ = self.example()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base/'raw.xml'
            source.write_bytes(ET.tostring(original, encoding='utf-8'))
            result = subprocess.run([sys.executable, str(ROOT/'tools/source_feed_pack.py'),
                '--input', str(source), '--output-gz', str(base/'out.xml.gz'),
                '--output-txt', str(base/'out.txt'), '--stats', str(base/'out.json'),
                '--label', 'stctv'], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            info = json.loads((base/'out.json').read_text())
            self.assertEqual(info['rejected_placeholder_title'], 2)
            self.assertEqual(info['removed_unavailable_descriptions'], 1)
            self.assertEqual(info['channels'], 1)
            self.assertEqual(info['programmes'], 3)

    def test_all_filler_leaves_channel_id_visible_with_no_epg(self):
        original, _, _, _ = self.example()
        for event in original.findall('programme')[2:]:
            original.remove(event)
        raw = gzip.compress(ET.tostring(original, encoding='utf-8'), mtime=0)
        sha = hashlib.sha256(raw).hexdigest()
        meta = {'label':'stctv', 'channels':1, 'programmes':2, 'sha256':sha,
                'size_bytes':len(raw), 'generated_utc':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}
        result, info, _ = clean_payload('stctv', raw, meta, sha)
        self.assertEqual(info['active_channels'], 0)
        self.assertEqual(info['zero_epg_ids'], ['stable.channel'])
        self.assertEqual(len(ET.fromstring(gzip.decompress(result)).findall('channel')), 1)


if __name__=='__main__':
    unittest.main()
