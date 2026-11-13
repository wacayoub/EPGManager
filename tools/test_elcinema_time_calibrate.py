#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for tools/elcinema_time_calibrate.py (synthetic feeds, no network)."""
from datetime import datetime, timedelta, timezone
import unittest
import xml.etree.ElementTree as ET

from tools import elcinema_time_calibrate as cal

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
BASE = datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)


def feed(channels):
    """channels: {cid: [(start_dt, minutes, title), ...]} -> XMLTV root."""
    root = ET.Element("tv")
    for cid, rows in channels.items():
        for start, minutes, title in rows:
            prog = ET.SubElement(root, "programme", start=cal.fmt_time(start),
                                 stop=cal.fmt_time(start + timedelta(minutes=minutes)), channel=cid)
            ET.SubElement(prog, "title").text = title
    return root


def grid(titles, start=BASE, step=95, offset_hours=0):
    """Irregular-length programmes, optionally shifted."""
    out, t = [], start + timedelta(hours=offset_hours)
    for i, title in enumerate(titles):
        minutes = step + (i % 3) * 20
        out.append((t, minutes, title))
        t += timedelta(minutes=minutes)
    return out


def refs(channels):
    return {"ref": cal.index_programmes(feed(channels))}


def starts(root, cid):
    return [p.get("start") for p in root.iter("programme") if p.get("channel") == cid]


TITLES = ["Film %d" % i for i in range(12)]


class Calibration(unittest.TestCase):
    def test_channel_five_hours_early_is_moved(self):
        elc = feed({"Rotana.sa@SD": grid(TITLES, offset_hours=-5)})
        learned = {}
        report = cal.calibrate(elc, refs({"Rotana.sa@SD": grid(TITLES)}), learned, NOW)
        self.assertEqual(report["calibrated"]["rotana.sa@sd"]["shift_minutes"], 300)
        self.assertEqual(starts(elc, "Rotana.sa@SD"), [cal.fmt_time(r[0]) for r in grid(TITLES)])
        stops = [p.get("stop") for p in elc.iter("programme")]
        self.assertEqual(stops[0], cal.fmt_time(BASE + timedelta(minutes=95)))
        self.assertEqual(learned["rotana.sa@sd"]["shift_minutes"], 300)

    def test_correct_channel_is_left_alone_and_recorded(self):
        elc = feed({"MBC3.ae@SD": grid(TITLES)})
        before = starts(elc, "MBC3.ae@SD")
        learned = {}
        report = cal.calibrate(elc, refs({"MBC3.ae@SD": grid(TITLES)}), learned, NOW)
        self.assertEqual(starts(elc, "MBC3.ae@SD"), before)
        self.assertIn("mbc3.ae@sd", report["unchanged_with_reference"])
        self.assertEqual(learned["mbc3.ae@sd"]["shift_minutes"], 0)

    def test_references_that_disagree_change_nothing(self):
        elc = feed({"Bolly.ae@SD": grid(TITLES)})
        before = starts(elc, "Bolly.ae@SD")
        two = {"a": cal.index_programmes(feed({"Bolly.ae@SD": grid(TITLES)})),
               "b": cal.index_programmes(feed({"Bolly.ae@SD": grid(TITLES, offset_hours=5)}))}
        report = cal.calibrate(elc, two, {}, NOW)
        self.assertEqual(starts(elc, "Bolly.ae@SD"), before)
        self.assertIn("bolly.ae@sd", report["conflicts"])

    def test_too_little_evidence_changes_nothing(self):
        elc = feed({"Few.eg@SD": grid(TITLES[:4], offset_hours=-5)})
        before = starts(elc, "Few.eg@SD")
        report = cal.calibrate(elc, refs({"Few.eg@SD": grid(TITLES[:4])}), {}, NOW)
        self.assertEqual(starts(elc, "Few.eg@SD"), before)
        self.assertEqual(report["channels_shifted"], 0)

    def test_no_reference_keeps_raw_times(self):
        elc = feed({"Alone.iq@SD": grid(TITLES)})
        before = starts(elc, "Alone.iq@SD")
        report = cal.calibrate(elc, {}, {}, NOW)
        self.assertEqual(starts(elc, "Alone.iq@SD"), before)
        self.assertEqual(report["no_reference"], 1)

    def test_learned_shift_used_when_reference_missing_and_expires(self):
        fresh = {"oman.om@sd": {"shift_minutes": 300, "verified_utc": (NOW - timedelta(days=3)).isoformat()}}
        elc = feed({"Oman.om@SD": grid(TITLES, offset_hours=-5)})
        report = cal.calibrate(elc, {}, fresh, NOW)
        self.assertIn("oman.om@sd", report["learned_applied"])
        self.assertEqual(starts(elc, "Oman.om@SD"), [cal.fmt_time(r[0]) for r in grid(TITLES)])

        old = {"oman.om@sd": {"shift_minutes": 300, "verified_utc": (NOW - timedelta(days=40)).isoformat()}}
        elc = feed({"Oman.om@SD": grid(TITLES, offset_hours=-5)})
        before = starts(elc, "Oman.om@SD")
        report = cal.calibrate(elc, {}, old, NOW)
        self.assertEqual(starts(elc, "Oman.om@SD"), before)

    def test_repeats_every_eight_hours_pick_the_aligned_shift(self):
        # Same six titles re-aired every 8 h: titles fit +5 h and -3 h equally;
        # only +5 h makes the start times line up with the reference.
        # Durations differ a little between re-airs, as on real channels, so
        # the schedule is not exactly periodic.
        block = ["Ep %d" % i for i in range(6)]
        ref_rows = []
        for cycle in range(4):
            t = BASE + timedelta(hours=8 * cycle)
            for i, title in enumerate(block):
                minutes = 70 + i * 7 + cycle * 4
                ref_rows.append((t, minutes, title))
                t += timedelta(minutes=minutes)
        elc_rows = [(s - timedelta(hours=5), m, title) for s, m, title in ref_rows]
        elc = feed({"Comedy.sa@SD": elc_rows})
        report = cal.calibrate(elc, refs({"Comedy.sa@SD": ref_rows}), {}, NOW)
        self.assertEqual(report["calibrated"]["comedy.sa@sd"]["shift_minutes"], 300)

    def test_offset_suffix_parsing(self):
        self.assertEqual(cal.parse_time("20261113050000 +0300"), datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc))
        self.assertEqual(cal.parse_time("20261113050000"), datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
