#!/usr/bin/env python3
"""Synthetic regression: Dubai Racing 1 may vanish when EPG is empty; core does not."""
import unittest
import receiver_release_gate as gate


class DmiReceiverContractTests(unittest.TestCase):
    def setUp(self):
        self.prefixes, self.required, self.optional = gate.PROVIDERS["provider-dmi"]

    def validate(self, actual):
        return {
            "missing": self.required - actual,
            "extra": actual - self.required - self.optional,
            "bad_namespaces": {x for x in actual if not x.startswith(self.prefixes)},
        }

    def test_only_racing1_can_be_missing(self):
        expected_optional = {"DMI.Dubai.Racing.1.ae"}
        self.assertEqual(self.optional, expected_optional)
        self.assertEqual(self.required | self.optional, gate.DMI)
        self.assertEqual(len(self.required), 8)
        self.assertTrue(all(not v for v in self.validate(self.required).values()))

    def test_racing1_is_reincluded_when_real_guide_exists(self):
        self.assertTrue(all(not v for v in self.validate(gate.DMI).values()))

    def test_any_other_core_channel_still_blocks(self):
        for cid in sorted(self.required):
            with self.subTest(cid=cid):
                result = self.validate(self.required - {cid})
                self.assertEqual(result["missing"], {cid})

    def test_unknown_id_still_blocks(self):
        result = self.validate(gate.DMI | {"DMI.Fake.Channel.ae"})
        self.assertEqual(result["extra"], {"DMI.Fake.Channel.ae"})
        result = self.validate(gate.DMI | {"Foreign.Channel"})
        self.assertIn("Foreign.Channel", result["bad_namespaces"])


if __name__ == "__main__":
    unittest.main()
