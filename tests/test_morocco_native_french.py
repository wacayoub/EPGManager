"""Editorial 2M language rules: no automated Arabic overwrite of French TV."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import morocco_cloud_runner_ar2 as ar2
import morocco_cloud_runner_ar2_quality as quality

TZ = ZoneInfo("Africa/Casablanca")


def row(day, hour, title):
    return SimpleNamespace(
        channel="2M",
        start=datetime.fromisoformat("%sT%s:00+01:00" % (day, hour)).astimezone(TZ),
        title=title, tl="ar", dl="ar",
        desc="وصف عربي صحيح",
    )


class MoroccoFrenchEditorial(unittest.TestCase):
    def test_info_soir_and_econews_all_airings_remain_french(self):
        rows = [
            row("2026-10-18", "11:00", "أخبار المساء"),
            row("2026-10-18", "11:10", "إيكو نيوز"),
            row("2026-10-18", "21:00", "أخبار المساء"),
            row("2026-10-18", "21:15", "النشرة الجوية"),
            row("2026-10-18", "21:25", "إيكو نيوز"),
            row("2026-10-18", "23:00", "النشرة الجوية"),
            row("2026-10-19", "21:00", "أخبار المساء"),
            row("2026-10-19", "21:15", "النشرة الجوية"),
        ]
        ar2._apply_evening_language_policy(rows)
        self.assertEqual([x.title for x in rows], [
            "Info Soir", "Eco News", "Info Soir", "Bulletin Météo",
            "Eco News", "النشرة الجوية", "Info Soir", "Bulletin Météo",
        ])
        self.assertEqual([x.tl for x in rows],
            ["fr", "fr", "fr", "fr", "fr", "ar", "fr", "fr"])
        self.assertTrue(all(x.dl == "ar" for x in rows))

    def test_weather_only_just_after_info_soir(self):
        rows = [
            row("2026-10-18", "20:00", "النشرة الجوية"),
            row("2026-10-18", "21:00", "أخبار المساء"),
            row("2026-10-18", "21:05", "برنامج مغربي"),
            row("2026-10-18", "21:15", "النشرة الجوية"),
        ]
        ar2._apply_evening_language_policy(rows)
        self.assertEqual(rows[0].title, "النشرة الجوية")
        self.assertEqual(rows[-1].title, "النشرة الجوية")

    def test_verified_french_not_mistaken_for_arabizi(self):
        rows = [
            row("2026-10-18", "10:00", "Auto-moto"),
            row("2026-10-18", "11:00", "Planète Foot"),
            row("2026-10-18", "12:00", "Ahsane pâtissier"),
            row("2026-10-18", "13:00", "Wlad Bladi"),
        ]
        ar2._apply_evening_language_policy(rows)
        self.assertEqual([x.tl for x in rows], ["fr", "fr", "fr", "ar"])
        self.assertEqual(rows[-1].title, "Wlad Bladi")

    def test_dictionary_still_translates_real_moroccan_arabizi(self):
        self.assertEqual(
            quality.translate_title_keep_real_name("Charqi ou Lgharbi"),
            "شرقي و الغربي",
        )


if __name__ == "__main__":
    unittest.main()
