# -*- coding: utf-8 -*-
"""GitHub online updater for EPG Manager on Enigma2/OpenATV."""

import hashlib
import json
import os
import re
import subprocess
from urllib.request import Request, urlopen

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


def _version_key(value):
    text = str(value or "")
    nums = [int(x) for x in re.findall(r"\d+", text)]
    return tuple(nums or [0])


def _is_newer(remote, local):
    return _version_key(remote) > _version_key(local)


def _read_url(url, timeout=8):
    req = Request(url, headers={"User-Agent": "EPGManager/%s" % __version__})
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.read()
    except Exception:
        return subprocess.check_output(["wget", "-qO-", url], timeout=timeout + 4)


def _download(url, target):
    try:
        os.unlink(target)
    except OSError:
        pass
    subprocess.check_call(["wget", "-q", "-O", target, url])


def _verify(path, sha256_value, size_value):
    expected_size = int(size_value or 0)
    actual_size = os.path.getsize(path)
    if expected_size and actual_size != expected_size:
        raise ValueError("File size mismatch: %s != %s" % (actual_size, expected_size))

    expected_sha = str(sha256_value or "").strip().lower()
    if expected_sha:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_sha = digest.hexdigest().lower()
        if actual_sha != expected_sha:
            raise ValueError("SHA256 mismatch")
    return True


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
        self["title"] = Label("EPG Manager - GitHub Online Update")
        self["current"] = Label("Installed: %s" % __version__)
        self["available"] = Label("Available: checking...")
        self["status"] = Label("Checking GitHub...")
        self["notes"] = Label("")
        self["key_red"] = Button("Close")
        self["key_green"] = Button("Install")
        self["key_yellow"] = Button("Check")
        self["key_blue"] = Button("Force reinstall")
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "green": self.install_update,
                "yellow": self.check_update,
                "blue": self.force_reinstall,
                "ok": self.install_update,
            },
            -1,
        )
        self.onLayoutFinish.append(self.check_update)

    def check_update(self):
        self["status"].setText("Checking GitHub...")
        try:
            payload = _read_url(MANIFEST_URL)
            data = json.loads(payload.decode("utf-8"))
            for field in ("version", "url", "sha256", "size"):
                if field not in data:
                    raise ValueError("Invalid update manifest: missing %s" % field)
            self.manifest = data
            remote = str(data.get("version"))
            self["available"].setText("Available: %s" % remote)
            self["notes"].setText(str(data.get("notes") or ""))
            if _is_newer(remote, __version__):
                self["status"].setText("New version available - GREEN to install")
            elif remote == str(__version__):
                self["status"].setText("EPG Manager is up to date")
            else:
                self["status"].setText("Installed version is newer than GitHub")
        except Exception as exc:
            self.manifest = None
            self["available"].setText("Available: unavailable")
            self["status"].setText("Update check failed")
            self["notes"].setText(str(exc))

    def install_update(self):
        if not self.manifest:
            self.check_update()
            if not self.manifest:
                return
        remote = str(self.manifest.get("version"))
        if not _is_newer(remote, __version__):
            self.session.open(
                MessageBox,
                "No newer version is available.\nUse BLUE only if you want to reinstall %s." % remote,
                MessageBox.TYPE_INFO,
                timeout=7,
            )
            return
        self._prepare_install(False)

    def force_reinstall(self):
        if not self.manifest:
            self.check_update()
            if not self.manifest:
                return
        self._prepare_install(True)

    def _prepare_install(self, force):
        remote = str(self.manifest.get("version"))
        action = "reinstall" if force else "install"
        self.session.openWithCallback(
            lambda answer: self._confirmed(answer),
            MessageBox,
            "Download and %s EPG Manager %s from GitHub?\n\nThe IPK checksum will be verified before installation." % (action, remote),
            MessageBox.TYPE_YESNO,
            default=True,
        )

    def _confirmed(self, answer):
        if not answer:
            return
        try:
            self["status"].setText("Downloading update...")
            _download(str(self.manifest["url"]), TMP_IPK)
            _verify(TMP_IPK, self.manifest.get("sha256"), self.manifest.get("size"))
            self["status"].setText("Checksum OK - installing...")
        except Exception as exc:
            self["status"].setText("Download/verification failed")
            self.session.open(MessageBox, "Update failed:\n%s" % exc, MessageBox.TYPE_ERROR)
            return

        try:
            os.unlink(STATUS_FILE)
        except OSError:
            pass

        command = (
            "rm -f {status}; "
            "if opkg install --force-reinstall {ipk}; "
            "then echo OK > {status}; else echo FAIL > {status}; fi"
        ).format(status=STATUS_FILE, ipk=TMP_IPK)

        self.session.openWithCallback(
            self._install_finished,
            Console,
            title="EPG Manager Online Update",
            cmdlist=[command],
            closeOnSuccess=True,
        )

    def _install_finished(self, *args):
        ok = False
        try:
            with open(STATUS_FILE, "r") as handle:
                ok = handle.read().strip() == "OK"
        except Exception:
            pass

        if not ok:
            self["status"].setText("Installation failed")
            self.session.open(
                MessageBox,
                "opkg reported an installation error. The existing plugin was not intentionally removed.",
                MessageBox.TYPE_ERROR,
            )
            return

        self["status"].setText("Update installed - restart GUI")
        self.session.openWithCallback(
            self._restart_answer,
            MessageBox,
            "EPG Manager was updated successfully.\nRestart Enigma2 GUI now?",
            MessageBox.TYPE_YESNO,
            default=True,
        )

    def _restart_answer(self, answer):
        if not answer:
            return
        try:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)
        except Exception:
            subprocess.call(["killall", "-9", "enigma2"])


# Compatibility alias used by the EPGManager dashboard.
OnlineUpdateScreen = EPGManagerOnlineUpdate
