"""Keep source/receiver/rc81 identical to the package installed on the receiver."""
import hashlib
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
RC81 = ROOT / 'source/receiver/rc81'


def manifest():
    rows = {}
    for line in (RC81 / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#'):
            digest, name = line.split(None, 1)
            rows[name.lstrip('*')] = digest
    return rows


class ReceiverSourceRc81(unittest.TestCase):
    def test_files_match_installed_package_checksums(self):
        expected = manifest()
        self.assertEqual(len(expected), 218)
        present = {p.relative_to(RC81).as_posix() for p in (RC81 / 'EPGManager').rglob('*') if p.is_file()}
        self.assertEqual(present, set(expected), 'files added to or missing from the rc81 snapshot')
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((RC81 / name).read_bytes()).hexdigest(), digest)

    def test_every_python_file_compiles(self):
        for path in sorted((RC81 / 'EPGManager').rglob('*.py')):
            with self.subTest(path=path.relative_to(RC81).as_posix()):
                compile(path.read_bytes(), str(path), 'exec')

    def test_version_and_control_agree(self):
        version = (RC81 / 'EPGManager/version.py').read_text(encoding='utf-8')
        control = (RC81 / 'CONTROL/control').read_text(encoding='utf-8')
        self.assertIn('__version__ = "2026.11.10-rc81"', version)
        self.assertIn('Version: 2026.11.10-rc81', control)


if __name__ == '__main__':
    unittest.main()
