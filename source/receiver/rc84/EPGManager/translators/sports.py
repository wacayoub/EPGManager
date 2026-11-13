# -*- coding: utf-8 -*-
"""Sports-aware Arabic normalization used by beIN Sports and future feeds.

beta93 goals:
- Prefer deterministic local translation over generic machine translation.
- Preserve Arabic text already supplied by a broadcaster.
- Translate the *structure* of sports listings (competition, round, leg,
  episode metadata) while keeping match team names in English.
- Preserve the literal `vs` separator and standalone `Live` status in match titles.
- Never invent a translation for an unknown club/player: unknown proper names
  stay unchanged instead of becoming misleading Arabic.
"""

from __future__ import unicode_literals

import html
import re


LEAGUE_MAP = {
    # Football - Europe
    "english premier league": "الدوري الإنجليزي الممتاز",
    "premier league english": "الدوري الإنجليزي الممتاز",
    "premier league": "الدوري الإنجليزي الممتاز",
    "epl": "الدوري الإنجليزي الممتاز",
    "spanish league - primera división": "الدوري الإسباني",
    "spanish league - primera division": "الدوري الإسباني",
    "spanish laliga": "الدوري الإسباني",
    "la liga": "الدوري الإسباني",
    "laliga": "الدوري الإسباني",
    "uefa champions league": "دوري أبطال أوروبا",
    "champions league": "دوري أبطال أوروبا",
    "ucl": "دوري أبطال أوروبا",
    "uefa europa league": "الدوري الأوروبي",
    "europa league": "الدوري الأوروبي",
    "uel": "الدوري الأوروبي",
    "uefa conference league": "دوري المؤتمر الأوروبي",
    "conference league": "دوري المؤتمر الأوروبي",
    "fa community shield": "درع الاتحاد الإنجليزي",
    "community shield": "درع الاتحاد الإنجليزي",
    "fa cup": "كأس الاتحاد الإنجليزي",
    "carabao cup": "كأس الرابطة الإنجليزية",
    "efl cup": "كأس الرابطة الإنجليزية",
    "english football league skybet - championship": "دوري البطولة الإنجليزية",
    "english league - championship": "دوري البطولة الإنجليزية",
    "efl championship": "دوري البطولة الإنجليزية",
    "championship": "دوري البطولة الإنجليزية",
    "bundesliga": "الدوري الألماني",
    "serie a": "الدوري الإيطالي",
    "french league - ligue 1": "الدوري الفرنسي",
    "french ligue 1": "الدوري الفرنسي",
    "ligue 1": "الدوري الفرنسي",
    "french league - ligue 2": "الدوري الفرنسي الدرجة الثانية",
    "french ligue 2": "الدوري الفرنسي الدرجة الثانية",
    "ligue 2": "الدوري الفرنسي الدرجة الثانية",
    "2. bundesliga": "الدوري الألماني الدرجة الثانية",
    "serie b": "الدوري الإيطالي الدرجة الثانية",
    "laliga 2": "الدوري الإسباني الدرجة الثانية",
    "segunda división": "الدوري الإسباني الدرجة الثانية",
    "segunda division": "الدوري الإسباني الدرجة الثانية",
    "french super cup": "كأس السوبر الفرنسي",
    "trophée des champions": "كأس السوبر الفرنسي",
    "trophee des champions": "كأس السوبر الفرنسي",
    "uefa super cup": "كأس السوبر الأوروبي",
    "turkish super league": "الدوري التركي الممتاز",
    "süper lig": "الدوري التركي الممتاز",
    "super lig": "الدوري التركي الممتاز",
    "eredivisie": "الدوري الهولندي",
    "primeira liga": "الدوري البرتغالي",
    "scottish premiership": "الدوري الإسكتلندي",

    # Football - international / MENA / continental
    "world cup qualifiers": "تصفيات كأس العالم",
    "fifa world cup qualifiers": "تصفيات كأس العالم",
    "fifa world cup": "كأس العالم",
    "world cup": "كأس العالم",
    "euro qualifiers": "تصفيات أمم أوروبا",
    "uefa european championship": "بطولة أمم أوروبا",
    "euro": "بطولة أمم أوروبا",
    "copa america": "كوبا أمريكا",
    "africa cup of nations": "كأس أمم إفريقيا",
    "afcon": "كأس أمم إفريقيا",
    "afc asian cup": "كأس آسيا",
    "copa del rey": "كأس ملك إسبانيا",
    "qatar stars league": "الدوري القطري",
    "saudi pro league": "الدوري السعودي للمحترفين",
    "uae pro league": "دوري أدنوك للمحترفين",
    "afc champions league elite": "دوري أبطال آسيا للنخبة",
    "afc champions league two": "دوري أبطال آسيا 2",
    "afc champions league": "دوري أبطال آسيا",
    "caf champions league": "دوري أبطال إفريقيا",
    "caf confederation cup": "كأس الكونفدرالية الإفريقية",
    "conmebol sudamericana": "كوبا سودامريكانا",
    "copa sudamericana": "كوبا سودامريكانا",
    "copa libertadores": "كوبا ليبرتادوريس",
    "international friendly": "مباراة دولية ودية",

    # Other sports
    "nba": "دوري كرة السلة الأمريكي NBA",
    "atp tour": "جولة ATP للتنس",
    "atp 1000": "بطولة ATP 1000",
    "atp 500": "بطولة ATP 500",
    "atp 250": "بطولة ATP 250",
    "wta tour": "جولة WTA للتنس",
    "formula 1": "فورمولا 1",
    "formula one": "فورمولا 1",
    "motogp": "موتو جي بي",
    "caf u17 africa cup of nations": "كأس أمم إفريقيا تحت 17 سنة",
    "africa cup of nations u17": "كأس أمم إفريقيا تحت 17 سنة",
    "qatar amir cup": "كأس أمير قطر",
    "amir cup": "كأس أمير قطر",
    "french handball starligue": "الدوري الفرنسي لكرة اليد",
    "lnh division 1": "الدوري الفرنسي لكرة اليد",
    "wimbledon": "بطولة ويمبلدون",
    "us open": "بطولة أمريكا المفتوحة للتنس",
    "u.s. open": "بطولة أمريكا المفتوحة للتنس",
    "world athletics championships": "بطولة العالم لألعاب القوى",
    "t100 triathlon": "بطولة T100 للترايثلون",

    # Generic categories (kept last by longest-first replacement anyway)
    "sports general": "رياضة عامة",
    "football": "كرة القدم",
    "basketball": "كرة السلة",
    "basket ball": "كرة السلة",
    "tennis": "التنس",
    "handball": "كرة اليد",
    "volleyball": "الكرة الطائرة",
    "athletics": "ألعاب القوى",
    "boxing": "الملاكمة",
    "baseball": "البيسبول",
}


PROGRAM_MAP = {
    # beIN SPORTS NEWS official/on-air Arabic names and high-confidence aliases.
    # Exact/long phrases are intentionally listed before shorter generic tokens;
    # _replace_phrase_map() also sorts by length.
    "the issue of the day": "الشوط الثالث",
    "nine o'clock bulletin": "نشرة التاسعة",
    "nine o’clock bulletin": "نشرة التاسعة",
    "six o'clock bulletin": "نشرة السادسة",
    "six o’clock bulletin": "نشرة السادسة",
    "news bulletin": "نشرة الأخبار",
    "fifa world cup 2026 stats": "إحصائيات كأس العالم FIFA 2026",
    "world cup 2026 stats": "إحصائيات كأس العالم 2026",
    "best goals world cup 2026": "أفضل أهداف كأس العالم 2026",
    "fifa world cup 2026 - arab participation": "المشاركة العربية في كأس العالم FIFA 2026",
    "fifa world cup 2026 - biggest disappointments": "أبرز خيبات كأس العالم FIFA 2026",
    "a day of world cup 2026": "يوم من كأس العالم 2026",
    "special coverage world cup 2026": "تغطية خاصة لكأس العالم 2026",
    "technical study group experts press conference": "المؤتمر الصحفي لخبراء مجموعة الدراسات الفنية",
    "best of bein world cup coverage": "أفضل تغطيات beIN لكأس العالم",
    "best of bein": "أفضل ما قدمته beIN",
    "md plus": "مونديال بلس",
    "countdown": "العد التنازلي",
    "al hassad": "الحصاد",
    "al hassila": "الحصيلة",
    "al jawla - live studio": "الجولة",
    "al jawla": "الجولة",

    # Recurring beIN SPORTS 1..9 magazine / replay labels.
    "ligue 1 weekly review": "المراجعة الأسبوعية للدوري الفرنسي",
    "ligue 1 show": "برنامج الدوري الفرنسي",
    "epl weekly review": "المراجعة الأسبوعية للدوري الإنجليزي الممتاز",
    "epl review": "مراجعة الدوري الإنجليزي الممتاز",
    "epl netbusters": "أهداف الدوري الإنجليزي الممتاز",
    "epl the big interview": "المقابلة الكبرى - الدوري الإنجليزي الممتاز",
    "epl match officials mic'd up": "حكام الدوري الإنجليزي الممتاز بالميكروفون",
    "epl match officials mic’d up": "حكام الدوري الإنجليزي الممتاز بالميكروفون",
    "this is paris": "هذا هو باريس",
    "epl moments": "لحظات الدوري الإنجليزي الممتاز",
    "behind the scene": "خلف الكواليس",
    "behind the scenes": "خلف الكواليس",
    "in the footsteps of": "على خطى",
    "film of the season": "فيلم الموسم",
    "destiny": "المصير",
    "club's history": "تاريخ النادي",
    "club’s history": "تاريخ النادي",
    "world cup journey": "رحلة كأس العالم",
    "morning session": "الفترة الصباحية",
    "evening session": "الفترة المسائية",
    "gentlemen’s singles": "فردي الرجال",
    "gentlemen's singles": "فردي الرجال",
    "ladies’ singles": "فردي السيدات",
    "ladies' singles": "فردي السيدات",
    "women’s singles": "فردي السيدات",
    "women's singles": "فردي السيدات",
    "men’s singles": "فردي الرجال",
    "men's singles": "فردي الرجال",
    "mini matches": "مباريات مختصرة",
    "weekly review": "المراجعة الأسبوعية",
    "season review": "مراجعة الموسم",
    "best goals": "أفضل الأهداف",
    "arab participation": "المشاركة العربية",
    "biggest disappointments": "أبرز خيبات الأمل",
    "press conference": "مؤتمر صحفي",
    "live studio": "الاستوديو المباشر",
    "part": "الجزء",
    "stats": "إحصائيات",
    "sprint qualifying": "تأهيل سباق السرعة",
    "free practice": "التجارب الحرة",
    "qualifying": "التجارب التأهيلية",
    "practice": "التجارب الحرة",
    "warm-up": "الإحماء",
    "warm up": "الإحماء",
    "pole position": "مركز الانطلاق الأول",
    "sprint": "سباق السرعة",
    "race": "السباق",
    "grand prix": "الجائزة الكبرى",

    "uefa champions league magazine": "UEFA مجلة - دوري أبطال أوروبا",
    "epl stories": "قصص الدوري الإنجليزي الممتاز",
    "epl season preview": "تقديم موسم الدوري الإنجليزي الممتاز",
    "champions league legends": "أساطير دوري أبطال أوروبا",
    "english premier league highlights": "ملخصات الدوري الإنجليزي الممتاز",
    "premier league highlights": "ملخصات الدوري الإنجليزي الممتاز",
    "la liga highlights": "ملخصات الدوري الإسباني",
    "ligue 1 highlights": "ملخصات الدوري الفرنسي",
    "season preview": "تقديم الموسم",
    "match preview": "تقديم المباراة",
    "mini match": "ملخص مختصر للمباراة",
    "extended highlights": "ملخص موسع",
    "match highlights": "ملخص المباراة",
    "highlights": "ملخصات",
    "pre-match": "قبل المباراة",
    "post-match": "بعد المباراة",
    "sports news": "أخبار الرياضة",
    "news bulletin": "نشرة الأخبار",
    "bulletin": "نشرة",
    "special interview": "مقابلة خاصة",
    "exclusive interview": "مقابلة حصرية",
    "special report": "تقرير خاص",
    "exclusive coverage of": "تغطية حصرية لـ",
    "live coverage of": "تغطية مباشرة لـ",
    "coverage of": "تغطية لـ",
    "on bein sports": "على beIN SPORTS",
    "magazine": "مجلة",
    "review": "مراجعة",
    "preview": "تقديم",
    "studio": "استوديو تحليلي",
    "football legends": "أساطير كرة القدم",
    "all the goals of world cup": "جميع أهداف كأس العالم",
    "all the goals of": "جميع أهداف",
    "story of fifa world cup": "قصة كأس العالم",
    "fifa film": "فيلم الفيفا",
    "road to the": "الطريق إلى",
    "episode": "حلقة",
    "play-offs": "الملحق",
    "playoffs": "الملحق",
    "play-off": "الملحق",
    "group stage": "دور المجموعات",
    "al hassad": "الحصاد",
    "al hassila": "الحصيلة",
    "aljawla": "الجولة",
    "al jawla": "الجولة",
    "the summary": "الموجز",
    "stories from the city": "قصص من المدينة",
    "from the city": "من المدينة",
    "stories": "قصص",
    "inside psg, the": "كواليس باريس سان جيرمان",
    "inside psg": "كواليس باريس سان جيرمان",
    "inside": "كواليس",
}


META_MAP = {
    "Round of 64": "دور الـ64",
    "Round of 32": "دور الـ32",
    "Round of 16": "دور الـ16",
    "Round of 8": "ربع النهائي",
    "Quarter Finals": "ربع النهائي",
    "Quarter Final": "ربع النهائي",
    "Quarterfinals": "ربع النهائي",
    "Quarterfinal": "ربع النهائي",
    "Semi Finals": "نصف النهائي",
    "Semi Final": "نصف النهائي",
    "Semifinals": "نصف النهائي",
    "Semifinal": "نصف النهائي",
    "Finals": "النهائي",
    "Final": "النهائي",
    "1st Leg": "الذهاب",
    "First Leg": "الذهاب",
    "2nd Leg": "الإياب",
    "Second Leg": "الإياب",
    "Matchday": "الجولة",
    "MD": "الجولة",
    "Week": "الجولة",
    "Group": "المجموعة",
    "Round": "الجولة",
    "R16": "دور الـ16",
    "R32": "دور الـ32",
    "QF": "ربع النهائي",
    "SF": "نصف النهائي",
}


# High-confidence names only. Unknown names intentionally remain Latin rather
# than being mangled by an unreliable phonetic transliterator.
ENTITY_MAP = {
    # England
    "Manchester United": "مانشستر يونايتد",
    "Manchester City": "مانشستر سيتي",
    "Manchester Utd": "مانشستر يونايتد",
    "Liverpool": "ليفربول",
    "Arsenal": "آرسنال",
    "Chelsea": "تشلسي",
    "Tottenham Hotspur": "توتنهام هوتسبير",
    "Tottenham": "توتنهام",
    "Newcastle United": "نيوكاسل يونايتد",
    "Aston Villa": "أستون فيلا",
    "Crystal Palace": "كريستال بالاس",
    "West Ham United": "وست هام يونايتد",
    "Everton": "إيفرتون",
    "Brentford": "برينتفورد",
    "Bournemouth": "بورنموث",
    "Fulham": "فولهام",
    "Wolverhampton Wanderers": "وولفرهامبتون",
    "Wolverhampton": "وولفرهامبتون",
    "Blackburn Rovers": "بلاكبيرن روفرز",
    "Middlesbrough": "ميدلزبره",
    "Sheffield United": "شيفيلد يونايتد",
    "Sheffield Utd": "شيفيلد يونايتد",
    "Birmingham City": "برمنغهام سيتي",
    "Birmingham": "برمنغهام",
    "Hull City": "هال سيتي",
    "Nottingham Forest": "نوتنغهام فورست",
    "Sunderland AFC": "سندرلاند",
    "Sunderland": "سندرلاند",
    "Derby County": "ديربي كاونتي",
    "Ipswich Town": "إيبسويتش تاون",
    "Brighton & Hove Albion": "برايتون أند هوف ألبيون",
    "Brighton and Hove Albion": "برايتون أند هوف ألبيون",
    "Brighton": "برايتون",
    "Leeds United": "ليدز يونايتد",
    "Spurs": "توتنهام",
    "Coventry City": "كوفنتري سيتي",
    "Lincoln City": "لينكولن سيتي",
    "Lincoln": "لينكولن",

    # Spain
    "Real Madrid": "ريال مدريد",
    "FC Barcelona": "برشلونة",
    "Barcelona": "برشلونة",
    "Atlético de Madrid": "أتلتيكو مدريد",
    "Atletico de Madrid": "أتلتيكو مدريد",
    "Atletico Madrid": "أتلتيكو مدريد",
    "Espanyol": "إسبانيول",
    "Sevilla": "إشبيلية",
    "Rayo Vallecano": "رايو فاييكانو",
    "Real Sociedad": "ريال سوسييداد",
    "Real Betis": "ريال بيتيس",
    "Villarreal": "فياريال",
    "Valencia": "فالنسيا",
    "Athletic Bilbao": "أتلتيك بلباو",
    "Athletic Club": "أتلتيك بلباو",
    "Girona": "جيرونا",
    "Getafe": "خيتافي",
    "Osasuna": "أوساسونا",
    "Mallorca": "مايوركا",
    "Celta Vigo": "سيلتا فيغو",
    "Elche": "إلتشي",
    "Málaga": "مالقة",
    "Malaga": "مالقة",

    # France / Germany / Italy / Portugal
    "Paris Saint-Germain": "باريس سان جيرمان",
    "Paris Saint Germain": "باريس سان جيرمان",
    "PSG": "باريس سان جيرمان",
    "Olympique Marseille": "مارسيليا",
    "Marseille": "مارسيليا",
    "Olympique Lyonnais": "ليون",
    "Lyon": "ليون",
    "Monaco": "موناكو",
    "Lens": "لانس",
    "Rennes": "رين",
    "Bayern Munich": "بايرن ميونيخ",
    "Bayern München": "بايرن ميونيخ",
    "Borussia Dortmund": "بوروسيا دورتموند",
    "Bayer Leverkusen": "باير ليفركوزن",
    "Juventus": "يوفنتوس",
    "Inter Milan": "إنتر ميلان",
    "Inter": "إنتر",
    "AC Milan": "ميلان",
    "Napoli": "نابولي",
    "Roma": "روما",
    "Benfica": "بنفيكا",
    "Porto": "بورتو",
    "Sporting CP": "سبورتينغ لشبونة",

    # Türkiye / Greece / Europe
    "Fenerbahce": "فنربخشة",
    "Fenerbahçe": "فنربخشة",
    "Galatasaray": "غلطة سراي",
    "Besiktas": "بشكتاش",
    "Beşiktaş": "بشكتاش",
    "Trabzonspor": "طرابزون سبور",
    "Kasimpasa": "قاسم باشا",
    "Kasımpaşa": "قاسم باشا",
    "Corum": "تشوروم",
    "Çorum": "تشوروم",
    "Celtic": "سيلتيك",
    "AEK Athens": "أيك أثينا",
    "LASK": "لاسك",
    "Levski Sofia": "ليفسكي صوفيا",
    "Bodo/Glimt": "بودو/غليمت",
    "Bodø/Glimt": "بودو/غليمت",

    # South America - recurring beIN competitions
    "Fluminense": "فلومينينسي",
    "São Paulo": "ساو باولو",
    "Sao Paulo": "ساو باولو",
    "Bolívar": "بوليفار",
    "Bolivar": "بوليفار",
    "Corinthians": "كورينثيانز",
    "Santos": "سانتوس",
    "Rosario Central": "روساريو سنترال",
    "Independiente Rivadavia": "إنديبندينتي ريفادافيا",
    "Macará": "ماكارا",
    "Macara": "ماكارا",

    # MENA clubs
    "Al Hilal": "الهلال",
    "Al Nassr": "النصر",
    "Al Ahli": "الأهلي",
    "Al Ittihad": "الاتحاد",
    "Al Sadd": "السد",
    "Al Duhail": "الدحيل",
    "Al Rayyan": "الريان",
    "Wydad": "الوداد",
    "Raja Casablanca": "الرجاء الرياضي",
    "Raja": "الرجاء الرياضي",

    # Countries / national teams
    "Morocco": "المغرب",
    "Egypt": "مصر",
    "Algeria": "الجزائر",
    "Tunisia": "تونس",
    "Qatar": "قطر",
    "Saudi Arabia": "السعودية",
    "United Arab Emirates": "الإمارات",
    "UAE": "الإمارات",
    "Brazil": "البرازيل",
    "Argentina": "الأرجنتين",
    "France": "فرنسا",
    "Spain": "إسبانيا",
    "England": "إنجلترا",
    "Germany": "ألمانيا",
    "Italy": "إيطاليا",
    "Portugal": "البرتغال",
    "Netherlands": "هولندا",
    "Belgium": "بلجيكا",
    "Turkey": "تركيا",
}


# Canonical reverse map used for beIN match *titles*.  beta88 keeps club and
# national-team names in English even when a broadcaster returns a known Arabic
# spelling.  setdefault() intentionally keeps the first/canonical English key
# when ENTITY_MAP contains aliases such as PSG / Paris Saint-Germain.
ENTITY_AR_TO_EN = {}
for _english_name, _arabic_name in ENTITY_MAP.items():
    ENTITY_AR_TO_EN.setdefault(_arabic_name, _english_name)

# Prefer familiar display forms for a few aliases.
ENTITY_AR_TO_EN.update({
    "برشلونة": "Barcelona",
    "باريس سان جيرمان": "Paris Saint-Germain",
    "مانشستر يونايتد": "Manchester United",
    "أتلتيكو مدريد": "Atletico Madrid",
    "ميلان": "AC Milan",
    "إنتر": "Inter Milan",
    "الرجاء الرياضي": "Raja Casablanca",
})


def _restore_entities_english(text):
    for ara, eng in sorted(ENTITY_AR_TO_EN.items(), key=lambda x: len(x[0]), reverse=True):
        text = text.replace(ara, eng)
    return text


COUNTRY_CODE_MAP = {
    "ARG": "الأرجنتين", "BRA": "البرازيل", "BOL": "بوليفيا",
    "ECU": "الإكوادور", "COL": "كولومبيا", "URU": "الأوروغواي",
    "PAR": "باراغواي", "CHI": "تشيلي", "PER": "بيرو", "VEN": "فنزويلا",
    "ENG": "إنجلترا", "SCO": "إسكتلندا", "WAL": "ويلز", "IRL": "أيرلندا",
    "FRA": "فرنسا", "ESP": "إسبانيا", "GER": "ألمانيا", "DEU": "ألمانيا",
    "ITA": "إيطاليا", "POR": "البرتغال", "NED": "هولندا", "BEL": "بلجيكا",
    "TUR": "تركيا", "GRE": "اليونان", "AUT": "النمسا", "SUI": "سويسرا",
    "CRO": "كرواتيا", "SRB": "صربيا", "BUL": "بلغاريا", "POL": "بولندا",
    "MAR": "المغرب", "EGY": "مصر", "ALG": "الجزائر", "TUN": "تونس",
    "QAT": "قطر", "KSA": "السعودية", "SAU": "السعودية", "UAE": "الإمارات",
    "USA": "الولايات المتحدة", "MEX": "المكسيك", "CAN": "كندا",
    "JPN": "اليابان", "KOR": "كوريا الجنوبية", "AUS": "أستراليا",
}


_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def contains_arabic(text):
    return bool(_ARABIC_RE.search(str(text or "")))


def _clean(text):
    text = html.unescape(str(text or ""))
    text = text.replace("\u200f", "").replace("\u200e", "").replace("\ufeff", "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _replace_phrase_map(text, mapping):
    for eng, ara in sorted(mapping.items(), key=lambda x: len(x[0]), reverse=True):
        # Lookarounds are safer than \b for phrases containing punctuation,
        # accents or digits and still avoid replacing a token inside a word.
        pattern = r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(eng)
        text = re.sub(pattern, ara, text, flags=re.IGNORECASE)
    return text


def _replace_entities(text):
    for eng, ara in sorted(ENTITY_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(eng)
        text = re.sub(pattern, ara, text, flags=re.IGNORECASE)
    return text


def _replace_country_codes(text):
    def repl(match):
        code = match.group(1).upper()
        return "(%s)" % COUNTRY_CODE_MAP.get(code, code)
    return re.sub(r"\(([A-Z]{3})\)", repl, text)


def translate_metadata(meta_text):
    text = _clean(meta_text)
    if not text:
        return ""
    text = _replace_phrase_map(text, META_MAP)
    # "Week 2" -> "الجولة 2" is handled by map; these normalize a few
    # broadcaster variants not captured as exact phrases.
    text = re.sub(r"\b(?:Matchday|MD|Week)\s*#?\s*(\d+)\b", r"الجولة \1", text, flags=re.I)
    text = re.sub(r"\bRound\s+of\s+(\d+)\b", r"دور الـ\1", text, flags=re.I)
    text = re.sub(r"\bGroup\s+([A-Z]|\d{1,2})\b", r"المجموعة \1", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def extract_metadata(text):
    """Backward-compatible metadata extractor used by older source code."""
    text = _clean(text)
    extracted = []

    for pattern in (r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
                    r"\b20\d{2}/\d{2,4}\b", r"\b20\d{2}-\d{2,4}\b"):
        for match in list(re.finditer(pattern, text)):
            extracted.append(match.group(0))
        text = re.sub(pattern, " ", text)

    round_patterns = [
        r"\b(?:MD|Matchday|Week)\s*#?\s*\d+\b",
        r"\bRound\s+of\s+\d+\b",
        r"\b(?:R16|R32|QF|SF)\b",
        r"\b(?:Quarter\s*Finals?|Semi\s*Finals?|Finals?)\b",
        r"\b(?:1st|2nd|First|Second)\s+Leg\b",
        r"\bGroup\s+(?:[A-Z]|\d{1,2})\b",
    ]
    for pattern in round_patterns:
        matches = list(re.finditer(pattern, text, flags=re.I))
        for match in matches:
            extracted.append(match.group(0))
        text = re.sub(pattern, " ", text, flags=re.I)

    metadata = " | ".join(translate_metadata(v) for v in dict.fromkeys(extracted) if v).strip()
    text = re.sub(r"\s*[-:/|]\s*[-:/|]\s*", " - ", text)
    text = re.sub(r"^\s*[-:/|]\s*|\s*[-:/|]\s*$", "", text)
    return re.sub(r"\s+", " ", text).strip(), metadata


def _normalize_bein_program_title(text):
    """Conservative beIN programme-title normalizer.

    It translates recurring programme branding/structure while preserving
    unknown proper names in Latin. This is intentionally deterministic: if a
    subtitle/name is not known, it remains unchanged rather than being guessed.
    """
    text = _clean(text)
    if not text:
        return ""

    # Exact current NEWS titles whose actual Arabic on-air names differ from a
    # literal English translation.
    exact = {
        "the issue of the day": "الشوط الثالث",
        "nine o'clock bulletin": "نشرة التاسعة",
        "nine o’clock bulletin": "نشرة التاسعة",
        "six o'clock bulletin": "نشرة السادسة",
        "six o’clock bulletin": "نشرة السادسة",
        "news bulletin": "نشرة الأخبار",
        "al hassad": "الحصاد",
        "al hassila": "الحصيلة",
        "al jawla": "الجولة",
        "al jawla - live studio": "الجولة",
        "md plus": "مونديال بلس",
    }
    low = text.lower().strip()
    if low in exact:
        return exact[low]

    # beta149: current beIN magazine packages need semantic ordering rather
    # than independent token replacement.  The feed commonly publishes
    # ``UEFA Champions League Magazine - Preview``; rendering every token in
    # place produced the awkward ``مجلة دوري أبطال أوروبا - تقديم UEFA``.
    m = re.match(r"^UEFA\s+Champions\s+League\s+Magazine\b(.*)$", text, flags=re.I)
    if m:
        tail = (m.group(1) or "").strip(" -–—:|")
        if re.search(r"\bPreview\b", tail, flags=re.I):
            return "UEFA مجلة - تقديم دوري أبطال أوروبا"
        return "UEFA مجلة - دوري أبطال أوروبا"

    # RC15: deterministic motorsport rendering for common OpenEPG/beIN labels.
    # Keep unknown circuit/event proper names intact rather than guessing them,
    # while translating the race-session structure cleanly.
    motorsport_gp = {
        "Italian": "إيطاليا",
        "San Marino": "سان مارينو",
        "Qatar": "قطر",
        "Bahrain": "البحرين",
        "Saudi Arabian": "السعودية",
        "British": "بريطانيا",
        "Spanish": "إسبانيا",
        "French": "فرنسا",
        "Belgian": "بلجيكا",
        "Dutch": "هولندا",
        "Austrian": "النمسا",
        "Hungarian": "المجر",
        "Japanese": "اليابان",
        "Australian": "أستراليا",
        "Canadian": "كندا",
        "Brazilian": "البرازيل",
        "Mexican": "المكسيك",
        "Singapore": "سنغافورة",
        "Abu Dhabi": "أبوظبي",
        "United States": "الولايات المتحدة",
    }
    session_map = {
        "Qualifying": "التجارب التأهيلية",
        "Race": "السباق",
        "Practice": "التجارب الحرة",
        "Free Practice": "التجارب الحرة",
        "Sprint": "سباق السرعة",
        "Sprint Qualifying": "تأهيل سباق السرعة",
        "Warm Up": "الإحماء",
        "Warm-Up": "الإحماء",
        "Pole Position": "مركز الانطلاق الأول",
    }
    m = re.match(r"^(Formula\s*(?:1|One)|MotoGP)\s*[-–—]\s*(.+?)\s+Grand Prix(?:\s*[-–—]\s*(.+))?$", text, flags=re.I)
    if m:
        brand = "فورمولا 1" if re.match(r"Formula", m.group(1), flags=re.I) else "موتو جي بي"
        place_raw = (m.group(2) or "").strip()
        place = next((ar for en, ar in motorsport_gp.items() if place_raw.casefold() == en.casefold()), place_raw)
        out = "%s - جائزة %s الكبرى" % (brand, place)
        session_raw = (m.group(3) or "").strip()
        if session_raw:
            session = next((ar for en, ar in session_map.items() if session_raw.casefold() == en.casefold()), "")
            if session:
                out += " - %s" % session
            else:
                out += " - %s" % session_raw
        return out

    # Current US Open court blocks are not matches; they are schedule packages
    # such as ``Day 8 - Arthur Ashe Court - Tennis - US Open 2026``.  Reorder
    # them once here to avoid mixed RTL/LTR output like
    # ``اليوم 8 - Arthur Ashe Court - التنس US Open 2026``.
    if re.search(r"\bUS\s+Open\b", text, flags=re.I):
        year_m = re.search(r"\b(20\d{2})\b", text)
        day_m = re.search(r"\bDay\s*#?\s*(\d{1,2})\b", text, flags=re.I)
        court_m = re.search(r"\b(Arthur Ashe Court|Louis Armstrong Court|Grandstand Court)\b", text, flags=re.I)
        code_m = re.search(r"\b(D\d+M\d+)\b", text, flags=re.I)
        mini_group = bool(re.search(r"\bMini Matches?\b", text, flags=re.I)) and not bool(re.search(r"\s+(?:vs\.?|v\.?)\s+", text, flags=re.I))
        brand = "بطولة أمريكا المفتوحة للتنس"
        if year_m:
            brand += " %s" % year_m.group(1)
        if day_m and court_m and not re.search(r"\s+(?:vs\.?|v\.?)\s+", text, flags=re.I):
            return "%s - اليوم %s - %s" % (brand, day_m.group(1), court_m.group(1))
        if mini_group and code_m:
            return "%s - مباريات مختصرة - %s" % (brand, code_m.group(1).upper())

    # Keep the official ATP/WTA brand in Latin and translate only Magazine.
    # This matches the requested hybrid on-screen style: "ATP Tour - مجلة".
    m = re.match(r"^(ATP|WTA) Tour\s*-\s*Magazine(?:\s+(20\d{2}))?$", text, flags=re.I)
    if m:
        out = "%s Tour - مجلة" % m.group(1).upper()
        if m.group(2):
            out += " %s" % m.group(2)
        return out

    # Remove feed-only studio/time suffixes from NEWS programme names.
    m = re.match(r"^News Bulletin(?:\s*-\s*Live Studio)?(?:\s*@?\d{1,2}:\d{2})?$", text, flags=re.I)
    if m:
        return "نشرة الأخبار"
    m = re.match(r"^Al Jawla(?:\s*-\s*Live Studio)?(?:\s*@?\d{1,2}:\d{2})?$", text, flags=re.I)
    if m:
        return "الجولة"

    # Current World Cup NEWS packages. Dates are normally removed upstream by
    # extract_metadata(); these rules therefore target the clean programme name.
    m = re.match(r"^FIFA World Cup\s+(20\d{2})\s+Stats$", text, flags=re.I)
    if m:
        return "إحصائيات كأس العالم FIFA %s" % m.group(1)
    m = re.match(r"^(?:FIFA\s+)?World Cup\s+(20\d{2})\s*-\s*Arab Participation$", text, flags=re.I)
    if m:
        return "المشاركة العربية في كأس العالم FIFA %s" % m.group(1)
    m = re.match(r"^(?:FIFA\s+)?World Cup\s+(20\d{2})\s*-\s*Biggest disappointments$", text, flags=re.I)
    if m:
        return "أبرز خيبات كأس العالم FIFA %s" % m.group(1)
    m = re.match(r"^Best Goals World Cup\s+(20\d{2})$", text, flags=re.I)
    if m:
        return "أفضل أهداف كأس العالم %s" % m.group(1)
    m = re.match(r"^A Day of World Cup\s+(20\d{2})$", text, flags=re.I)
    if m:
        return "يوم من كأس العالم %s" % m.group(1)
    m = re.match(r"^Special Coverage World Cup\s+(20\d{2})$", text, flags=re.I)
    if m:
        return "تغطية خاصة لكأس العالم %s" % m.group(1)

    # Road-to-team packages shown on NEWS. Keep the feed's team number but put
    # all programme structure in Arabic.
    m = re.match(r"^FIFA\s+(?:WC|World Cup)\s+(20\d{2})\s+(Spain|England)\s+Road\s*[–—-]\s*Team\s*(\d+)$", text, flags=re.I)
    if m:
        country = "إسبانيا" if m.group(2).lower() == "spain" else "إنجلترا"
        return "طريق %s إلى كأس العالم FIFA %s - المنتخب %s" % (country, m.group(1), m.group(3))

    m = re.match(r"^World Cup Journey\s*[–—-]\s*FIFA World Cup\s+(20\d{2})\s+S(\d+)\s*\|\s*E(\d+)$", text, flags=re.I)
    if m:
        return "رحلة كأس العالم FIFA %s - الموسم %s - الحلقة %s" % (m.group(1), m.group(2), m.group(3))
    m = re.match(r"^Spain,?\s+behind the scenes? during (?:the )?World Cup\s+(20\d{2})$", text, flags=re.I)
    if m:
        return "إسبانيا خلف الكواليس خلال كأس العالم %s" % m.group(1)

    m = re.match(r"^World Athletics Championships\s+Tokyo\s+(20\d{2})\s*-\s*Day\s+(\d+)\s*[–—-]\s*(Morning|Evening) Session$", text, flags=re.I)
    if m:
        session = "الفترة الصباحية" if m.group(3).lower() == "morning" else "الفترة المسائية"
        return "بطولة العالم لألعاب القوى - طوكيو %s - اليوم %s - %s" % (m.group(1), m.group(2), session)
    # Common channel 1..9 magazine/replay programmes.
    m = re.match(r"^EPL Stories(?:\s+EP\.?\s*#?(\d+))?(?:\s*-\s*(.+))?$", text, flags=re.I)
    if m:
        subtitle = (m.group(2) or "").strip()
        if subtitle.lower() == "soaring seagulls":
            subtitle = "النوارس المحلّقة"
        parts = []
        if subtitle:
            parts.append(subtitle)
        if m.group(1):
            parts.append("الحلقة %s" % m.group(1))
        parts.append("قصص الدوري الإنجليزي الممتاز")
        return " - ".join(parts)
    m = re.match(r"^Ligue 1 Weekly Review$", text, flags=re.I)
    if m:
        return "المراجعة الأسبوعية للدوري الفرنسي"
    m = re.match(r"^Ligue 1 Show$", text, flags=re.I)
    if m:
        return "برنامج الدوري الفرنسي"
    m = re.match(r"^In the Footsteps of\s+(.+)$", text, flags=re.I)
    if m:
        return "على خطى %s" % m.group(1).strip()

    return text


def translate_sports_title(text):
    """Normalize a beIN sports title while keeping team names in English.

    Target example:
      Liverpool vs Nottingham Forest - الدوري الإنجليزي الممتاز - الجولة 3

    Competition/round/live metadata may be Arabic, but club/national-team
    entities and the `vs` separator stay Latin/English.
    """
    text = _clean(text)
    if not text:
        return ""

    text = text.replace("الرياضة العام", "").strip()

    text = _normalize_bein_program_title(text)

    # Hybrid ATP/WTA branding is already final after programme normalization.
    # Do not let LEAGUE_MAP rewrite "ATP Tour" into Arabic.
    if re.fullmatch(r"(?:ATP|WTA) Tour\s*-\s*مجلة(?:\s+20\d{2})?", text, flags=re.I):
        return re.sub(r"\s+", " ", text).strip(" -|")

    # Restore known Arabic team/country names only in actual match titles.
    # Programme names such as "المشاركة العربية" must stay Arabic.
    is_match_title = bool(re.search(r"\s+(?:vs\.?|v\.?)\s+|\s*ضد\s*", text, flags=re.I))
    if is_match_title:
        text = _restore_entities_english(text)

    # Translate only programme/competition/round structure.  For actual
    # matches, NEVER run the competition dictionary over the Team A vs Team B
    # segment itself: club names such as ``AFC Wimbledon`` are not evidence for
    # the tennis tournament Wimbledon.  Only trailing/leading metadata segments
    # around a dash/pipe are translated.
    if is_match_title:
        chunks = re.split(r"(\s+[-–—|]\s+)", text)
        rendered = []
        for chunk in chunks:
            if re.fullmatch(r"\s+[-–—|]\s+", chunk or ""):
                rendered.append(chunk)
                continue
            if re.search(r"\s+(?:vs\.?|v\.?)\s+|\s*ضد\s*", chunk or "", flags=re.I):
                rendered.append(chunk)
                continue
            chunk = _replace_phrase_map(chunk, PROGRAM_MAP)
            chunk = _replace_phrase_map(chunk, LEAGUE_MAP)
            chunk = translate_metadata(chunk)
            rendered.append(chunk)
        text = "".join(rendered)
    else:
        text = _replace_phrase_map(text, PROGRAM_MAP)
        text = _replace_phrase_map(text, LEAGUE_MAP)
        text = translate_metadata(text)
        text = _replace_entities(text)

    # User-facing match separator is always Latin `vs`.
    text = re.sub(r"\s+vs\.?\s+", " vs ", text, flags=re.I)
    text = re.sub(r"\s+v\.?\s+", " vs ", text, flags=re.I)
    text = re.sub(r"\s*ضد\s*", " vs ", text)

    text = re.sub(r"\bEP\.?\s*#?\s*(\d+)\b", r"الحلقة \1", text, flags=re.I)
    text = re.sub(r"\bEpisode\s*#?\s*(\d+)\b", r"الحلقة \1", text, flags=re.I)
    text = re.sub(r"\bDay\s*#?\s*(\d+)\b", r"اليوم \1", text, flags=re.I)
    text = re.sub(r"English\s+الدوري الإنجليزي الممتاز", "الدوري الإنجليزي الممتاز", text, flags=re.I)
    text = re.sub(r"French\s+الدوري الفرنسي", "الدوري الفرنسي", text, flags=re.I)
    text = re.sub(r"Spanish\s+الدوري الإسباني", "الدوري الإسباني", text, flags=re.I)
    text = re.sub(r"\bLive\b\s*:?\s*", "مباشر ", text, flags=re.I)

    text = re.sub(r"\s+-\s+-\s+", " - ", text)
    text = re.sub(r"\s*\|\s*", " | ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    return text.strip()


def translate_sports_text(text):
    """Return a clean Arabic-first sports label.

    Arabic supplied by beIN is preserved. Latin structural terms and known
    entities inside Arabic text are still normalized. Unknown proper names are
    retained verbatim so the EPG stays factually correct.
    """
    text = _clean(text)
    if not text:
        return ""

    # Common broadcaster noise from the legacy feed.
    text = text.replace("الرياضة العام", "").strip()
    text = _normalize_bein_program_title(text)

    text = _replace_country_codes(text)
    # Program phrases first: e.g. "EPL Stories" must become one natural
    # Arabic phrase before the shorter "EPL" competition token is replaced.
    text = _replace_phrase_map(text, PROGRAM_MAP)
    text = _replace_phrase_map(text, LEAGUE_MAP)
    text = translate_metadata(text)
    text = _replace_entities(text)

    # Match separator only when it is a standalone Latin token.
    text = re.sub(r"\s+vs\.?\s+", " ضد ", text, flags=re.I)
    text = re.sub(r"\s+v\.?\s+", " ضد ", text, flags=re.I)

    # Episode markers that commonly appear in beIN listings.
    text = re.sub(r"\bEP\.?\s*#?\s*(\d+)\b", r"الحلقة \1", text, flags=re.I)
    text = re.sub(r"\bEpisode\s*#?\s*(\d+)\b", r"الحلقة \1", text, flags=re.I)
    text = re.sub(r"\bDay\s*#?\s*(\d+)\b", r"اليوم \1", text, flags=re.I)

    # Normalize variants introduced by overlapping source labels.
    text = re.sub(r"English\s+الدوري الإنجليزي الممتاز", "الدوري الإنجليزي الممتاز", text, flags=re.I)
    text = re.sub(r"French\s+الدوري الفرنسي", "الدوري الفرنسي", text, flags=re.I)
    text = re.sub(r"Spanish\s+الدوري الإسباني", "الدوري الإسباني", text, flags=re.I)
    text = re.sub(r"\bLive\b\s*:?\s*", "مباشر ", text, flags=re.I)

    # Punctuation/spacing cleanup without disturbing Arabic glyphs.
    text = re.sub(r"\s+-\s+-\s+", " - ", text)
    text = re.sub(r"\s*\|\s*", " | ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    return text.strip()


def translate_text(text):
    """Backward-compatible public function."""
    return translate_sports_text(text)


def get_league_arabic(text):
    text = _clean(text)
    if not text:
        return ""
    lower = text.lower()
    for english_name, arabic_name in sorted(LEAGUE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if english_name.lower() in lower:
            return arabic_name
    # If source is already Arabic, return a known competition phrase only
    # when we can identify it exactly enough to avoid broad false positives.
    arabic_values = sorted(set(LEAGUE_MAP.values()), key=len, reverse=True)
    for arabic_name in arabic_values:
        if arabic_name in text:
            return arabic_name
    return ""
