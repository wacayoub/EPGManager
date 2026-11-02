# -*- coding: utf-8 -*-
"""MENA reconstructs whenever a verified feed changes, not only when all 14 fresh."""
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools.mena_source_snapshot import get_source_snapshot,needs_rebuild

REPO=Path(__file__).resolve().parents[1]

def feed(root,name,cid,when,valid_future=True):
    f=root/"feeds"
    f.mkdir(parents=True,exist_ok=True)
    tv=ET.Element("tv")
    ET.SubElement(ET.SubElement(tv,"channel",{"id":cid}),
                  "display-name",{"lang":"en"}).text=cid
    for i in range(3):
        if valid_future:
            start=when+timedelta(hours=2+i*4)
        else:
            start=when-timedelta(days=5,hours=12)+timedelta(hours=i*4)
        stop=start+timedelta(hours=1)
        p=ET.SubElement(tv,"programme",{"channel":cid,
                 "start":start.astimezone(timezone(timedelta(hours=3))).strftime("%Y%m%d%H%M%S %z"),
                 "stop":stop.astimezone(timezone(timedelta(hours=3))).strftime("%Y%m%d%H%M%S %z")})
        ET.SubElement(p,"title",{"lang":"ar"}).text=f"برنامج حقيقي {i}"
        ET.SubElement(p,"desc",{"lang":"ar"}).text="وصف عربي حقيقي من المصدر"
    data=gzip.compress(ET.tostring(tv,encoding="utf-8",xml_declaration=True),mtime=0)
    (f/f"{name}.xml.gz").write_bytes(data)
    (f/f"{name}.json").write_text(json.dumps({
        "label":name,"channels":1,"programmes":3,
        "size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),
        "generated_utc":when.isoformat()}))
    return data

def rebuild(root):
    p=subprocess.run([sys.executable,str(REPO/"tools/build_derived_feeds.py")],
                     cwd=root,timeout=30,capture_output=True,text=True)
    assert p.returncode==0,(p.stdout,p.stderr)
    meta=json.loads((root/"feeds/mena.json").read_text())
    actual=ET.fromstring(gzip.decompress((root/"feeds/mena.xml.gz").read_bytes()))
    return meta,actual

class TestMenaAll14Snapshot(unittest.TestCase):
    def test_source_snapshot_matches_true_bytes_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            now=datetime.now(timezone.utc).replace(microsecond=0)
            a=feed(root,"morocco","2M",now)
            b=feed(root,"tabie","TabieNow.qa",now)
            snap=get_source_snapshot(root/"feeds",("morocco","tabie","absent"))
            self.assertEqual(snap,{"morocco":hashlib.sha256(a).hexdigest(),
                                   "tabie":hashlib.sha256(b).hexdigest(),
                                   "absent":None})
            self.assertTrue(needs_rebuild(snap,{}))
            self.assertFalse(needs_rebuild(snap,{"source_snapshot":snap}))
            self.assertTrue(needs_rebuild(snap,{"source_snapshot":{"morocco":"old"}}))
            with self.assertRaisesRegex(ValueError,"DUPLICATE"):
                get_source_snapshot(root/"feeds",("morocco","morocco"))
            meta=root/"feeds/morocco.json"
            o=json.loads(meta.read_text());o["sha256"]="0"*64
            meta.write_text(json.dumps(o))
            with self.assertRaisesRegex(ValueError,"SHA_OR_SIZE"):
                get_source_snapshot(root/"feeds",("morocco","tabie"))

    def test_expired_tabie_omitted_until_real_authentic_future_programme(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/"config").mkdir()
            (root/"reports").mkdir()
            (root/"config/canonical-channel-policy.json").write_text(json.dumps({
                "default_source_order":["morocco","tabie"],
                "alias_groups":[],"family_rules":[],
                "quality":{"min_future_programmes":1,"min_future_hours":4,
                           "min_description_pct":35,"min_language_fit_pct":50}}))
            feed(root,"morocco","2M",now)
            feed(root,"tabie","TabieNow.qa",now,valid_future=False)
            first,xml1=rebuild(root)
            self.assertEqual(first["channels"],1)
            self.assertEqual({c.get("id") for c in xml1.findall("channel")},{"2M"})
            self.assertEqual(set(first["source_snapshot"]),{"morocco","tabie"})
            self.assertFalse(needs_rebuild(
                get_source_snapshot(root/"feeds",["morocco","tabie"]),first))
            old_sha=first["source_snapshot"]["tabie"]
            feed(root,"tabie","TabieNow.qa",now,valid_future=True)
            next_sha=get_source_snapshot(root/"feeds",["morocco","tabie"])
            self.assertNotEqual(next_sha["tabie"],old_sha)
            self.assertTrue(needs_rebuild(next_sha,first))
            second,xml2=rebuild(root)
            self.assertEqual(second["channels"],2)
            self.assertEqual({c.get("id") for c in xml2.findall("channel")},
                             {"2M","TabieNow.qa"})
            self.assertEqual(second["source_snapshot"],next_sha)
            self.assertFalse(needs_rebuild(next_sha,second))
            self.assertEqual(len(xml2.findall("programme")),6)
            self.assertTrue(all(x.get("channel") in {"2M","TabieNow.qa"}
                                for x in xml2.findall("programme")))

if __name__=="__main__":
    unittest.main()
