"""Preserve the broadcaster's actual French 2M programme names."""
import unittest
import morocco_cloud_runner_ar2_quality as quality


class NativeFrench2MNames(unittest.TestCase):
    def test_auto_moto_remains_native_french(self):
        for raw in ("Auto-moto", "AUTO MOTO", "auto moto"):
            self.assertEqual(quality.translate_title_keep_real_name(raw), "Auto-moto")

    def test_other_confirmed_francophone_show_names(self):
        self.assertEqual(
            quality.translate_title_keep_real_name("Planète Foot"), "Planète Foot"
        )
        self.assertEqual(
            quality.translate_title_keep_real_name("Ahsane pâtissier"),
            "Ahsane pâtissier",
        )

    def test_unknown_name_preservation_still_exists(self):
        self.assertTrue(callable(quality.translate_title_keep_real_name))


if __name__ == "__main__":
    unittest.main()
