#!/usr/bin/env python3
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path("vendor/iptv-org-epg/sites")
OUT = Path("output/source-build")

# Explicit production exclusions: repeatedly failed the ElCinema 48h audit.
# Keep unreliable IDs out of the source catalogue and all retry catalogues.
ELCINEMA_PRODUCTION_EXCLUDE = {
    "AlManar.lb@SD",
    "ATV.kw@SD",
    "ElWatania1.tn@SD",
    "ElWatania2.tn@SD",
    "SalamTV.ly@SD",
    "StarWorldMiddleEast.ae@SD",
}

def elcinema_excluded(cid: str) -> bool:
    return cid in ELCINEMA_PRODUCTION_EXCLUDE

SHAHID_MBC_EXTRA = {
    "Alarabiya.ae@SD",
    "AlArabiyaBusiness.ae@SD",
    "AlArabiyaEnglish.sa@SD",
    "AlArabiyaPrograms.ae",
    "AlHadath.sa@SD",
    "Wanasah.ae@SD",
}

# OSN's official API currently exposes several active OSN-branded services
# without an iptv-org xmltv_id.  They were therefore silently discarded by
# choose(), even though the schedule API has real listings for them.  Keep
# stable EPGManager-local XMLTV IDs keyed by OSN's official site_id.
OSN_ALFA_NAME_ID_OVERRIDES = {
    "alfaserieshd": "AlfaSeries.ae@SD",
    "alfaserieschannel": "AlfaSeries.ae@SD",
    "alfaseries2hd": "AlfaSeriesPlus2.ae@SD",
    "alfaseriesplus2hd": "AlfaSeriesPlus2.ae@SD",
    "alfaalyawmhd": "AlYawm.ae@SD",
    "alfaalyawm": "AlYawm.ae@SD",
    "alfaalsafwa": "AlSafwa.ae@SD",
    "alsafwa": "AlSafwa.ae@SD",
    "alfafann": "Fann.ae@SD",
    "alfacinema1": "Cinema1.ae@SD",
    "alfacinema2": "Cinema2.ae@SD",
    "alfamusichd": "MusicNow.ae@SD",
    "musicnow": "MusicNow.ae@SD",
}

def _osn_norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())

# Channels shown by OSN's official "Other boxes" guide but omitted from the
# Android catalogue. Keep them in EPGManager's OSN ID index even while the
# provider hides their GUIDs from /apidata/channels?platform=Android.
OSN_WEB_ONLY_CHANNELS = {
    "AlfaSeries.ae@SD": ("web-alfa-series", "Alfa Series HD"),
    "AlfaSeriesPlus2.ae@SD": ("web-alfa-series-plus2", "Alfa Series +2 HD"),
    "AlYawm.ae@SD": ("web-al-yawm", "Alfa Al Yawm HD"),
    "AlSafwa.ae@SD": ("web-al-safwa", "Alfa Al Safwa"),
    "Fann.ae@SD": ("web-alfa-fann", "Alfa Fann"),
    "Cinema1.ae@SD": ("web-alfa-cinema1", "Alfa Cinema 1"),
    "Cinema2.ae@SD": ("web-alfa-cinema2", "Alfa Cinema 2"),
    "MusicNow.ae@SD": ("web-music-now", "Alfa Music HD"),
}

OSN_OFFICIAL_ID_OVERRIDES = {
    "204": "OSNOne.ae@SD",
    "208": "OSNShowcaseClassics.ae@SD",
    "221": "OSNIQIYI.ae@SD",
    "225": "OSNMoviesHorror.ae@SD",
    "226": "OSNPopUp2.ae@SD",
    "307": "OSNCrime.ae@SD",
    "314": "OSNDocumentary.ae@SD",
    "4502": "OSNNow.ae@SD",
    "5666": "OSNMoviesFamily.ae@SD",
    "5669": "OSNMoviesComedy.ae@SD",
    "5672": "OSNPopUp.ae@SD",
    "206": "OSNBlippiAndFriends",
    "1101": "OSNKTVChannel1HD",
    "6607": "OSNeClutchAccess",
    "6609": "OSNEsport24",
    "6610": "OSNeClutchLIVE",
    "6611": "OSNPadelTV",
    "6612": "OSNeClutchLIVE2",
    "6613": "OSNeClutchArabic",
    "9957": "OSNSTV1HD",
}

def shahid_mbc_allowed(cid: str) -> bool:
    # Full-source mode: retain every valid XMLTV ID exposed by Shahid.
    return bool(cid)

OUT.mkdir(parents=True, exist_ok=True)

def choose(key, sites, arabic_only=False):
    rows=[]
    for site_rank, site in enumerate(sites):
        base=ROOT/site
        files=sorted(base.glob("*.channels.xml"))
        if arabic_only:
            preferred=base/f"{site}_ar.channels.xml"
            if preferred.exists():
                files=[preferred]
        for p in files:
            try:
                rr=ET.parse(p).getroot()
            except Exception:
                continue
            for c in rr.findall("channel"):
                cid=(c.get("xmltv_id") or "").strip()
                sid=(c.get("site_id") or "").strip()
                if key=="osn" and not cid and sid in OSN_OFFICIAL_ID_OVERRIDES:
                    c=ET.fromstring(ET.tostring(c,encoding="utf-8"))
                    cid=OSN_OFFICIAL_ID_OVERRIDES[sid]
                    c.set("xmltv_id",cid)
                if not cid and sid and key in {"osn","shahid","elcinema"}:
                    # Preserve every provider entry even when iptv-org has not
                    # assigned a canonical XMLTV ID yet. The provider site_id is
                    # stable and therefore safe for a local EPGManager ID.
                    c=ET.fromstring(ET.tostring(c,encoding="utf-8"))
                    safe_sid=re.sub(r"[^A-Za-z0-9._-]+","",sid) or "unknown"
                    cid=f"{key}.{safe_sid}"
                    c.set("xmltv_id",cid)
                if not cid or not sid:
                    continue
                if key=="elcinema" and elcinema_excluded(cid):
                    continue
                if key=="shahid" and not shahid_mbc_allowed(cid):
                    continue
                lang=(c.get("lang") or "").lower()
                lang_rank=0 if lang.startswith("ar") else (1 if lang.startswith("en") else 2)
                rows.append((cid,site_rank,lang_rank,p.name,c))
    best={}
    for cid,srank,lrank,name,c in rows:
        rank=(srank,lrank,name)
        if cid not in best or rank < best[cid][0]:
            best[cid]=(rank,c)
    root=ET.Element("channels")
    for cid in sorted(best,key=str.casefold):
        root.append(ET.fromstring(ET.tostring(best[cid][1],encoding="utf-8")))
    ET.indent(root,space="  ")
    path=OUT/f"{key}.channels.xml"
    path.write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    print(f"{key}: {len(root)} channels")
    if len(root)==0:
        raise SystemExit(f"{key}: empty catalogue")

choose("elcinema",["elcinema.com"],arabic_only=True)

def build_elcinema_fallback():
    base=ROOT/"elcinema.com"
    ar_file=base/"elcinema.com_ar.channels.xml"
    en_file=base/"elcinema.com_en.channels.xml"
    ar_root=ET.parse(ar_file).getroot()
    en_root=ET.parse(en_file).getroot()
    en_by_id={(c.get("xmltv_id") or "").strip():c for c in en_root.findall("channel")
              if (c.get("xmltv_id") or "").strip() and (c.get("site_id") or "").strip()}
    root=ET.Element("channels")
    recovered=0
    for ar in ar_root.findall("channel"):
        cid=(ar.get("xmltv_id") or "").strip()
        sid=(ar.get("site_id") or "").strip()
        if not sid:
            continue
        if not cid:
            cid=f"elcinema.{re.sub(r'[^A-Za-z0-9._-]+','',sid) or 'unknown'}"
            ar=ET.fromstring(ET.tostring(ar,encoding="utf-8"))
            ar.set("xmltv_id",cid)
        if elcinema_excluded(cid):
            continue
        node=en_by_id.get(cid)
        if node is not None:
            root.append(ET.fromstring(ET.tostring(node,encoding="utf-8")))
            recovered+=1
        else:
            root.append(ET.fromstring(ET.tostring(ar,encoding="utf-8")))
    ET.indent(root,space="  ")
    path=OUT/"elcinema_fallback.channels.xml"
    path.write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    print(f"elcinema fallback: {len(root)} channels ({recovered} English alternates)")

build_elcinema_fallback()

def _osn_local_id(site_id: str) -> str:
    safe_sid=re.sub(r"[^A-Za-z0-9._-]+","",site_id) or "unknown"
    return f"osn.{safe_sid}"

def _osn_static_id_map():
    """Preserve canonical IDs already known upstream, keyed by OSN GUID."""
    base=ROOT/"osn.com"
    out=dict(OSN_OFFICIAL_ID_OVERRIDES)
    for path in (base/"osn.com_ar.channels.xml", base/"osn.com_en.channels.xml"):
        if not path.exists():
            continue
        try:
            rr=ET.parse(path).getroot()
        except Exception:
            continue
        for ch in rr.findall("channel"):
            sid=(ch.get("site_id") or "").strip()
            cid=(ch.get("xmltv_id") or "").strip()
            if sid and cid:
                out.setdefault(sid,cid)
    return out

def build_osn_catalogue(lang: str, output_name: str):
    """Union the checked-in OSN catalogue with the live OSN API catalogue.

    refresh-source-core creates osn_live_<lang>.channels.xml directly from
    OSN's current /apidata/channels endpoint via the upstream adapter.  The
    checked-in iptv-org file is retained as a fallback and as the canonical
    site_id -> xmltv_id mapping.  Newly exposed OSN channels are never dropped:
    when iptv-org has no canonical ID yet, use the stable provider GUID as
    EPGManager-local ID (osn.<guid>).
    """
    base=ROOT/"osn.com"
    static_path=base/f"osn.com_{lang}.channels.xml"
    live_path=OUT/f"osn_live_{lang}.channels.xml"
    paths=[static_path]
    if live_path.exists():
        try:
            if ET.parse(live_path).getroot().findall("channel"):
                paths.append(live_path)
        except Exception:
            pass

    canonical=_osn_static_id_map()
    by_sid={}
    source_counts={}
    for path in paths:
        if not path.exists():
            continue
        try:
            rr=ET.parse(path).getroot()
        except Exception:
            continue
        source_counts[path.name]=len(rr.findall("channel"))
        for ch in rr.findall("channel"):
            sid=(ch.get("site_id") or "").strip()
            if not sid:
                continue
            node=ET.fromstring(ET.tostring(ch,encoding="utf-8"))
            cid=(node.get("xmltv_id") or "").strip()
            if not cid:
                name_key=_osn_norm_name(node.text or "")
                cid=canonical.get(sid) or OSN_ALFA_NAME_ID_OVERRIDES.get(name_key) or _osn_local_id(sid)
            node.set("xmltv_id",cid)
            node.set("lang",lang)
            # Live catalogue comes last and therefore refreshes the display name
            # while canonical IDs remain stable through the site_id mapping.
            by_sid[sid]=node

    by_cid={}
    for sid,node in by_sid.items():
        cid=(node.get("xmltv_id") or "").strip()
        if cid and cid not in by_cid:
            by_cid[cid]=node

    # OSN's public web guide contains Alfa channels that are not returned by
    # the Android channel API. Retain stable IDs in the source index so the
    # receiver can map them and report them as 0-EPG instead of hiding them.
    for cid,(sid,name) in OSN_WEB_ONLY_CHANNELS.items():
        if cid in by_cid:
            continue
        node=ET.Element("channel", {
            "site": "osn.com",
            "site_id": sid,
            "lang": lang,
            "xmltv_id": cid,
        })
        node.text=name
        by_cid[cid]=node

    root=ET.Element("channels")
    for cid in sorted(by_cid,key=str.casefold):
        root.append(ET.fromstring(ET.tostring(by_cid[cid],encoding="utf-8")))
    ET.indent(root,space="  ")
    out=OUT/output_name
    out.write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    mode="live+static" if live_path in paths else "static-fallback"
    print(f"osn {lang}: {len(root)} channels mode={mode} sources={source_counts}")
    if len(root)==0:
        raise SystemExit(f"osn {lang}: empty catalogue")

build_osn_catalogue("ar","osn.channels.xml")
build_osn_catalogue("en","osn_en.channels.xml")
choose("shahid",["shahid.mbc.net"],arabic_only=True)
choose("rotana",["rotana.net"],arabic_only=True)

# Full-source mode for the beIN feed. Al Kass is intentionally excluded here:
# it has its own direct official source (tools/alkass_source.py), so keeping the
# same IDs in beIN creates duplicate ownership and misleading 0-EPG warnings.
# Obsolete services such as beIN Sports NBA are also excluded. AFC temporary
# feeds remain excluded.
BEIN_ZERO_EPG_EXCLUDE = {
    "beINSportsNBA.qa@SD",
}

BEIN_SITE_ID_OVERRIDES_AR = {
    ("bein.com", "entertainment#8"): ("beINBoxOffice1.qa@SD", "beIN BOX OFFICE 1"),
    ("bein.com", "entertainment#30"): ("beINBoxOffice2.qa@SD", "beIN BOX OFFICE 2"),
}

BEIN_SITE_ID_OVERRIDES_EN = {
    ("bein.com", "entertainment#8"): ("beINBoxOffice1.qa@SD", "beIN BOX OFFICE 1"),
    # The official English beIN guide uses a different channel slot from AR.
    ("bein.com", "entertainment#31"): ("beINBoxOffice2.qa@SD", "beIN BOX OFFICE 2"),
}

BEIN_PACKAGE_DISPLAY_OVERRIDES = {
    "AlJazeeraDocumentary.qa@SD": "Al Jazeera Documentary",
    "Baraem.qa@SD": "Baraem",
    "BeJunior.qa@SD": "beJunior",
    "BloombergTV.us@MiddleEast": "Bloomberg",
    "CBeebiesMiddleEast.uk@SD": "CBeebies Middle East",
    "ClubMTVEurope.uk@SD": "Club MTV Europe",
    "CNNArabic.ae@SD": "CNN Arabic",
    "EuronewsEnglish.fr@SD": "Euronews English",
    "Fatafeat.ae@SD": "Fatafeat",
    "FoodNetworkEMEA.us@SD": "Food Network",
    "FoxActionMoviesMENA.hk@SD": "Fox Action Movies MENA",
    "FoxArabia.ae@SD": "Fox Arabia",
    "FoxMoviesMiddleEast.us@SD": "Fox Movies Middle East",
    "HGTVArabia.us@SD": "HGTV Arabia",
    "JeemTV.qa@SD": "Jeem TV",
    "MTV80s.uk@SD": "MTV 80s",
    "MTV90s.uk@SD": "MTV 90s",
    "StarMoviesMiddleEast.ae@SD": "Star Movies Middle East",
    "StarWorldMiddleEast.ae@SD": "Star World Middle East",
}

BEIN_CANONICAL_NAME_IDS = {
    "jeem": "JeemTV.qa@SD",
    "jeemtv": "JeemTV.qa@SD",
    "baraem": "Baraem.qa@SD",
    "baraemtv": "Baraem.qa@SD",
    "bejunior": "BeJunior.qa@SD",
    "cbeebies": "CBeebiesMiddleEast.uk@SD",
    "cbeebiesmiddleeast": "CBeebiesMiddleEast.uk@SD",
}

# Verified against the live official beIN guide on 2026-09-27.
# The page's href labels are stale/repeated for several rows, so Kids channels
# must be pinned by schedule-bearing slot, not by the displayed link label.
BEIN_LIVE_KIDS_AR = {
    "entertainment#16": ("BeJunior.qa@SD", "beJunior"),
    "entertainment#32": ("JeemTV.qa@SD", "Jeem TV"),
    "entertainment#33": ("Baraem.qa@SD", "Baraem"),
}
BEIN_LIVE_KIDS_EN = {
    "entertainment#23": ("BeJunior.qa@SD", "beJunior"),
    "entertainment#24": ("JeemTV.qa@SD", "Jeem TV"),
    "entertainment#25": ("Baraem.qa@SD", "Baraem"),
}



def _bein_norm_name(value: str) -> str:
    raw="".join(ch.lower() for ch in (value or "") if ch.isalnum())
    # The two official MENA endpoints use both "SPORTS EN 1" and
    # "SPORTS1EN" (same service). Canonicalize token order before dedupe.
    m=re.fullmatch(r"beinsports(\d+)(en|fr)",raw)
    if m:
        return f"beinsports{m.group(2)}{m.group(1)}"
    m=re.fullmatch(r"beinsports(en|fr)(\d+)",raw)
    if m:
        return f"beinsports{m.group(1)}{m.group(2)}"
    return raw

def _bein_generated_id(site: str, sid: str, name: str) -> str:
    # Prefer a semantic ID so the same MENA service discovered through the
    # sports API and the legacy beIN guide converges to one stable ID.
    slug = _bein_norm_name(name)
    if slug and not slug.isdigit():
        return f"beINMENA.{slug}"
    safe_sid = "".join(ch for ch in sid if ch.isalnum())
    prefix = "sports" if site == "beinsports.com" else "guide"
    return f"beINMENA.{prefix}.{safe_sid}"

def build_bein():
    rows=[]
    source_counts={"bein_live_ar":0,"bein_static_ar":0,"beinsports_mena_ar":0}
    generated_ids=set()

    # English mirrors are used only to recover missing canonical XMLTV IDs.
    fallback_by_site={}
    for p in [
        ROOT/"beinsports.com"/"beinsports.com_mena-en.channels.xml",
        ROOT/"bein.com"/"bein.com_en.channels.xml",
    ]:
        if not p.exists():
            continue
        try:
            rr=ET.parse(p).getroot()
        except Exception:
            continue
        for ch in rr.findall("channel"):
            sid=(ch.get("site_id") or "").strip()
            cid=(ch.get("xmltv_id") or "").strip()
            if sid and cid:
                fallback_by_site[(ch.get("site") or p.parent.name,sid)]=cid

    # First pass: collect canonical name->ID mappings already known in the
    # Arabic MENA catalogues.
    known_name_to_id={}
    # Prefer the official full beIN TV guide supplied by the user for both
    # Sports and Entertainment. beinsports.com remains a sports-only fallback.
    selected_files=[
        ("bein.com",OUT/"bein_live_ar.channels.xml",0),
        ("bein.com",ROOT/"bein.com"/"bein.com_ar.channels.xml",1),
        ("beinsports.com",ROOT/"beinsports.com"/"beinsports.com_mena-ar.channels.xml",2),
    ]
    parsed=[]
    for site,p,rank in selected_files:
        if not p.exists():
            continue
        try:
            rr=ET.parse(p).getroot()
        except Exception:
            continue
        if site == "beinsports.com":
            source_counts["beinsports_mena_ar"] = len(rr.findall("channel"))
        elif p == OUT/"bein_live_ar.channels.xml":
            source_counts["bein_live_ar"] = len(rr.findall("channel"))
        else:
            source_counts["bein_static_ar"] = len(rr.findall("channel"))
        for ch in rr.findall("channel"):
            sid=(ch.get("site_id") or "").strip()
            if not sid:
                continue
            cid=(ch.get("xmltv_id") or "").strip()
            name=(ch.text or "").strip()
            # The live beIN HTML repeats incorrect href labels across many
            # entertainment rows. Use the live catalogue only for the three
            # Kids slots verified by their actual schedules; all other services
            # continue to use the stable static/canonical catalogues.
            name_key=_bein_norm_name(name)
            if p == OUT/"bein_live_ar.channels.xml":
                forced_live=BEIN_LIVE_KIDS_AR.get(sid)
                if not forced_live:
                    continue
                cid,name=forced_live
                name_key=_bein_norm_name(name)
            else:
                # Never keep a static service on a slot now occupied by a
                # verified live Kids channel (notably Star World on old #16).
                if site == "bein.com" and sid in BEIN_LIVE_KIDS_AR:
                    continue
                forced=BEIN_SITE_ID_OVERRIDES_AR.get((site,sid))
                if forced:
                    cid,name=forced
                    name_key=_bein_norm_name(name)
                elif not cid:
                    cid=fallback_by_site.get((site,sid),"")
            if not cid:
                cid=BEIN_CANONICAL_NAME_IDS.get(name_key,"")
            if cid in BEIN_PACKAGE_DISPLAY_OVERRIDES:
                name=BEIN_PACKAGE_DISPLAY_OVERRIDES[cid]
                name_key=_bein_norm_name(name)
            if cid and name:
                known_name_to_id.setdefault(name_key,cid)
            parsed.append((site,rank,p.name,ch,sid,cid,name))

    for site,rank,filename,ch,sid,cid,name in parsed:
        node=ET.fromstring(ET.tostring(ch,encoding="utf-8"))
        node.text=name
        generated=False
        if not cid:
            cid=known_name_to_id.get(_bein_norm_name(name),"")
        if not cid:
            cid=_bein_generated_id(site,sid,name)
            generated=True
        node.set("xmltv_id",cid)

        service_key=_bein_norm_name(name)
        # Keep beIN-owned services plus canonical package partners, but never
        # duplicate Al Kass here: Al Kass 1-8 are owned by the dedicated
        # official `alkass` source.
        is_bein_owned=service_key.startswith("bein")
        is_alkass=service_key.startswith("alkass")
        is_canonical_partner=(not generated and bool(cid))
        if is_alkass:
            continue
        if not (is_bein_owned or is_canonical_partner):
            continue
        if "afc" in service_key or "afc" in cid.casefold():
            continue
        # The checked-in static Arabic catalogue contains a stale
        # entertainment#23 "beINJUNIOR" row. Ignore only that stale fallback;
        # never suppress the live-discovered beJunior service.
        if filename == "bein.com_ar.channels.xml" and sid == "entertainment#23" and service_key == "beinjunior":
            continue
        if cid in BEIN_ZERO_EPG_EXCLUDE:
            continue

        if generated:
            generated_ids.add(cid)
        # Deduplicate spelling/order aliases such as SPORTS1EN vs SPORTS EN 1.
        # Prefer a canonical upstream XMLTV ID over a generated local ID.
        rows.append((service_key,cid,rank,filename,node,generated))

    best={}
    for service_key,cid,rank,filename,ch,generated in rows:
        pref=(1 if generated else 0,rank,filename,cid.casefold())
        if service_key not in best or pref < best[service_key][0]:
            best[service_key]=(pref,cid,ch)

    # Different provider pages occasionally expose spelling aliases that map to
    # the same canonical XMLTV ID (e.g. beIN SPORTS 4 / beINSPORT4). Keep one
    # source per XMLTV ID, preferring the MENA sports provider by rank.
    unique_by_id={}
    for service_key,(pref,cid,ch) in best.items():
        old=unique_by_id.get(cid)
        if old is None or pref < old[0]:
            unique_by_id[cid]=(pref,service_key,ch)
    winning_keys={row[1] for row in unique_by_id.values()}

    root=ET.Element("channels")
    for cid in sorted(unique_by_id,key=str.casefold):
        root.append(ET.fromstring(ET.tostring(unique_by_id[cid][2],encoding="utf-8")))
    ET.indent(root,space="  ")
    path=OUT/"bein.channels.xml"
    path.write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))

    # Build a full English mirror using exactly the same canonical IDs.
    # This is the title source for the EN-title / AR-description production policy.
    en_best={}
    for site,p,rank in [
        ("bein.com",OUT/"bein_live_en.channels.xml",0),
        ("bein.com",ROOT/"bein.com"/"bein.com_en.channels.xml",1),
        ("beinsports.com",ROOT/"beinsports.com"/"beinsports.com_mena-en.channels.xml",2),
    ]:
        if not p.exists():
            continue
        try:
            rr=ET.parse(p).getroot()
        except Exception:
            continue
        for ch in rr.findall("channel"):
            sid=(ch.get("site_id") or "").strip()
            name=(ch.text or "").strip()
            if not sid:
                continue
            if p == OUT/"bein_live_en.channels.xml":
                forced_live=BEIN_LIVE_KIDS_EN.get(sid)
                if not forced_live:
                    continue
                _,name=forced_live
            else:
                if site == "bein.com" and sid in BEIN_LIVE_KIDS_EN:
                    continue
                forced=BEIN_SITE_ID_OVERRIDES_EN.get((site,sid))
                if forced:
                    _,name=forced
            key=_bein_norm_name(name)
            canonical_id=BEIN_CANONICAL_NAME_IDS.get(key)
            if key not in winning_keys and canonical_id not in unique_by_id:
                continue
            if "afc" in key:
                continue
            node=ET.fromstring(ET.tostring(ch,encoding="utf-8"))
            if p == OUT/"bein_live_en.channels.xml":
                mapped_id = BEIN_LIVE_KIDS_EN[sid][0]
            else:
                mapped_id = best[key][1] if key in best else BEIN_CANONICAL_NAME_IDS.get(key)
            if not mapped_id:
                continue
            node.set("xmltv_id",mapped_id)
            pref=(rank,p.name)
            if key not in en_best or pref<en_best[key][0]:
                en_best[key]=(pref,node)

    en_root=ET.Element("channels")
    for key in sorted(en_best,key=lambda k:(en_best[k][1].get("xmltv_id") or "").casefold()):
        en_root.append(ET.fromstring(ET.tostring(en_best[key][1],encoding="utf-8")))
    ET.indent(en_root,space="  ")
    en_path=OUT/"bein_en.channels.xml"
    en_path.write_bytes(ET.tostring(en_root,encoding="utf-8",xml_declaration=True))

    import json
    audit={
        "scope":"beIN MENA official package: bein.com AR+EN primary, beinsports.com fallback; Al Kass delegated to dedicated official source; obsolete NBA and AFC excluded",
        "source_priority":["bein.com live-discovered AR+EN","bein.com static fallback","beinsports.com MENA fallback"],
        "source_entries":source_counts,
        "catalogue_ids":len(root),
        "service_keys_before_id_dedupe":len(best),
        "english_catalogue_ids":len(en_root),
        "generated_local_ids":len(generated_ids),
        "generated_ids":sorted(generated_ids,key=str.casefold),
        "zero_epg_policy":"retain valid beIN MENA IDs",
        "third_party_package_channels":"retained when canonical XMLTV ID exists",
        "alkass":"excluded_from_bein_use_dedicated_alkass_source",
        "obsolete_ids":["beINSportsNBA.qa@SD"],
        "afc":"excluded",
    }
    (OUT/"bein-catalogue.json").write_text(
        json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"
    )
    print(
        f"bein MENA package: {len(root)} IDs "
        f"(live AR={source_counts['bein_live_ar']}, "
        f"static AR={source_counts['bein_static_ar']}, "
        f"sports fallback={source_counts['beinsports_mena_ar']}, "
        f"English mirror={len(en_root)}, generated={len(generated_ids)})"
    )
    if len(root)==0:
        raise SystemExit("bein MENA: empty catalogue")
    if len(en_root)<20:
        raise SystemExit(f"bein MENA English catalogue unexpectedly small: {len(en_root)}")

build_bein()


def build_bein_hybrid_english():
    """English catalogue for beIN entertainment channels used by Hybrid mode."""
    wanted={
        "beINGourmet.qa@SD",
        "beINMovies1Premiere.qa@SD",
        "beINMovies2Action.qa@SD",
        "beINMovies3Drama.qa@SD",
        "beINMovies4Family.qa@SD",
        "beINSeries1.qa@SD",
    }
    site_id_overrides={
        "entertainment#9": "beINSeries1.qa@SD",
        "entertainment#12": "beINGourmet.qa@SD",
    }
    src=ROOT/"bein.com"/"bein.com_en.channels.xml"
    rr=ET.parse(src).getroot()
    rows=[]
    for ch in rr.findall("channel"):
        sid=(ch.get("site_id") or "").strip()
        cid=(ch.get("xmltv_id") or "").strip()
        if not cid and sid in site_id_overrides:
            ch=ET.fromstring(ET.tostring(ch,encoding="utf-8"))
            cid=site_id_overrides[sid]
            ch.set("xmltv_id",cid)
        if cid in wanted and sid:
            rows.append((cid,ch))
    root=ET.Element("channels")
    for cid,ch in sorted(rows,key=lambda x:x[0].casefold()):
        root.append(ET.fromstring(ET.tostring(ch,encoding="utf-8")))
    ET.indent(root,space="  ")
    path=OUT/"bein_hybrid_en.channels.xml"
    path.write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    found={cid for cid,_ in rows}
    missing=sorted(wanted-found)
    print(f"bein hybrid English: {len(root)} channels; missing={missing}")
    if missing:
        raise SystemExit(f"bein hybrid English missing IDs: {missing}")

build_bein_hybrid_english()
