# -*- coding: utf-8 -*-
"""Deterministic XMLTV-ID -> channel-name bridge for beta41.

This module resolves presentation/language differences only.  It never creates
or guesses DVB Service References; SRPs still come exclusively from the live
receiver lamedb/lamedb5 registry.
"""
from __future__ import print_function

import re
import unicodedata


# Full XMLTV-ID corrections validated by the operator.  These are checked
# BEFORE stripping the country/source suffix, because the same Arabic label can
# mean different channels in different OpenEPG catalogues (e.g. الثقافية.ps
# vs الثقافية.ae).  Values are still only channel identities; the real SRP is
# always taken from the live receiver lamedb.
_SOURCE_EXACT = {
    "medi1tv_ar.ma": "Medi1 TV Arabic",
    "medi1tv_maghreb.ma": "Medi1 TV Maghreb",
    "روتانا أفلام بلاس.eg": "Rotana Aflam +",
    "روتانا+ أتش دي.eg": "Rotana + HD",
    "الثقافية.ps": "TV8 EDHAKIRA",
    "الجزائرية الخامسة القرآن.ps": "TV 5 CORAN",
    "الجزائرية الخامسة  القرآن.ps": "TV 5 CORAN",
    "الجزائرية الرابعة.ps": "TV 4 TAMAZIGHT",
    "تلفزيون العالم الأبدي.ps": "Al Alam HD",
    "bein sports-boxoffice-bein.com.qa": "beIN BOX OFFICE 1",
    # rc44: source-ID-specific disambiguation for current direct ElCinema IDs.
    # Generic Arabic labels are ambiguous across countries: الثانية is also used
    # for Morocco 2M, while عَمان/عمان can be confused with Oman TV.
    "althania.sy@sd": "Al Thania TV",
    "ammantv.jo@sd": "Amman TV",
    "lbc.sa@sd": "LBC SAT",
    "lbcinternational.lb@sd": "LBC International",
    "bein4k.qa@sd": "beIN 4K",
    "beinsports4khdr.qa": "beIN SPORTS 4K HDR",
    # rc55: stable Dubai+ official XMLTV IDs. These are identities only;
    # Service References still come from the receiver registry.
    "dubaitv.ae@sd": "Dubai TV",
    "samadubai.ae@sd": "Sama Dubai",
    "dubaisports1.ae@sd": "Dubai Sports 1",
    "dubaisports2.ae@sd": "Dubai Sports 2",
    "dubairacing1.ae@sd": "Dubai Racing 1",
    "dubairacing2.ae@sd": "Dubai Racing 2",
    "noordubaitv.ae@sd": "Noor Dubai TV",
    "dubaione.ae@sd": "Dubai One",
    "dubaizaman.ae@sd": "Dubai Zaman",
}

# Exact channel-name translations/aliases observed in OpenEPG MENA catalogues.
# Values are conservative Latin service identities that can be validated against
# the live lamedb by srp_master_engine.
_EXACT = {
    # Egypt1 Arabic -> service identity
    "آي فيلم": "IFilm Arabic",
    "أبو ظبي": "Abu Dhabi TV",
    "أو إس إن شو كايس": "OSNtv Showcase",
    "أو إس إن فاميلي موفيز": "OSNtv Movies Family",
    "أو إس إن كرايم": "OSNtv Crime",
    "أو إس إن كوميدي": "OSNtv Comedy",
    "أو إس إن كيدز": "OSNtv Kids",
    "أو إس إن موفيز أكشن": "OSNtv Movies Action",
    "أو إس إن موفيز بريميير": "OSNtv Movies Premiere",
    "أو إس إن موفيز كوميدي": "OSNtv Movies Comedy",
    "أو إس إن موفيز هوليوود": "OSNtv Movies Hollywood",
    "أو إٍس إن ناو": "OSNtv Now",
    "أو إس إن ناو": "OSNtv Now",
    "أو إس إن وان": "OSNtv One",
    "أو إس إن ياهلا أفلام": "OSNtv Yahala Aflam",
    "أو إس إن ياهلا بالعربي": "OSNtv Yahala Bil Arabi",
    "أو إس إن ياهلا": "OSNtv Yahala",
    "أون إي": "ON E",
    "أون دراما": "ON Drama",
    "إس بي سي": "SBC",
    "إل بي سي آي": "LBC International",
    "إل بي سي": "LBC SAT",
    "إم بي سي 1": "MBC1",
    "إم بي سي 2": "MBC 2",
    "إم بي سي 3": "MBC 3",
    "إم بي سي 4": "MBC 4",
    "إم بي سي 5": "MBC 5",
    "إم بي سي أكشن": "MBC Action",
    "إم بي سي العراق": "MBC IRAQ",
    "إم بي سي بوليوود": "MBC Bollywood",
    "إم بي سي دراما plus": "MBC Drama +",
    "إم بي سي دراما": "MBC Drama",
    "إم بي سي ماكس": "MBC MAX",
    "إم بي سي مصر 2": "MBC MASR 2",
    "إم بي سي مصر دراما": "MBC MASR DRAMA",
    "إم بي سي مصر": "MBC MASR",
    "إم بي سي": "MBC1",
    "إم تي في": "MTV Lebanon",
    "إي تي سي": "ETC TV",
    "ال تي في": "LTV",
    "ال دي سي": "LDC",
    "الأردن": "Jordan TV",
    "الأولى المغربية": "Al Aoula",
    "اﻹمارات": "Al Emarat TV",
    "الإمارات": "Al Emarat TV",
    "البحرين": "Bahrain TV",
    "التلفزيون العربي 2": "Al Araby 2",
    "الثانية": "2M",
    "الجديد": "Al Jadeed",
    "الحياة": "Al Hayat",
    "الرشيد": "Al Rasheed TV",
    "السعودية": "Saudi TV",
    "السعيدة": "Al Saeeda",
    "السومرية": "Alsumaria",
    "الشارقة": "Sharjah TV",
    "الشرقية": "Al Sharqiya",
    "الشروق الجزائرية": "Echorouk TV",
    "الشمس": "Al Shams",
    "الظفرة": "Al Dafrah",
    "العراقية": "Iraqia TV",
    "الفجيرة": "Fujairah TV",
    "القاهرة والناس 2": "Al Kahera Wal Nas 2",
    "القاهرة والناس": "Al Kahera Wal Nas",
    "الكويت": "Kuwait TV 1",
    "المحور": "El Mehwar",
    "المصرية": "Al Masriya",
    "المغربية الثانية": "2M",
    "المنار": "Al Manar",
    "النهار دراما": "Al-Nahar Drama",
    "النهار": "Al-Nahar One",
    "الوسط": "Al Wousta",
    "بي إن دراما": "beIN DRAMA",
    "بي إن سيريس إتش دي 2": "beIN SERIES 2",
    "بي إن سيريس": "beIN SERIES 1",
    "بي إن موفيز أكشن": "beIN MOVIES 2",
    "بي إن موفيز بريمير": "beIN MOVIES 1",
    "بي إن موفيز دراما": "beIN MOVIES 3",
    "بي إن موفيز فاميلي": "beIN MOVIES 4",
    "تلفزيون حضرموت": "Hadramaut TV",
    "تن": "TeN TV",
    "دبي زمان": "Dubai Zaman",
    "دبي وان": "Dubai One",
    "دبي": "Dubai TV",
    "دجلة": "Dijlah TV",
    "دي إم سي دراما": "DMC Drama",
    "دي إم سي": "DMC",
    "ديسكفري": "Discovery",
    "ذكريات": "Thikrayat",
    "رؤيا": "Roya TV",
    "روتانا أفلام بلاس": "Rotana Aflam +",
    "روتانا خليجية": "Rotana Khalijia",
    "روتانا دراما": "Rotana Drama",
    "روتانا سينما مصر": "Rotana Cinema EGY",
    "روتانا سينما": "Rotana Cinema",
    "روتانا كلاسيك": "Rotana Classic",
    "روتانا كوميدي": "Rotana Comedy",
    "روتانا plus أتش دي": "Rotana+ HD",
    "زي ألوان": "Zee Alwan",
    "ستار أكشن": "Star Action",
    "ستار موفيز": "Star Movies",
    "ستار ورلد": "Star World",
    "سلام": "Salam TV",
    "سما دبي": "Sama Dubai",
    "سي بي سي دراما": "CBC Drama",
    "سي بي سي سفرة": "CBC Sofra",
    "سي بي سي": "CBC",
    "سيما": "Cima",
    "صدى البلد 2": "Sada El Balad 2",
    "صدى البلد دراما": "Sada ElBalad 3",
    "صدى البلد": "Sada El Balad",
    "عمان": "Oman TV General",
    "عَمان": "Oman TV General",
    "قطر": "Qatar TV",
    "قناة مصر الأولى": "Egypt Channel 1",
    "ماسبيرو زمان": "Maspero Zaman",
    "ماسة": "Masah",
    "ميكس بالعربي": "Mix Bel Araby",
    "ميكس وان": "Mix One",
    "ميكس": "Mix",
    "ناشونال جيوجرافيك أبو ظبي": "National Geographic Abu Dhabi",
    "ناشيونال جيوجرافيك": "National Geographic",
    "نايل دراما": "Nile Drama",
    "نايل لايف": "Nile Life",
    "نتورك بالعربية": "Cartoon Network Arabic",
    "نسمة": "Nessma",
    "وان دراما": "ON Drama",
    "وان موفيز": "One Movies",
    "يو تي في": "UTV Iraq",

    # Arabic IDs seen in UAE / Palestine catalogues.
    "الإخبارية": "Al Ekhbariya",
    "الثقافية": "Al Thaqafiya",
    "الحدث": "Al Hadath",
    "السعودية الآن": "Saudia Alaan TV",
    "السنة النبوية": "Saudi CH For Sunnah",
    "الشرق": "Asharq News",
    "العربية": "Al Arabiya",
    "العربية business": "Al Arabiya Business",
    "القرآن الكريم": "Saudi CH For Quran",
    "بداية": "Bedaya",
    "وناسه": "Wanas TV",
    "وناسة": "Wanas TV",
    "الأولى": "Al Aoula",
    "الأمازيغية": "Tamazight",
    "الرياضية": "Arryadia",
    "السادسة": "Assadissa",
    "المغربية": "Al Maghribia",
    "العيون": "Laayoune TV",
    "الجزيرة الإنجليزية": "Al Jazeera English",
    "العربية": "Al Arabiya",
    "سكاي نيوز عربية": "Sky News Arabia",
    "بي بي سي عربي": "BBC Arabic",
    "تي ار تي عربي تركيا": "TRT Arabi",
    "تي آر تي عربي تركيا": "TRT Arabi",
    "فرنسا 24": "France 24 Arabic",
    "فرانس 24 (انجليزي)": "France 24 English",
    "قناة دبي": "Dubai TV",
    "قناة السعودية تي في": "Saudi TV",
    "قناة السعودية القرآن الكريم": "Saudi CH For Quran",
    "قناة السعودية السنة النبوية": "Saudi CH For Sunnah",
    "تلفزيون قَطَر‎": "Qatar TV",
    "عُمان القناة العامة": "Oman TV General",
    "عُمان  القناة العامة": "Oman TV General",
    "كردستان ٢٤": "Kurdistan 24",
    "التلفزيون الأردني": "Jordan TV",
    "السودان تي في": "Sudan TV",
    "المياديين": "Al Mayadeen",
    "الميادين": "Al Mayadeen",
    "بحرين تي في": "Bahrain TV",
    "تلفزيون الشارقة": "Sharjah TV",
    "إيران إنترناشيونال": "Iran International",
    "تلفزيون بلومبرغ": "Bloomberg",
    "تلفزيون داي ستار": "Daystar",
    "تي في 5 موند أوروبا": "TV5MONDE EUROPE",
    "تي في إي الدولية إتش دي": "TVE Internacional",
    "تيلـيسـور": "Telesur",
    "دي دبليو روسيا": "DW Russian",
    "آر تي في آي": "RTVI",
    "آر تي في سان مارينو": "San Marino RTV",
    "آرتي إنفيستيمينتي": "Arte Investimenti",
    "عالم أريرانج": "Arirang",
    "كي بي اس الدولي": "KBS WORLD",
    "راي جالب": "Rai Gulp",
    "راي سبورت بلس": "Rai Sport",
    "راي ستوريا": "Rai Storia",
    "راي سكولا": "Rai Scuola",
    "فيديولينا": "Videolina",
    "كلاس تي في مودا": "Class TV Moda",
    "تي في آر إنترناشيونال": "TVR International",
    "تي في بي إنفو": "TVP INFO",
    "تي في إس": "TVS",
    "تيلي 5": "TELE 5",
    "يورونيوز الإنجليزية": "Euronews English",
    "يورونيوز إيطاليانو": "Euronews Italian",
    "راديو إيطاليا تي في": "Radio Italia TV",
    "راديو إيطاليا لايف": "Radio Italia Live",
    "راديو فريتشا": "Radio Freccia",
    "راديو نوربا تي في": "Radio Norba TV",
    "روناهي tv": "Ronahi TV",
    "سالفايشن تي في مينا": "Salvation TV MENA",
    "مانجو": "Mango",
    "ميلادي": "Milady",
    "باور تي في": "POWER TV",
    "أوتدور تشانل": "Outdoor Channel",
    "أنجل تيفي أوروبا": "Angel TV Europe",
    "غوردون لايف": "GORDON Live",
    "سـفوبودا": "SVOBODA",
    "سـفوبودا نيوز": "SVOBODA NEWS",
    "+سـفوبودا": "SVOBODA+",
    "ورلد فيشينج نتورك": "World Fishing Network",
    "ورلد بوكر تور": "World Poker Tour",
    "وايلد تي في": "Wild TV",
    "سي جي تي أن": "CGTN",
    "سي جي تي أن العربية": "CGTN Arabic",
    "سي جي تي أن الفرنسية": "CGTN French",
    "سي جي تي أن الروسية": "CGTN Russian",
    "سي جي تي أن الأسبانية": "CGTN Spanish",
    "سي جي تي أن الوثائقية": "CGTN Documentary",
    "قناة الاولى ۔ الكويت": "Kuwait TV 1",
    "قناة الأولى الكويت": "Kuwait TV 1",
    "الوطنية ۔ تونس تي في ١": "Tunisia 1",
}

# Exact Latin/source presentation mismatches that are safe and recurring.
_LATIN = {
    "2m tv": "2M",
    "al aoula morocco": "Al Aoula",
    "al dafrah": "Al Dafrah",
    "al dafrah hd": "Al Dafrah",
    "al nahar tv": "Al-Nahar One",
    "hadhramaut tv": "Hadramaut TV",
    "kuwait": "Kuwait TV 1",
    "mehwar": "El Mehwar",
    "one drama": "ON Drama",
    "network arabic": "Cartoon Network Arabic",
    "osn ya hala aflam": "OSNtv Yahala Aflam",
    "osn ya hala": "OSNtv Yahala",
    "rotana cinema masr": "Rotana Cinema EGY",
    "rotana khalejia": "Rotana Khalijia",
    "saudiya tv": "Saudi TV",
    "saudi sports 1": "KSA Sports 1",
    "saudi sports 2": "KSA Sports 2",
    "saudi sports 3": "KSA Sports 3",
    "ksa now": "Saudia Alaan TV",
    "wousta from al dhaid": "Al Wousta from Al Dhaid",
    "tunisia al wataniya 2": "Tunisia 2",
    "al saudiah now": "Saudia Alaan TV",
    "al quran al kareem tv": "Saudi CH For Quran",
    "al sunnah al nabawiyah tv": "Saudi CH For Sunnah",
    "thekrayat": "Zekrayat",
    "zekrayat": "Zekrayat",
    "cn arabic": "Cartoon Network Arabic",
    "nat geographic": "National Geographic",
    "nat. geographic": "National Geographic",
    "wanasah": "Wanas TV",
    "zekrayat": "Thikrayat",
    "thekrayat": "Thikrayat",
    "al rai": "Alrai TV",
    "asharq": "Asharq News Channel",
    "cnn international": "CNN International",
    "al resalah": "AL Resala",
    "al resalah hd": "AL Resala",
    "al masriya": "AL Masryia",
    "al saeeda": "AlSaeedah",
    "cbc extra news": "Extra News",
    "cnbc arabia": "CNBC Arabiya",
    "mbc variety": "MBC+ Variety",
    "mbc maser": "MBC MASR",
    "mbc maser 2": "MBC MASR 2",
    "rotana mousica": "Rotana Music",
    "alhayat mosalsalat": "Alhayat Muslsalat",
    "almajd holy quran": "Almajd 09",
    "almajd holy hadeeth": "Almajd 08",
    "almajd islamic science": "Almajd 07",
    "almajd news": "Almajd 10",
    "almajd documentary": "Almajd 11",
    "almajd nature": "Almajd 12",
    "masah": "Almajd 02",
    "basma": "Almajd 03",
    "taghreed": "Almajd 04",
    "majd": "Almajd 05",
    "rawda": "Almajd 06",
    "dal radio": "Almajd 13",
    "al ajaweed": "Almajd 14",
    "al majd tv": "Almajd 01",
    "majid kids tv": "Majd Kids TV",
    "nile tv international": "Nile TV",
    "nile live": "Nile Life",
    "sada al balad": "Sada El Balad",
    "kapatid tv5 international": "Kapatid TV5",
    "dzmm teleradyo": "TeleRadyo",
    "c and i": "C I",
    "france 24 arabic": "France 24 (in Arabic)",
    "france 24 eng": "France 24 (in English)",
    "bbc world news": "BBC News",
    "bbc world": "BBC News",
    "oman mubasher": "Oman TV Live",
    "oman tv mubashir": "Oman TV Live",
    "oman al thakafeyah": "Oman TV Culture",
    "ktv channel 1": "Kuwait TV 1",
    "ktv channel 2": "Kuwait TV 2",
    "ktv sports 2": "Kuwait Sport Plus",
    "palestinian channel": "Palestine",
    "tv 5 monde maghreb": "TV5MONDE MAGHREB ORIENT",
    "al iraqia (el ekhbariya)": "Iraqia News",
}

_ASSET_EXACT = {
    "0001 awg cn brand logo 1color black 4c": "Cartoon Network",
    "0004 cnn international logo svg": "CNN International",
    "aljazeera documentary 1": "Al Jazeera Documentary",
    "baby tv 2": "Baby TV",
    "bara3em 1": "Baraem",
    "bbcearth new 200x200": "BBC Earth",
    "bejunior 1": "beJunior",
    "bloomberg 1": "Bloomberg",
    "boxoffice1 digital": "beIN BOX OFFICE 1",
    "boxoffice2 digital": "beIN BOX OFFICE 2",
    "cartoon network arabic 1": "Cartoon Network Arabic",
    "cbeebies 1": "CBeebies",
    "discovery logo 1": "Discovery",
    "fatafeat 1": "Fatafeat",
    "food network logo 2018 1": "Food Network",
    "jeem hd 1 01": "Jeem TV",
    "movies1 premiere digital": "beIN MOVIES 1",
    "movies2 action digital": "beIN MOVIES 2",
    "movies3 drama digital": "beIN MOVIES 3",
    "movies4 family digital": "beIN MOVIES 4",
    "series1 digital": "beIN SERIES 1",
    "series2 digital": "beIN SERIES 2",
    "drama stacked nov 2024 200x200": "beIN DRAMA",
    "4k digital": "beIN 4K",
    "star action 2024 200x200 1": "Star Action",
    "star movies 2024 200x200 1": "Star Movies",
    "star world 2024 200x200 1": "Star World",
    "updatez ngw full logo 20182": "Nat Geo Wild",
}

_RADIO_WORDS = ("radio", " fm", "fm ", "إذاعة", "idaa", "idaât", "alwatania.ma", "alamazighia.ma", "chaine inter")
_ONLINE_WORDS = (" online", "online ", "youtube", "web tv", "webtv")
_BAD_ASSET_WORDS = ("logo.svg", " logo ", "200x200", "1color", "colour-blue", "bein.com-05", "bein.com-06", "bein.com-07", "bein.com-08")


def _strip_suffix(value):
    text = str(value or "").strip()
    text = re.sub(r"\.(?:eg|qa|sa|ae|ps|ma|dz|tn|fr|es)$", "", text, flags=re.I)
    return text.strip()


def _plain(value):
    text = _strip_suffix(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("ـ", "")
    text = text.replace("+", " plus ")
    text = re.sub(r"[._/\\|:\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _asset_plain(value):
    text = _plain(value)
    text = re.sub(r"^2023\s+", "", text)
    text = re.sub(r"\.(?:svg|png|jpg|jpeg)$", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def source_override(channel_id):
    """Return an authoritative full XMLTV-ID identity override, if any."""
    return _SOURCE_EXACT.get(str(channel_id or "").strip().lower(), "")


def aliases(channel_id, display_name=""):
    """Return deterministic human channel-name candidates for one XMLTV row."""
    out = []
    def add(v):
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)

    full_id = str(channel_id or "").strip().lower()
    if full_id in _SOURCE_EXACT:
        # Exact operator-validated XMLTV-ID corrections are authoritative.
        # Do not also add a generic Arabic-name candidate that could point to
        # another broadcaster with the same label.
        add(_SOURCE_EXACT[full_id])
        return out

    for raw in (display_name, channel_id):
        p = _plain(raw)
        if p in _EXACT:
            add(_EXACT[p])
        if p in _LATIN:
            add(_LATIN[p])
        ap = _asset_plain(raw)
        if ap in _ASSET_EXACT:
            add(_ASSET_EXACT[ap])

        # beIN Qatar source assets often encode language/variant in the
        # filename rather than in a clean display-name.  These are exact,
        # deterministic rewrites and keep FR/EN/XTRA variants separate.
        m = re.search(r"\bbein\s+sports\s*xtra\s*0*([1-9])(?:\s+bein\s+com)?\b", ap, flags=re.I)
        if m:
            add("beIN SPORTS XTRA %d" % int(m.group(1)))
        m = re.search(r"\bbein\s+sports\s*([12])\s+english\b", ap, flags=re.I)
        if m:
            add("beIN SPORTS EN %d" % int(m.group(1)))
        m = re.search(r"\bbein\s+sports\s*([12])\s+french\b", ap, flags=re.I)
        if m:
            add("beIN SPORTS FR %d" % int(m.group(1)))
        if re.search(r"\bbein\s+sports6\s+digital\s+d\s*1\b", ap, flags=re.I) or re.search(r"\bbein\s+sports66\b", ap, flags=re.I):
            add("beIN SPORTS 6")

        # Local/OpenEPG Alkass assets: 2023_Alkass_1 .. 8.
        m = re.search(r"(?:^|\s)alkass\s*0*([1-8])(?:\s|$)", ap, flags=re.I)
        if m:
            add("Alkass %d" % int(m.group(1)))

        # Common source decorations hiding an otherwise clean broadcaster name.
        cleaned = ap
        cleaned = re.sub(r"\b(?:new|full|stacked)\b", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"\b(?:logo|digital|mono)\b", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"\b(?:2018|2024|200x200|01)\b", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and cleaned != ap and len(cleaned) >= 4 and not any(x in cleaned for x in ("1color", "colour blue")):
            add(cleaned)

    return out


def classify_hint(channel_id, display_name=""):
    """Classify obvious non-TV/asset IDs for diagnostics only."""
    text = " %s %s " % (str(channel_id or "").lower(), str(display_name or "").lower())
    if any(x in text for x in _RADIO_WORDS):
        return "NON-TV"
    if any(x in text for x in _ONLINE_WORDS):
        return "ONLINE/VIRTUAL"
    if any(x in text for x in _BAD_ASSET_WORDS) and not aliases(channel_id, display_name):
        return "BAD SOURCE ID"
    return ""
