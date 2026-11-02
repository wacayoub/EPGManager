# -*- coding: utf-8 -*-
"""Live-day ElCinema MBC/Dubai+ donor integration no-regression fixtures."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from tools import elcinema_daily_donors as integration
from tools import elcinema_exact_id_gap_shadow as donor
from tools.test_elcinema_exact_id_gap_shadow import pack


class TestDailyElCinemaDonors(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        # Patch the module actually used by prepare(), regardless of import
        # style (tools.* versus script-level imports in a discovery run).
        self.old = integration.donor.EXACT_DONORS
        integration.donor.EXACT_DONORS = {
            "shahid": ("MBC1.ae@SD",),
            "dubaiplus": ("DubaiOne.ae@SD",),
        }

    def tearDown(self):
        integration.donor.EXACT_DONORS = self.old

    def fixture(self, root):
        root = Path(root)
        (root / "feeds").mkdir(exist_ok=True)
        start = self.now
        original = [
            ("MBC1.ae@SD", start-timedelta(minutes=30), start+timedelta(minutes=30), "أصلية MBC"),
            ("DubaiOne.ae@SD", start-timedelta(minutes=30), start+timedelta(minutes=30), "أصلية دبي"),
        ]
        other = [
            ("MBC1.ae@SD", start+timedelta(minutes=30), start+timedelta(hours=2), "MBC original verified"),
        ]
        dubai = [
            ("DubaiOne.ae@SD", start+timedelta(minutes=30), start+timedelta(hours=2), "Dubai original verified"),
        ]
        pack(root, "elcinema", original, generated=self.now)
        pack(root, "shahid", other, generated=self.now)
        pack(root, "dubaiplus", dubai, generated=self.now)
        return root

    def test_dry_run_no_source_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.fixture(td)
            before = (root/"feeds/elcinema.xml.gz").read_bytes()
            response = integration.prepare(root, now=self.now)
            self.assertEqual(response["donor_rows_added"], 2)
            self.assertFalse(response["source_modified"])
            self.assertEqual((root/"feeds/elcinema.xml.gz").read_bytes(), before)
            self.assertFalse((root/"reports/elcinema-daily-donor.json").exists())

    def test_apply_preserves_ids_titles_and_utc_times(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.fixture(td)
            before = ET.fromstring(gzip.decompress((root/"feeds/elcinema.xml.gz").read_bytes()))
            report = integration.prepare(root, now=self.now, apply=True)
            after = ET.fromstring(gzip.decompress((root/"feeds/elcinema.xml.gz").read_bytes()))
            self.assertEqual(report["donor_rows_added"], 2)
            self.assertEqual(len(after.findall("programme")), 4)
            self.assertEqual(
                [p.get("start") for p in before.findall("programme")],
                [p.get("start") for p in after.findall("programme")[:2]])
            self.assertEqual(
                [p.findtext("title") for p in after.findall("programme")],
                ["أصلية MBC", "أصلية دبي", "MBC original verified", "Dubai original verified"])
            meta=json.loads((root/"feeds/elcinema.json").read_text())
            self.assertEqual(meta["generated_utc"], self.now.isoformat())
            self.assertEqual(meta["programmes"], 4)
            self.assertEqual(meta["verified_donor_extensions"]["fabricated_programmes"], 0)
            self.assertTrue((root/"reports/elcinema-quality.json").exists())
            self.assertTrue((root/"reports/elcinema-48h-audit.json").exists())

    def test_second_run_idempotent_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.fixture(td)
            integration.prepare(root, now=self.now, apply=True)
            before = (root/"feeds/elcinema.xml.gz").read_bytes()
            result = integration.prepare(root, now=self.now + timedelta(minutes=2), apply=True)
            self.assertEqual(result["status"], "NO_NEW_REAL_EVENTS")
            self.assertEqual(before, (root/"feeds/elcinema.xml.gz").read_bytes())

    def test_provider_scrape_from_yesterday_must_refuse(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td)
            meta_path=root/"feeds/shahid.json"
            meta=json.loads(meta_path.read_text())
            meta["generated_utc"]=(self.now-timedelta(days=1)).isoformat()
            meta_path.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, "SOURCE_NOT_UPDATED_TODAY"):
                integration.prepare(root, now=self.now)

    def test_bad_sha_must_refuse_unchanged_original(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td)
            original=(root/"feeds/elcinema.xml.gz").read_bytes()
            meta_path=root/"feeds/dubaiplus.json"
            meta=json.loads(meta_path.read_text())
            meta["sha256"]="0"*64
            meta_path.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                integration.prepare(root, now=self.now, apply=True)
            self.assertEqual(original, (root/"feeds/elcinema.xml.gz").read_bytes())

    def test_no_external_receiver_operations(self):
        text=(Path(__file__).parent/"elcinema_daily_donors.py").read_text()
        for forbidden in ("opkg install", "/etc/enigma2", "subprocess.run(", "os.system(", "requests.get(", "urlopen("):
            self.assertNotIn(forbidden, text)


if __name__=="__main__":
    unittest.main()
