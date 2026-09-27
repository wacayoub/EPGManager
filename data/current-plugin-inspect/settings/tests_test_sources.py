# -*- coding: utf-8 -*-
"""
tests/test_sources.py

Tests the EPGSource base class contract: a source that raises must never
propagate the exception to the Manager, network failures must be retried
per config.retry_count, and one source's failure must not affect another's
result (spec sections 11/19/20).

Run with:
    python3 -m unittest EPGManager.tests.test_sources -v
"""

import unittest
from unittest.mock import patch, MagicMock

from ..sources.base import EPGSource, UpdateResult
from ..core.downloader import DownloadError
from ..core.parser import SourceWarning


class _FakeConfig(object):
    def get_epg_days(self): return 1
    def get_retry_count(self): return 2
    def get_timeout_seconds(self): return 5
    def get_parallel_workers(self): return 2
    def set_last_update(self, ts): pass


class _AlwaysFailsSource(EPGSource):
    id = "always_fails"
    name = "Always Fails"
    output_filename = "does_not_matter.xml"

    def fetch(self, downloader, tzm, days, config, builder):
        raise RuntimeError("boom")


class _EmptyResultSource(EPGSource):
    id = "empty_result"
    name = "Empty Result"
    output_filename = "does_not_matter2.xml"

    def fetch(self, downloader, tzm, days, config, builder):
        return []


class _NetworkFailureSource(EPGSource):
    id = "network_failure"
    name = "Network Failure"
    output_filename = "does_not_matter3.xml"

    def fetch(self, downloader, tzm, days, config, builder):
        raise DownloadError("simulated network failure")


class TestSourceIsolation(unittest.TestCase):
    def test_unexpected_exception_returns_failed_update_result_not_raise(self):
        source = _AlwaysFailsSource()
        result = source.update(config=_FakeConfig())
        self.assertIsInstance(result, UpdateResult)
        self.assertFalse(result.ok)
        self.assertIn("boom", result.message)

    def test_empty_fetch_result_is_a_warning_not_a_crash(self):
        source = _EmptyResultSource()
        result = source.update(config=_FakeConfig())
        self.assertFalse(result.ok)
        self.assertEqual(result.program_count, 0)

    def test_download_error_is_caught_and_reported(self):
        source = _NetworkFailureSource()
        result = source.update(config=_FakeConfig())
        self.assertFalse(result.ok)
        self.assertIn("Network error", result.message)


class TestManagerRunsSourcesIndependently(unittest.TestCase):
    def test_one_failing_source_does_not_block_others(self):
        from ..core.manager import Manager
        from ..core.config import Config

        class _WorkingSource(EPGSource):
            id = "working"
            name = "Working"
            output_filename = "does_not_matter4.xml"

            def update(self, config, cancel_event=None):
                return UpdateResult(True, 42, "OK")

        manager = Manager(config=_FakeConfig())
        manager.register(_AlwaysFailsSource())
        manager.register(_WorkingSource())

        # Patch is_source_enabled since _FakeConfig doesn't implement it.
        manager.config.is_source_enabled = lambda sid: True

        done = {}

        def on_complete(results):
            done.update(results)

        manager._run_all(on_complete)  # synchronous call for deterministic test

        self.assertFalse(done["always_fails"])

    def test_last_update_is_persisted_even_when_every_source_fails(self):
        """Regression test for the 2026-08-09 bug: Scheduler.mark_update_
        finished() (which calls config.set_last_update()) existed but was
        NEVER actually invoked anywhere in the real flow, so
        is_update_due() saw last_update=None forever and the scheduler
        re-triggered a full update every 5 minutes indefinitely - which is
        what got SNRT to IP-ban the box. _run_all() must call
        set_last_update() unconditionally, success or failure, so a
        persistently-failing/blocked source stops being hammered."""
        from ..core.manager import Manager

        calls = []

        class _RecordingConfig(_FakeConfig):
            def is_source_enabled(self, sid):
                return True

            def set_last_update(self, ts):
                calls.append(ts)

        manager = Manager(config=_RecordingConfig())
        manager.register(_AlwaysFailsSource())

        manager._run_all(on_complete=None)

        self.assertEqual(len(calls), 1, "set_last_update must be called exactly once per cycle")

    def test_manual_single_source_update_also_persists_last_update(self):
        """Regression test for the 2026-08-09 bug: _run_one_wrapper()
        (used by the dashboard's checkbox 'Update Selected', the '9'
        quick-update shortcut, and the Sources screen's per-source update)
        never called set_last_update() at all - only a full scheduler
        cycle through _run_all() did. Since manual single-source updates
        are the primary way most people interact with the dashboard,
        'Last Update' could show 'Never' or a stale time indefinitely even
        right after a successful manual update."""
        from ..core.manager import Manager

        calls = []

        class _WorkingSource(EPGSource):
            id = "manual_working"
            name = "Manual Working"
            output_filename = "manual_working.xml"
            def update(self, config, cancel_event=None):
                return UpdateResult(True, 10, "OK")

        class _RecordingConfig(_FakeConfig):
            def is_source_enabled(self, sid):
                return True
            def set_last_update(self, ts):
                calls.append(ts)

        manager = Manager(config=_RecordingConfig())
        source = _WorkingSource()
        manager.register(source)

        done = {}
        manager._run_one_wrapper(source.id, on_complete=lambda r: done.update(r))

        self.assertEqual(len(calls), 1, "set_last_update must be called after a manual single-source update")
        self.assertTrue(done[source.id])


class TestConfigurableOutputPath(unittest.TestCase):
    """Regression coverage for the configurable EPG export directory
    (Settings -> 'EPG export directory')."""

    def test_defaults_to_the_historical_directory_when_config_lacks_the_method(self):
        source = _AlwaysFailsSource()
        path = source.get_output_path(_FakeConfig())  # no get_epg_output_dir()
        self.assertEqual(path, "/etc/epgimport/jedi_epg/does_not_matter.xml")

    def test_defaults_when_config_is_none(self):
        source = _AlwaysFailsSource()
        path = source.get_output_path(None)
        self.assertEqual(path, "/etc/epgimport/jedi_epg/does_not_matter.xml")

    def test_uses_configured_directory(self):
        class _ConfigWithCustomDir(_FakeConfig):
            def get_epg_output_dir(self):
                return "/media/hdd/epg"

        source = _AlwaysFailsSource()
        path = source.get_output_path(_ConfigWithCustomDir())
        self.assertEqual(path, "/media/hdd/epg/does_not_matter.xml")

    def test_update_populates_output_path_before_fetching(self):
        class _ConfigWithCustomDir(_FakeConfig):
            def get_epg_output_dir(self):
                return "/tmp/custom_epg_dir"

        source = _AlwaysFailsSource()
        source.update(config=_ConfigWithCustomDir())
        self.assertEqual(source.output_path, "/tmp/custom_epg_dir/does_not_matter.xml")

    def test_broken_get_epg_output_dir_falls_back_gracefully(self):
        class _BrokenConfig(_FakeConfig):
            def get_epg_output_dir(self):
                raise RuntimeError("boom")

        source = _AlwaysFailsSource()
        path = source.get_output_path(_BrokenConfig())
        self.assertEqual(path, "/etc/epgimport/jedi_epg/does_not_matter.xml")


class TestSourceCooldown(unittest.TestCase):
    """The second, independent guard against hammering a site: a source
    can't be re-attempted within MIN_SOURCE_COOLDOWN_SECONDS, regardless
    of who's triggering it (protects against a person mashing 'Update
    Selected' on the same source repeatedly, not just the scheduler bug)."""

    def test_second_attempt_within_cooldown_is_skipped(self):
        from ..core import manager as manager_module

        class _RecordingConfig(_FakeConfig):
            def is_source_enabled(self, sid):
                return True
            def set_last_update(self, ts):
                pass

        m = manager_module.Manager(config=_RecordingConfig())
        source = _AlwaysFailsSource()
        m.register(source)

        first = m._run_one(source)
        second = m._run_one(source)  # immediately again, well within cooldown

        self.assertFalse(first)
        self.assertFalse(second)
        status = m.get_status(source.id)
        self.assertEqual(status["status"], manager_module.STATUS_COOLDOWN)

    def test_cooldown_does_not_affect_a_different_source(self):
        from ..core import manager as manager_module

        class _WorkingSource(EPGSource):
            id = "working2"
            name = "Working2"
            output_filename = "does_not_matter5.xml"
            def update(self, config, cancel_event=None):
                return UpdateResult(True, 5, "OK")

        class _RecordingConfig(_FakeConfig):
            def is_source_enabled(self, sid):
                return True
            def set_last_update(self, ts):
                pass

        m = manager_module.Manager(config=_RecordingConfig())
        failing = _AlwaysFailsSource()
        working = _WorkingSource()
        m.register(failing)
        m.register(working)

        m._run_one(failing)
        m._run_one(failing)  # on cooldown now
        result = m._run_one(working)  # different source - must NOT be blocked

        self.assertTrue(result)


class TestDownloaderRetry(unittest.TestCase):
    @patch("EPGManager.core.downloader.requests")
    def test_retries_transient_errors_then_succeeds(self, mock_requests):
        from ..core.downloader import Downloader

        ok_response = MagicMock(status_code=200, text="<html>ok</html>")
        fail_response = MagicMock(status_code=503, text="")

        mock_session = MagicMock()
        mock_session.get.side_effect = [fail_response, fail_response, ok_response]
        mock_requests.Session.return_value = mock_session

        d = Downloader(retries=3, timeout=1, backoff_base=0.01)
        resp = d.get("https://example.invalid/test")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_session.get.call_count, 3)

    @patch("EPGManager.core.downloader.requests")
    def test_gives_up_after_max_retries(self, mock_requests):
        from ..core.downloader import Downloader

        fail_response = MagicMock(status_code=503, text="")
        mock_session = MagicMock()
        mock_session.get.return_value = fail_response
        mock_requests.Session.return_value = mock_session

        d = Downloader(retries=3, timeout=1, backoff_base=0.01)
        with self.assertRaises(DownloadError):
            d.get("https://example.invalid/test")
        self.assertEqual(mock_session.get.call_count, 3)


if __name__ == "__main__":
    unittest.main()
