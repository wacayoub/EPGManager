# -*- coding: utf-8 -*-
"""Synthetic-only regression for private RC79.1 -> RC80 Mapping parity.
Never includes original receiver code or user settings.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import audit_rc80_mapping_parity_readonly as gate

NAMES = sorted(gate.REVIEWED_PREVIEW | gate.PROTECTED_APIS)
def source(changed=(), removed=(), unsafe_poll=False):
    lines = ["class ChannelMappingScreen:", "    skin = 'unchanged'"]
    for name in NAMES:
        if name in removed:
            continue
        if unsafe_poll and name == "_poll_github_best_match_health":
            body = "self.refresh_async()"
        else:
            body = "return %d" % (2 if name in changed else 1)
        lines.extend(["    def %s(self):" % name, "        " + body])
    return ("\n".join(lines) + "\n").encode("utf-8")


class RC80MappingParity(unittest.TestCase):
    def test_four_preview_methods_only(self):
        old = source()
        new = source(changed=gate.REVIEWED_PREVIEW)
        report = gate.require_safe_preview_parity(old, new)
        self.assertTrue(report["auto_manual_mapping_writers_ast_preserved"])
        self.assertFalse(report["authorized_to_install"])
        self.assertEqual(set(report["changed_preview_methods"]), gate.REVIEWED_PREVIEW)

    def test_active_auto_writer_replaced(self):
        old = source()
        new = source(changed=gate.REVIEWED_PREVIEW | {"_auto_batch_match"})
        with self.assertRaisesRegex(ValueError, "unreviewed receiver Mapping behavior"):
            gate.require_safe_preview_parity(old, new)

    def test_manual_mapping_api_missing(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            gate.require_safe_preview_parity(source(), source(
                changed=gate.REVIEWED_PREVIEW, removed={"undo_last_mapping"}))

    def test_unreviewed_class_layout_change(self):
        old = source()
        new = source(changed=gate.REVIEWED_PREVIEW).replace(
            b"skin = 'unchanged'", b"skin = 'different'")
        with self.assertRaisesRegex(ValueError, "class configuration"):
            gate.require_safe_preview_parity(old, new)

    def test_synchronous_poll_or_shortcut_rejected(self):
        with self.assertRaisesRegex(ValueError, "poll"):
            gate.require_safe_preview_parity(source(), source(
                changed=gate.REVIEWED_PREVIEW, unsafe_poll=True))

    def test_unmodified_preview_does_not_count_as_valid_fusion(self):
        with self.assertRaisesRegex(ValueError, "missing one or more"):
            gate.require_safe_preview_parity(source(), source(
                changed={"_queue_github_best_match_health"}))

    def test_invalid_python_rejected(self):
        with self.assertRaisesRegex(ValueError, "parse"):
            gate.require_safe_preview_parity(source(), b"class X(: pass")


if __name__ == "__main__":
    unittest.main()
