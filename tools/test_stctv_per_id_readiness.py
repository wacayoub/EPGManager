# -*- coding: utf-8 -*-
"""STC 48h chronology PASS is insufficient evidence of real programme titles."""
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools import stctv_per_id_readiness as guard

def format_dt(dt):
    return dt.strftime("%Y%m%d%H%M%S +0000")

def mini_feed(root, now, corrupt=False, duplicate=False, aged=0):
    tv=ET.Element("tv")
    channels=[("good","Full genuine schedule"),
              ("placeholder","Channel Loop"),
              ("repeated","Live News"),
              ("empty","Empty Channel")]
    for cid,name in channels:
        c=ET.SubElement(tv,"channel",{"id":cid})
        ET.SubElement(c,"display-name",{"lang":"en"}).text=name
    if duplicate:
        tv.append(ET.fromstring(ET.tostring(tv.find("channel"))))
    for cid,name in channels[:3]:
        slots=10 if cid=="repeated" else 8
        width=48/slots
        for idx in range(slots):
            start=now-timedelta(minutes=5)+timedelta(hours=idx*width)
            stop=start+timedelta(hours=width)
            p=ET.SubElement(tv,"programme",{"channel":cid,
                    "start":format_dt(start),"stop":format_dt(stop)})
            title = (f"Real authentic broadcast {idx} series" if cid=="good"
                else "Channel Loop" if cid=="placeholder"
                else "Live News Segment")
            ET.SubElement(p,"title",{"lang":"en"}).text=title
            ET.SubElement(p,"desc",{"lang":"ar"}).text="وصف حقيقي للأحداث"
    contents=ET.tostring(tv,encoding="utf-8",xml_declaration=True)
    compressed=gzip.compress(contents,mtime=0)
    feed=root/"stctv.xml.gz"
    meta=root/"stctv.json"
    feed.write_bytes(compressed)
    info={"label":"stctv","channels":len(channels),
          "programmes":len(tv.findall("programme")),
          "size_bytes":len(compressed),
          "sha256":hashlib.sha256(compressed).hexdigest(),
          "generated_utc":(now-timedelta(hours=aged)).isoformat()}
    if corrupt:info["sha256"]="0"*64
    meta.write_text(json.dumps(info))
    return feed,meta

class TestSTCPerIDGenuineContent(unittest.TestCase):
    def test_reject_100_percent_fake_channel_title_as_best_match(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            feed,meta=mini_feed(Path(d),now)
            sha=hashlib.sha256(feed.read_bytes()).hexdigest()
            report=guard.ready_report(feed,meta,now=now)
            rows={v["xmltv_id"]:v for v in report["ids"]}
            self.assertEqual(report["channels"],4)
            self.assertEqual(report["original_programmes_preserved"],26)
            self.assertEqual(report["source_sha256"],sha)
            self.assertEqual(report["synthetic_programmes_added"],0)
            self.assertEqual(report["counts"],{
                "VERIFIED":1,"REVIEW":2,"NO_EPG":1})
            self.assertTrue(rows["good"]["best_match_green_allowed"])
            self.assertEqual(rows["placeholder"]["reason"],
                             "SUSPECTED_CHANNEL_TITLE_OR_GENERIC_DESCRIPTION")
            self.assertFalse(rows["placeholder"]["best_match_green_allowed"])
            self.assertEqual(rows["repeated"]["reason"],
                             "LOW_PROGRAMME_TITLE_DIVERSITY")
            self.assertFalse(rows["repeated"]["best_match_green_allowed"])
            self.assertEqual(rows["empty"]["status"],"NO_EPG")
            self.assertTrue(all(x["manual_mapping_visible"] for x in rows.values()))
            self.assertEqual(sha,hashlib.sha256(feed.read_bytes()).hexdigest())

    def test_stale_complete_feed_cannot_be_auto_green(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            feed,meta=mini_feed(Path(d),now,aged=48)
            report=guard.ready_report(feed,meta,now=now)
            by={x["xmltv_id"]:x for x in report["ids"]}
            self.assertEqual(by["good"]["status"],"REVIEW")
            self.assertEqual(by["good"]["reason"],"PUBLISHED_FEED_STALE")

    def test_tampered_published_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            feed,meta=mini_feed(Path(d),datetime.now(timezone.utc),corrupt=True)
            with self.assertRaisesRegex(ValueError,"HASH_OR_SIZE"):
                guard.ready_report(feed,meta)

    def test_duplicate_xmltv_ids_are_not_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            feed,meta=mini_feed(Path(d),datetime.now(timezone.utc),duplicate=True)
            with self.assertRaisesRegex(ValueError,"CATALOGUE_ID_INVALID"):
                guard.ready_report(feed,meta)

    def test_bad_feed_label_is_never_stc(self):
        with tempfile.TemporaryDirectory() as d:
            feed,meta=mini_feed(Path(d),datetime.now(timezone.utc))
            obj=json.loads(meta.read_text())
            obj["label"]="starzplay"
            meta.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError,"WRONG_STCTV_LABEL"):
                guard.ready_report(feed,meta)

if __name__=="__main__":
    unittest.main()
