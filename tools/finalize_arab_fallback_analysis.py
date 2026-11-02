#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Finalize fallback duplicate analysis and zero-EPG blacklist.

Analysis-only. It does not modify production feeds.

Rules:
- Healthy production/direct IDs always win over fallback candidates.
- Same fallback channel appearing in several source files is grouped by
  normalized channel name / canonical ID.
- Winner is selected by future coverage, programme count, description
  completeness, language fitness, and provider preference.
- IDs with zero future programmes are placed in an auto-blacklist.
- Source files with zero active channels or invalid XML are source-blacklisted.
"""
from __future__ import annotations
import csv, gzip, json, re, unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

ALL=Path("reports/arab-fallback-all-channels.csv")
UNMAPPED=Path("reports/unmapped-arab-fallback.csv")
HEALTH=Path("reports/arab-fallback-health.json")
OUT=Path("reports")

AR=re.compile(r"[\u0600-\u06ff]")
LATAM_BAD=re.compile(r"\b(argentina|latinoam[eé]rica|latin america|pakapaka|tooncast)\b",re.I)
EXCLUDED_SOURCE_KEYS={("epgshare","AR1")}
QUARANTINED_SOURCE_KEYS={("epgshare","AE1")}
AUTO_QUARANTINE_CLONE_PCT=35.0
BEIN_FAMILY_RE=re.compile(r"(?:\bbe\s*in\b|\bbein\b|بي\s*إن|بي\s*ان)",re.I)
BAD_SAMPLE_RE=re.compile(r"(?:tv\s*guide\s*is\s*not\s*available|edge\s*of\s*the\s*unknown\s*with\s*jimmy\s*chin|no\s*scheduled\s*events)",re.I)
JUNK_NAME_RE=re.compile(r"(?:logo|\.svg\b|updatez|brand\s*logo|\bawg\b|\btci\b|\bcopy\b)",re.I)
RADIO_DATA_RE=re.compile(r"(?:\bradio\b|\bfm\b|إذاعة|اذاعه|راديو|\bdata\b)",re.I)
DIRECT_FAMILY_PATTERNS={
    "bein": BEIN_FAMILY_RE,
    "osn": re.compile(r"(?:\bosn\b|أو\s*إس\s*إن|او\s*اس\s*ان)",re.I),
    "mbc": re.compile(r"(?:\bmbc\b|إم\s*بي\s*سي|ام\s*بي\s*سي)",re.I),
    "rotana": re.compile(r"(?:\brotana\b|روتانا)",re.I),
    "dubai_dmi": re.compile(r"(?:\bdubai\b|\bsama\s*dubai\b|دبي|سما\s*دبي)",re.I),
    "adm": re.compile(r"(?:abu\s*dhabi|ad\s*sports|al\s*emarat|أبو\s*ظبي|ابو\s*ظبي|الإمارات|الامارات)",re.I),
    "morocco": re.compile(r"(?:\b2m\b|al\s*aoula|alaoula|arryadia|arrabiaa|almaghribiya|assadisa|tamazight|snrt|الأولى|الاولى|الرياضية|الثقافية|المغربية|السادسة|تمازيغت)",re.I),
    "sport24": re.compile(r"(?:\bsport\s*24\b|\besport\s*24\b)",re.I),
}
PREFIX_RE=re.compile(r"^(?:en|ar)\s*:\s*",re.I)
MULTINATIONAL_TITLE_EN_RE=re.compile(r"(?:national\s*geographic|nat\.?\s*geo|osn|cnn|bbc(?:\s*earth)?|discovery|disney|cartoon\s*network|cartoonito|cbeebies|baby[\s-]*tv|blippi|nick(?:elodeon|toons|\s*jr)|history|animal\s*planet|tlc|hgtv|food\s*network|bloomberg|cnbc|euronews|euro\s*news|france\s*24|\brt\b|\bdw\b|al\s*jazeera\s*english|star\s*(?:action|movies|world)|starzplay)",re.I)
PROTECTED_DIRECT_FEED_STEMS={"bein","osn","elcinema","sport24","2m","morocco","snrt"}
KNOWN_BAD_FALLBACK_IDS={"baby-tv-2.qa","cbeebies-1.qa","fatafeat-1.qa"}
BEIN_IMPLICIT_RE=re.compile(r"^(?:movies[1-4]\b.*|boxoffice[12]\b.*|4k\s+digital\b.*)$",re.I)
AFC_BEIN_RE=re.compile(r"^\s*afc(?:\s|[-_]|\d)",re.I)
ALKASS_BAD_SAMPLE_RE=re.compile(r"^(?:beIN Sports MAX|News|Akhbar|أخبار)$",re.I)
SOURCE_OVERRIDES={"2023 alkass 3.qa":("openepg","qatar2")}

ALIASES={
    "mbc 3":"mbc3","mbc3":"mbc3",
    "mbc 1":"mbc1","mbc1":"mbc1",
    "mbc action":"mbc action",
    "mbc masr hd":"mbc masr","mbc masr":"mbc masr",
    "mbc masr 2 hd":"mbc masr 2","mbc masr 2":"mbc masr 2",
    "abu dhabi sports 1":"ad sports 1","ad sports 1 hd":"ad sports 1",
    "abu dhabi sports 2":"ad sports 2","ad sports 2 hd":"ad sports 2",
    "dubai sports 1 hd":"dubai sports 1",
    "dubai sports 2 hd":"dubai sports 2",
    "ksa sports 1":"saudi sports 1",
    "ksasports 1":"saudi sports 1",
    "bbc arabic":"bbc arabic news",
    "cartoon network hd":"cartoon network arabic",
    "cn arabic":"cartoon network arabic",
    "cartoon network arabic 1":"cartoon network arabic",
    "space toon":"spacetoon",
    # Arabic <-> Latin canonical aliases for common MENA channels.
    "العربية":"al arabiya",
    "العربية business":"al arabiya business",
    "الحدث":"al hadath",
    "دبي":"dubai",
    "دبي وان":"dubai one",
    "دبي زمان":"dubai zaman",
    "سما دبي":"sama dubai",
    "الشارقة":"sharjah",
    "السعودية":"saudiya",
    "السعودية tv":"saudiya",
    "إس بي سي":"sbc",
    "ام بي سي":"mbc1",
    "إم بي سي":"mbc1",
    "ام بي سي 1":"mbc1",
    "إم بي سي 1":"mbc1",
    "ام بي سي 2":"mbc2",
    "إم بي سي 2":"mbc2",
    "ام بي سي 3":"mbc3",
    "إم بي سي 3":"mbc3",
    "ام بي سي 4":"mbc4",
    "إم بي سي 4":"mbc4",
    "ام بي سي 5":"mbc5",
    "إم بي سي 5":"mbc5",
    "إم بي سي أكشن":"mbc action",
    "إم بي سي ماكس":"mbc max",
    "إم بي سي بوليوود":"mbc bollywood",
    "إم بي سي العراق":"mbc iraq",
    "إم بي سي مصر":"mbc masr",
    "إم بي سي مصر 2":"mbc masr 2",
    "إم بي سي مصر دراما":"mbc masr drama",
    "روتانا سينما":"rotana cinema",
    "روتانا سينما مصر":"rotana cinema masr",
    "روتانا كلاسيك":"rotana classic",
    "روتانا كوميدي":"rotana comedy",
    "روتانا دراما":"rotana drama",
    "روتانا خليجية":"rotana khalijia",
    "الجديد":"al jadeed",
    "السومرية":"alsumaria",
    "الرشيد":"alrasheed",
    "الأردن":"jordan",
    "رؤيا":"roya",
    "الفجيرة":"fujairah",
    "الظفرة":"al dafrah",
    "الحياة":"alhayat",
    "القاهرة والناس":"al kahera wal nas",
    "القاهرة والناس 2":"al kahera wal nas 2",
    "النهار":"al nahar",
    "النهار دراما":"al nahar drama",
    "المحور":"mehwar",
    "أون إي":"on e",
    "أون دراما":"on drama",
    "دي إم سي":"dmc",
    "دي إم سي دراما":"dmc drama",
    "سي بي سي":"cbc",
    "سي بي سي دراما":"cbc drama",
    "صدى البلد":"sada el balad",
    "صدى البلد 2":"sada el balad 2",
    "صدى البلد دراما":"sada el balad drama",
    "نايل دراما":"nile drama",
    "نايل لايف":"nile life",
    "ميكس وان":"mix one",
    "زي ألوان":"zee alwan",
    "أو إس إن وان":"osn tv one",
    "أو إٍس إن ناو":"osn tv now",
    "أو إس إن كوميدي":"osn tv comedy",
    "أو إس إن كرايم":"osn tv crime",
    "أو إس إن شو كايس":"osn tv showcase",
    "أو إس إن ياهلا":"osn ya hala",
    "أو إس إن ياهلا بالعربي":"osn tv yahala bil arabi",
    "أو إس إن ياهلا أفلام":"osn ya hala aflam",
    "بي إن دراما":"bein drama",
    "بي إن موفيز أكشن":"bein movies action",
    "بي إن موفيز دراما":"bein movies drama",
    "بي إن موفيز فاميلي":"bein movies family",
    "بي إن موفيز بريمير":"bein movies premiere",
}

def norm(s):
    s=PREFIX_RE.sub("",s or "")
    s=unicodedata.normalize("NFKC",s).casefold()
    s=s.replace("&"," and ")
    s=re.sub(r"\b(?:hd|sd|fhd|uhd|4k)\b"," ",s)
    s=re.sub(r"\b(?:tv|channel)\b"," ",s)
    s=re.sub(r"[._:/\\-]+"," ",s)
    s=re.sub(r"[^\w\u0600-\u06ff]+"," ",s)
    s=" ".join(s.split())
    return ALIASES.get(s,s)

def direct_identity_keys(raw):
    """Aggressive-but-safe identity keys for direct-feed protection."""
    raw=(raw or "").strip()
    if not raw:
        return set()
    vals={raw}
    vals.add(re.sub(r"\.(?:ae|sa|eg|qa|net|bein)$","",raw,flags=re.I))
    out=set()
    for v in vals:
        n=norm(v)
        if not n:
            continue
        out.add(n)
        stripped=re.sub(r"\b(?:live|digital|arabic|english|mono)\b"," ",n,flags=re.I)
        stripped=" ".join(stripped.split())
        if stripped:
            out.add(stripped)
        compact=re.sub(r"[^\w\u0600-\u06ff]+","",stripped or n)
        if compact:
            out.add(compact)
    return out

def direct_family_match(r):
    txt=" ".join(str(r.get(k,"") or "") for k in ("name","id","source","provider"))
    for family,pat in DIRECT_FAMILY_PATTERNS.items():
        if pat.search(txt):
            return family
    return ""

def is_bein_family(r):
    txt=" ".join(str(r.get(k,"") or "") for k in ("name","id","source","provider"))
    return bool(BEIN_FAMILY_RE.search(txt))

def arabic_pct(s):
    if not s: return 0.0
    letters=[c for c in s if c.isalpha()]
    if not letters: return 0.0
    return 100*sum(1 for c in letters if AR.search(c))/len(letters)

def classify_language(name,id_):
    txt=(name+" "+id_).casefold()
    # Foreign/international channels should not be penalized for non-Arabic titles.
    if any(k in txt for k in ("english","cnn","bbc world","france 24 english","nhk","trt world","cgtn","dw ","zee","star plus","hgtv")):
        return "international"
    # Movie/series branded feeds: original/English title can be acceptable.
    if any(k in txt for k in ("movie","movies","series","action","thriller","persia","bollywood")):
        return "mixed"
    return "arabic"

def lang_score(r):
    cls=classify_language(r["name"],r["id"])
    name_ar=arabic_pct(r["name"])
    desc=float(r["desc_pct"])
    if cls=="arabic":
        # We do not have event-language % in this CSV, so reward Arabic channel
        # naming only mildly; description completeness remains decisive.
        return (8 if name_ar>30 else 0) + desc*0.25
    if cls=="mixed":
        return desc*0.30
    return desc*0.25

def provider_score(r):
    # OpenEPG slightly preferred when all else is equal; EPGShare remains fallback.
    return 8 if r["provider"]=="openepg" else 4

def latin_title_score(s):
    s=(s or "").strip()
    letters=[ch for ch in s if ch.isalpha()]
    if not letters:
        return 0.0
    latin=sum(1 for ch in letters if ("LATIN" in unicodedata.name(ch,"")))
    return 100.0*latin/len(letters)

def use_english_title_policy(r):
    blob=" ".join(str(r.get(k,"") or "") for k in ("name","id"))
    return bool(MULTINATIONAL_TITLE_EN_RE.search(blob))

def choose_bilingual_event_fields(arr,winner):
    """For multinational channels only: prefer English title + Arabic description."""
    if not use_english_title_policy(winner):
        winner["title_desc_policy"]="NATIVE_TITLE_POLICY"
        return winner

    title_candidates=[]
    desc_candidates=[]
    for x in arr:
        t=(x.get("sample_title") or "").strip()
        d=(x.get("sample_desc") or "").strip()
        if t:
            title_candidates.append((latin_title_score(t), -arabic_pct(t), t, x))
        if d:
            desc_candidates.append((arabic_pct(d), len(d), d, x))

    en_found=False
    ar_desc_found=False

    if title_candidates:
        title_candidates.sort(key=lambda z:(z[0],z[1],len(z[2])),reverse=True)
        best_t=title_candidates[0]
        if best_t[0] >= 60.0:
            winner["sample_title"]=best_t[2]
            winner["title_source"]=f'{best_t[3].get("provider","")}:{best_t[3].get("source","")}:{best_t[3].get("id","")}'
            en_found=True

    if desc_candidates:
        desc_candidates.sort(key=lambda z:(z[0],z[1]),reverse=True)
        best_d=desc_candidates[0]
        if best_d[0] >= 20.0:
            winner["sample_desc"]=best_d[2]
            winner["desc_source"]=f'{best_d[3].get("provider","")}:{best_d[3].get("source","")}:{best_d[3].get("id","")}'
            ar_desc_found=True

    if en_found and ar_desc_found:
        winner["title_desc_policy"]="EN_TITLE_AR_DESC_PAIRED"
    elif en_found:
        winner["title_desc_policy"]="EN_TITLE_NO_AR_DESC"
    else:
        winner["title_desc_policy"]="NO_EN_TITLE_MATCH"
    return winner

def epg_arabic_score(r):
    """Arabic share across channel name + sample event title/description."""
    blob=" ".join(str(r.get(k,"") or "") for k in ("name","sample_title","sample_desc"))
    return arabic_pct(blob)

def has_arabic_epg(r):
    return epg_arabic_score(r) >= 20.0 or language_rank_marker(r)==0

def language_rank_marker(r):
    name=(r.get("name") or "").strip()
    cid=(r.get("id") or "").strip()
    txt=(name+" "+cid).casefold()
    ar_marker=re.search(r"(?:^|[_.\s-])ar(?:$|[_.\s-])",txt,re.I)
    en_marker=re.search(r"(?:^|[_.\s-])en(?:$|[_.\s-])",txt,re.I)
    if AR.search(name) or AR.search(cid) or ar_marker or re.match(r"^\s*arabic\b",name,re.I):
        return 0
    if en_marker or re.match(r"^\s*english\b",name,re.I) or " english" in txt:
        return 2
    return 1

def language_rank(r):
    """Arabic EPG first, then neutral/original, English last."""
    if has_arabic_epg(r):
        return 0
    return language_rank_marker(r)

def bilingual_key(r):
    """Collapse AR/EN variants even when language is in prefix or suffix."""
    name=(r.get("name") or "").strip()
    cid=(r.get("id") or "").strip()
    for raw in (name,cid):
        s=raw
        s=re.sub(r"\.(?:ae|sa|eg|qa|bein|net)$","",s,flags=re.I)
        s=re.sub(r"^\s*(?:ar|en|arabic|english)\s*[:._ -]*","",s,flags=re.I)
        s=re.sub(r"[:._ -]+(?:ar|en|arabic|english)\s*$","",s,flags=re.I)
        s=re.sub(r"(?i)(?:_DIGITAL_Mono)?_(?:AR|EN)$","",s)
        s=norm(s)
        if s:
            return s
    return norm(name) or norm(cid)

def dynamic_quarantined_sources(health):
    out=set(QUARANTINED_SOURCE_KEYS)
    for h in health.get("health",[]):
        if h.get("status")!="ok":
            continue
        active=int(h.get("active_channels",0) or 0)
        clones=int(h.get("suspicious_clone_channels",0) or 0)
        raw_active=active+clones
        pct=(100.0*clones/raw_active) if raw_active else 0.0
        if raw_active>=10 and pct>=AUTO_QUARANTINE_CLONE_PCT:
            out.add((h.get("provider",""),h.get("source","")))
    return out

def load_direct_catalogue():
    """Return healthy published direct IDs and protected direct identities."""
    direct_ids=set()
    direct_names=defaultdict(list)
    protected_names=defaultdict(list)
    now_dt=datetime.now(timezone.utc)
    for path in sorted(Path("feeds").glob("*.xml.gz")):
        stem=path.name.replace(".xml.gz","").casefold()
        protected=(stem in PROTECTED_DIRECT_FEED_STEMS or
                   any(x in stem for x in ("bein","osn","elcinema","sport24","morocco","snrt")))
        try:
            data=path.read_bytes()
            if data[:2]==b"\x1f\x8b":
                data=gzip.decompress(data)
            root=ET.fromstring(data)
        except Exception:
            continue

        future_counts=Counter()
        for p in root.findall("programme"):
            raw=(p.get("stop") or "").strip()
            try:
                stop=datetime.strptime(raw[:14],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if stop>now_dt:
                future_counts[(p.get("channel") or "").strip()] += 1

        for ch in root.findall("channel"):
            cid=(ch.get("id") or "").strip()
            if not cid or future_counts[cid] <= 0:
                continue
            names=[(x.text or "").strip() for x in ch.findall("display-name") if (x.text or "").strip()]
            if not names:
                names=[cid]
            direct_ids.add(cid)
            entries=[{"id":cid,"feed":path.name,"name":names[0]}]
            for raw in names+[cid]:
                for key in direct_identity_keys(raw):
                    direct_names[key].extend(entries)
                    if protected:
                        protected_names[key].extend(entries)
    return direct_ids,direct_names,protected_names

def load_compare_only_channels():
    """Discovery-only channel rows shown in new-arab-epg-ids.csv for comparison."""
    rows=[]
    seen=set()
    rotana_art_programmes={}
    rap=OUT/"rotana-art-programmes.json"
    if rap.exists():
        try:
            rr=json.loads(rap.read_text(encoding="utf-8"))
            rotana_art_programmes={x.get("id",""):x for x in rr.get("channels",[]) if x.get("id")}
        except Exception:
            rotana_art_programmes={}

    # Shahid/MBC public live-channel discovery.
    path=OUT/"commercial-arab-platforms.json"
    if path.exists():
        try:
            data=json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data={}
        shahid=(data.get("platforms") or {}).get("shahid") or {}
        blob=json.dumps(shahid,ensure_ascii=False)
        urls=sorted(set(re.findall(r'https://shahid\.mbc\.net/en/livestream/[^"\\]+/livechannel-\d+',blob,re.I)))
        for u in urls:
            m=re.search(r'/livestream/([^/]+)/livechannel-(\d+)',u,re.I)
            if not m:
                continue
            slug=m.group(1)
            chid=m.group(2)
            key=("shahid",chid)
            if key in seen:
                continue
            seen.add(key)
            name=slug.replace("-"," ").replace("’","'").strip()
            name=" ".join(w.capitalize() if not w.upper().startswith("MBC") else w.upper() for w in name.split())
            aliases={
                "Mbc1":"MBC1","Mbc Drama":"MBC Drama","Mbc Masr":"MBC Masr",
                "Al Arabiya":"Al Arabiya","Al Hadath":"Al Hadath",
                "Boq’at Daw’ Channel":"Boq’at Daw’ Channel",
                "Boq'at Daw' Channel":"Boq’at Daw’ Channel",
            }
            name=aliases.get(name,name)
            rows.append({
                "country":"MENA","name":name,"id":f"shahid.livechannel.{chid}",
                "provider":"shahid","source":"public-livestream-discovery",
                "future_programmes":0,"future_hours":0,"desc_pct":0,
                "sample_title":"","sample_desc":"","title_source":"","desc_source":"",
                "title_desc_policy":"DISCOVERY_ONLY","language_audit":"NOT_VALIDATED_YET",
                "alternatives":0,"alternative_sources":"","merged_alternatives":"",
                "url":u,"integration_status":"COMPARE_ONLY",
            })

    # Official Arab broadcaster channels confirmed by the deep probe.
    official_rows=[
        {
            "country":"Egypt","name":"Maspero Channel 1 / القناة الأولى",
            "id":"maspero.egypt.channel1","provider":"official","source":"maspero",
            "url":"https://www.maspero.eg/stream/2"
        },
        {
            "country":"Egypt","name":"Maspero Channel 2 / القناة الثانية",
            "id":"maspero.egypt.channel2","provider":"official","source":"maspero",
            "url":"https://www.maspero.eg/stream/3"
        },
        {
            "country":"Yemen","name":"Yemen TV / قناة اليمن",
            "id":"official.yemen.tv","provider":"official","source":"yementv",
            "url":"https://yementv.tv/live"
        },
        {
            "country":"Palestine","name":"Palestine TV",
            "id":"official.palestine.tv","provider":"official","source":"pbc",
            "url":"https://www.pbc.ps/live/"
        },
        {
            "country":"Palestine","name":"Palestine Mubasher / فلسطين مباشر",
            "id":"official.palestine.mubasher","provider":"official","source":"pbc",
            "url":"https://www.pbc.ps/palestinemubasherchannel/"
        },
        {
            "country":"Palestine","name":"Musawa / مساواة",
            "id":"official.palestine.musawa","provider":"official","source":"pbc",
            "url":"https://www.pbc.ps/musawa/"
        },
        {
            "country":"MENA","name":"Rotana Cinema KSA","id":"official.rotana.cinema.ksa","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Rotana Cinema Egypt","id":"official.rotana.cinema.egypt","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Rotana Comedy","id":"official.rotana.comedy","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Rotana Classic","id":"official.rotana.classic","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Rotana Drama","id":"official.rotana.drama","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Rotana Khalijia","id":"official.rotana.khalijia","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"LBC","id":"official.rotana.lbc","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Rotana Clip","id":"official.rotana.clip","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Rotana Music","id":"official.rotana.music","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"Al Resalah","id":"official.rotana.resalah","provider":"official","source":"rotana",
            "url":"https://www.rotana.net/tv/channels"
        },
        {
            "country":"MENA","name":"ART Aflam 1","id":"official.art.aflam1","provider":"official","source":"artonline",
            "url":"https://www.artonline.tv/guide"
        },
        {
            "country":"MENA","name":"ART Aflam 2","id":"official.art.aflam2","provider":"official","source":"artonline",
            "url":"https://www.artonline.tv/guide"
        },
        {
            "country":"MENA","name":"ART Hekayat","id":"official.art.hekayat","provider":"official","source":"artonline",
            "url":"https://www.artonline.tv/guide"
        },
        {
            "country":"MENA","name":"ART Cinema","id":"official.art.cinema","provider":"official","source":"artonline",
            "url":"https://www.artonline.tv/guide"
        },
        {
            "country":"MENA","name":"ART Hekayat 2","id":"official.art.hekayat2","provider":"official","source":"artonline",
            "url":"https://www.artonline.tv/guide"
        },
    ]
    for x in official_rows:
        key=(x["provider"],x["id"])
        if key in seen:
            continue
        seen.add(key)
        prog=rotana_art_programmes.get(x["id"],{})
        sample_desc=(prog.get("sample_desc") or "")
        rows.append({
            **x,
            "future_programmes":int(prog.get("programmes") or 0),
            "future_hours":float(prog.get("future_hours") or 0),
            "desc_pct":100.0 if sample_desc else 0.0,
            "sample_title":prog.get("sample_title") or "",
            "sample_desc":sample_desc,
            "title_source":f'{x["provider"]}:{x["source"]}:{x["id"]}' if prog.get("sample_title") else "",
            "desc_source":f'{x["provider"]}:{x["source"]}:{x["id"]}' if sample_desc else "",
            "title_desc_policy":"DISCOVERY_PROGRAMME_SAMPLE" if prog.get("sample_title") else "DISCOVERY_ONLY",
            "language_audit":"PROGRAMME_SAMPLE_FOUND" if prog.get("sample_title") else "NOT_VALIDATED_YET",
            "alternatives":0,"alternative_sources":"","merged_alternatives":"",
            "integration_status":"COMPARE_ONLY",
        })

    return rows

def rank(r):
    fp=min(int(float(r["future_programmes"])),300)
    fh=min(float(r["future_hours"]),168.0)
    desc=float(r["desc_pct"])
    score=fp*0.70 + fh*1.35 + desc*0.85 + lang_score(r) + provider_score(r)
    if LATAM_BAD.search(r["name"]+" "+r["id"]):
        score-=500
    return round(score,2)

def main():
    now=datetime.now(timezone.utc).isoformat()
    rows=list(csv.DictReader(ALL.open(encoding="utf-8")))
    rows=[r for r in rows if (r.get("provider",""),r.get("source","")) not in EXCLUDED_SOURCE_KEYS]
    for r in rows:
        r["future_programmes"]=int(float(r.get("future_programmes") or 0))
        r["future_hours"]=float(r.get("future_hours") or 0)
        r["desc_pct"]=float(r.get("desc_pct") or 0)
        r["canonical"]=norm(r["name"]) or norm(r["id"])
        r["rank_score"]=rank(r)

    # Dynamic zero-EPG blacklist: refreshed each run.
    zero=[r for r in rows if r["future_programmes"]<=0 or r["future_hours"]<=0]
    zero.sort(key=lambda x:(x["provider"],x["source"],x["name"].casefold()))
    zfields=["provider","source","id","name","future_programmes","future_hours","desc_pct","url"]
    with (OUT/"arab-fallback-zero-epg-blacklist.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=zfields+["reason"])
        w.writeheader()
        for r in zero:
            w.writerow({**{k:r.get(k,"") for k in zfields},"reason":"ZERO_FUTURE_EPG"})

    health=json.load(HEALTH.open(encoding="utf-8"))
    dynamic_quarantine=dynamic_quarantined_sources(health)
    source_black=[]
    for h in health.get("health",[]):
        if h.get("status")=="excluded":
            source_black.append({
                "provider":h["provider"],"source":h["source"],
                "reason":h.get("reason","EXCLUDED_SOURCE"),
                "detail":h.get("error","")
            })
        elif (h.get("provider"),h.get("source")) in dynamic_quarantine:
            source_black.append({
                "provider":h["provider"],"source":h["source"],
                "reason":"SOURCE_QUARANTINED_BAD_CHANNEL_PROGRAMME_MAPPING",
                "detail":f'{h.get("suspicious_clone_channels",0)} cloned channels / {h.get("channels",0)} total'
            })
        elif h.get("status")!="ok":
            source_black.append({
                "provider":h["provider"],"source":h["source"],"reason":"INVALID_SOURCE_XML",
                "detail":h.get("error","")
            })
        elif int(h.get("active_channels",0))==0:
            source_black.append({
                "provider":h["provider"],"source":h["source"],"reason":"ZERO_ACTIVE_CHANNELS",
                "detail":f'{h.get("channels",0)} channels, 0 active'
            })
    with (OUT/"arab-fallback-source-blacklist.json").open("w",encoding="utf-8") as f:
        json.dump({"generated_at":now,"sources":source_black},f,ensure_ascii=False,indent=2)
        f.write("\n")

    # Only active rows enter duplicate arbitration.
    active=[r for r in rows if r not in zero]
    groups=defaultdict(list)
    for r in active:
        groups[r["canonical"]].append(r)

    decisions=[]
    for canonical,arr in groups.items():
        arr=sorted(arr,key=lambda x:(x["rank_score"],x["future_programmes"],x["desc_pct"],x["future_hours"]),reverse=True)
        winner=arr[0]
        decisions.append({
            "canonical":canonical,
            "winner_provider":winner["provider"],
            "winner_source":winner["source"],
            "winner_id":winner["id"],
            "winner_name":winner["name"],
            "future_programmes":winner["future_programmes"],
            "future_hours":winner["future_hours"],
            "desc_pct":winner["desc_pct"],
            "rank_score":winner["rank_score"],
            "alternatives_count":len(arr)-1,
            "alternatives":";".join(
                f'{x["provider"]}:{x["source"]}:{x["id"]}:{x["rank_score"]}'
                for x in arr[1:]
            ),
            "decision":"BLACKLIST_LATAM" if LATAM_BAD.search(winner["name"]+" "+winner["id"]) else (
                "UNIQUE" if len(arr)==1 else "WINNER_OF_DUPLICATES"
            )
        })
    decisions.sort(key=lambda x:(x["decision"]=="BLACKLIST_LATAM",-x["rank_score"]))

    dfields=["canonical","winner_provider","winner_source","winner_id","winner_name",
             "future_programmes","future_hours","desc_pct","rank_score",
             "alternatives_count","alternatives","decision"]
    with (OUT/"arab-fallback-duplicate-decisions.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=dfields); w.writeheader(); w.writerows(decisions)

    direct_ids,direct_names,protected_direct_names=load_direct_catalogue()

    # New IDs list = current unmapped candidates after analysis, excluding obvious
    # LATAM pollution and excluding zero-EPG IDs.
    unmapped=list(csv.DictReader(UNMAPPED.open(encoding="utf-8")))
    raw_new=[]
    zero_keys={(r["provider"],r["source"],r["id"]) for r in zero}
    for r in unmapped:
        if (r["provider"],r["source"],r["id"]) in zero_keys:
            continue
        if (r.get("provider",""),r.get("source","")) in EXCLUDED_SOURCE_KEYS:
            continue
        if (r.get("provider",""),r.get("source","")) in dynamic_quarantine:
            continue
        if (r.get("provider",""),r.get("source","")) == ("openepg","qatar6"):
            continue
        if LATAM_BAD.search((r.get("name") or "")+" "+(r.get("id") or "")):
            continue
        sample_blob=((r.get("sample_title") or "")+" "+(r.get("sample_desc") or "")).strip()
        name_blob=((r.get("name") or "")+" "+(r.get("id") or "")).strip()
        rid=(r.get("id") or "").strip().casefold()
        rname=(r.get("name") or "").strip()
        if BAD_SAMPLE_RE.search(sample_blob):
            continue
        if rid in KNOWN_BAD_FALLBACK_IDS:
            continue
        if JUNK_NAME_RE.search(name_blob) or RADIO_DATA_RE.search(name_blob):
            continue
        if BEIN_IMPLICIT_RE.search(rname) or BEIN_IMPLICIT_RE.search((r.get("id") or "")):
            continue
        if (r.get("country") or "").casefold()=="qatar" and (AFC_BEIN_RE.search(rname) or AFC_BEIN_RE.search((r.get("id") or ""))):
            continue
        if "alkass" in name_blob.casefold() and ALKASS_BAD_SAMPLE_RE.search((r.get("sample_title") or "").strip()):
            continue
        # Families already covered by dedicated healthy direct feeds must
        # never be promoted from fallback. This includes beIN, OSN, MBC,
        # Rotana, Dubai/DMI, ADM/Abu Dhabi and Morocco/SNRT/2M.
        family=direct_family_match(r)
        if family:
            continue
        raw_new.append(r)

    # Merge all fallback variants by real channel identity.
    # Arabic EPG is mandatory first. English can be accepted only after 4 audits.
    lang_groups=defaultdict(list)
    for r in raw_new:
        lang_groups[bilingual_key(r)].append(r)

    # Cross-source index used by audits 2 and 3.
    all_by_key=defaultdict(list)
    for r in rows:
        all_by_key[bilingual_key(r)].append(r)
        n=norm(r.get("name") or "")
        if n:
            all_by_key[n].append(r)

    merged=[]
    english_audit=[]
    language_duplicates_removed=0
    english_rejected_for_arabic=0
    english_accepted_after_4_audits=0

    for canonical,arr in lang_groups.items():
        # Audit 1: Arabic candidate inside the immediate duplicate group.
        arabic_local=[x for x in arr if has_arabic_epg(x)]

        # Audit 2: Arabic candidate for same canonical channel across every fallback source.
        cross_candidates=all_by_key.get(canonical,[])
        arabic_cross=[x for x in cross_candidates if has_arabic_epg(x) and int(float(x.get("future_programmes") or 0))>0]

        # Audit 3: Arabic candidate through normalized/alias channel name.
        alias_keys={norm(x.get("name") or "") for x in arr}
        arabic_alias=[]
        for key in alias_keys:
            if not key:
                continue
            arabic_alias.extend(
                x for x in all_by_key.get(key,[])
                if has_arabic_epg(x) and int(float(x.get("future_programmes") or 0))>0
            )

        # Explicit per-channel source overrides requested after manual verification.
        override=None
        for x in arr+cross_candidates:
            xid=(x.get("id") or "").strip().casefold()
            xname=(x.get("name") or "").strip().casefold()
            target=SOURCE_OVERRIDES.get(xid) or SOURCE_OVERRIDES.get(xname)
            if target and (x.get("provider"),x.get("source"))==target and int(float(x.get("future_programmes") or 0))>0:
                override=x
                break

        # Prefer the strongest Arabic pool found by the first three audits.
        arabic_pool=[]
        seen_ar=set()
        for x in arabic_local+arabic_cross+arabic_alias:
            k=(x.get("provider",""),x.get("source",""),x.get("id",""))
            if k not in seen_ar:
                seen_ar.add(k); arabic_pool.append(x)

        pool=[override] if override is not None else (arabic_pool if arabic_pool else arr)
        pool=sorted(
            pool,
            key=lambda r:(
                language_rank(r),
                -epg_arabic_score(r),
                -int(float(r.get("future_programmes") or 0)),
                -float(r.get("desc_pct") or 0),
                -float(r.get("future_hours") or 0),
                -provider_score(r),
                (r.get("name") or "").casefold(),
                r.get("id") or ""
            )
        )
        winner=dict(pool[0])

        # Build a text-pairing pool from the same channel across all source
        # variants, not just the already-collapsed unmapped winner list.
        text_pool=[]
        seen_text=set()
        for x in arr+cross_candidates+arabic_alias:
            k=(x.get("provider",""),x.get("source",""),x.get("id",""))
            if k in seen_text:
                continue
            seen_text.add(k)
            text_pool.append(x)
        winner=choose_bilingual_event_fields(text_pool,winner)

        # Audit 4: if winner is English/non-Arabic, accept only when all prior
        # Arabic searches failed AND content itself contains no meaningful Arabic alternative.
        is_english=language_rank_marker(winner)==2 or epg_arabic_score(winner)<5.0
        audit_record={
            "canonical":canonical,
            "winner_id":winner.get("id",""),
            "winner_name":winner.get("name",""),
            "provider":winner.get("provider",""),
            "source":winner.get("source",""),
            "audit1_local_arabic":bool(arabic_local),
            "audit2_cross_source_arabic":bool(arabic_cross),
            "audit3_alias_arabic":bool(arabic_alias),
            "audit4_content_arabic_pct":round(epg_arabic_score(winner),1),
            "english_candidate":is_english,
            "decision":"ARABIC_SELECTED" if arabic_pool else "ENGLISH_ACCEPTED_AFTER_4_AUDITS",
        }

        if is_english and arabic_pool:
            english_rejected_for_arabic+=1
            audit_record["decision"]="ENGLISH_REJECTED_ARABIC_AVAILABLE"
        elif is_english:
            english_accepted_after_4_audits+=1

        winner["merge_key"]=canonical
        winner["language_audit"]=audit_record["decision"]
        winner["merged_alternatives"]=";".join(
            f'{x.get("provider","")}:{x.get("source","")}:{x.get("id","")}'
            for x in arr if (x.get("provider"),x.get("source"),x.get("id")) != (winner.get("provider"),winner.get("source"),winner.get("id"))
        )
        merged.append(winner)
        english_audit.append(audit_record)
        language_duplicates_removed += max(0,len(arr)-1)

    with (OUT/"arab-fallback-language-audit.csv").open("w",newline="",encoding="utf-8") as f:
        afields=["canonical","winner_id","winner_name","provider","source",
                 "audit1_local_arabic","audit2_cross_source_arabic","audit3_alias_arabic",
                 "audit4_content_arabic_pct","english_candidate","decision"]
        w=csv.DictWriter(f,fieldnames=afields); w.writeheader(); w.writerows(english_audit)


    # Remove anything already covered by a healthy priority/direct source.
    # Match exact XMLTV ID plus normalized/bilingual channel identity.
    new=[]
    already_covered=[]
    for r in merged:
        direct_matches=[]
        if (r.get("id") or "") in direct_ids:
            direct_matches.append({"id":r["id"],"feed":"exact-id","name":r.get("name","")})
        keys=set()
        for raw in (r.get("name") or "",r.get("id") or "",bilingual_key(r)):
            keys.update(direct_identity_keys(raw))
        # Explicit hard protection for OSN, beIN, ElCinema, Sport24 and Morocco/2M/SNRT.
        for key in keys:
            if key in protected_direct_names:
                direct_matches.extend(protected_direct_names[key])
        # Also keep the generic exact/normalized protection for any other healthy direct feed.
        if not direct_matches:
            for key in keys:
                if key in direct_names:
                    direct_matches.extend(direct_names[key])

        if direct_matches:
            seen=set()
            uniq=[]
            for m in direct_matches:
                k=(m["id"],m["feed"])
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(m)
            rr=dict(r)
            rr["covered_by"]=";".join(f'{m["feed"]}:{m["id"]}' for m in uniq)
            already_covered.append(rr)
        else:
            new.append(r)

    # Final missing-ID list only: alphabetical by channel name.
    new.sort(key=lambda r:((r.get("name") or "").casefold(),(r.get("id") or "").casefold()))
    already_covered.sort(key=lambda r:((r.get("name") or "").casefold(),(r.get("id") or "").casefold()))

    nfields=["country","name","id","provider","source","future_programmes","future_hours","desc_pct","sample_title","sample_desc","title_source","desc_source","title_desc_policy","language_audit","alternatives","alternative_sources","merged_alternatives","url","integration_status"]
    compare_only=load_compare_only_channels()
    with (OUT/"new-arab-epg-ids.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=nfields); w.writeheader()
        for r in new:
            rr=dict(r)
            rr["integration_status"]="VALIDATED_MISSING"
            w.writerow({k:rr.get(k,"") for k in nfields})
        for r in compare_only:
            w.writerow({k:r.get(k,"") for k in nfields})
    (OUT/"new-arab-epg-ids.txt").write_text(
        "\n".join(f'{r["id"]}\t{r["name"]}\t{r["provider"]}:{r["source"]}' for r in new)+"\n",
        encoding="utf-8"
    )

    covered_fields=nfields+["covered_by"]
    with (OUT/"arab-fallback-already-covered.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=covered_fields); w.writeheader()
        for r in already_covered:
            w.writerow({k:r.get(k,"") for k in covered_fields})

    counts=Counter(x["decision"] for x in decisions)
    summary={
        "generated_at":now,
        "active_rows":len(active),
        "zero_epg_blacklisted_ids":len(zero),
        "source_blacklist":source_black,
        "canonical_groups":len(groups),
        "duplicate_groups":sum(1 for a in groups.values() if len(a)>1),
        "duplicate_extra_rows":sum(max(0,len(a)-1) for a in groups.values()),
        "decision_counts":dict(counts),
        "merged_fallback_winners_before_direct_filter":len(merged),
        "already_covered_by_direct_sources":len(already_covered),
        "bein_family_fallback_policy":"ALWAYS_EXCLUDE_USE_DIRECT_BEIN",
        "protected_direct_families":["bein","osn","mbc","rotana","dubai_dmi","adm","morocco","sport24"],
        "protected_direct_feeds":["bein","osn","elcinema","sport24","2m","morocco","snrt"],
        "missing_ids_final":len(new),
        "compare_only_channels":len(compare_only),
        "new_ids_after_zero_and_latam_filter":len(new),
        "language_duplicates_removed":language_duplicates_removed,
        "language_policy":"ARABIC_FIRST_ENGLISH_ONLY_AFTER_4_AUDITS",
        "event_text_policy":"EN_TITLE_AR_DESC_ONLY_FOR_MULTINATIONAL_CHANNELS",
        "english_rejected_for_arabic":english_rejected_for_arabic,
        "english_accepted_after_4_audits":english_accepted_after_4_audits,
        "sort_order":"CHANNEL_NAME_ASC",
        "excluded_sources":["epgshare:AR1"],
        "quarantined_sources":[f"{p}:{s}" for p,s in sorted(dynamic_quarantine)],
        "integration_policy":{
            "healthy_direct_feed":"always_keep",
            "fallback":"only_missing_ids_not_covered_by_any_healthy_direct_source",
            "duplicate":"use winner; keep alternatives for failover",
            "zero_epg":"dynamic_blacklist_until_future_programmes_return",
            "source_zero_or_invalid":"source_blacklist"
        }
    }
    (OUT/"arab-fallback-final-analysis.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    lines=["# Arab fallback final analysis","",
           f'Active fallback rows: **{len(active)}**  ',
           f'Zero-EPG IDs blacklisted: **{len(zero)}**  ',
           f'Canonical groups: **{len(groups)}**  ',
           f'Duplicate groups: **{summary["duplicate_groups"]}**  ',
           f'Extra duplicate rows collapsed: **{summary["duplicate_extra_rows"]}**  ',
           f'New IDs after zero/LatAm filtering: **{len(new)}**',"",
           "## Source blacklist","",
           "| Provider | Source | Reason | Detail |","|---|---|---|---|"]
    for s in source_black:
        lines.append(f'| {s["provider"]} | {s["source"]} | {s["reason"]} | {s["detail"]} |')
    lines += ["","## Integration rule","",
              "Healthy direct sources are never replaced. Fallback winners are only candidates for channels that still have zero EPG. Duplicate alternatives remain available as failover; zero-programme IDs stay dynamically blacklisted until they recover future programmes."]
    (OUT/"arab-fallback-final-analysis.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

    print(json.dumps(summary,ensure_ascii=False))
    print("NEW_IDS",len(new))
    print("ZERO_BLACKLIST",len(zero))
    print("DUPLICATE_GROUPS",summary["duplicate_groups"])
    return 0

if __name__=="__main__":
    raise SystemExit(main())
