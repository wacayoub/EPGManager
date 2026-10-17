"""User-observed Grand Format on HD2/HD3 must not pollute fixture search."""
import unittest
from datetime import date
from arryadia_fixture_candidates import (
    SOURCES, EXCLUDED_MATCH_CHANNELS, parse_fixture_candidates, flag_collisions,
)


def fixture_row(clock, home, away, league):
    return (
      '<tr><td class="hora">%s</td><td class="detalles">'
      '<label title="%s">%s</label></td>'
      '<td class="local"><span title="%s">%s</span></td>'
      '<td class="visitante"><span title="%s">%s</span></td></tr>'
    )%(clock,league,league,home,home,away,away)


class FixtureScope(unittest.TestCase):
    def setUp(self):
        self.day=date(2026,10,17)
        self.hd1_url=SOURCES["Arryadia_HD1"]

    def test_hd2_and_hd3_excluded(self):
        self.assertEqual(set(SOURCES),{"Arryadia_HD1"})
        self.assertEqual(EXCLUDED_MATCH_CHANNELS,{"Arryadia_HD2","Arryadia_HD3"})
        for cid in EXCLUDED_MATCH_CHANNELS:
            with self.subTest(cid=cid), self.assertRaisesRegex(ValueError,"excluded"):
                parse_fixture_candidates("<h1>Live football on Arryadia</h1>",cid,self.day,"unused")

    def test_other_channel_fixture_does_not_become_hd1(self):
        html=('<h1>Live football on Arryadia HD2</h1><table>'
              '<tr class="cabeceraTabla"><td>Sunday 18/10/2026</td></tr>'
              +fixture_row("21:00","Team A","Team B","Botola")+'</table>')
        self.assertEqual(parse_fixture_candidates(html,"Arryadia_HD1",self.day,self.hd1_url),[])

    def test_hd1_same_clock_conflicts(self):
        html=('<h1>Live football on Arryadia HD1</h1><table>'
              '<tr class="cabeceraTabla"><td>Sunday 18/10/2026</td></tr>'
              +fixture_row("21:00","A","B","Botola")
              +fixture_row("21:00","C","D","Botola")+'</table>')
        events=flag_collisions(parse_fixture_candidates(html,"Arryadia_HD1",self.day,self.hd1_url))
        self.assertEqual(len(events),2)
        self.assertTrue(all(e["status"].startswith("CONFLICT") and not e["may_publish"] for e in events))

    def test_stale_rows_and_unknown_timezone(self):
        html=('<h1>Live football on Arryadia HD1</h1><table>'
              '<tr class="cabeceraTabla"><td>Monday 21/09/2026</td></tr>'
              +fixture_row("21:00","A","B","Botola")+'</table>')
        self.assertEqual(parse_fixture_candidates(html,"Arryadia_HD1",self.day,self.hd1_url),[])
        future=('<h1>Live football on Arryadia HD1</h1><table>'
                '<tr class="cabeceraTabla"><td>Monday 19/10/2026</td></tr>'
                +fixture_row("21:00","A","B","Botola")+'</table>')
        match=parse_fixture_candidates(future,"Arryadia_HD1",self.day,self.hd1_url)[0]
        self.assertFalse(match["may_publish"])
        self.assertEqual(match["site_timezone"],"UNVERIFIED")


if __name__=="__main__":
    unittest.main()
