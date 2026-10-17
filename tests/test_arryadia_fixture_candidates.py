"""Arryadia fixture advisory must never create programme events."""
import unittest
from datetime import date
from arryadia_fixture_candidates import parse_fixture_candidates,flag_collisions


def fixture_row(hour,a,b,league):
    return (
      '<tr><td class="hora">%s</td><td class="detalles"><label title="%s">%s</label></td>'
      '<td class="local"><span title="%s">%s</span></td>'
      '<td class="visitante"><span title="%s">%s</span></td></tr>'
    )%(hour,league,league,a,a,b,b)


class SportsSafety(unittest.TestCase):
    def setUp(self):
        self.day=date(2026,10,17)
        self.url="https://www.livefootballtv.info/channel/moroccan-sports"

    def test_hd3_collision_two_matches_same_date_and_clock(self):
        page='<h1>Live football on Arryadia HD3</h1><table>' + (
            '<tr class="cabeceraTabla"><td>Sunday 18/10/2026</td></tr>' +
            fixture_row("21:00","Widad Témara","KAC Marrakech","Botola Pro")+
            fixture_row("21:00","Maghreb Fez","ASEC Mimosas","CAF Champions League")+
            fixture_row("23:00","Wydad","Amal Tiznit","Botola Pro")
            ) + '</table>'
        rows=flag_collisions(parse_fixture_candidates(page,"Arryadia_HD3",self.day,self.url))
        self.assertEqual(len(rows),3)
        self.assertEqual(sum(r['status'].startswith("CONFLICT") for r in rows),2)
        self.assertTrue(all(not x["may_publish"] for x in rows))
        self.assertTrue(all(x["site_timezone"]=="UNVERIFIED" for x in rows))

    def test_cross_channel_fail_closed(self):
        page='<h1>Live football on Arryadia HD2</h1><table>'+fixture_row("21:00","A","B","Botola")+'</table>'
        self.assertEqual(parse_fixture_candidates(page,"Arryadia_HD3",self.day,self.url),[])

    def test_archived_rows_not_republished(self):
        page=('<h1>Live football on Arryadia HD3</h1><table>'
              '<tr class="cabeceraTabla"><td>Monday 21/09/2026</td></tr>'+
              fixture_row("21:00","Team A","Team B","Botola")+'</table>')
        self.assertEqual(parse_fixture_candidates(page,"Arryadia_HD3",self.day,self.url),[])

    def test_future_clock_unverified_even_if_no_collision(self):
        page=('<h1>Live football on Arryadia HD3</h1><table>'
              '<tr class="cabeceraTabla"><td>Monday 19/10/2026</td></tr>'+
              fixture_row("21:00","Team A","Team B","Botola")+'</table>')
        row=parse_fixture_candidates(page,"Arryadia_HD3",self.day,self.url)[0]
        self.assertFalse(row["may_publish"])
        self.assertIn("NOT_EPG",row["status"])
        self.assertEqual(row["site_clock"],"21:00")


if __name__=="__main__":
    unittest.main()
