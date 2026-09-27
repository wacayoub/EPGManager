# -*- coding: utf-8 -*-
"""
sources/base.py

Common interface every EPG source module implements (spec section 11).
"""

import os

from ..core.logger import get_logger
from ..core.downloader import Downloader, DownloadError, CancelledError
from ..core.timezone_manager import TimezoneManager
from ..core.xmltv import XMLTVBuilder
from ..core.parser import SourceWarning
from ..core.config import DEFAULT_EPG_OUTPUT_DIR


class UpdateResult(object):
    def __init__(self, ok, program_count=0, message="", days_covered=0,
                 date_from=None, date_to=None, cancelled=False):
        self.ok = ok
        self.program_count = program_count
        self.message = message
        self.days_covered = days_covered
        self.date_from = date_from
        self.date_to = date_to
        self.cancelled = cancelled

    def __repr__(self):
        return "<UpdateResult ok=%s programs=%d days=%d cancelled=%s message=%r>" % (
            self.ok, self.program_count, self.days_covered, self.cancelled, self.message)


class EPGSource(object):
    """Base class for all EPG sources. Subclasses implement `fetch()` which
    returns a list of core.xmltv.Programme objects (and register their
    channels via `self.builder.add_channel(...)` inside fetch()).

    update() is the only method the Manager calls; it wires together the
    downloader/timezone/xmltv objects, handles all exceptions, and always
    returns an UpdateResult - it never raises, so one broken source can
    never take down the update cycle for the others.
    """

    id = "base"
    name = "Base Source"
    output_filename = None  # e.g. "medi1tv_ar.xml" - subclasses set this;
                              # combined with config.get_epg_output_dir() at
                              # update() time so the export path is
                              # configurable from Settings, not hardcoded
    default_days = 7
    max_days = None       # hard cap for sources whose site can't return more
                            # (e.g. Almajd's real forward window), regardless
                            # of the operator's global "EPG days" setting
    min_programs = 1
    use_cloudscraper = False
    verify_ssl = True      # sources can override to False if their site's
                             # cert chain genuinely requires it (see
                             # sources/bein_sports.py for a documented case)

    def __init__(self):
        self.log = get_logger(self.id)
        self.output_path = None  # populated by get_output_path()/update()

    def get_output_path(self, config=None):
        output_dir = DEFAULT_EPG_OUTPUT_DIR
        if config is not None and hasattr(config, "get_epg_output_dir"):
            try:
                output_dir = config.get_epg_output_dir()
            except Exception:
                pass
        return os.path.join(output_dir, self.output_filename)

    def fetch(self, downloader, tzm, days, config, builder):
        """Subclasses must override. Return a list of core.xmltv.Programme
        objects; call builder.add_channel(...) for each channel along the
        way (builder is a fresh core.xmltv.XMLTVBuilder for this run)."""
        raise NotImplementedError

    def update(self, config, cancel_event=None):
        days = getattr(config, "get_epg_days", lambda: self.default_days)()
        if self.max_days is not None:
            days = min(days, self.max_days)
        retries = config.get_retry_count() if config else 3
        timeout = config.get_timeout_seconds() if config else 15
        self.output_path = self.get_output_path(config)

        downloader = Downloader(
            retries=retries, timeout=timeout, use_cloudscraper=self.use_cloudscraper,
            verify_ssl=self.verify_ssl, cancel_event=cancel_event,
        )
        tzm = TimezoneManager()
        builder = XMLTVBuilder(generator_info_name_safe(self.name), tzm=tzm)

        try:
            programmes = self.fetch(downloader, tzm, days, config, builder)
            if not programmes:
                raise SourceWarning(
                    "%s returned zero programmes" % self.name)

            builder.add_programmes(programmes)
            days_covered, date_from, date_to = builder.get_date_range()
            result = builder.save(self.output_path, min_programs=self.min_programs)

            if not result.ok:
                return UpdateResult(False, 0, "; ".join(result.errors))

            return UpdateResult(True, result.program_count, "OK",
                                 days_covered=days_covered,
                                 date_from=date_from, date_to=date_to)

        except CancelledError:
            self.log.info("%s: update cancelled by user", self.name)
            return UpdateResult(False, 0, "Cancelled by user", cancelled=True)
        except SourceWarning as w:
            self.log.warning(str(w))
            return UpdateResult(False, 0, str(w))
        except DownloadError as e:
            self.log.error("Download failed: %s", e)
            return UpdateResult(False, 0, "Network error: %s" % e)
        except Exception as e:
            self.log.exception("Unexpected error in %s", self.id)
            return UpdateResult(False, 0, "Unexpected error: %s" % e)
        finally:
            downloader.close()

    def validate(self, config=None):
        """Lightweight self-check used by the GUI's 'Sources' screen before
        a manual run (e.g. confirm output directory is writable)."""
        try:
            path = self.get_output_path(config)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return True
        except Exception:
            return False

    def get_status(self):
        return {"id": self.id, "name": self.name}


def generator_info_name_safe(name):
    return "EPG Manager - %s" % name
