# -*- coding: utf-8 -*-
"""
core/downloader.py

One shared HTTP client used by every source module, replacing the five
different ad-hoc retry/timeout patterns in the original scripts (Medi1TV had
none, SNRT/Arryadia had a plain single try, 2M/Chada's `_safe_get` had the
most complete 3-attempt loop, beIN had a 2-attempt inner loop).

Handles: timeouts, DNS errors, SSL errors, connection reset, HTTP 403/404/
429/5xx, empty responses - and never lets a broken site hang Enigma2, since
callers always run this inside a background worker thread (see
core/manager.py), never on the GUI thread.
"""

import time
import random

from .logger import get_logger
from . import dependencies

log = get_logger(__name__)

try:
    import requests
    from requests.exceptions import (
        ConnectionError as ReqConnectionError,
        Timeout as ReqTimeout,
        SSLError as ReqSSLError,
        RequestException,
    )
except ImportError:
    requests = None

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,fr;q=0.8,en;q=0.6",
}

# HTTP statuses worth retrying (transient); others fail fast.
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class DownloadError(Exception):
    """Raised (and caught by the source's own error handling) when every
    retry attempt has been exhausted."""
    def __init__(self, message, status_code=None):
        super(DownloadError, self).__init__(message)
        self.status_code = status_code


class CancelledError(DownloadError):
    """Raised when a cancel_event was set (person pressed the Cancel
    button) - propagated all the way up to sources/base.py, which reports
    a distinct CANCELLED status instead of FAILED/WARNING. Unlike other
    DownloadErrors, Downloader.get_or_none() re-raises this rather than
    swallowing it, so a cancellation is never silently treated as "this
    one page just failed"."""
    pass


class Downloader(object):
    def __init__(self, retries=3, timeout=15, backoff_base=1.5,
                 headers=None, verify_ssl=True, use_cloudscraper=False,
                 cancel_event=None):
        self.retries = max(1, retries)
        self.timeout = timeout
        self.backoff_base = backoff_base
        self.headers = dict(DEFAULT_HEADERS)
        if headers:
            self.headers.update(headers)
        self.verify_ssl = verify_ssl
        self.cancel_event = cancel_event  # threading.Event or None; checked
                                            # before every request attempt
        if not verify_ssl:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        self._session = None
        self._use_cloudscraper = use_cloudscraper and dependencies.has_cloudscraper()

        if self._use_cloudscraper:
            try:
                import cloudscraper
                self._session = cloudscraper.create_scraper()
                log.debug("Downloader using cloudscraper backend")
            except Exception as e:
                log.warning("cloudscraper requested but failed to init (%s); "
                            "falling back to requests", e)
                self._use_cloudscraper = False

        if self._session is None and requests is not None:
            self._session = requests.Session()

    def get(self, url, params=None, data=None, method="GET", extra_headers=None):
        """GET or POST with retry/backoff. Raises DownloadError on final
        failure; callers should catch this and report a per-source warning
        rather than crashing the whole update cycle. Raises CancelledError
        immediately (before making any request) if cancel_event is set."""
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise CancelledError("Update cancelled by user")

        if requests is None:
            raise DownloadError(
                "The 'requests' module is not installed. Install it via "
                "your image's package manager (opkg install python3-requests)."
            )

        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)

        last_error = None
        last_status = None

        for attempt in range(1, self.retries + 1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise CancelledError("Update cancelled by user")
            try:
                if method.upper() == "POST":
                    resp = self._session.post(
                        url, data=data, headers=headers,
                        timeout=self.timeout, verify=self.verify_ssl,
                    )
                else:
                    resp = self._session.get(
                        url, params=params, headers=headers,
                        timeout=self.timeout, verify=self.verify_ssl,
                    )

                if resp.status_code == 200:
                    if not resp.text:
                        last_error = "Empty response body"
                        log.warning("Empty response from %s (attempt %d/%d)",
                                    url, attempt, self.retries)
                    else:
                        return resp

                elif resp.status_code in RETRYABLE_STATUSES:
                    last_status = resp.status_code
                    last_error = "HTTP %d" % resp.status_code
                    log.warning("Retryable HTTP %d from %s (attempt %d/%d)",
                                resp.status_code, url, attempt, self.retries)

                else:
                    # 403/404/etc: not transient, fail fast without burning
                    # the remaining retry budget.
                    raise DownloadError(
                        "HTTP %d for %s" % (resp.status_code, url),
                        status_code=resp.status_code,
                    )

            except DownloadError:
                raise
            except ReqSSLError as e:
                last_error = "SSL error: %s" % e
                log.warning("SSL error on %s (attempt %d/%d): %s",
                            url, attempt, self.retries, e)
            except ReqTimeout as e:
                last_error = "Timeout: %s" % e
                log.warning("Timeout on %s (attempt %d/%d)", url, attempt, self.retries)
            except ReqConnectionError as e:
                last_error = "Connection error (DNS/refused/reset): %s" % e
                log.warning("Connection error on %s (attempt %d/%d): %s",
                            url, attempt, self.retries, e)
            except RequestException as e:
                last_error = str(e)
                log.warning("Request exception on %s (attempt %d/%d): %s",
                            url, attempt, self.retries, e)

            if attempt < self.retries:
                sleep_for = (self.backoff_base ** attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_for)

        raise DownloadError(
            "Failed to fetch %s after %d attempts: %s" % (url, self.retries, last_error),
            status_code=last_status,
        )

    def fork(self, retries=None, timeout=None, backoff_base=None, headers=None):
        """Create an independent HTTP client for bounded parallel fetches.

        A requests.Session should not be shared by several worker threads.
        Sources such as beIN therefore create one small child Downloader per
        worker while inheriting cancellation, TLS and header policy.
        """
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)
        return Downloader(
            retries=self.retries if retries is None else retries,
            timeout=self.timeout if timeout is None else timeout,
            backoff_base=self.backoff_base if backoff_base is None else backoff_base,
            headers=merged_headers,
            verify_ssl=self.verify_ssl,
            use_cloudscraper=self._use_cloudscraper,
            cancel_event=self.cancel_event,
        )

    def get_or_none(self, url, **kwargs):
        """Convenience wrapper for source modules that want to continue with
        an empty result set instead of propagating the exception.
        CancelledError is the one exception NOT swallowed here - it must
        reach sources/base.py so a cancelled run is reported distinctly
        from a merely-failed one."""
        try:
            return self.get(url, **kwargs)
        except CancelledError:
            raise
        except DownloadError as e:
            log.error("%s", e)
            return None

    def close(self):
        try:
            if self._session:
                self._session.close()
        except Exception:
            pass
