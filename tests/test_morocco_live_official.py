"""No fabricated EPG and strict official page selector regressions."""
import unittest
from datetime import date, timedelta
from morocco_current_official import parse_alaoula_html, parse_chada_html


class OfficialGrids(unittest.TestCase):
    def test_snrt_current_roller_and_broadcast_midnight(self):
        def row(tm,title):
            return '<div class="grille-item"><div class="info"><span class="when">%s</span> %s</div></div>' % (tm,title)
        content = '<div id="main-roller-content">' + "".join(row(t,n) for t,n in [
            ("07H00","Coran"), ("08H00","Morning"), ("09H00","Documentaire"),
            ("10H00","Journal"),("11H00","Série"),("12H00","Sport"),
            ("13H00","Magazine"),("20H00","Météo"),("23H00","Cinéma"),
            ("00H30","Journal nuit")]) + "</div>"
        events = parse_alaoula_html(content,date(2026,10,17))
        self.assertEqual(len(events),10)
        self.assertEqual(events[-1].start.date(),date(2026,10,18))
        self.assertTrue(all(e.stop > e.start for e in events))
        self.assertTrue(all("AlAoula" == e.channel for e in events))

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
