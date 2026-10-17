# -*- coding: utf-8 -*-
"""One-shot end-to-end synthetic packaging QA: NO receiver or opkg access."""
import hashlib
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import assemble_rc80_one_shot_offline as one_shot
import build_rc80_offline_blocked_ipk as builder
import audit_rc80_rollback_package_readonly as rollback
import apply_rc80_private_functional_fixes as functional
import test_rc80_offline_blocked_ipk as fixtures


class TestOneShotEndToEnd(unittest.TestCase):
    @staticmethod
    def fixture(tmp):
        return fixtures.TestBlockedOfflinePackage().fixture(tmp)

    def test_private_offline_check_only_and_blocked_build(self):
        with TemporaryDirectory() as tmp:
            rescue, _, _, _, digest = self.fixture(tmp)
            destination = Path(tmp) / 'private-output'
            destination.mkdir()
            actual_prepare = builder.prepare
            def synthetic_prepare(*args, **kwargs):
                return actual_prepare(*args, expected_rescue_sha=digest, **kwargs)
            with patch.object(one_shot, 'RESCUE_SHA', digest), patch.object(builder, 'RESCUE_SHA256', digest), \
                 patch.object(builder, 'prepare', side_effect=synthetic_prepare), \
                 patch.object(functional, 'apply_to_staged', return_value={}):
                checked = one_shot.prepare(ROOT, rescue)
                self.assertEqual(checked['source_modules'], 140)
                self.assertEqual(checked['unchanged_other_assets'], 75)
                self.assertEqual(checked['files_in_candidate'], 215)
                self.assertFalse(checked['authorized_to_install'])
                self.assertTrue(checked['read_only'])
                self.assertEqual(list(destination.iterdir()), [])
                built = one_shot.prepare(ROOT, rescue, destination, build=True)
            self.assertFalse(built['authorized_to_install'])
            self.assertFalse(built['read_only'])
            self.assertEqual(checked['candidate_sha256'], built['candidate_sha256'])
            ipk = destination / builder.OUTPUT_NAME
            self.assertTrue(ipk.is_file())
            self.assertEqual(ipk.stat().st_mode & 0o777, 0o600)
            self.assertEqual(hashlib.sha256(ipk.read_bytes()).hexdigest(), built['candidate_sha256'])
            archive = rollback.read_ar(ipk.read_bytes())
            controls, modes = rollback.read_tar(archive['control.tar.gz'])
            payload, _ = rollback.read_tar(archive['data.tar.gz'])
            self.assertEqual(len(payload), 215)
            self.assertEqual(controls['preinst'], builder.BLOCKED_PREINST)
            result = subprocess.run(['sh'], input=controls['preinst'], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 90)
            self.assertEqual(result.stdout, b'')
            self.assertEqual(result.stderr, b'')
            self.assertFalse(any('/etc/enigma2/' in name for name in payload))

    def test_incorrect_private_rescue_checksum_rejected_without_output(self):
        with TemporaryDirectory() as tmp:
            rescue, _, _, _, _ = self.fixture(tmp)
            destination = Path(tmp) / 'output'
            destination.mkdir()
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                one_shot.prepare(ROOT, rescue, destination, build=True)
            self.assertEqual(list(destination.iterdir()), [])

    def test_public_overlay_change_fails_closed(self):
        with TemporaryDirectory() as tmp:
            rescue, _, _, _, digest = self.fixture(tmp)
            with patch.object(one_shot, 'RESCUE_SHA', digest), patch.object(builder, 'RESCUE_SHA256', digest):
                with patch.dict(one_shot.BLOBS, {'core/verified_epg_match.py': '0' * 40}):
                    with self.assertRaisesRegex(ValueError, 'public pins or module inventory changed'):
                        one_shot.prepare(ROOT, rescue)


if __name__ == '__main__':
    unittest.main()