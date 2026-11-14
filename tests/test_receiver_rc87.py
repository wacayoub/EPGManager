"""rc87: home screen, import time label and receiver time zone (no Enigma2)."""
import ast
from pathlib import Path
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'source/receiver/rc87/EPGManager'


def static_method(path, cls, name):
    tree = ast.parse((PLUGIN / path).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    item.decorator_list = []
                    ns = {'time': time}
                    exec(compile(ast.Module(body=[item], type_ignores=[]), path, 'exec'), ns)
                    return ns[name]
    raise LookupError(name)


def screen_class(path='ui/main.py'):
    tree = ast.parse((PLUGIN / path).read_text(encoding='utf-8'))
    return next(n.name for n in tree.body if isinstance(n, ast.ClassDef)
                and any(isinstance(i, ast.FunctionDef) and i.name == '_iso_ts' for i in n.body))


class FeedAge(unittest.TestCase):
    def setUp(self):
        self.iso = static_method('ui/main.py', screen_class(), '_iso_ts')

    def test_parses_github_generated_stamps(self):
        self.assertEqual(self.iso('2026-11-14T07:47:38.829185+00:00'), 1794642458)
        self.assertEqual(self.iso('2026-11-14T08:47:38+01:00'), 1794642458)
        self.assertEqual(self.iso('2026-11-14T07:47:38Z'), 1794642458)

    def test_bad_values_are_zero(self):
        for value in (None, '', 'yesterday', 12):
            self.assertEqual(self.iso(value), 0)


class LastImport(unittest.TestCase):
    def test_note_has_unit_date_and_duration(self):
        note = static_method('ui/main.py', screen_class(), '_import_note')
        text = note({'timestamp': 1794641949, 'elapsed': 425.2})
        self.assertTrue(text.startswith('progs • '), text)
        self.assertEqual(len(text), len('progs • 14/11 07:39'), text)
        self.assertEqual(note({}), 'No import yet')


class Dashboard(unittest.TestCase):
    src = (PLUGIN / 'ui/main.py').read_text(encoding='utf-8')

    def test_monitor_positive_ids_count_as_having_a_guide(self):
        self.assertIn('if health in ("OK", "WARNING"): green_ids += 1', self.src)
        self.assertIn('"IDS WITH GUIDE"', self.src)

    def test_all_fourteen_direct_feeds_have_a_card(self):
        for stem in ('tabie', 'tunisiatv', 'alkass', 'starzplay', 'morocco'):
            self.assertIn('"ext_epgscrapers_%s"' % stem, self.src)

    def test_system_check_runs_after_scheduled_import(self):
        sched = (PLUGIN / 'core/scheduler.py').read_text(encoding='utf-8')
        self.assertIn('self._start_post_import_check()', sched)
        self.assertIn('system_check.run(config)', sched)


class StaleLocalCopy(unittest.TestCase):
    def setUp(self):
        import os
        tree = ast.parse((PLUGIN / 'core/system_check.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_stale_local_copy')
        ns = {'os': os, 'time': time}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'system_check', 'exec'), ns)
        self.stale = ns['_stale_local_copy']

    def test_month_old_copy_is_ignored_and_fresh_copy_is_used(self):
        import os, tempfile
        with tempfile.NamedTemporaryFile(suffix='.xml', delete=False) as handle:
            path = handle.name
        try:
            meta = {'ok': True, 'generated': '2026-11-14T07:47:38.829185+00:00'}
            os.utime(path, (1792000000, 1792000000))          # 14 Oct 2026
            self.assertEqual(self.stale(path, meta), 1792000000)
            os.utime(path, (1794640000, 1794640000))          # same day
            self.assertEqual(self.stale(path, meta), 0)
            self.assertEqual(self.stale(path, dict(meta, ok=False)), 0)
            self.assertEqual(self.stale(None, meta), 0)
        finally:
            os.unlink(path)


def module_function(path, name, extra=None):
    import os
    tree = ast.parse((PLUGIN / path).read_text(encoding='utf-8'))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ([name] + list(extra or []))]
    ns = {'os': os, 'time': time}
    exec(compile(ast.Module(body=fns, type_ignores=[]), path, 'exec'), ns)
    return ns


class ImportTimeLabel(unittest.TestCase):
    def setUp(self):
        self.ns = module_function('ui/settings.py', '_import_time_label',
                                  ['_utc_offset_minutes', '_format_offset', '_import_time_local_note'])

    def element(self, hh, mm):
        return type('E', (), {'value': [hh, mm]})()

    def test_utc_box(self):
        self.ns['_utc_offset_minutes'] = lambda: 0
        self.assertEqual(self.ns['_import_time_label'](), 'EPG Import start time (UTC)')
        self.assertIn('13:15 UTC is also 13:15', self.ns['_import_time_local_note'](self.element(13, 15)))

    def test_morocco_and_india(self):
        self.ns['_utc_offset_minutes'] = lambda: 60
        self.assertEqual(self.ns['_import_time_label'](), 'EPG Import start time (UTC; local UTC+1)')
        self.assertEqual(self.ns['_import_time_local_note'](self.element(13, 15)),
                         '13:15 UTC = 14:15 on this receiver (UTC+1).')
        self.ns['_utc_offset_minutes'] = lambda: 330
        self.assertEqual(self.ns['_import_time_local_note'](self.element(23, 0)),
                         '23:00 UTC = 04:30 on this receiver (UTC+5:30).')

    def test_new_york(self):
        self.ns['_utc_offset_minutes'] = lambda: -300
        self.assertEqual(self.ns['_import_time_label'](), 'EPG Import start time (UTC; local UTC-5)')
        self.assertEqual(self.ns['_import_time_local_note'](self.element(3, 0)),
                         '03:00 UTC = 22:00 on this receiver (UTC-5).')


class ReceiverTimeZone(unittest.TestCase):
    def test_check_is_information_only(self):
        ns = module_function('core/system_check.py', '_receiver_timezone_check')
        row = ns['_receiver_timezone_check']()
        self.assertEqual(row['name'], 'RECEIVER TIME ZONE')
        self.assertEqual(row['status'], 'INFO')
        self.assertIn('utc_offset_minutes', row)


class Rc87Package(unittest.TestCase):
    def test_version_strings(self):
        self.assertIn('__version__ = "2026.11.14-rc87"', (PLUGIN / 'version.py').read_text(encoding='utf-8'))
        self.assertIn('Version: 2026.11.14-rc87', (PLUGIN.parent / 'CONTROL/control').read_text(encoding='utf-8'))
        self.assertIn('CHANGELOG-rc87.txt', (PLUGIN / 'PACKAGE_MANIFEST.txt').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
