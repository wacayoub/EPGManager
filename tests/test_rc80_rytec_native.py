"""Rytec global maps must not be confused with per-feed programme membership."""
import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import prepare_rc80_rytec_native as fix
import prepare_rc80_rytec_ui as ui_fix

REF = "1:0:1:123:456:789:C00000:0:0:0:"
A = {"id": "rytec_a", "provider": "RYTEC", "channels_name": "rytec.channels.xml.xz"}
B = {"id": "rytec_b", "provider": "RYTEC", "channels_name": "rytec.channels.xml.xz"}
C = {"id": "github", "provider": "GITHUB"}


def environment(persisted=None):
    ns = {"source_catalog": types.SimpleNamespace(mapping_source_id=lambda x:x["id"]),
          "source_priority": types.SimpleNamespace(ownership_rank=lambda item,svc:(2 if item["id"]=="github" else 1, ())),
          "source_channel_cache": types.SimpleNamespace(get=lambda sid:[]),
          "channel_mapper": types.SimpleNamespace(_token_score=lambda a,b:100, classify_service_ref=lambda x:"DVB"),
          "_store_rows": lambda store:{}, "_persisted_ref_targets": lambda *a,**k:persisted or {},
          "_manual_ref_targets": lambda *a,**k:{}, "_canon_ref": lambda x:str(x).strip(),
          "_persisted_target_rank": lambda cand,score:(3 if cand.get("mode")=="manual" else 2, cand.get("confidence",0),score)}
    exec(fix.NATIVE_POLICY,ns)
    return ns


class ArbitrationTests(unittest.TestCase):
    def run_plan(self,items,parsed,persisted=None):
        ns=environment(persisted)
        baseline=(Path(__file__).parent/"fixtures/rc80_rytec_arbitration_baseline.py").read_text()
        exec(fix.correct_arbitration(baseline),ns)
        return ns["_fast_single_owner_maps"](items,parsed,({REF:{"name":"Example"}},{}),store_rows={})

    def test_two_rytec_feeds_retain_the_shared_official_channel(self):
        out,stats=self.run_plan([A,B],{"rytec_a":{"id":[REF]},"rytec_b":{"id":[REF]}})
        self.assertEqual(set(out),{"rytec_a","rytec_b"})
        self.assertEqual(stats["suppressed_duplicate_refs"],0)

    def test_different_official_ids_are_kept_until_actual_feed_parsing(self):
        out,_=self.run_plan([A,B],{"rytec_a":{"id.a":[REF]},"rytec_b":{"id.b":[REF]}})
        self.assertEqual(out["rytec_b"],{"id.b":[REF]})

    def test_same_feed_official_aliases_are_not_ranked_by_channel_names(self):
        out,_=self.run_plan([A],{"rytec_a":{"id.a":[REF],"id.b":[REF]}})
        self.assertEqual(set(out["rytec_a"]),{"id.a","id.b"})

    def test_unrelated_preferred_source_still_wins(self):
        out,_=self.run_plan([A,C],{"rytec_a":{"id":[REF]},"github":{"id":[REF]}})
        self.assertEqual(set(out),{"github"})

    def test_saved_exact_manual_owner_still_excludes_every_other_claim(self):
        lock={REF:[{"source_id":"rytec_b","channel_id":"chosen","mode":"manual"}]}
        out,_=self.run_plan([A,B],{"rytec_a":{"id":[REF]},"rytec_b":{"chosen":[REF],"wrong":[REF]}},lock)
        self.assertEqual(out,{"rytec_b":{"chosen":[REF]}})

    def test_different_native_maps_do_not_share_ownership(self):
        other=dict(B,channels_name="another.channels.xml")
        out,_=self.run_plan([A,other],{"rytec_a":{"id":[REF]},"rytec_b":{"id":[REF]}})
        self.assertEqual(len(out),1)


class NativeBridgeTests(unittest.TestCase):
    def bridge(self):
        ns={"os":os,"importlib":importlib}
        class Local:
            def __init__(self,*a): self.source_id=a[0]; self.offset=0
        ns["LocalEPGSource"]=Local
        exec(fix.NATIVE_BRIDGE,ns)
        return ns

    def test_native_download_custom_map_and_offset_are_delegated(self):
        ns=self.bridge();calls=[]
        native=types.SimpleNamespace(items={"id":[REF]},offset=3600,
            downloadables=lambda:["mirror1","mirror2"],update=lambda *a:calls.append(a))
        mapped=ns["FilteredNativeChannelMap"](native,{},lambda items:dict(items))
        self.assertEqual(mapped.downloadables(),["mirror1","mirror2"])
        mapped.update(bool,"downloaded.xz")
        self.assertEqual(mapped.items,{"id":[REF]});self.assertEqual(mapped.offset,3600)
        self.assertEqual(calls[0][1],"downloaded.xz")

    def test_failed_download_keeps_cached_map_without_mutating_native_shared_map(self):
        ns=self.bridge();native=types.SimpleNamespace(items={},update=lambda *a:None)
        fallback={"id":[REF]}
        mapped=ns["FilteredNativeChannelMap"](native,fallback,lambda rows:{k:list(v) for k,v in rows.items()})
        mapped.update(bool)
        self.assertEqual(mapped.items,fallback);self.assertEqual(native.items,{})

    def test_installed_source_registry_and_all_mirrors_are_preserved(self):
        ns=self.bridge();native=types.SimpleNamespace(items={})
        candidate=types.SimpleNamespace(urls=["http://m1/rytecTest.xz","http://m2/rytecTest.xz"],url="http://m2/rytecTest.xz",channels=native,offset=3600)
        module=types.SimpleNamespace(enumSourcesFile=lambda path:[candidate])
        with patch.object(importlib,"import_module",return_value=module):
            source=ns["load_rytec_native_source"](dict(A,url="http://m1/rytecTest.xz"),{},dict)
        self.assertIsNot(source,candidate);self.assertIs(candidate.channels,native)
        self.assertEqual(source.urls,candidate.urls);self.assertEqual(source.offset,3600)

    def test_missing_definition_uses_native_channel_parser_and_catalogue_mirrors(self):
        ns=self.bridge();calls=[]
        module=types.SimpleNamespace(enumSourcesFile=lambda path:[],EPGChannel=lambda *a,**kw:calls.append((a,kw)) or types.SimpleNamespace(items={}))
        item=dict(A,url="http://m1/feed.xz",urls=["http://m1/feed.xz","http://m2/feed.xz"],channels_urls=["http://m1/map.xz","http://m2/map.xz"])
        with patch.object(importlib,"import_module",return_value=module):
            source=ns["load_rytec_native_source"](item,{},dict)
        self.assertEqual(source.urls,item["urls"]);self.assertEqual(calls[0][1]["urls"],item["channels_urls"])

    def test_native_engine_unavailable_is_explicit_error(self):
        ns=self.bridge()
        with patch.object(importlib,"import_module",side_effect=ImportError("missing")):
            with self.assertRaisesRegex(RuntimeError,"EPGConfig unavailable"):
                ns["load_rytec_native_source"](dict(A,url="http://m/feed.xz"),{},dict)


class ProtectionTests(unittest.TestCase):
    def transform(self,manual=None,persisted=None,parsed=None,rows=None,only=False):
        ns=environment(persisted);ns["_manual_ref_targets"]=lambda *a,**kw:manual or {}
        return ns["_rytec_map_transform"](A,[A,B,C],parsed or {},({REF:{}},{}),object(),rows or {},only)

    def test_native_refresh_cannot_overwrite_manual_owner_in_other_source(self):
        transform=self.transform(manual={REF:{("github","manual.id")}})
        self.assertEqual(transform({"rytec.id":[REF]}),{})

    def test_own_manual_exact_id_is_restored_and_other_ids_rejected(self):
        transform=self.transform(manual={REF:{("rytec_a","saved.id")}},rows={"rytec_a::saved.id":{"mode":"manual","refs":[REF]}})
        self.assertEqual(transform({"other.id":[REF]}),{"saved.id":[REF]})

    def test_native_refresh_respects_selected_auto_owner(self):
        transform=self.transform(persisted={REF:[{"source_id":"github","channel_id":"saved","mode":"auto"}]})
        self.assertEqual(transform({"id":[REF]}),{})

    def test_shared_native_map_ids_survive_per_source_refresh(self):
        transform=self.transform(parsed={"rytec_b":{"another.id":[REF]}})
        self.assertEqual(transform({"id":[REF]}),{"id":[REF]})

    def test_native_refresh_does_not_steal_unrelated_preferred_owner(self):
        transform=self.transform(parsed={"github":{"id":[REF]}})
        self.assertEqual(transform({"id":[REF]}),{})

    def test_iptv_only_import_never_writes_dvb_events(self):
        self.assertEqual(self.transform(only=True)({"id":[REF]}),{})

    def test_transformation_never_modifies_provider_input(self):
        source={"id":[REF]};self.transform()(source)
        self.assertEqual(source,{"id":[REF]})

    def test_unknown_receiver_references_are_excluded(self):
        self.assertEqual(self.transform()({"id":["unknown"]}),{})

    def test_pinned_repair_rejects_unreviewed_baseline(self):
        with self.assertRaisesRegex(ValueError,"baseline drift"):
            fix.corrected({name:b"drift" for name in fix.ORIGINAL})

class OfficialCountTests(unittest.TestCase):
    def test_counts_only_receiver_matches_and_reuses_shared_cache(self):
        reads=[];parses=[]
        data={"sat":{"services":[{"ref":REF}]},"iptv":{"services":[]}}
        ns={"INDEX_PATH":"index","os":types.SimpleNamespace(stat=lambda p:types.SimpleNamespace(st_mtime=1,st_mtime_ns=1,st_size=1)),
            "channel_registry":types.SimpleNamespace(SAT_PATH="sat",IPTV_PATH="iptv",canonical_service_ref=lambda x:x),
            "_read_json":lambda p:reads.append(p) or data[p],
            "parse_channels_xml":lambda p:parses.append(p) or {"valid":[REF],"foreign":["foreign"]}}
        exec(fix.OFFICIAL_INDEX,ns)
        self.assertEqual(ns["_cached_official_counts"]("map")["channels"],1)
        self.assertEqual(ns["_cached_official_counts"]("map")["refs"],1)
        self.assertEqual(parses,["map"]);self.assertEqual(reads,["sat","iptv"])

    def test_existing_zero_index_gets_read_only_counts_without_a_save(self):
        ns={"INDEX_PATH":"index"};exec(fix.OFFICIAL_INDEX,ns)
        ns["_read_json"]=lambda p:{"sources":{"a":{"provider":"RYTEC","official":True,"map_path":"map","channels":0,"refs":0}}}
        ns["_cached_official_counts"]=lambda p:{"channels":3,"refs":5}
        self.assertEqual(ns["load_index"]()["sources"]["a"]["channels"],3)

class NativeDisplayTests(unittest.TestCase):
    def test_shared_map_is_counted_once_but_generated_maps_remain_distinct(self):
        ns={};exec(ui_fix.UNIQUE_ROWS,ns)
        rows={str(i):{"provider":"RYTEC","official":True,"map_path":"same","channels":2130} for i in range(54)}
        rows["other"]={"provider":"GITHUB","channels":15}
        result=ns["_unique_mapping_rows"]({"sources":rows})
        self.assertEqual(len(result),2);self.assertEqual(sum(r["channels"] for r in result),2145)

    def test_incomplete_native_header_does_not_claim_zero_programmes_or_missing_mapping(self):
        ns={};exec(ui_fix.NATIVE_DISPLAY,ns)
        item={"provider":"RYTEC","_health":"MAP NEEDED","_channels":3014,"_programmes":0}
        ns["_native_rytec_display"](item,{"complete":False})
        self.assertEqual(item["_health"],"NATIVE MAP");self.assertIsNone(item["_programmes"])
        self.assertEqual(item["_epg_ids_text"],"Shared SRP")

    def test_proven_no_epg_stays_no_epg(self):
        ns={};exec(ui_fix.NATIVE_DISPLAY,ns)
        item={"provider":"RYTEC","_health":"NO EPG","_programmes":0}
        ns["_native_rytec_display"](item,{"complete":True})
        self.assertEqual(item["_health"],"NO EPG");self.assertEqual(item["_programmes"],0)

if __name__ == "__main__": unittest.main()
