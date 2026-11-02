"""No fabricated EPG and strict official page selector regressions."""
import unittest
from datetime import date, timedelta
from morocco_current_official import parse_alaoula_html, parse_chada_html


class OfficialGrids(unittest.TestCase):
    def test_snrt_current_roller_and_broadcast_midnight(self):
        def row(tm,title):
            return '<div class="grille-item"><div class="info"><span class="when">%s</span> %s</div></div>' % (tm,title)
        content = '<div id="main-roller-content">' + "".join(row(t,n) for t,n in [
            ("07H00","القرآن الكريم"), ("08H00","برنامج الصباح"), ("09H00","وثائقي"),
            ("10H00","الأخبار"),("11H00","مسلسل"),("12H00","الرياضة"),
            ("13H00","مجلة"),("20H00","أحوال الطقس"),("23H00","سهرة سينمائية"),
            ("00H30","الأخبار الأخيرة")]) + "</div>"
        events = parse_alaoula_html(content,date(2026,10,17))
        self.assertEqual(len(events),10)
        self.assertEqual(events[-1].start.date(),date(2026,10,18))
        self.assertTrue(all(e.stop > e.start for e in events))
        self.assertTrue(all("AlAoula" == e.channel for e in events))
        self.assertTrue(all(e.tl == "ar" and e.dl == "ar" for e in events))
        self.assertTrue(all(e.source == "snrt-official-arabic" for e in events))
        self.assertIn("القرآن الكريم", [e.title for e in events])
        weather = [e for e in events if e.title == "أحوال الطقس"]
        self.assertEqual(len(weather), 1)
        self.assertEqual(
            weather[0].desc,
            "نشرة جوية مفصلة حول حالة الطقس في المملكة المغربية.",
        )

    def test_snrt_french_grid_is_not_reused(self):
        def row(t, label):
            return '<div class="grille-item"><div class="info"><span class="when">%s</span> %s</div></div>' % (t,label)
        html = '<div id="main-roller-content">' + ''.join(
            row('%02dH00' % h,'Journal Télévisé') for h in range(7,20)
        ) + '</div>'
        self.assertEqual(parse_alaoula_html(html,date(2026,10,17)), [])

    def test_snrt_do_not_invent_when_grid_missing(self):
        self.assertEqual(parse_alaoula_html('<div class="old-grille"></div>',date.today()), [])

    def test_chada_calendar_monday_and_midnight(self):
        html = '<button class="chada-calendar-tab" data-day="1">Lundi</button>' + (
            '<div class="chada-day-panel" data-day-panel="1">'
            '<article class="chada-epg-row"><span class="chada-epg-time__start">20:00</span>'
            '<span class="chada-epg-time__end">24:00</span><h3>مساء الخير</h3>'
            '<p>برنامج مباشر حقيقي</p></article></div>')
        rows = parse_chada_html(html,date(2026,10,17),7)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].start.date(),date(2026,10,19))
        self.assertEqual(rows[0].stop.date(),date(2026,10,20))
        self.assertEqual(rows[0].title,"مساء الخير")

    def test_chada_no_made_up_programmes(self):
        self.assertEqual(parse_chada_html('<main>Chada</main>',date.today()),[])


if __name__ == "__main__":
    unittest.main()
