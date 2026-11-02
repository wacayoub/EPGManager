import importlib.util
import sys,types,unittest
from datetime import timedelta

# The tested helpers need no network/parser dependencies; stub optional modules
# so this regression suite can run in the workflow and on a minimal host.
sys.modules.setdefault('requests',types.ModuleType('requests'))
bs4=types.ModuleType('bs4'); bs4.BeautifulSoup=object; sys.modules.setdefault('bs4',bs4)
lxml=types.ModuleType('lxml'); lxml.html=types.ModuleType('lxml.html'); sys.modules.setdefault('lxml',lxml); sys.modules.setdefault('lxml.html',lxml.html)

from pathlib import Path
spec=importlib.util.spec_from_file_location('morocco_epg',str(Path(__file__).with_name('morocco_epg.py')))
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

class Reply:
    def __init__(self, translated): self.translated=translated
    def json(self): return [[[self.translated, None, None, None]]]

class FakeHttp:
    def __init__(self, translations=None): self.translations=translations or {}; self.calls=[]
    def get(self, url, params):
        q=params['q']; self.calls.append(q)
        return Reply(self.translations.get(q,'عنوان مترجم'))

class TwoMTranslationTests(unittest.TestCase):
    def test_unlisted_french_title_gets_translated(self):
        h=FakeHttp({'A new programme title':'عنوان برنامج جديد'})
        self.assertEqual(m.tr2m_title(h,'A new programme title'),'عنوان برنامج جديد')
        self.assertEqual(h.calls,['A new programme title'])

    def test_programme_category_and_unknown_show_are_both_translated(self):
        h=FakeHttp({'A new show':'عرض جديد'})
        self.assertEqual(m.tr2m_title(h,'Serie A new show'),'مسلسل : عرض جديد')
        self.assertEqual(h.calls,['A new show'])

    def test_mixed_arabic_latin_title_is_sent_for_translation(self):
        h=FakeHttp({'S1 صباحيات 2M':'الموسم الأول صباحيات 2M'})
        self.assertEqual(m.tr2m_title(h,'S1 صباحيات 2M'),'الموسم الأول صباحيات 2M')
        self.assertEqual(h.calls,['S1 صباحيات 2M'])

    def test_known_2m_shows_are_deterministically_arabic(self):
        expected={
            '3ayne Lkebrite':'عين الكبريت',
            'Auto-moto':'السيارات والدراجات النارية',
            'Mabrouk 3Lina':'مبروك علينا',
            'Chef F Daro':'الطاهي في داره',
            'Kan ya ma kan':'كان يا ما كان',
            'Les filles de Lalla Mennana':'بنات لالة منانة',
            'Capsules Festival International du Film de Femmes de Salé':'كبسولات المهرجان الدولي لفيلم المرأة بسلا',
        }
        h=FakeHttp()
        for title,arabic in expected.items():
            with self.subTest(title=title): self.assertEqual(m.tr2m_title(h,title),arabic)
        self.assertEqual(h.calls,[])

    def test_native_arabic_title_does_not_call_translator(self):
        h=FakeHttp()
        self.assertEqual(m.tr2m_title(h,'الخبراء'),'الخبراء')
        self.assertEqual(h.calls,[])

    def test_repeat_title_uses_per_run_cache(self):
        h=FakeHttp({'Repeated mystery title':'عنوان عربي'})
        expected='عنوان عربي'
        self.assertEqual(m.tr2m_title(h,'Repeated mystery title'),expected)
        self.assertEqual(m.tr2m_title(h,'Repeated mystery title'),expected)
        self.assertEqual(h.calls,['Repeated mystery title'])

    def test_quality_gate_rejects_untranslated_show_titles(self):
        now=m.datetime.now(m.TZ)
        rows=[m.Event('2M',now+timedelta(minutes=i+1),'عنوان عربي',stop=now+timedelta(hours=1)) for i in range(6)]
        self.assertTrue(m.valid('2m',rows)[0])
        rows[0].title='Auto-moto'; rows[1].title='Untranslated unknown show'
        self.assertFalse(m.valid('2m',rows)[0])

if __name__=='__main__': unittest.main(verbosity=2)
