# -*- coding: utf-8 -*-
"""Safe, non-blocking GitHub updater for EPGManager on Enigma2/OpenATV.

Network and backup work happens in a worker thread. Only the eTimer callback
updates Enigma2 widgets or launches the installer console on the GUI thread.
"""
from __future__ import print_function

import hashlib
import json
import os
import re
import subprocess
import tarfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from enigma import eTimer
from Components.ActionMap import ActionMap
from Components.Button import Button
from Components.Label import Label
from Screens.Console import Console
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen

from .version import __version__

MANIFEST_URL = "https://raw.githubusercontent.com/wacayoub/EPGManager/main/update.json"
TMP_IPK = "/tmp/epgmanager-online-update.ipk"
STATUS_FILE = "/tmp/epgmanager-online-update.status"
BACKUP_DIR = "/etc/enigma2/epgmanager-update-backups"


def _version_key(value):
    return tuple(int(x) for x in re.findall(r"\d+", str(value or ""))) or (0,)


def _is_newer(remote, local):
    return _version_key(remote) > _version_key(local)


def _read_url(url, timeout=8):
    request = Request(url, headers={"User-Agent": "EPGManager/%s" % __version__})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception:
        return subprocess.check_output(["wget", "-qO-", url], timeout=timeout + 4)


def _validated_manifest(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid update manifest")
    for key in ("version", "url", "sha256", "size"):
        if key not in data:
            raise ValueError("Missing manifest field: %s" % key)
    version = str(data["version"])
    sha = str(data["sha256"]).lower()
    url = str(data["url"])
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", version):
        raise ValueError("Invalid release version")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("A valid SHA256 is mandatory")
    try:
        size = int(data["size"])
    except (TypeError, ValueError):
        raise ValueError("Invalid package size")
    if not 0 < size < 200 * 1024 * 1024:
        raise ValueError("Package size outside safety range")
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "github.com"
            or not parts.path.startswith("/wacayoub/EPGManager/releases/download/")
            or not parts.path.endswith(".ipk")
            or parts.query or parts.fragment):
        raise ValueError("Untrusted release download URL")
    return dict(data, version=version, url=url, sha256=sha, size=size)


def _download(url, target):
    staging = target + ".part"
    try:
        os.unlink(staging)
    except OSError:
        pass
    try:
        subprocess.check_call(["wget", "-q", "-O", staging, url], timeout=90)
        os.replace(staging, target)
    finally:
        try:
            os.unlink(staging)
        except OSError:
            pass


def _verify(path, sha256_value, size_value):
    expected_sha = str(sha256_value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("Missing or invalid release SHA256")
    expected_size = int(size_value)
    if expected_size <= 0 or os.path.getsize(path) != expected_size:
        raise ValueError("Release package size mismatch")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected_sha:
        raise ValueError("Release package SHA256 mismatch")
    return True


def _make_backup():
    """Backup before opkg changes any plugin files. Restore is manual for now."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, "pre-update-%s.tar.gz" % time.strftime("%Y%m%d-%H%M%S"))
    plugin_path = os.path.dirname(os.path.abspath(__file__))
    with tarfile.open(target, "w:gz") as archive:
        archive.add(plugin_path, arcname="EPGManager", recursive=True)
        for name in ("settings", "epgmanager_mappings.json",
                     "epgmanager_source_preferences.json"):
            path = os.path.join("/etc/enigma2", name)
            if os.path.isfile(path):
                archive.add(path, arcname="enigma2/" + name)
    os.chmod(target, 0o600)
    return target


class EPGManagerOnlineUpdate(Screen):
    skin = """
    <screen name="EPGManagerOnlineUpdate" position="center,center" size="1120,610" title="EPG Manager - Online Update">
        <widget name="title" position="40,30" size="1040,52" font="Regular;34" />
        <widget name="current" position="40,105" size="1040,38" font="Regular;26" />
        <widget name="available" position="40,150" size="1040,38" font="Regular;26" />
        <widget name="status" position="40,205" size="1040,48" font="Regular;28" />
        <widget name="notes" position="40,270" size="1040,190" font="Regular;23" />
        <widget name="key_red" position="40,525" size="240,50" font="Regular;24" />
        <widget name="key_green" position="300,525" size="240,50" font="Regular;24" />
        <widget name="key_yellow" position="560,525" size="240,50" font="Regular;24" />
        <widget name="key_blue" position="820,525" size="260,50" font="Regular;24" />
    </screen>
    """

    def __init__(self, session):
        Screen.__init__(self, session)
        self.manifest = None
        self._busy = False
        self._pending = None
        self._closed = False
        self._backup_path = None
        self["title"] = Label("EPG Manager - Online Update")
        self["current"] = Label("Installed: %s" % __version__)
        self["available"] = Label("Available: not checked")
        self["status"] = Label("Press YELLOW to check GitHub")
        self["notes"] = Label("")
        self["key_red"] = Button("Close")
        self["key_green"] = Button("Install")
        self["key_yellow"] = Button("Check")
        self["key_blue"] = Button("Reinstall locked")
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "green": self.install_update,
                "yellow": self.check_update,
                "blue": self.force_reinstall,
                "ok": self.install_update,
            }, -1,
        )
        self._poll_timer = eTimer()
        try:
            self._poll_timer.callback.append(self._poll_job)
        except AttributeError:
            self._poll_timer.timeout.get().append(self._poll_job)
        self.onClose.append(self._cleanup)
        self.onLayoutFinish.append(self.check_update)

    def _cleanup(self):
        self._closed = True
        self._poll_timer.stop()

    def _start_job(self, kind, callback):
        if self._busy or self._closed:
            return False
        self._busy = True
        self._pending = None

        def run():
            try:
                payload = (True, callback())
            except Exception as exc:
                payload = (False, str(exc))
            self._pending = (kind, payload)

        thread = threading.Thread(target=run, name="EPGManagerUpdate-" + kind)
        thread.daemon = True
        thread.start()
        self._poll_timer.start(150, False)
        return True

    def _poll_job(self):
        if self._closed:
            return
        result = self._pending
        if result is None:
            return
        self._pending = None
        self._busy = False
        self._poll_timer.stop()
        kind, payload = result
        ok, data = payload
        if not ok:
            self["status"].setText("Update check failed" if kind == "check" else "Download or backup failed")
            self["notes"].setText(str(data)[:500])
            if kind == "download":
                self.session.open(MessageBox, "Update canceled safely:\n%s" % data, MessageBox.TYPE_ERROR)
            return

        if kind == "check":
            self.manifest = data
            remote = data["version"]
            self["available"].setText("Available: %s" % remote)
            self["notes"].setText(str(data.get("notes") or ""))
            if _is_newer(remote, __version__):
                self["status"].setText("New version available - GREEN to install")
            elif remote == str(__version__):
                self["status"].setText("EPG Manager is up to date")
            else:
                self["status"].setText("Installed version is newer than GitHub")
        elif kind == "download":
            self._backup_path = data
            self["status"].setText("Verified; backup saved; installing...")
            self._install_verified_ipk()

    def check_update(self):
        if self._busy:
            return
        self.manifest = None
        self["status"].setText("Checking GitHub in background...")
        self._start_job("check", lambda: _validated_manifest(
            json.loads(_read_url(MANIFEST_URL).decode("utf-8"))))

    def install_update(self):
        if self._busy:
            return
        if not self.manifest:
            self["status"].setText("Check updates first with YELLOW")
            return
        remote = self.manifest["version"]
        if not _is_newer(remote, __version__):
            self.session.open(MessageBox, "No newer version available. Forced reinstall is disabled to protect receiver fixes.", MessageBox.TYPE_INFO, timeout=8)
            return
        self.session.openWithCallback(
            self._confirmed, MessageBox,
            "Install EPGManager %s? An on-device backup is mandatory.\nNo automatic rollback is available yet." % remote,
            MessageBox.TYPE_YESNO, default=False,
        )

    def force_reinstall(self):
        self.session.open(MessageBox,
                          "Force reinstall is temporarily disabled to protect your working EPGManager installation.",
                          MessageBox.TYPE_INFO, timeout=9)

    def _confirmed(self, answer):
        if not answer or not self.manifest or self._busy:
            return
        manifest = dict(self.manifest)
        self["status"].setText("Downloading and verifying in background...")

        def prepare():
            _download(manifest["url"], TMP_IPK)
            _verify(TMP_IPK, manifest["sha256"], manifest["size"])
            return _make_backup()

        self._start_job("download", prepare)

    def _install_verified_ipk(self):
        try:
            os.unlink(STATUS_FILE)
        except OSError:
            pass
        # Only normal upgrades; never silently force-reinstall the receiver.
        command = ("rm -f {status}; if opkg install {ipk}; "
                   "then echo OK > {status}; else echo FAIL > {status}; fi").format(
                       status=STATUS_FILE, ipk=TMP_IPK)
        self.session.openWithCallback(
            self._install_finished, Console,
            title="EPG Manager Online Update", cmdlist=[command],
            closeOnSuccess=True,
        )

    def _install_finished(self, *args):
        try:
            with open(STATUS_FILE, "r") as handle:
                ok = handle.read().strip() == "OK"
        except Exception:
            ok = False
        if not ok:
            self["status"].setText("Installation failed - backup available")
            self.session.open(MessageBox,
                              "opkg reported an error. Backup (manual recovery):\n%s" % self._backup_path,
                              MessageBox.TYPE_ERROR)
            return
        self["status"].setText("Update installed - restart GUI")
        self.session.openWithCallback(
            self._restart_answer, MessageBox,
            "EPGManager was installed. Restart Enigma2 GUI now?",
            MessageBox.TYPE_YESNO, default=True,
        )

    def _restart_answer(self, answer):
        if not answer:
            return
        try:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)
        except Exception as exc:
            self.session.open(MessageBox, "Please restart Enigma2 GUI manually: %s" % exc,
                              MessageBox.TYPE_INFO)


OnlineUpdateScreen = EPGManagerOnlineUpdate
