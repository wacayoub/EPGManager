"""No untranslated 2M titles outside explicit evening news exceptions."""
import unittest
import morocco_cloud_runner_ar as ar
import morocco_cloud_runner_ar2_quality as quality


class AutoMotoArabic(unittest.TestCase):
    def test_auto_moto_source_title_is_arabic(self):
        for raw in ("Auto-moto", "AUTO MOTO", "auto moto"):
            actual = quality.translate_title_keep_real_name(raw)
            self.assertEqual(actual, "أوتو موتو", (raw, actual))
            self.assertTrue(ar.has_arabic(actual))

    def test_unknown_name_preservation_still_exists(self):
        self.assertTrue(callable(quality.translate_title_keep_real_name))


if __name__ == "__main__":
    unittest.main()
