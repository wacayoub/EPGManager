# -*- coding: utf-8 -*-
"""Safe, narrow ElCinema adapter date rollover patch regression."""
import unittest
from elcinema_midnight_patch import OLD, NEW, OLD_TZ, PIN_TZ, patch

BASE = """module.exports = {
  parser({content, channel, date}) {
""" + OLD + """      const stop = start.add(duration, 'm')
    })
  }
}
function parseStart(item, initDate) {
  """ + OLD_TZ + """
}
"""

class MidnightPatch(unittest.TestCase):
    def test_exact_time_fix_keeps_other_adapter_bytes(self):
        after=patch(BASE)
        self.assertIn(NEW,after)
        self.assertIn(PIN_TZ,after)
        self.assertNotIn(OLD,after)
        self.assertNotIn(OLD_TZ,after)
        self.assertIn("const stop = start.add(duration, 'm')",after)
    def test_idempotent(self):
        first=patch(BASE)
        self.assertEqual(patch(first),first)
    def test_refuse_ambiguous_old_parser(self):
        with self.assertRaises(ValueError):
            patch(BASE+OLD)
    def test_refuse_upstream_date_anchor_change(self):
        with self.assertRaises(ValueError):
            patch(BASE.replace("const start = parseStart(item, date)",
                                "const start = new_source_date(item, date)"))
    def test_refuse_timezone_contract_change(self):
        with self.assertRaises(ValueError):
            patch(BASE.replace(OLD_TZ,"return dayjs.tz(time, 'UTC')"))
    def test_never_rewrite_unrelated_code(self):
        extra="// DO NOT DROP EXISTING XMLTV OR MAPPINGS\n"
        self.assertTrue(patch(BASE+extra).endswith(extra))
    def test_reject_nonstring_and_bloated_input(self):
        for x in (None,1,"X"*100001):
            with self.subTest(x_type=type(x).__name__):
                with self.assertRaises(ValueError):
                    patch(x)

if __name__ == "__main__":
    unittest.main()
