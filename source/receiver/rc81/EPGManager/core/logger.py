# -*- coding: utf-8 -*-
"""
core/logger.py

Centralized, rotating logger for EPG Manager.

Design notes
------------
- Enigma2 images differ in what's writable. We try, in order:
    1. /var/log                (most images, if not read-only-rootfs)
    2. /media/hdd/epgmanager    (if an HDD/USB is mounted - common on Vu+)
    3. /tmp/epgmanager          (always writable, but wiped on reboot)
  The first writable location wins and is cached for the process lifetime.
- Uses logging.handlers.RotatingFileHandler (stdlib) instead of hand-rolled
  rotation so behavior is well-defined and it costs zero extra dependencies.
- A module-level singleton is used so every core/source module can just call
  get_logger(__name__) and share the same handlers/log file.
"""

import logging
import logging.handlers
import os
import sys

_LOG_CANDIDATES = [
    "/var/log",
    "/media/hdd/epgmanager/logs",
    "/tmp/epgmanager/logs",
]

_MAX_BYTES = 2 * 1024 * 1024   # 2 MB per file
_BACKUP_COUNT = 3              # epgmanager.log, .log.1, .log.2, .log.3

_configured = False
_log_dir = None


def _pick_writable_dir():
    global _log_dir
    if _log_dir is not None:
        return _log_dir

    for candidate in _LOG_CANDIDATES:
        try:
            os.makedirs(candidate, exist_ok=True)
            test_file = os.path.join(candidate, ".wtest")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            _log_dir = candidate
            return _log_dir
        except Exception:
            continue

    # Should not happen, but never crash the plugin over logging.
    _log_dir = "/tmp"
    return _log_dir


def configure(level="INFO", debug=False):
    """Configure the root 'epgmanager' logger once. Safe to call multiple times."""
    global _configured
    logger = logging.getLogger("epgmanager")

    if _configured:
        logger.setLevel(logging.DEBUG if debug else _level_from_str(level))
        return logger

    logger.setLevel(logging.DEBUG if debug else _level_from_str(level))
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_dir = _pick_writable_dir()
    log_path = os.path.join(log_dir, "epgmanager.log")

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except Exception:
        # If even /tmp fails for some bizarre reason, fall back to stdout only.
        pass

    # Also mirror to stdout when run interactively (CLI debug mode / -e2 debug)
    if sys.stdout is not None:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(fmt)
        logger.addHandler(stream_handler)

    _configured = True
    logger.info("Logger initialized. Log directory: %s", log_dir)
    return logger


def _level_from_str(level):
    return getattr(logging, str(level).upper(), logging.INFO)


def get_logger(name=None):
    """Get a child logger under 'epgmanager'. Configures defaults if needed."""
    if not _configured:
        configure()
    if name:
        short = name.split(".")[-1]
        return logging.getLogger("epgmanager.%s" % short)
    return logging.getLogger("epgmanager")


def get_log_path():
    """Return the active log file path (useful for the Logs GUI screen)."""
    log_dir = _pick_writable_dir()
    return os.path.join(log_dir, "epgmanager.log")
