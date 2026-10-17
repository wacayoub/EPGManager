"""Arryadia fixture advisory excludes HD3, never publishes unverified games."""
import unittest
from datetime import date
from arryadia_fixture_candidates import (
    SOURCES, EXCLUDED_MATCH_CHANNELS, parse_fixture_candidates, flag_collisions
)

def fixture_row(hour,a,b,league):
    return (
      '<tr><td class="hora">%s</td><td class="detalles"><label title="%s">%s</label></td>'
      '<td class="local"><span title="%s">%s</span></td>'
      '<td class="visitante"><span title="%s">%s</span></td></tr>'
    )%(hour,league,league,a,a,b,b)

class ScopeAndSafety(unittest.TestCase):
    def setUp(self):
        self.day=date(2026,10,17)
        self.url="https://www.livefootballtv.info/channel/arryadia-hd2"

    def test_hd3_explicitly_excluded_from_monitoring(self):
        self.assertEqual(set(SOURCES),{"Arryadia_HD1","Arryadia_HD2"})
        self.assertEqual(EXCLUDED_MATCH_CHANNELS,{"Arryadia_HD3"})
        with self.assertRaisesRegex(ValueError,"excluded or unsupported"):
            parse_fixture_candidates(
                '<h1>Live football on Arryadia HD3</h1>',
                "Arryadia_HD3",self.day,"https://www.livefootballtv.info/channel/moroccan-sports"
            )

    def test_cross_channel_hd3_does_not_become_hd2(self):
        page='<h1>Live football on Arryadia HD3</h1><table>' + (
            '<tr class="cabeceraTabla"><td>Sunday 18/10/2026</td></tr>'+
            fixture_row("21:00","Widad Témara","KAC Marrakech","Botola Pro")
            )+'</table>'
        self.assertEqual(parse_fixture_candidates(page,"Arryadia_HD2",self.day,self.url),[])

    def test_hd2_same_channel_same_clock_conflicts_quarantined(self):
        page='<h1>Live football on Arryadia HD2</h1><table>' + (
            '<tr class="cabeceraTabla"><td>Sunday 18/10/2026</td></tr>'+
            fixture_row("21:00","Team A","Team B","Botola")+
            fixture_row("21:00","Team C","Team D","Botola")
            )+'</table>'
        rows=flag_collisions(parse_fixture_candidates(page,"Arryadia_HD2",self.day,self.url))
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r["status"].startswith("CONFLICT") for r in rows))
        self.assertTrue(all(r["may_publish"] is False for r in rows))

    def test_past_hd1_fixture_rejected(self):
        page=('<h1>Live football on Arryadia HD1</h1><table>'
              '<tr class="cabeceraTabla"><td>Monday 21/09/2026</td></tr>'+
              fixture_row("21:00","Team A","Team B","Botola")+'</table>')
        self.assertEqual(parse_fixture_candidates(page,"Arryadia_HD1",self.day,SOURCES["Arryadia_HD1"]),[])

    def test_even_valid_future_hd1_match_stays_advisory(self):
        page=('<h1>Live football on Arryadia HD1</h1><table>'
              '<tr class="cabeceraTabla"><td>Monday 19/10/2026</td></tr>'+
              fixture_row("21:00","Team A","Team B","Botola")+'</table>')
        row=parse_fixture_candidates(page,"Arryadia_HD1",self.day,SOURCES["Arryadia_HD1"])[0]
        self.assertFalse(row["may_publish"])
        self.assertEqual(row["site_timezone"],"UNVERIFIED")
        self.assertIn("NOT_EPG",row["status"])

if __name__=="__main__":
    unittest.main()
