# -*- coding: utf-8 -*-
"""Any source: a valid current guide replaces a fully expired LKG (Tunisia TV, 14 Nov 2026)."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from tools.test_bundle_fixture import write_bundle_metadata
from tools.test_tabie_expired_lkg_recovery import feed

REPO = Path(__file__).resolve().parents[1]
PUBLISH = REPO / "tools/publish_lkg.py"
IDS = ("ElWatania1.tn@SD", "ElWatania2.tn@SD")


def publish(candidate_lead, old_lead, programmes=13, ids=IDS):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        current = root / "output/final/tunisiatv.xml.gz"
        previous = root / "feeds/tunisiatv.xml.gz"
        feed(current, ids, now, candidate_lead, interval_minutes=40, programmes_each=programmes)
        write_bundle_metadata(current)
        feed(previous, IDS, now, old_lead, interval_minutes=40, programmes_each=30)
        before = previous.read_bytes()
        p = subprocess.run([sys.executable, str(PUBLISH), "--source", "tunisiatv",
                            "--report", "reports/t.json"], cwd=root, text=True,
                           capture_output=True, timeout=25)
        assert p.returncode == 0, (p.stdout, p.stderr)
        row = json.loads((root / "reports/t.json").read_text())["sources"]["tunisiatv"]
        return row, before != previous.read_bytes()


class ExpiredLkgRecovery(unittest.TestCase):
    def test_short_current_guide_replaces_an_expired_feed(self):
        # 26 programmes left for the afternoon (< 30), old feed ended two days ago.
        row, changed = publish(candidate_lead=-0.2, old_lead=-60)
        self.assertEqual(row["decision"], "PUBLISH", row)
        self.assertTrue(changed)
        self.assertTrue(row["candidate_stats"]["partial_recovery_from_expired_lkg"])

    def test_short_guide_does_not_replace_a_feed_that_is_still_useful(self):
        row, changed = publish(candidate_lead=-0.2, old_lead=-2)
        self.assertEqual(row["decision"], "KEEP_LKG")
        self.assertFalse(changed)

    def test_guide_missing_a_channel_is_not_a_recovery(self):
        row, changed = publish(candidate_lead=-0.2, old_lead=-60, ids=IDS[:1])
        self.assertEqual(row["decision"], "KEEP_LKG")
        self.assertFalse(changed)

    def test_guide_starting_far_ahead_is_not_a_recovery(self):
        row, changed = publish(candidate_lead=9, old_lead=-60)
        self.assertEqual(row["decision"], "KEEP_LKG")
        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
