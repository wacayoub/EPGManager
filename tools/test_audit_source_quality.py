#!/usr/bin/env python3
"""Source-quality regression: a complete synthetic grid is not genuine EPG."""
import gzip
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
import audit_source_quality

class SourceQualityTests(unittest.TestCase):
    def test_detect_repeated_and_placeholder_grid(self):
        now = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
        root=ET.Element("tv")
        for cid,title in (("DMC.eg@SD","DMC HD"),("OSNMoviesAction.ae@SD","OSN Movies Action")):
            ch=ET.SubElement(root,"channel",{"id":cid})
            ET.SubElement(ch,"display-name").text=title
            for i in range(12):
                start=now+timedelta(hours=i)
                stop=start+timedelta(hours=1)
                p=ET.SubElement(root,"programme",{
                    "channel":cid,
                    "start":start.strftime("%Y%m%d%H%M%S +0000"),
                    "stop":stop.strftime("%Y%m%d%H%M%S +0000")})
                ET.SubElement(p,"title").text=("DMC HD" if cid=="DMC.eg@SD" else "Film %s" % (i+1))
                ET.SubElement(p,"desc").text=("جدول البرامج غير متوفر" if cid=="DMC.eg@SD" else "Programme réel")
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"test.xml.gz"
            p.write_bytes(gzip.compress(ET.tostring(root,encoding="utf-8")))
            result=audit_source_quality.analyze(p,"test",now)
        counts=result["future_programmes_by_channel"]
        placeholders=dict((row["id"],row["count"]) for row in result["suspected_placeholder_channels"])
        self.assertEqual(counts["DMC.eg@SD"],12)
        self.assertEqual(counts["OSNMoviesAction.ae@SD"],12)
        self.assertGreaterEqual(placeholders["DMC.eg@SD"],12)
        self.assertNotIn("OSNMoviesAction.ae@SD",placeholders)
        self.assertIn("DMC.eg@SD",result["suspected_repeated_title_channels"])
        self.assertNotIn("OSNMoviesAction.ae@SD",result["suspected_repeated_title_channels"])

if __name__=="__main__":
    unittest.main()
