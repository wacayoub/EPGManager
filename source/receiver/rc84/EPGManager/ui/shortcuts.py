# -*- coding: utf-8 -*-
"""Remote-control shortcut reference for EPGManager."""
from __future__ import print_function

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from . import theme
from .compat import adapt_skin


class RemoteShortcutsScreen(Screen):
    skin = ("""
    <screen name="RemoteShortcutsScreen" position="0,0" size="1920,1080" title="EPGManager Remote Shortcuts" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header" position="0,0" size="1920,112" backgroundColor="%(PANEL)s" />
      <widget name="title" position="55,26" size="1200,58" font="Regular;40" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="subtitle" position="1180,34" size="680,40" font="Regular;22" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="rule" position="55,104" size="1810,2" backgroundColor="%(RULE)s" />
      <widget name="left" position="55,145" size="860,770" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="0" />
      <widget name="right" position="1000,145" size="865,770" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="0" />
      <widget name="footer" position="55,935" size="1810,70" font="Regular;22" halign="center" foregroundColor="%(TEXT)s" backgroundColor="%(NAV_PANEL)s" transparent="0" />
      <widget name="key_red_bar" position="30,1010" size="9,54" backgroundColor="%(BTN_RED)s" />
      <widget name="key_red" position="52,1010" size="480,54" font="Regular;27" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
    </screen>""") % theme.__dict__

    def __init__(self, session):
        Screen.__init__(self, session)
        for n in ("header", "rule"):
            self[n] = Label("")
        self["title"] = Label("EPG MANAGER  /  REMOTE SHORTCUTS")
        self["subtitle"] = Label("Numbers = direct access")
        self["left"] = Label(
            "MAIN SCREEN\n\n"
            "OK       EPG Status\n"
            "1        Auto Mapping Repair\n"
            "2        GitHub Direct Sync\n"
            "3        Manual Smart Mapping\n"
            "4        Native Import (advanced)\n"
            "5        Logs / Diagnostics\n"
            "6        GitHub Direct Sync\n"
            "7        All Settings\n"
            "8        Online Update\n\n"
            "GREEN    Manual Smart Mapping\n"
            "YELLOW   Update + Import\n"
            "BLUE     GitHub Direct Sync\n"
            "MENU     All Settings\n"
            "RED/EXIT Close")
        self["right"] = Label(
            "MAPPING REPAIR\n\n"
            "GREEN/OK Auto Search + SAFE fix\n"

            "BLUE     Review Auto fixes\n"
            "0        Undo last Auto Repair\n\n"
            "MANUAL SMART MAPPING\n\n"
            "GREEN    Find / Change EPG\n"
            "RIGHT    Source Override\n"
            "BLUE/0   More / Mapping Options\n"
            "INFO     Why this match?\n"
            "CH+/CH-  Page navigation\n"
            "MENU     Settings")
        self["footer"] = Label("Rule: RED/EXIT = Back   •   OK = natural action   •   MENU = Settings   •   CH+/CH- = Page navigation")
        self["key_red_bar"] = Label("")
        self["key_red"] = Label("Back")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "NumberActions"], {
            "cancel": self.close, "red": self.close, "ok": self.close, "9": self.close,
        }, -1)


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (RemoteShortcutsScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
