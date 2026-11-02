# -*- coding: utf-8 -*-
"""Regression tests: exact-ID-only shadow; never overwrite real ElCinema rows."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools import elcinema_exact_id_gap_shadow as shadow

NOW = datetime(2026, 10, 14, 23, 30, tzinfo=timezone.utc)
fmt = lambda x: x.strftime("%Y%m%d%H%M%S +0000")


def pack(root, source, rows, ids=None, generated=NOW):
    ids = ids or ["MBC1.ae@SD", "DubaiOne.ae@SD"]
    tv = ET.Element("tv")
    for cid in ids:
        el = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(el, "display-name").text = cid
    for cid, st, en, title in rows:
        el = ET.SubElement(tv, "programme", channel=cid, start=fmt(st), stop=fmt(en))
        ET.SubElement(el, "title", lang="ar").text = title
        ET.SubElement(el, "desc", lang="ar").text = "وصف حقيقي"
    gz = gzip.compress(ET.tostring(tv, encoding="utf-8"), mtime=0)
    (Path(root) / "feeds" / (source + ".xml.gz")).write_bytes(gz)
    meta = {"label": source, "sha256": hashlib.sha256(gz).hexdigest(),
            "size_bytes": len(gz), "channels": len(ids), "programmes": len(rows),
            "generated_utc": generated.isoformat()}
    (Path(root) / "feeds" / (source + ".json")).write_text(json.dumps(meta))
    return meta


class TestExactDonorShadow(unittest.TestCase):
    def make_workspace(self, root):
        root = Path(root)
        (root / "feeds").mkdir()
        old = [
            ("MBC1.ae@SD", NOW-timedelta(hours=1), NOW+timedelta(hours=2), "أصلي إم بي سي"),
            ("DubaiOne.ae@SD", NOW-timedelta(hours=1), NOW+timedelta(hours=1), "أصلي دبي"),
        ]
        donors_mbc = [
            ("MBC1.ae@SD", NOW-timedelta(hours=1), NOW+timedelta(hours=2), "تكرار"),
            ("MBC1.ae@SD", NOW+timedelta(hours=2), NOW+timedelta(hours=3), "برنامج فعلي جديد"),
            ("MBC1.ae@SD", NOW+timedelta(hours=3), NOW+timedelta(hours=4), ""),
        ]
        donor_dubai = [
            ("DubaiOne.ae@SD", NOW+timedelta(hours=1), NOW+timedelta(hours=2), "دبي جديد"),
        ]
        pack(root, "elcinema", old)
        pack(root, "shahid", donors_mbc)
        pack(root, "dubaiplus", donor_dubai)
        return root

    def test_only_nonoverlapping_valid_donor_programmes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_workspace(td)
            orig = (root / "feeds/elcinema.xml.gz").read_bytes()
            expected = {"shahid": ("MBC1.ae@SD",), "dubaiplus": ("DubaiOne.ae@SD",)}
            old = shadow.EXACT_DONORS
            try:
                shadow.EXACT_DONORS = expected
                report = shadow.build(root, NOW, shadow=True)
            finally:
                shadow.EXACT_DONORS = old
            self.assertEqual(report["donor_rows_appended"], 2)
            self.assertEqual(report["channels_extended_count"], 2)
            self.assertEqual(report["original_programmes_kept"], 2)
            self.assertTrue(report["read_only_receiver"])
            self.assertFalse(report["production_feeds_modified"])
            self.assertEqual((root / "feeds/elcinema.xml.gz").read_bytes(), orig)
            xml = ET.fromstring(gzip.decompress(
                (root / "output/shadow/elcinema-verified-gap-shadow.xml.gz").read_bytes()))
            programmes = xml.findall("programme")
            self.assertEqual(len(programmes), 4)
            self.assertEqual([p.findtext("title") for p in programmes[:2]],
                             ["أصلي إم بي سي", "أصلي دبي"])
            self.assertEqual({p.findtext("title") for p in programmes[2:]},
                             {"برنامج فعلي جديد", "دبي جديد"})
            self.assertEqual({p.get("channel") for p in programmes},
                             {"MBC1.ae@SD", "DubaiOne.ae@SD"})

    def test_default_analysis_never_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_workspace(td)
            old = shadow.EXACT_DONORS
            try:
                shadow.EXACT_DONORS = {"shahid": ("MBC1.ae@SD",)}
                report = shadow.build(root, NOW)
            finally:
                shadow.EXACT_DONORS = old
            self.assertEqual(report["donor_rows_appended"], 1)
            self.assertFalse((root / "output").exists())

    def test_fuzzy_channel_never_qualifies(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_workspace(td)
            old = shadow.EXACT_DONORS
            try:
                shadow.EXACT_DONORS = {"shahid": ("MBC One.ae@SD",)}
                with self.assertRaisesRegex(ValueError, "Exact identity missing"):
                    shadow.build(root, NOW)
            finally:
                shadow.EXACT_DONORS = old

    def test_nonmatching_sha_and_stale_donor_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_workspace(td)
            old = shadow.EXACT_DONORS
            try:
                shadow.EXACT_DONORS = {"shahid": ("MBC1.ae@SD",)}
                meta = json.loads((root / "feeds/shahid.json").read_text())
                meta["sha256"] = "0"*64
                (root / "feeds/shahid.json").write_text(json.dumps(meta))
                with self.assertRaisesRegex(ValueError, "fingerprint"):
                    shadow.build(root, NOW)
                pack(root, "shahid", [], generated=NOW-timedelta(days=9))
                with self.assertRaisesRegex(ValueError, "Stale/future"):
                    shadow.build(root, NOW)
            finally:
                shadow.EXACT_DONORS = old

    def test_future_expiry_and_bad_titles_do_not_fabricate(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_workspace(td)
            old = shadow.EXACT_DONORS
            try:
                shadow.EXACT_DONORS = {"shahid": ("MBC1.ae@SD",)}
                pack(root, "shahid", [
                    ("MBC1.ae@SD", NOW+timedelta(hours=50), NOW+timedelta(hours=51), "trop tard"),
                    ("MBC1.ae@SD", NOW+timedelta(hours=2), NOW+timedelta(hours=3), ""),
                ])
                report = shadow.build(root, NOW)
                self.assertEqual(report["donor_rows_appended"], 0)
            finally:
                shadow.EXACT_DONORS = old

    def test_times_are_never_truncated_or_shifted(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_workspace(td)
            old = shadow.EXACT_DONORS
            try:
                shadow.EXACT_DONORS = {"shahid": ("MBC1.ae@SD",)}
                pack(root, "shahid", [
                    ("MBC1.ae@SD", NOW+timedelta(hours=1, minutes=30),
                     NOW+timedelta(hours=2, minutes=30), "overlaps boundary"),
                    ("MBC1.ae@SD", NOW+timedelta(hours=2, minutes=30),
                     NOW+timedelta(hours=3), "safe original timestamp"),
                ])
                report = shadow.build(root, NOW, shadow=True)
                self.assertEqual(report["donor_rows_appended"], 1)
                contents = ET.fromstring(gzip.decompress(
                    (root / "output/shadow/elcinema-verified-gap-shadow.xml.gz").read_bytes()))
                self.assertEqual(contents.findall("programme")[-1].get("start"),
                                 fmt(NOW+timedelta(hours=2, minutes=30)))
            finally:
                shadow.EXACT_DONORS = old

    def test_no_production_writes_or_network_shell(self):
        src = (Path(__file__).parent / "elcinema_exact_id_gap_shadow.py").read_text()
        for forbidden in ("/etc/enigma2", "opkg install", "requests.get(", "urlopen(",
                          "subprocess.run(", "os.system(", "rm -rf"):
            self.assertNotIn(forbidden, src)
        self.assertEqual(sum(len(v) for v in shadow.EXACT_DONORS.values()), 12)


if __name__ == "__main__":
    unittest.main()
