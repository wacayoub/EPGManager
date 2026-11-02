# -*- coding: utf-8 -*-
"""No-network ElCinema short-guide selection + append-tail safety."""
from datetime import datetime,timedelta,timezone
import unittest
import xml.etree.ElementTree as ET
from elcinema_short_horizon_retry import build_catalogue,extend_future
N=datetime(2026,10,17,11,0,tzinfo=timezone.utc)
def fmt(t):return t.strftime("%Y%m%d%H%M%S %z")
def cata(*ids):
    x=ET.Element("channels")
    for cid in ids: ET.SubElement(x,"channel",xmltv_id=cid,site_id="provider-"+cid)
    return x
def feed(*ids):
    x=ET.Element("tv")
    for cid in ids: ET.SubElement(x,"channel",id=cid)
    return x
def show(x,cid,start,stop,title="Real verified programme"):
    p=ET.SubElement(x,"programme",channel=cid,start=fmt(start),stop=fmt(stop))
    ET.SubElement(p,"title").text=title
    return p
class TestShortGuide(unittest.TestCase):
    def test_build_short_channels_only_and_reuse_fallback_site_ids(self):
        cat=cata("short","complete","empty")
        fall=cata("short","empty")
        for c in fall.findall("channel"):
            c.set("site_id","better-"+c.get("xmltv_id"))
        x=feed("short","complete","empty")
        show(x,"short",N+timedelta(hours=2),N+timedelta(hours=25))
        show(x,"complete",N+timedelta(hours=2),N+timedelta(hours=48))
        out,rep=build_catalogue(cat,x,fall,N,min_hours=40,max_ids=45)
        self.assertEqual([z.get("xmltv_id") for z in out.findall("channel")],["empty","short"])
        self.assertEqual(out.find("channel").get("site_id"),"better-empty")
        self.assertEqual(rep["selected"],2)
    def test_extension_only_genuine_new_tail(self):
        base=feed("A")
        show(base,"A",N,N+timedelta(hours=2))
        ret=feed("A")
        show(ret,"A",N+timedelta(hours=1),N+timedelta(hours=3),"Conflict overlapping")
        show(ret,"A",N+timedelta(hours=2),N+timedelta(hours=4),"New tail")
        out=extend_future(base,ret,N)
        self.assertEqual(out["events_added"],1)
        self.assertEqual(len(base.findall("programme")),2)
        self.assertEqual([t.text for p in base.findall("programme") for t in p.findall("title")],
                         ["Real verified programme","New tail"])
    def test_wrong_unknown_channel_not_added(self):
        base=feed("A");ret=feed("B")
        show(ret,"B",N+timedelta(hours=1),N+timedelta(hours=2))
        self.assertEqual(extend_future(base,ret,N)["events_added"],0)
    def test_empty_title_and_invalid_time_not_added(self):
        base=feed("A");ret=feed("A")
        show(ret,"A",N+timedelta(hours=1),N+timedelta(hours=2),"")
        p=show(ret,"A",N+timedelta(hours=3),N+timedelta(hours=4))
        p.set("start","BOGUS")
        self.assertEqual(extend_future(base,ret,N)["events_added"],0)
    def test_invalid_unqualified_timezone_never_used(self):
        base=feed("A");ret=feed("A")
        p=show(ret,"A",N,N+timedelta(hours=1))
        p.set("start","20261017110000")
        self.assertEqual(extend_future(base,ret,N)["events_added"],0)
    def test_no_regression_with_only_past_retry(self):
        base=feed("A")
        show(base,"A",N,N+timedelta(hours=2))
        ret=feed("A")
        show(ret,"A",N-timedelta(hours=3),N-timedelta(hours=2))
        self.assertEqual(extend_future(base,ret,N)["events_added"],0)
        self.assertEqual(len(base.findall("programme")),1)
    def test_can_start_new_live_guide_when_no_existing_programmes(self):
        base=feed("A");ret=feed("A")
        show(ret,"A",N-timedelta(minutes=5),N+timedelta(minutes=25))
        # Not appended: start before NOW and could conflict with unseen programme.
        self.assertEqual(extend_future(base,ret,N)["events_added"],0)
        show(ret,"A",N+timedelta(minutes=1),N+timedelta(minutes=50))
        self.assertEqual(extend_future(base,ret,N)["events_added"],1)
    def test_build_zero_catalogue_returns_none_without_mutation(self):
        root=feed("A")
        c=cata("A")
        out,report=build_catalogue(c,root,now=N)
        self.assertEqual(report["selected"],1)
        self.assertEqual(len(root.findall("programme")),0)
if __name__=="__main__":
    unittest.main()
