#!/usr/bin/env python3
"""Regression: preserve valid OSN Kids one-minute content without ignoring defects."""
import sys
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from osn_id_audit_policy import policy_verdict, verified_kids_single_minute_clip


def programme(start="20261018022900 +0000", stop="20261018023000 +0000",
              title="Adam Wa Mishmish", desc="أغنية تعلم الأطفال الأشكال"):
    node = ET.Element("programme", {"start": start, "stop": stop})
    ET.SubElement(node, "title").text = title
    ET.SubElement(node, "desc").text = desc
    return node


class TestKidsMicroclip(unittest.TestCase):
    def row(self, **kwargs):
        data = {"id": "OSN.Kids.ae", "events": 124, "coverage_hours": 31.2, "desc_ar_pct": 100.0,
                "short_lt_2m": 1, "verified_kids_single_minute_clip": True}
        data.update(kwargs)
        return data

    def test_actual_canonical_one_minute_song(self):
        sample = programme()
        for cid in ("OSN.Kids.ae", "OSNKids.ae@SD"):
            self.assertTrue(verified_kids_single_minute_clip(cid, [sample]))
        verdict, issues, warnings, diagnostic = policy_verdict(self.row())
        self.assertEqual((verdict, issues, warnings), ("PASS", [], []))
        self.assertIn("VERIFIED_OSN_KIDS_60S_CLIP=1", diagnostic)

    def test_real_song_shorter_than_one_minute_is_not_waived(self):
        sample = programme(stop="20261018022930 +0000")
        self.assertFalse(verified_kids_single_minute_clip("OSN.Kids.ae", [sample]))

    def test_multiple_short_events_are_not_waived(self):
        self.assertFalse(verified_kids_single_minute_clip(
            "OSN.Kids.ae", [programme(), programme()]))
        self.assertEqual(policy_verdict(self.row(short_lt_2m=2))[0], "REVIEW")

    def test_missing_metadata_fails_proof(self):
        self.assertFalse(verified_kids_single_minute_clip(
            "OSN.Kids.ae", [programme(title="")]))
        self.assertFalse(verified_kids_single_minute_clip(
            "OSN.Kids.ae", [programme(desc="English description only")]))

    def test_non_kids_id_is_not_whitelisted(self):
        self.assertFalse(verified_kids_single_minute_clip(
            "OSN.Comedy.ae", [programme()]))
        self.assertEqual(policy_verdict(self.row(
            verified_kids_single_minute_clip=False))[0], "REVIEW")

    def test_timezone_mismatch_is_not_waived(self):
        self.assertFalse(verified_kids_single_minute_clip(
            "OSN.Kids.ae", [programme(stop="20261018023000 +0100")]))

    def test_all_structural_warnings_remain_active(self):
        for kwargs, expected in [
            ({"coverage_hours": 23.5}, "REVIEW"),
            ({"gaps_gt_2h": 1, "gap_hours": 3.0}, "REVIEW"),
            ({"long_gt_6h": 1}, "REVIEW"),
            ({"overlaps": 1}, "FAIL"),
            ({"invalid": 1}, "FAIL"),
            ({"empty_title": 1}, "FAIL"),
            ({"mixed_tz_days": 1}, "FAIL"),
        ]:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(policy_verdict(self.row(**kwargs))[0], expected)


if __name__ == "__main__":
    unittest.main()
