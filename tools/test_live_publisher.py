#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline synthetic proof that partial real EPG beats expired LKG.

Never fetch providers, update GitHub, edit receiver or fabricate source data.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools.test_bundle_fixture import write_bundle_metadata

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "tools/publish_lkg.py"
PACKER = ROOT / "tools/source_feed_pack.py"


def timeline(channels=9, live=5, horizon=12, shift=-5,
             period=30, invalid_titles=0):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    tv = ET.Element("tv")
    for idx in range(channels):
        ch = ET.SubElement(tv, "channel", {"id": f"qa.{idx}"})
        ET.SubElement(ch, "display-name").text = f"Qatar {idx}"
    for idx in range(live):
        t = now + timedelta(minutes=shift)
        end = now + timedelta(hours=horizon)
        number = 0
        while t < end:
            stop = min(t + timedelta(minutes=period), end)
            p = ET.SubElement(tv, "programme", {
                "channel": f"qa.{idx}",
                "start": t.strftime("%Y%m%d%H%M%S +0000"),
                "stop": stop.strftime("%Y%m%d%H%M%S +0000"),
            })
            ET.SubElement(p, "title", {"lang": "ar"}).text = (
                "" if idx == 0 and number < invalid_titles else
                f"برنامج موثق {idx}-{number}"
            )
            t = stop
            number += 1
    return ET.tostring(tv, encoding="utf-8", xml_declaration=True)


class TestLivePublication(unittest.TestCase):
    def setup_workspace(self, root, candidate, previous):
        root = Path(root)
        (root / "output/final").mkdir(parents=True)
        (root / "feeds").mkdir()
        (root / "output/final/tabie.xml.gz").write_bytes(gzip.compress(candidate))
        write_bundle_metadata(root / "output/final/tabie.xml.gz")
        old = root / "feeds/tabie.xml.gz"
        old.write_bytes(gzip.compress(previous))
        old_meta = old.read_bytes()
        return old, old_meta

    def publish(self, root):
        process = subprocess.run(
            [sys.executable, str(PUBLISHER), "--source", "tabie",
             "--report", "reports/publish-test.json"],
            cwd=str(root), text=True, capture_output=True, timeout=25)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads((Path(root) / "reports/publish-test.json").read_text())
        return report["sources"]["tabie"]

    def test_real_12h_recovery_replaces_completely_expired_feed(self):
        with tempfile.TemporaryDirectory() as td:
            old, before = self.setup_workspace(
                td, timeline(), timeline(horizon=-60, shift=-3*24*60))
            outcome = self.publish(td)
            self.assertTrue(outcome["published"], outcome)
            # 8h is now the ordinary Tabie minimum. A 12h authentic feed no
            # longer needs the historical 24h exemption to replace expired LKG.
            self.assertGreaterEqual(outcome["candidate_stats"]["future_horizon_hours"], 8)
            self.assertGreaterEqual(outcome["candidate_stats"]["current_channels"], 4)
            self.assertNotEqual(old.read_bytes(), before)

    def test_recovery_exemption_never_overwrites_current_lkg(self):
        with tempfile.TemporaryDirectory() as td:
            old, before = self.setup_workspace(
                td, timeline(), timeline(horizon=36))
            outcome = self.publish(td)
            self.assertFalse(outcome["published"], outcome)
            self.assertIn("future_horizon_regression>35%", outcome["reasons"])
            self.assertEqual(old.read_bytes(), before)

    def test_recovery_exemption_rejects_a_future_only_schedule(self):
        with tempfile.TemporaryDirectory() as td:
            old, before = self.setup_workspace(
                td, timeline(shift=180, horizon=15),
                timeline(horizon=-60, shift=-3*24*60))
            outcome = self.publish(td)
            self.assertFalse(outcome["published"], outcome)
            self.assertTrue(any("current_ratio<" in reason for reason in outcome["reasons"]))
            self.assertEqual(old.read_bytes(), before)

    def test_blank_title_never_publishes_to_receiver(self):
        with tempfile.TemporaryDirectory() as td:
            old, before = self.setup_workspace(
                td, timeline(invalid_titles=1),
                timeline(horizon=-60, shift=-3*24*60))
            outcome = self.publish(td)
            self.assertFalse(outcome["published"])
            self.assertTrue(any("missing_title_rows" in x for x in outcome["reasons"]))
            self.assertEqual(old.read_bytes(), before)

    def test_elcinema_compares_coverage_instead_of_freezing_cron(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        def guide(current):
            tv = ET.Element("tv")
            for n in range(97):
                ET.SubElement(tv, "channel", {"id": f"elc.{n}"})
                if n < current:
                    start, stop = now - timedelta(minutes=5), now + timedelta(minutes=15)
                else:
                    start, stop = now + timedelta(minutes=60), now + timedelta(minutes=80)
                for a, b in ((start, stop),
                             (now + timedelta(hours=2), now + timedelta(hours=49))):
                    p = ET.SubElement(tv, "programme", {
                        "channel": f"elc.{n}",
                        "start": a.strftime("%Y%m%d%H%M%S +0000"),
                        "stop": b.strftime("%Y%m%d%H%M%S +0000"),
                    })
                    ET.SubElement(p, "title").text = "برنامج من المصدر"
            return gzip.compress(ET.tostring(tv, encoding="utf-8"))

        for previous_current, expected_published in ((97, False), (7, True)):
            with self.subTest(previous_current=previous_current):
                with tempfile.TemporaryDirectory() as td:
                    base = Path(td)
                    (base / "output/final").mkdir(parents=True)
                    (base / "feeds").mkdir()
                    candidate = guide(7)
                    (base / "output/final/elcinema.xml.gz").write_bytes(candidate)
                    write_bundle_metadata(base / "output/final/elcinema.xml.gz")
                    old = base / "feeds/elcinema.xml.gz"
                    prior = guide(previous_current)
                    old.write_bytes(prior)
                    result = subprocess.run(
                        [sys.executable, str(PUBLISHER), "--source", "elcinema",
                         "--report", "reports/elcinema-test.json"],
                        cwd=td, text=True, capture_output=True, timeout=25)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads((base / "reports/elcinema-test.json").read_text())
                    row = report["sources"]["elcinema"]
                    self.assertEqual(row["published"], expected_published, row)
                    if not expected_published:
                        self.assertIn("current_coverage_regression>25pp", row["reasons"])
                        self.assertEqual(old.read_bytes(), prior)
                    else:
                        self.assertEqual(old.read_bytes(), candidate)

    def test_packer_drops_only_blank_titles_without_faking_replacements(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            inp = path / "real.xml"
            inp.write_bytes(timeline(channels=1, live=1, horizon=0.5, invalid_titles=1))
            gz = path / "published.xml.gz"
            stats = path / "published.json"
            cmd = [
                sys.executable, str(PACKER), "--input", str(inp),
                "--output-gz", str(gz), "--output-txt", str(path / "feed.txt"),
                "--stats", str(stats), "--label", "synthetic", "--window-hours", "48",
            ]
            proc = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=25)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            meta = json.loads(stats.read_text())
            root = ET.fromstring(gzip.decompress(gz.read_bytes()))
            self.assertEqual(meta["rejected_missing_title"], 1)
            # The only programme covering now was blank. Future rows do not
            # mislead Smart Mapping into believing that EPG exists right now.
            self.assertEqual(meta["currently_on_air_channels"], 0)
            self.assertEqual(meta["current_coverage_pct"], 0.0)
            self.assertEqual(meta["currently_uncovered_ids"], ["qa.0"])
            self.assertEqual(meta["programmes"], 1)
            self.assertEqual(len(root.findall("programme")), 1)
            self.assertTrue(all((p.findtext("title") or "").strip()
                                for p in root.findall("programme")))
            self.assertEqual(meta["sha256"], hashlib.sha256(gz.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
