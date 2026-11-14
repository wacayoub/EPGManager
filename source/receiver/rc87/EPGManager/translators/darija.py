# -*- coding: utf-8 -*-
"""Safe Arabic translator for the 2M local source.

beta52 keeps sentence translation safe but treats programme TITLES separately:
verified 2M Arabic title aliases win, clearly-French broadcast titles stay French,
and Moroccan Arabizi titles are converted to Arabic script instead of being
misinterpreted as French/English sentences.

Policy:
  * exact operator/official title dictionary wins;
  * no partial-substring title replacements;
  * structured prefixes (film/serie/documentaire/...) are translated safely;
  * Moroccan Arabizi titles use deterministic Arabic-script transliteration;
  * other unknown non-French titles use Google Translate with source-language auto detection;
  * descriptions use the same MSA Arabic translation and fall back to the
    original source text if the result is not clearly Arabic;
  * proper/brand titles on a small protected list stay untouched;
  * a new v2 cache is used so old bad Darija cache entries cannot leak back in.
"""

import re

from ..core.cache import JSONCache
from ..core.logger import get_logger
from .arabic import normalize_text, contains_latin_letters, contains_arabic
from .hygiene import is_mixed_sentence, is_arabic_first

log = get_logger(__name__)

EPG_DIR = "/etc/epgmanager_epg"
CACHE_PATH = "%s/translation_cache_ar_v4.json" % EPG_DIR
CUSTOM_DICT_PATH = "%s/custom_dictionary.json" % EPG_DIR
UNKNOWN_SHOWS_PATH = "%s/unknown_shows.json" % EPG_DIR
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"


class DarijaTranslator(object):
    """Compatibility class name retained for existing source imports.

    The output is now safe Modern Standard Arabic (MSA), not synthetic Darija.
    """

    def __init__(self, downloader=None, enable_network=True):
        if downloader is not None:
            self.downloader = downloader
        elif enable_network:
            from ..core.downloader import Downloader
            self.downloader = Downloader(retries=1, timeout=5)
        else:
            self.downloader = None

        self.cache = JSONCache(CACHE_PATH)
        self.custom_dict_cache = JSONCache(CUSTOM_DICT_PATH)
        self.unknown_shows_cache = JSONCache(UNKNOWN_SHOWS_PATH)

        # beta52: operator-requested French whitelist.  These are genuine
        # French/brand titles and must NEVER be translated to Arabic.
        self.french_shows_whitelist = {
            "auto moto", "grand angle", "rallye aicha", "pop up",
            "replay", "info soir", "infosoir", "eco news", "eclairages", "jt francais",
            "le journal", "meteo", "jt", "journal francais", "sport",
            "documentaire francais", "film francais", "magazine",
            "planete foot", "esme & roy", "mondial stories", "jamel comedy club",
            "bulletin meteo", "bulletin météo", "guide fitness",
            "y africa", "y'africa", "l auberge", "l'auberge", "salon shehrazade",
            "l amour aime les hasards", "des histoires et des hommes",
            "studio live", "vivi cat",
        }
        # Compatibility name used by older code/tests.
        self.protected_titles = set(self.french_shows_whitelist)

        # Strong French markers for titles not yet in the explicit whitelist.
        # Structured categories (Serie/Film/Documentaire...) are handled before
        # this heuristic so a Moroccan work title can still be Arabicized.
        self._french_words = {
            "le", "la", "les", "un", "une", "des", "du", "de", "avec",
            "pour", "sur", "dans", "en", "au", "aux", "soir", "journal",
            "francais", "francaise", "documentaire", "magazine", "film",
            "sport", "meteo", "replay", "info", "actualite", "actualites",
        }

        self.tv_prefixes = {
            "serie marocaine": "مسلسل مغربي",
            "serie turque": "مسلسل تركي",
            "serie": "مسلسل",
            "film tv": "فيلم تلفزي",
            "film marocain": "فيلم مغربي",
            "film": "فيلم",
            "documentaire": "وثائقي",
            "magazine": "برنامج",
            "best of": "أفضل ما في",
            "rediffusion": "إعادة",
        }

        # Human-curated recurring 2M titles from the operator source baseline.
        self.official_titles = {
            "al akhbar": "الأخبار",
            "journal amazigh": "الأخبار بالأمازيغية",
            "jt arabe": "الأخبار بالعربية",
            "al massaiya": "المسائية",
            "al dahira": "الظهيرة",
            "clips": "كليبات",
            "sabahiyat": "2M صباحيات",
            "sabahiyat 2m": "2M صباحيات",
            "rachid show": "رشيد شو",
            "kif el hal": "كيف الحال",
            "kif al hal": "كيف الحال",
            "moudawala": "مداولة",
            "stand up": "ستاند آب",
            "master chef": "ماستر شيف",
            "lmatch": "الماتش",
            "ma3a ramdani": "مع الرمضاني",
            "mubasharatan maakum": "مباشرة معكم",
            "massar": "مسار",
            "naghma watay": "نغمة وأتاي",
            "chhiwat bladi": "شهيوات بلادي",
            "maqtou3 men chajra": "مقطوع من شجرة",
            "chhiwa ma3a choumicha": "شهيوات مع شميشة",
            "3ailti": "عائلتي",
            "sahatna jmi3": "صحتنا جميع",
            "mawazine": "موازين",
            "asrar al mondial": "أسرار المونديال",
            "al barlamane wa annass": "البرلمان والناس",
            "zour bladek": "زور بلادك",
            "riad azzaytoune": "رياض الزيتون",
            "lmktoub": "المكتوب",
            "bghit hyatek": "بغيت حياتك",
            "kaina dorouf": "كاين ظروف",
            "jouj wjouh": "جوج وجوه",
            "dar nsa": "دار النسا",
            "jib darkom": "جيب داركم",
            "samhini": "سامحيني",
            "al waed": "الوعد",
            "3ayne libra": "عين ليبرا",
            "3ayn libra": "عين ليبرا",
            "alhane 3chaqnaha": "ألحان عشقناها",
            "alhane 3cha9naha": "ألحان عشقناها",
            "alhan 3chaqnaha": "ألحان عشقناها",
            # beta51 - aliases confirmed/curated against the official 2M Replay
            # catalogue.  Keep several common Telecablesat spellings.
            "al khobarae": "الخبراء",
            "al khobaraa": "الخبراء",
            "alkhobarae": "الخبراء",
            "alkhobaraa": "الخبراء",
            "wlad 3li": "ولاد علي",
            "wlad ali": "ولاد علي",
            "oulad 3li": "ولاد علي",
            "oulad ali": "ولاد علي",

            # beta52 - current 2M grid aliases. Prefer channel-used Arabic spellings
            # over generic machine translation/transliteration.
            "al amana": "الأمانة",
            "alamana": "الأمانة",
            "qalb aswad": "قلب أسود",
            "bahr addalam": "بحر الظلام",
            "bahr al dalam": "بحر الظلام",
            "bahr addhalam": "بحر الظلام",
            "bent bladi": "بنت بلادي",
            "twahachnak": "توحشناك",
            "sir al morjane": "سر المرجان",
            "sir al morjan": "سر المرجان",
            "najm acha3bi": "النجم الشعبي",
            "najm achaabi": "النجم الشعبي",
            "najm cha3bi": "النجم الشعبي",
            "najm chaabi": "النجم الشعبي",
            "chkoun yistatmar fmachrou3i": "شكون غادي يستثمر فمشروعي؟",
            "chkoune yistatmar fmachrou3i": "شكون غادي يستثمر فمشروعي؟",
            "chkoun yistatmar f machrou3i": "شكون غادي يستثمر فمشروعي؟",
            "chkoune yistatmar f machrou3i": "شكون غادي يستثمر فمشروعي؟",
            "dna al hayawanat": "DNA الحيوانات",
            "dna al hayawanat": "DNA الحيوانات",
            "hikayat fi al adghal": "حكايات في الأدغال",
            "hikayat fi al adgal": "حكايات في الأدغال",
            "chhiwat maa choumicha": "شهيوات مع شميشة",
            "ch hiwat maa choumicha": "شهيوات مع شميشة",
            "chhiwa maa choumicha": "شهيوات مع شميشة",
            "chef f daro": "شيف فدارو",
            "kan ya ma kan": "كان يا ما كان",
            "khir lblad": "خير لبلاد",
            "khir l blad": "خير لبلاد",
            "yid al fellah": "يد الفلاح",
            "yad al fellah": "يد الفلاح",
            "ahsane patissier": "أحسن Pâtissier",
            "ahsan patissier": "أحسن Pâtissier",
            "ahsane pâtissier": "أحسن Pâtissier",
            "ahsan pâtissier": "أحسن Pâtissier",
            "al mouhit al atlassi bahr al anouar": "المحيط الأطلسي: بحر الأنوار",
            "al mouhit al atlassi bahr al anwar": "المحيط الأطلسي: بحر الأنوار",
            "3lach la": "علاش لا؟",
            "lharba": "الهربة",
            "ghidae wa siha": "غذاء وصحة",
            "daif al akhbar": "ضيف الأخبار",
            "radar al mondial": "رادار المونديال",
            "tourouq al 3arifine": "طرق العارفين",
            "al islam 3amal wa soulouk": "الإسلام عمل وسلوك",
            "addine wa annass": "الدين والناس",
            "dari mektoubi": "داري مكتوبي",
            "hikayat chama": "حكايات شامة",
            "nass al khir": "ناس الخير",
            "hiwarate": "حوارات",
            "min qalb al jiha": "من قلب الجهة",
            "hadik hyati": "هاديك حياتي",
            "tariq al ward": "طريق الورد",
            "azzawaj attani": "الزواج الثاني",
            "chada al alhane": "شذى الألحان",
            "zina": "زينة",
            # beta111 - current September 2026 2M grid spellings cross-checked
            # against multiple public TV listings. These are exact aliases only.
            "tariq attahaddi": "طريق التحدي",
            "tariq al tahaddi": "طريق التحدي",
            "al wassit": "الوسيط",
            "al wasit": "الوسيط",
            "aqba lik": "عقبى ليك",
            "khoyout al qadar": "خيوط القدر",
            "khoyout el qadar": "خيوط القدر",
            "sa3at assaraha": "ساعة الصراحة",
            "saat assaraha": "ساعة الصراحة",
            "akhir tamane": "آخر ثمن",
            "akher kalam": "آخر كلام",
            "addam al machrouk": "الدم المشروك",
            "adam al machrouk": "الدم المشروك",
            "coran avec laureats": "القرآن مع المتوجين",
            "coran avec lauréats": "القرآن مع المتوجين",

            # beta149 - recurring 2M labels reported from the live September
            # grid.  They are exact programme-title aliases, not broad word
            # substitutions, so genuine French news brands remain protected.
            "charqi ou lgharbi": "شرقي أو غربي",
            "charqi ou gharbi": "شرقي أو غربي",
            "soiree chaabi": "سهرة شعبية",
            "soiree cha3bi": "سهرة شعبية",
            "attahssina": "التحصينة",
            "at tahssina": "التحصينة",
            "priere du vendredi": "صلاة الجمعة",
            "priere vendredi": "صلاة الجمعة",
            "ayne lkebrite": "عين الكبريت",
            "ayn lkebrite": "عين الكبريت",
            "ain lkebrite": "عين الكبريت",
            "ain el kebrite": "عين الكبريت",
            # Generic recurring labels - exact only, never substring.
        }
        self.official_titles.update(self.custom_dict_cache.as_dict())

    def flush(self):
        self.cache.save()
        self.unknown_shows_cache.save()

    def is_whitelisted_french(self, text):
        """True for an explicit operator-protected French broadcast title.

        Metadata such as "- Rediffusion" / season / episode must not make a
        protected title fall out of the whitelist.
        """
        base, _suffix = self.extract_metadata(str(text or ""))
        return normalize_text(base) in self.french_shows_whitelist

    def _is_clearly_french(self, text):
        """Conservative French-title detector.

        Do not classify Arabizi (3/5/7/9) as French.  One strong French token
        is enough for short labels such as "Le Journal"; otherwise require two
        markers so names are not accidentally frozen in Latin script.
        """
        raw = str(text or "").strip()
        norm = normalize_text(raw)
        if not norm:
            return False
        if norm in self.french_shows_whitelist:
            return True
        if re.search(r"[2356789]", norm):
            return False
        if re.search(r"[éèêëàâäçùûüôöîïœ]", raw.lower()):
            return True
        tokens = re.findall(r"[a-z]+", norm)
        markers = sum(1 for token in tokens if token in self._french_words)
        if markers >= 2:
            return True
        return bool(markers == 1 and len(tokens) <= 3)

    @staticmethod
    def _looks_moroccan_arabizi(text):
        norm = normalize_text(text)
        if not norm or not re.search(r"[a-z]", norm):
            return False
        if re.search(r"[2356789]", norm):
            return True
        tokens = set(norm.split())
        darija = {
            "wlad", "oulad", "bnat", "dar", "nsa", "jouj", "bghit",
            "hyatek", "lmktoub", "lmatch", "chhiwa", "chhiwat", "blad",
            "bladi", "moudawala", "sabahiyat", "ramdani", "maakum",
            "mubasharatan", "naghma", "atay", "mawazine", "samhini",
            "waed", "khobarae", "khobaraa", "kif", "hal",
            "qalb", "aswad", "bahr", "addalam", "bent", "twahachnak",
            "sir", "morjane", "najm", "acha3bi", "cha3bi", "chkoun",
            "chkoune", "yistatmar", "fmachrou3i", "hayawanat", "hikayat",
            "adghal", "chef", "daro", "khir", "lblad", "fellah",
        }
        return bool(tokens & darija)

    @staticmethod
    def _arabizi_to_arabic(text):
        """Small deterministic Moroccan Arabizi -> Arabic-script converter.

        It is intentionally a TITLE fallback, not a sentence translator. Exact
        official aliases are always preferred.  The mapping is tuned for 2M
        programme names (3=ع, 7=ح, 9=ق, kh=خ, ch/sh=ش, gh=غ).
        """
        src = str(text or "").strip().lower()
        if not src:
            return text
        # Common whole words first; this dramatically improves Moroccan titles.
        word_map = {
            "wlad":"ولاد", "oulad":"ولاد", "bnat":"بنات", "dar":"دار",
            "nsa":"النسا", "jouj":"جوج", "bghit":"بغيت", "hyatek":"حياتك",
            "lmktoub":"المكتوب", "lmatch":"الماتش", "kif":"كيف", "hal":"الحال",
            "ma3a":"مع", "maakum":"معكم", "bladi":"بلادي", "chhiwa":"شهيوة",
            "chhiwat":"شهيوات", "samhini":"سامحيني", "waed":"الوعد",
            "3li":"علي", "ali":"علي", "qalb":"قلب", "aswad":"أسود",
            "bahr":"بحر", "addalam":"الظلام", "bent":"بنت",
            "twahachnak":"توحشناك", "sir":"سر", "morjane":"المرجان",
            "najm":"النجم", "acha3bi":"الشعبي", "cha3bi":"الشعبي",
            "chkoun":"شكون", "chkoune":"شكون", "yistatmar":"يستثمر",
            "fmachrou3i":"فمشروعي", "machrou3i":"مشروعي",
            "hikayat":"حكايات", "fi":"في", "adghal":"الأدغال",
            "hayawanat":"الحيوانات", "chef":"شيف", "daro":"دارو",
            "khir":"خير", "lblad":"لبلاد", "fellah":"الفلاح",
        }
        pieces = re.split(r"(\s+|[-:/&+])", src)
        out=[]
        multi = [("kh","خ"),("gh","غ"),("ch","ش"),("sh","ش"),("ou","و"),("oo","و"),("ee","ي")]
        single = {
            "2":"ء", "3":"ع", "5":"خ", "6":"ط", "7":"ح", "8":"غ", "9":"ق",
            "a":"ا", "b":"ب", "c":"ك", "d":"د", "e":"", "f":"ف", "g":"ݣ",
            "h":"ه", "i":"ي", "j":"ج", "k":"ك", "l":"ل", "m":"م", "n":"ن",
            "o":"و", "p":"ب", "q":"ق", "r":"ر", "s":"س", "t":"ت", "u":"و",
            "v":"ڤ", "w":"و", "x":"كس", "y":"ي", "z":"ز",
        }
        for piece in pieces:
            if not piece or re.fullmatch(r"\s+|[-:/&+]", piece):
                out.append(piece); continue
            if piece in word_map:
                out.append(word_map[piece]); continue
            token=piece
            buf=[]; i=0
            while i < len(token):
                matched=False
                for seq, ar in multi:
                    if token.startswith(seq, i):
                        buf.append(ar); i += len(seq); matched=True; break
                if matched: continue
                buf.append(single.get(token[i], token[i])); i += 1
            out.append("".join(buf))
        result="".join(out)
        result=re.sub(r"\s+", " ", result).strip()
        return result or text

    def extract_metadata(self, text):
        saison, episode = "", ""
        s_match = re.search(r"(?i)saison\s*(\d+)", text)
        if s_match:
            saison = s_match.group(1)
            text = text[:s_match.start()] + text[s_match.end():]

        e_match = re.search(r"(?i)(?:épisode|episode|ep|ép)\s*(\d+)", text)
        if e_match:
            episode = e_match.group(1)
            text = text[:e_match.start()] + text[e_match.end():]

        text = re.sub(r"(?i)\s*-\s*rediffusion.*", "", text)
        text = re.sub(r"(?i)\s*rediffusion.*", "", text)
        text = re.sub(r"(?i)\s*-\s*rediff.*", "", text)
        text = re.sub(r"\s*[:\-]\s*$", "", text.strip())

        suffix = ""
        if saison:
            suffix += " الموسم %s" % saison
        if episode:
            suffix += " الحلقة %s" % episode
        return text.strip(), suffix

    @staticmethod
    def _strip_normalized_prefix(text, normalized_prefix):
        """Remove a prefix matched through :func:`normalize_text` while
        preserving the original spelling of the remaining programme title.

        This is intentionally accent-insensitive (``Série`` == ``serie``).
        A regex built from the normalized form would fail on accented source
        text and could leave the prefix duplicated in the final title.
        """
        words = str(text or "").strip().split()
        for count in range(1, min(len(words), 6) + 1):
            head = " ".join(words[:count]).strip(" :-–—")
            if normalize_text(head) == normalized_prefix:
                return " ".join(words[count:]).lstrip(" :-–—").strip()
        # Common source markup separates category and title with a colon.
        for sep in (":", " - ", " – ", " — "):
            if sep in text:
                head, tail = text.split(sep, 1)
                if normalize_text(head) == normalized_prefix:
                    return tail.strip()
        return str(text or "").strip()

    @staticmethod
    def _has_clear_arabic(text):
        if not text or not contains_arabic(text):
            return False
        ar = len(re.findall(r"[\u0600-\u06ff]", text))
        letters = len(re.findall(r"[A-Za-zÀ-ÿ\u0600-\u06ff]", text))
        return ar >= 2 and (letters == 0 or float(ar) / float(letters) >= 0.28)

    def translate(self, text, is_after_massaiya=False, is_after_infosoir=False,
                  is_desc=False, keep_french=False):
        if not text:
            return ""
        text = text.strip()
        if not text:
            return ""
        if contains_arabic(text) and not contains_latin_letters(text):
            return text

        if is_desc:
            # French programme *titles* such as Info Soir/Eco News stay as
            # broadcast, but descriptions are still localized to Arabic.
            return self.translate_description(text)

        if re.fullmatch(r"\d{1,2}[:h]\d{2}", text.lower()):
            return text

        base_text, meta_suffix = self.extract_metadata(text)
        if not base_text:
            return meta_suffix.strip()

        norm_base = normalize_text(base_text)

        # Explicit French whitelist wins even over legacy dictionaries. This
        # is what keeps Info Soir / Le Journal / Météo / JT etc. in French.
        if keep_french or norm_base in self.french_shows_whitelist:
            return ("%s%s" % (base_text, meta_suffix)).strip()

        # Verified/curated 2M title aliases are the highest-priority Arabic
        # mapping for every other title.
        if norm_base in self.official_titles:
            return ("%s%s" % (self.official_titles[norm_base], meta_suffix)).strip()

        # Translate the programme category/prefix structurally, then Arabicize
        # only the actual Moroccan work title.
        for fr_pref, ar_pref in sorted(self.tv_prefixes.items(), key=lambda x: len(x[0]), reverse=True):
            if norm_base == fr_pref or norm_base.startswith(fr_pref + " "):
                main_title = self._strip_normalized_prefix(base_text, fr_pref)
                if not main_title:
                    return ("%s%s" % (ar_pref, meta_suffix)).strip()
                main_norm = normalize_text(main_title)
                if main_norm in self.official_titles:
                    rendered = self.official_titles[main_norm]
                elif self._looks_moroccan_arabizi(main_title):
                    rendered = self._arabizi_to_arabic(main_title)
                elif self._is_clearly_french(main_title):
                    rendered = main_title
                else:
                    rendered = self._translate_google_safe(main_title, kind="title")
                return ("%s : %s%s" % (ar_pref, rendered, meta_suffix)).strip()

        # Pure French title -> keep exactly as broadcast.
        if self._is_clearly_french(base_text):
            return ("%s%s" % (base_text, meta_suffix)).strip()

        # Moroccan Latin/Arabizi title -> Arabic script (e.g. Wlad 3li).
        if self._looks_moroccan_arabizi(base_text):
            return ("%s%s" % (self._arabizi_to_arabic(base_text), meta_suffix)).strip()

        translated = self._translate_google_safe(base_text, kind="title")
        if translated == base_text:
            # beta52: Telecablesat frequently exposes Arabic programme names in
            # Latin transliteration. If Google leaves such an unknown title
            # untouched and it is not clearly French/protected, render it in
            # Arabic script instead of leaking raw Arabizi into the EPG.
            if self._looks_moroccan_arabizi(base_text):
                translated = self._arabizi_to_arabic(base_text)
            else:
                self.unknown_shows_cache.set(norm_base, "")
        return ("%s%s" % (translated, meta_suffix)).strip()

    @staticmethod
    def _curated_description(raw_text):
        """Deterministic Arabic for recurring 2M descriptions seen in the grid.

        These are phrase-level translations, not keyword substitution, so they
        cannot create Arabic/French hybrids and they avoid a Google request for
        the most common repeated programmes.
        """
        raw = str(raw_text or "").strip()
        norm = normalize_text(raw)
        if not norm:
            return ""
        rows = (
            ("sous la forme d un voyage gastronomique a travers les regions marocaines",
             "رحلة في فنون الطبخ عبر مختلف مناطق المغرب لاكتشاف الأطباق والتخصصات المحلية بمشاركة سكان المنطقة."),
            ("tous les conseils pour apprendre a gerer au mieux son foyer",
             "نصائح عملية لتدبير شؤون الأسرة وتربية الأطفال والتعامل مع تحديات الحياة الزوجية."),
            ("figure incontournable de la cuisine marocaine",
             "تقدم شميشة وصفات متنوعة وسهلة تبرز منتجات المطبخ المغربي ومكوناته المحلية."),
            ("des analyses approfondies des reportages thematiques",
             "تحليلات معمقة وتقارير موضوعاتية ومداخلات خبراء حول قضايا المجتمع والاقتصاد والثقافة والسياسة."),
            ("les dynamiques familiales les relations intergenerationnelles",
             "يتناول العمل العلاقات داخل الأسرة وبين الأجيال وتحديات الحياة اليومية في أسرة مغربية معاصرة."),
            ("un show musical eblouissant qui revisite",
             "عرض موسيقي يستعيد روائع الأغنية المغربية بمختلف ألوانها مع شهادات ومواد أرشيفية."),
            ("emission de divertissement dediee a l art culinaire marocain",
             "مسابقة ترفيهية في فن الحلويات يتنافس فيها هواة لإبراز مهاراتهم والفوز بلقب أحسن صانع حلويات."),
            ("la serie relate l histoire d une lutte acharnee pour la prise de controle de la ferme familiale",
             "دراما عائلية تدور حول صراع للسيطرة على المزرعة والمشروع العائلي بعد وفاة الشخصية التي كانت تديرهما."),
            ("les destins croises de personnages dont les choix secrets et liens familiaux",
             "دراما تتقاطع فيها مصائر شخصيات تكشف اختياراتها وأسرارها وروابطها العائلية عن صراعات ومفاجآت متتالية."),
            ("amira poursuit son reve de devenir chanteuse",
             "تسعى أميرة لتحقيق حلمها بأن تصبح مغنية رغم معارضة والدها، قبل أن تتغير حياتها بعد دخوله في غيبوبة."),
        )
        for marker, arabic in rows:
            if marker in norm:
                return arabic
        return ""

    @staticmethod
    def _description_fallback(raw_text):
        raw = str(raw_text or "").strip()
        norm = normalize_text(raw)
        if not norm:
            return ""
        if ("resume n est pas disponible" in norm or
                "resume nest pas disponible" in norm or
                "resume n'est pas disponible" in raw.lower() or
                "le resume n est pas disponible" in norm):
            return ""
        if "journal televise de la redaction de 2m" in norm:
            return "نشرة إخبارية من إعداد وتقديم هيئة تحرير قناة 2M."
        if "journal d information presente par la redaction de 2m" in norm:
            return "نشرة إخبارية تقدمها هيئة تحرير قناة 2M."
        if "magazine economique" in norm and "2m" in norm:
            return "برنامج اقتصادي تقدمه هيئة تحرير قناة 2M."
        if "previsions meteorologiques" in norm or "bulletin meteo" in norm or "meteo" in norm:
            return "نشرة تقدم توقعات الطقس ودرجات الحرارة والرياح والتساقطات."
        if "journal" in norm or "actualite" in norm or "information" in norm:
            return "برنامج إخباري من قناة 2M."
        if "documentaire" in norm:
            return "برنامج وثائقي يُعرض على قناة 2M."
        if "telefilm" in norm or re.search(r"\bfilm\b", norm):
            return "فيلم يُعرض على قناة 2M."
        if "serie" in norm:
            return "مسلسل يُعرض على قناة 2M."
        if "divertissement" in norm or "show" in norm:
            return "برنامج ترفيهي يُعرض على قناة 2M."
        if "magazine" in norm or "debat" in norm:
            return "برنامج تلفزيوني يُعرض على قناة 2M."
        return "برنامج يُعرض على قناة 2M."

    def translate_description(self, text):
        raw = str(text or "").strip()
        if not raw:
            return ""
        if contains_arabic(raw) and not contains_latin_letters(raw):
            return raw
        curated = self._curated_description(raw)
        if curated:
            return curated
        translated = self._translate_google_safe(raw, kind="desc")
        if translated and translated != raw and is_arabic_first(translated, allow_latin_words=3) and not is_mixed_sentence(translated, min_latin_chars=6):
            return translated
        # Never expose half-translated French/English prose. Proper names may
        # remain Latin in a good Arabic translation, but foreign grammar does not.
        return self._description_fallback(raw)

    def _translate_google_safe(self, base_text, kind="text"):
        if not contains_latin_letters(base_text):
            return base_text

        cache_key = "%s:%s" % (kind, base_text.lower().strip())
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self.downloader is None:
            return base_text

        try:
            resp = self.downloader.get_or_none(
                GOOGLE_TRANSLATE_URL,
                params={"client": "gtx", "sl": "auto", "tl": "ar", "dt": "t", "q": base_text},
            )
            if resp is not None:
                data = resp.json()
                if data and isinstance(data, list) and data[0]:
                    out = "".join(row[0] for row in data[0] if row and row[0]).strip()
                    out = re.sub(r"\s+", " ", out)
                    if out and self._has_clear_arabic(out):
                        self.cache.set(cache_key, out)
                        return out
        except Exception as e:
            log.debug("Safe Arabic translation failed for %r: %s", base_text, e)

        return base_text
