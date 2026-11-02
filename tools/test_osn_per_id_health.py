# -*- coding: utf-8 -*-
"""OSN source candidates must have real verified programmes, not catalogue IDs."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from tools.osn_per_id_health import publish_health

NOW = datetime(2026,10,18,6,tzinfo=timezone.utc)

def stamp(x):
    return x.strftime("%Y%m%d%H%M%S +0000")

def fixture(folder, *, age_hours=2, falsified=False):
    folder=Path(folder)
    root=ET.Element("tv")
    for cid,name in (("OSNComedy.ae@SD","OSN Comedy"),
                     ("AlfaSeries.ae@SD","Alfa Series"),
                     ("OSNOne.ae@SD","OSN One"),
                     ("OSNMoviesAction.ae@SD","OSN Movies Action")):
        c=ET.SubElement(root,"channel",id=cid)
        ET.SubElement(c,"display-name").text=name
    for h in range(12):
        p=ET.SubElement(root,"programme",channel="OSNComedy.ae@SD",
                        start=stamp(NOW+timedelta(hours=h)),
                        stop=stamp(NOW+timedelta(hours=h+1)))
        ET.SubElement(p,"title").text="Show %d"%(h+1)
    p=ET.SubElement(root,"programme",channel="OSNOne.ae@SD",
                    start=stamp(NOW),
                    stop=stamp(NOW+timedelta(hours=1)))
    ET.SubElement(p,"title").text="Real Film"
    for h in range(12):
        p=ET.SubElement(root,"programme",channel="OSNMoviesAction.ae@SD",
                        start=stamp(NOW+timedelta(hours=h)),
                        stop=stamp(NOW+timedelta(hours=h+1)))
        ET.SubElement(p,"title").text="OSN Movies Action"
    contents=gzip.compress(ET.tostring(root,encoding='utf-8'),mtime=0)
    feed=folder/"osn.xml.gz"
    feed.write_bytes(contents)
    meta={"label":"osn","channels":4,"programmes":25,
          "sha256":hashlib.sha256(contents).hexdigest(),
          "size_bytes":len(contents),
          "generated_utc":(NOW-timedelta(hours=age_hours)).isoformat()}
    if falsified:
        meta["sha256"]="0"*64
    sidecar=folder/"osn.json"
    sidecar.write_text(json.dumps(meta))
    return feed,sidecar

class OSNPerIdTest(unittest.TestCase):
    def test_verified_short_zero_and_fake_are_kept_as_separate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            f,m=fixture(tmp)
            r=publish_health(f,m,NOW)
            by={x["xmltv_id"]:x for x in r["ids"]}
            self.assertEqual(r["counts"],{"VERIFIED":1,"REVIEW":2,"NO_EPG":1})
            self.assertTrue(by["OSNComedy.ae@SD"]["best_match_green_allowed"])
            self.assertEqual(by["OSNOne.ae@SD"]["reason"],"REAL_BUT_SHORT_OR_GAPPED_GUIDE")
            self.assertEqual(by["AlfaSeries.ae@SD"]["reason"],"NO_FUTURE_PROGRAMMES")
            self.assertTrue(by["AlfaSeries.ae@SD"]["manual_mapping_visible"])
            self.assertEqual(by["OSNMoviesAction.ae@SD"]["reason"],"PLACEHOLDER_OR_MISSING_TITLE")
            self.assertEqual(r["programme_rows_unchanged"],25)
            self.assertEqual(r["synthetic_programmes_added"],0)

    def test_stale_source_cannot_green_even_if_long(self):
        with tempfile.TemporaryDirectory() as tmp:
            f,m=fixture(tmp,age_hours=37)
            r=publish_health(f,m,NOW)
            by={x["xmltv_id"]:x for x in r["ids"]}
            self.assertFalse(by["OSNComedy.ae@SD"]["best_match_green_allowed"])
            self.assertEqual(by["OSNComedy.ae@SD"]["reason"],"STALE_PUBLISHED_SOURCE")
            self.assertEqual(r["counts"]["NO_EPG"],1)

    def test_mismatched_published_hash_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            f,m=fixture(tmp,falsified=True)
            with self.assertRaisesRegex(ValueError,"SHA_OR_SIZE_MISMATCH"):
                publish_health(f,m,NOW)

    def test_bad_time_or_unmapped_programme_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            f,m=fixture(tmp)
            raw=gzip.decompress(f.read_bytes()).replace(b'channel="OSNOne.ae@SD"',b'channel="Unknown.one"')
            data=gzip.compress(raw,mtime=0)
            f.write_bytes(data)
            meta=json.loads(m.read_text())
            meta["sha256"]=hashlib.sha256(data).hexdigest()
            meta["size_bytes"]=len(data)
            m.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,"INVALID_OR_ORPHAN"):
                publish_health(f,m,NOW)

if __name__=="__main__":
    unittest.main()
