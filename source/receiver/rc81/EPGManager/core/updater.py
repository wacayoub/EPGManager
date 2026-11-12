# -*- coding: utf-8 -*-
"""Secure-ish online updater for EPG Manager.

The updater is intentionally repository-agnostic.  The receiver only needs a
small HTTPS JSON manifest.  The manifest points to the current IPK and may
include its SHA-256 digest.  Once the manifest URL is configured once, future
updates can be checked/downloaded/installed entirely from the remote control.
"""
from __future__ import absolute_import

import hashlib
import json
import os
import re
import subprocess
import tempfile
import shutil
import glob
import time

try:
    from urllib.parse import urljoin
except ImportError:  # pragma: no cover - Python 2 compatibility is not target, harmless fallback
    from urlparse import urljoin

from .logger import get_logger

log = get_logger(__name__)

UPDATER_SETTINGS = "/etc/enigma2/epgmanager_online_update.json"
DEFAULT_SETTINGS = {
    "manifest_url": "https://raw.githubusercontent.com/wacayoub/EPGManager/main/update.json",
    "channel": "stable",
    "github_repo": "wacayoub/EPGManager",
}


class UpdateError(Exception):
    pass


def _version_parts(value):
    """Return a SemVer-like tuple for stable/rc/beta/alpha Enigma builds.

    Stable releases sort after their prereleases:
    6.4.0-beta2 < 6.4.0-beta3 < 6.4.0-rc1 < 6.4.0 < 6.4.1.
    """
    text = str(value or "").strip().lower().lstrip("v")
    base_match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not base_match:
        return (0, 0, 0, 0, 0)
    base = tuple(int(x or 0) for x in base_match.groups())
    suffix = text[base_match.end():].lstrip("-._ ")
    if not suffix:
        return base + (4, 0)  # stable
    stage = 0
    number = 0
    m = re.search(r"(rc|beta|b|alpha|a|dev|pre)[-._ ]*(\d*)", suffix)
    if m:
        label = m.group(1)
        number = int(m.group(2) or 0)
        if label == "rc": stage = 3
        elif label in ("beta", "b"): stage = 2
        elif label in ("alpha", "a"): stage = 1
        else: stage = 0
    return base + (stage, number)


def is_newer_version(remote, local):
    return _version_parts(remote) > _version_parts(local)


def load_updater_settings(path=UPDATER_SETTINGS):
    data = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(path):
            with open(path, "r") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data.update(loaded)
                # A settings file created by an older build may contain an empty
                # manifest_url.  Fall back to the official GitHub repository so
                # Online Update works without entering the URL manually.
                if not str(data.get("manifest_url") or "").strip():
                    data["manifest_url"] = DEFAULT_SETTINGS["manifest_url"]
    except Exception as exc:
        log.warning("Could not read updater settings %s: %s", path, exc)
    return data


def save_updater_settings(data, path=UPDATER_SETTINGS):
    payload = dict(DEFAULT_SETTINGS)
    payload.update(data or {})
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.rename(tmp, path)
    return payload


def normalize_manifest(payload, manifest_url=""):
    if not isinstance(payload, dict):
        raise UpdateError("Update manifest is not a JSON object")
    version = str(payload.get("version") or "").strip()
    url = str(payload.get("url") or payload.get("ipk") or "").strip()
    if not version:
        raise UpdateError("Update manifest has no version")
    if not url:
        raise UpdateError("Update manifest has no IPK URL")
    if manifest_url:
        url = urljoin(manifest_url, url)
    sha256 = str(payload.get("sha256") or "").strip().lower()
    if sha256 and not re.match(r"^[0-9a-f]{64}$", sha256):
        raise UpdateError("Manifest SHA-256 is invalid")
    try:
        size = int(payload.get("size") or 0)
    except Exception:
        size = 0
    return {
        "version": version,
        "build": str(payload.get("build") or ""),
        "url": url,
        "sha256": sha256,
        "size": max(0, size),
        "notes": str(payload.get("notes") or payload.get("changelog") or ""),
        "mandatory": bool(payload.get("mandatory", False)),
    }


def create_safety_backup(label="online-update"):
    """Back up only authoritative/sensitive EPGManager user state.

    Rebuildable XMLTV, indexes, channel caches, telemetry and logs are never
    copied. Backup history is bounded to avoid flash accumulation.
    """
    from . import storage_hygiene
    root = "/etc/epgmanager_epg/backups"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    target = os.path.join(root, "%s-%s" % (re.sub(r"[^0-9A-Za-z._-]", "_", label), stamp))
    copied = 0
    try:
        if not os.path.isdir(target):
            os.makedirs(target)
        for rel in storage_hygiene.SENSITIVE_RELATIVE:
            src = "/" + str(rel).lstrip("/")
            if not os.path.isfile(src):
                continue
            name = src.strip("/").replace("/", "__")
            try:
                shutil.copy2(src, os.path.join(target, name))
                copied += 1
            except Exception:
                pass
        # Enigma2-native settings live inside /etc/enigma2/settings. Preserve
        # only this plugin's lines instead of copying the receiver-wide file.
        settings = "/etc/enigma2/settings"
        if os.path.isfile(settings):
            try:
                rows = [line for line in open(settings, "r", encoding="utf-8", errors="ignore")
                        if line.startswith("config.plugins.epgmanager.")]
                if rows:
                    with open(os.path.join(target, "enigma2__epgmanager_settings.conf"), "w", encoding="utf-8") as fh:
                        fh.writelines(rows)
                    copied += 1
            except Exception:
                pass
        if copied:
            try:
                with open(os.path.join(root, "latest"), "w") as handle:
                    handle.write(target + "\n")
            except Exception:
                pass
            storage_hygiene.rotate_sensitive_backups(root="/", keep=3)
            return target
        try:
            os.rmdir(target)
        except Exception:
            pass
    except Exception as exc:
        log.warning("Could not create update safety backup: %s", exc)
    return ""


def _github_release_info(payload, repo):
    if not isinstance(payload, dict):
        raise UpdateError("GitHub release response is invalid")
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    version = tag.lstrip("vV")
    if not version:
        raise UpdateError("GitHub release has no version")
    assets = payload.get("assets") or []
    ipk = None
    for asset in assets:
        name = str((asset or {}).get("name") or "")
        if name.lower().endswith(".ipk") and "epgmanager" in name.lower():
            ipk = asset
            break
    if not ipk:
        raise UpdateError("GitHub release has no EPGManager IPK asset")
    return {
        "version": version,
        "build": "github-release",
        "url": str(ipk.get("browser_download_url") or ""),
        "sha256": "",
        "size": int(ipk.get("size") or 0),
        "notes": str(payload.get("body") or "GitHub stable release"),
        "mandatory": False,
        "source": "github:%s" % repo,
    }


class OnlineUpdater(object):
    def __init__(self, local_version, timeout=20):
        self.local_version = str(local_version)
        self.timeout = max(5, int(timeout))
        self.manifest = None

    def _requests(self):
        try:
            import requests
            return requests
        except ImportError:
            raise UpdateError("python3-requests is required for Online Update")

    def check(self, manifest_url):
        """Check the official stable manifest and GitHub latest release, then keep the newest."""
        manifest_url = str(manifest_url or "").strip()
        settings = load_updater_settings()
        repo = str(settings.get("github_repo") or DEFAULT_SETTINGS["github_repo"]).strip()
        requests = self._requests()
        candidates = []
        errors = []

        if manifest_url:
            if not manifest_url.lower().startswith(("https://", "http://")):
                raise UpdateError("Manifest URL must start with https:// or http://")
            try:
                response = requests.get(
                    manifest_url, timeout=self.timeout,
                    headers={"User-Agent": "EPGManager/%s" % self.local_version},
                )
                response.raise_for_status()
                info = normalize_manifest(response.json(), manifest_url)
                info["source"] = "github-manifest"
                candidates.append(info)
            except Exception as exc:
                errors.append("manifest: %s" % exc)

        # Stable GitHub Releases fallback also protects against a stale update.json.
        if repo and "/" in repo:
            try:
                api = "https://api.github.com/repos/%s/releases/latest" % repo
                response = requests.get(
                    api, timeout=self.timeout,
                    headers={
                        "User-Agent": "EPGManager/%s" % self.local_version,
                        "Accept": "application/vnd.github+json",
                    },
                )
                response.raise_for_status()
                candidates.append(_github_release_info(response.json(), repo))
            except Exception as exc:
                errors.append("github: %s" % exc)

        if not candidates:
            raise UpdateError("Cannot check GitHub updates: %s" % (" | ".join(errors) or "no stable source"))
        info = max(candidates, key=lambda item: _version_parts(item.get("version")))
        info["newer"] = is_newer_version(info["version"], self.local_version)
        self.manifest = info
        return dict(info)

    def download(self, manifest=None, progress=None):
        info = dict(manifest or self.manifest or {})
        if not info.get("url"):
            raise UpdateError("No update package is available")
        requests = self._requests()
        target = "/tmp/epgmanager_%s.ipk" % re.sub(r"[^0-9A-Za-z._-]", "_", info.get("version", "update"))
        tmp = target + ".part"
        digest = hashlib.sha256()
        received = 0
        expected = int(info.get("size") or 0)
        try:
            response = requests.get(
                info["url"], stream=True, timeout=self.timeout,
                headers={"User-Agent": "EPGManager/%s" % self.local_version},
            )
            response.raise_for_status()
            if not expected:
                try:
                    expected = int(response.headers.get("Content-Length") or 0)
                except Exception:
                    expected = 0
            with open(tmp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress:
                        progress(received, expected)
            if received < 1024:
                raise UpdateError("Downloaded package is unexpectedly small")
            expected_sha = str(info.get("sha256") or "").lower()
            actual_sha = digest.hexdigest().lower()
            if expected_sha and actual_sha != expected_sha:
                raise UpdateError("SHA-256 verification failed")
            if os.path.exists(target):
                os.unlink(target)
            os.rename(tmp, target)
            return target, actual_sha, received
        except UpdateError:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
            raise UpdateError("Update download failed: %s" % exc)

    def install(self, ipk_path):
        if not ipk_path or not os.path.exists(ipk_path):
            raise UpdateError("Downloaded IPK file is missing")
        # Final releases always take a tiny authoritative-state backup before opkg.
        create_safety_backup("pre-online-update-%s" % self.local_version)
        cmd = ["opkg", "install", "--force-reinstall", ipk_path]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            output = proc.communicate()[0]
            if not isinstance(output, str):
                output = output.decode("utf-8", "replace")
        except Exception as exc:
            raise UpdateError("Could not start opkg: %s" % exc)
        if proc.returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-8:])
            raise UpdateError("opkg failed (code %d)\n%s" % (proc.returncode, tail))
        return output
