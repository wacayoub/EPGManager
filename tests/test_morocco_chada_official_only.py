"""Source provenance regression for CHADA TV (only broadcaster's official grid)."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import morocco_epg as base
import morocco_cloud_runner as runner
import morocco_cloud_final as cloud
import morocco_current_official as official

TZ = ZoneInfo("Africa/Casablanca")


def event(cid, source, minute=0):
    start = datetime.now(TZ).replace(second=0, microsecond=0) + timedelta(hours=2, minutes=minute)
    return base.Event(cid, start, "برنامج حقيقي", "وصف موثق", start + timedelta(minutes=30),
                      "ar", "ar", source)


class ChadaOnly(unittest.TestCase):
    def test_cloud_calls_only_official_provider(self):
        originals = [event("Chada TV", "chada-official-weekly", x * 45) for x in range(4)]
        with patch.object(official, "scrape_chada", return_value=originals) as mock_official:
            result = runner.scrape_chada(7)
            self.assertEqual(result, originals)
            mock_official.assert_called_once_with(7)

    def test_no_official_guide_means_no_donor_or_receiver_lkg(self):
        old_choose = runner.choose
        try:
            cloud._install_per_channel_lkg()
            # A fully valid previous feed is deliberately *not* accepted.
            donor = [event("Chada TV", "chada", x * 45) for x in range(4)]
            rows, mode = runner.choose("chada", [], donor, {"Chada TV"}, 3)
            self.assertEqual(rows, [])
            self.assertEqual(mode, "official-unavailable")
            rows, mode = runner.choose("chada", donor, donor, {"Chada TV"}, 3)
            self.assertEqual(rows, [])
            self.assertEqual(mode, "official-unavailable")
        finally:
            runner.choose = old_choose

    def test_live_official_only_and_no_old_feed(self):
        old_choose = runner.choose
        try:
            cloud._install_per_channel_lkg()
            real = [event("Chada TV", "chada-official-weekly", x * 45) for x in range(4)]
            donor = [event("Chada TV", "tele-news", 9 * 45)]
            rows, mode = runner.choose("chada", real + donor, donor, {"Chada TV"}, 3)
            self.assertEqual(mode, "official-only")
            self.assertEqual(rows, real)
            self.assertTrue(all(e.source == "chada-official-weekly" for e in rows))
        finally:
            runner.choose = old_choose

    def test_xml_boundary_refuses_unauthorized_chada_programme(self):
        old_write = runner.write_xml
        try:
            runner.write_xml = lambda rows, path: {"Chada TV": sum(e.channel == "Chada TV" for e in rows)}
            cloud._install_strict_writer()
            core = [
                event("2M", "sudinfo", 0),
                event("Arryadia_HD", "arryadia-current-official", 45),
                event("MEDI1TV_AR.ma", "medi1", 90),
            ]
            self.assertEqual(runner.write_xml(core, "unused")["Chada TV"], 0)
            with self.assertRaisesRegex(RuntimeError, "nonofficial"):
                runner.write_xml(core + [event("Chada TV", "tele-news", 135)], "unused")
            verified = core + [event("Chada TV", "chada-official-weekly", 135)]
            self.assertEqual(runner.write_xml(verified, "unused")["Chada TV"], 1)
        finally:
            runner.write_xml = old_write


if __name__ == "__main__":
    unittest.main()
