# -*- coding: utf-8 -*-
"""Rotana may borrow authentic Al Resalah, never suspicious Rotana Clip loop."""
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools.rotana_verified_stc_fallback import add_verified_donor,CROSS_PROVIDER

def fmt(t):
    return t.strftime("%Y%m%d%H%M%S +0000")

def source(root, now, corrupt=False, wrong_identity=False):
    tv=ET.Element("tv")
    for dest,(sid,name) in CROSS_PROVIDER.items():
        c=ET.SubElement(tv,"channel",{"id":sid})
        ET.SubElement(c,"display-name",{"lang":"en"}).text=(
            "Wrong Replacement" if wrong_identity and name=="Al Resalah" else name)
        ET.SubElement(c,"url",{"system":"stctv-id"}).text="real-stc-provider-id"
        for i in range(51):
            start=now-timedelta(hours=1)+timedelta(hours=i)
            stop=start+timedelta(hours=1)
            p=ET.SubElement(tv,"programme",{"channel":sid,"start":fmt(start),"stop":fmt(stop)})
            ET.SubElement(p,"title",{"lang":"ar"}).text=(
                "Rotana Clip" if name=="Rotana Clip" else f"حلقة التفسير {i+1} من برنامج معتمد")
            ET.SubElement(p,"desc",{"lang":"ar"}).text=f"وصف أصلي للحلقة {i+1}"
    data=gzip.compress(ET.tostring(tv,encoding="utf-8"),mtime=0)
    (root/"stctv.xml.gz").write_bytes(data)
    meta={"label":"stctv","channels":2,"programmes":102,
          "generated_utc":now.isoformat(),
          "sha256":("0"*64 if corrupt else hashlib.sha256(data).hexdigest()),
          "size_bytes":len(data)}
    (root/"stctv.json").write_text(json.dumps(meta))

def rotana(root,now,primary_alresalah=False):
    r=ET.Element("tv")
    for id_ in ["RotanaCinemaKSA.sa@SD","RotanaClip.sa@SD","AlResalah.sa@SD"]:
        ET.SubElement(ET.SubElement(r,"channel",{"id":id_}),"display-name").text=id_
    p=ET.SubElement(r,"programme",{"channel":"RotanaCinemaKSA.sa@SD",
        "start":fmt(now-timedelta(minutes=5)),"stop":fmt(now+timedelta(hours=1))})
    ET.SubElement(p,"title").text="فيلم روتانا أصلي"
    if primary_alresalah:
        p=ET.SubElement(r,"programme",{"channel":"AlResalah.sa@SD",
            "start":fmt(now-timedelta(minutes=10)),"stop":fmt(now+timedelta(hours=1))})
        ET.SubElement(p,"title").text="حلقة أصلية من الرسالة"
    return r

class TestRotanaTrustedSTCOnly(unittest.TestCase):
    def test_only_verified_alresalah_can_fill_no_rotanaclip_placeholder(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source(base,now)
            r=rotana(base,now)
            before=ET.tostring(r)
            report=add_verified_donor(r,base,now)
            self.assertEqual(report["status"],"VERIFIED_REAL_FALLBACK_USED",report)
            self.assertEqual(report["ids"]["AlResalah.sa@SD"]["reason"],
                             "STC_REAL_BROADCAST_PROGRAMMES_COPIED")
            self.assertGreater(report["ids"]["AlResalah.sa@SD"]["added"],10)
            self.assertEqual(report["ids"]["RotanaClip.sa@SD"]["added"],0)
            self.assertEqual(report["ids"]["RotanaClip.sa@SD"]["reason"],
                             "STC_DONOR_NOT_VERIFIED_PER_ID")
            self.assertEqual(report["synthetic_programmes_added"],0)
            self.assertEqual(r.findall("programme")[0].findtext("title"),"فيلم روتانا أصلي")
            self.assertTrue(all(p.get("channel")=="AlResalah.sa@SD" for p in r.findall("programme")[1:]))
            self.assertTrue(all(p.get("source-info-url")=="https://web.stctv.com/"
                     for p in r.findall("programme")[1:]))
            self.assertEqual(len(r.findall("channel")),3)
            self.assertNotEqual(ET.tostring(r),before)

    def test_rotana_first_never_overwritten_by_stc(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source(base,now)
            r=rotana(base,now,primary_alresalah=True)
            original=ET.tostring(r)
            report=add_verified_donor(r,base,now)
            self.assertEqual(report["ids"]["AlResalah.sa@SD"]["reason"],
                             "ROTANA_PRIMARY_PRESENT_UNTOUCHED")
            self.assertEqual(ET.tostring(r),original)

    def test_corrupted_donor_sha_fails_closed_without_touching_rotana(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source(base,now,corrupt=True)
            r=rotana(base,now)
            original=ET.tostring(r)
            report=add_verified_donor(r,base,now)
            self.assertEqual(report["added"],0)
            self.assertEqual(ET.tostring(r),original)
            self.assertEqual(report["status"],"DONOR_UNAVAILABLE_OR_FAILED_PROVENANCE_OR_QUALITY")

    def test_wrong_channel_identity_never_used(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source(base,now,wrong_identity=True)
            r=rotana(base,now)
            report=add_verified_donor(r,base,now)
            self.assertEqual(report["added"],0)
            self.assertEqual(report["ids"]["AlResalah.sa@SD"]["reason"],
                             "STC_DONOR_CHANNEL_IDENTITY_NOT_VERIFIED")

if __name__=="__main__":unittest.main()
