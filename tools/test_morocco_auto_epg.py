from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from unittest.mock import patch

from tools import morocco_epg as m
from tools.morocco_auto_epg import generate, is_auto_fallback, SERVICES
from tools.programme_authenticity import AUTO_SOURCE, automatic_fallback_schedule, estimated_schedule
from tools.audit_source_quality import analyze
from tools.morocco_seven_day_coverage import coverage
from tools.test_mena_readiness_audit import fixture, check
from tools.test_mena_source_snapshot import rebuild

ROOT = Path(__file__).resolve().parents[1]


class MoroccoAutoEPG(unittest.TestCase):
    def test_main_arryadia_hd_gaps_are_filled_around_smart_estimates(self):
        # 14 Nov 2026: SNRT's grid stopped in September, so the smart model left
        # 13:25-22:00 empty on the main channel.
        now = datetime(2026, 11, 14, 12, 0, tzinfo=m.TZ)
        smart = [m.Event('Arryadia_HD', now+timedelta(hours=1), '[Estimé] موجز الرياضية', 'تقدير',
                         now+timedelta(hours=1, minutes=25), 'ar', 'ar', 'arryadia-smart-estimate'),
                 m.Event('Arryadia_HD', now+timedelta(hours=10), '[Estimé] موجز الرياضية', 'تقدير',
                         now+timedelta(hours=10, minutes=25), 'ar', 'ar', 'arryadia-smart-estimate')]
        auto, _ = generate(smart, now, hours=24)
        hd = sorted([e for e in auto if e.channel == 'Arryadia_HD'] + smart, key=lambda e: e.start)
        self.assertEqual(hd[0].start, now)
        self.assertEqual(hd[-1].stop, now+timedelta(hours=24))
        self.assertTrue(all(a.stop == b.start for a, b in zip(hd, hd[1:])))
        self.assertTrue(all(is_auto_fallback(e) for e in auto))
        self.assertEqual([e.title for e in hd if e.source != AUTO_SOURCE], [s.title for s in smart])

    def test_rolling_seven_days_cover_only_the_three_authorized_services(self):
        now = datetime.now(m.TZ).replace(microsecond=0)
        rows, report = generate([], now)
        self.assertEqual({e.channel for e in rows}, set(SERVICES))
        self.assertTrue(all(is_auto_fallback(e) for e in rows))
        for cid in SERVICES:
            own = [e for e in rows if e.channel == cid]
            self.assertEqual(own[0].start, now)
            self.assertEqual(own[-1].stop, now+timedelta(hours=168))
            self.assertTrue(all(a.stop == b.start for a, b in zip(own, own[1:])))
        self.assertFalse(report['receiver_action'])

    def test_partial_official_and_exact_lkg_have_priority_on_their_own_service(self):
        now = datetime.now(m.TZ).replace(minute=0, second=0, microsecond=0)
        for cid in SERVICES:
            with self.subTest(channel=cid):
                official = m.Event(cid, now+timedelta(hours=1, minutes=17), 'Official programme',
                                   'Official description', now+timedelta(hours=5, minutes=23), source='old')
                rows, _ = generate([official], now, hours=12)
                own = [e for e in rows if e.channel == cid]
                self.assertFalse(any(e.start < official.stop and e.stop > official.start for e in own))
                cursor = now
                for e in sorted(own+[official], key=lambda e: e.start):
                    self.assertEqual(e.start, cursor)
                    cursor = e.stop
                self.assertEqual(cursor, now+timedelta(hours=12))

    def test_official_satellite_programme_is_never_copied_to_tnt(self):
        now = datetime.now(m.TZ)
        sat = m.Event('Arryadia_HD', now, 'Satellite match', '', now+timedelta(hours=2))
        rows, _ = generate([sat], now, hours=2)
        self.assertTrue(any(e.channel == 'Arryadia_TNT' for e in rows))
        self.assertFalse(any('Satellite match' in e.title for e in rows))

    def test_disabled_scope_or_missing_labels_are_rejected_by_writer(self):
        rows, _ = generate([], datetime.now(m.TZ), hours=1)
        for attr, value in [('channel', 'Arryadia_HD'), ('title', 'Actual film'), ('desc', 'Official guide')]:
            event = m.Event(**vars(rows[0]))
            setattr(event, attr, value)
            with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, 'Unauthorized'):
                m.to_xml([event], Path(tmp)/'bad.xml')

    def test_old_generated_blocks_do_not_survive_as_genuine_lkg(self):
        now = datetime.now(m.TZ)
        rows, _ = generate([], now, hours=2)
        selected, _ = m.select_groups({}, rows, now=now)
        self.assertEqual(selected, [])
        self.assertEqual(m._arryadia_fill([], history=rows), [])

    def test_official_arrival_replaces_previous_fallback_on_next_build(self):
        now = datetime.now(m.TZ)
        old, _ = generate([], now, hours=12)
        for cid in SERVICES:
            official = m.Event(cid, now, 'Official dated show', 'Source synopsis', now+timedelta(hours=3), source='old')
            group = 'snrt' if cid == 'AFLAM.ma' else 'arryadia'
            selected, _ = m.select_groups({group: [official]}, old, now=now)
            self.assertEqual(selected, [official])
            new, _ = generate(selected, now, hours=12)
            self.assertFalse(any(e.channel == cid and e.start < official.stop for e in new))

    def test_provenance_survives_packing_and_derived_feed_without_becoming_official(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, now = fixture(root)
            rows, _ = generate([], now)
            raw = root/'morocco.xml'
            m.to_xml(rows, raw)
            saved = m.previous(raw)
            self.assertTrue(saved)
            self.assertTrue(all(e.source == AUTO_SOURCE for e in saved))
            result = subprocess.run([sys.executable, str(ROOT/'tools/source_feed_pack.py'),
                                    '--input', str(raw), '--output-gz', str(root/'feeds/morocco.xml.gz'),
                                    '--output-txt', str(root/'feeds/morocco.txt'),
                                    '--stats', str(root/'feeds/morocco.json'), '--label', 'morocco',
                                    '--window-hours', '168'], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = analyze(root/'feeds/morocco.xml.gz', 'morocco', now)
            self.assertEqual(report['automatic_fallback_channels'], sorted(SERVICES))
            self.assertEqual(report['suspected_placeholder_channels'], [])
            cov = coverage(root/'feeds/morocco.xml.gz', now)
            for row in cov['ids']:
                if row['id'] in SERVICES:
                    self.assertEqual(row['source_programmes'], 0)
                    self.assertGreater(row['automatic_fallback_programmes'], 0)
                    self.assertEqual(row['status'], 'FULL_REQUESTED_WINDOW')
            _, xml = rebuild(root)
            for cid in SERVICES:
                nodes = [p for p in xml.findall('programme') if p.get('channel') == cid]
                self.assertTrue(nodes)
                self.assertTrue(all(automatic_fallback_schedule(n) and estimated_schedule(n) for n in nodes))
                ready = next(r for r in check(root, now)['ids'] if r['xmltv_id'] == cid)
                self.assertFalse(ready['best_match_green_allowed'])

    def test_invalid_windows_are_rejected(self):
        for hours in (0, -1, 169):
            with self.assertRaises(ValueError):
                generate([], datetime.now(timezone.utc), hours)

    def test_production_collector_adds_fallback_after_real_source_selection(self):
        now = datetime.now(m.TZ)
        official = m.Event('AFLAM.ma', now+timedelta(hours=1), 'فيلم معلن رسمياً',
                           'وصف منشور', now+timedelta(hours=3), 'ar', 'ar', 'snrt-ar')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sys, 'argv', ['morocco_epg.py', '--output-dir', tmp, '--days', '7']), \
                patch.object(m, 'scrape_snrt', return_value=[official]), \
                patch.object(m, 'scrape_arryadia', return_value=[]), \
                patch.object(m, 'scrape_2m', return_value=[]), \
                patch.object(m, 'scrape_chada', return_value=[]), \
                patch.object(m, 'scrape_medi1', return_value=[]):
            self.assertEqual(m.main(), 0)
            root = Path(tmp)
            xml = ET.fromstring(gzip.decompress((root/'morocco.xml.gz').read_bytes()))
            official_nodes = [p for p in xml.findall('programme') if p.findtext('title') == official.title]
            self.assertEqual(len(official_nodes), 1)
            self.assertFalse(estimated_schedule(official_nodes[0]))
            auto = [p for p in xml.findall('programme') if automatic_fallback_schedule(p)]
            self.assertEqual({p.get('channel') for p in auto}, set(SERVICES))
            self.assertEqual(json.loads((root/'auto-fallback.json').read_text())['requested_hours'], 168)
            status = json.loads((root/'manifest.json').read_text())['sources']
            self.assertFalse(status['snrt']['qualified'])


if __name__ == '__main__':
    unittest.main()
