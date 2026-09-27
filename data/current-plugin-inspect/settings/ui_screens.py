# -*- coding: utf-8 -*-
"""
ui/screens.py

- SourcesScreen: per-source status list + PERSISTENT enable/disable +
  manual "update this source only" (spec sections 32/33).
- LogsAboutScreen: the last 80 lines of the rotating log file in a
  scrollable viewer, plus About text (spec 37/49).
"""

from enigma import eTimer
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel

from .widgets import SourceStatusList
from . import theme
from ..core.logger import get_log_path
from ..version import __version__


class SourcesScreen(Screen):
    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 1008, 420, 54, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 510, 1008, 510, 54, theme.BTN_GREEN) +
        theme.key_bar_skin("key_yellow", 1080, 1008, 420, 54, theme.BTN_YELLOW)
    )

    skin = ("""
    <screen name="SourcesScreen" position="0,0" size="1920,1080" title="EPG Manager - Sources" backgroundColor="%(BG)s">
        <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
        <widget name="content_bg" position="30,125" size="1860,865" backgroundColor="%(PANEL)s" />
        <widget name="title" position="55,26" size="1500,60" font="Regular;39" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="hint" position="60,145" size="1800,70" font="Regular;25" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="list" position="60,225" size="1800,720" backgroundColor="%(PANEL)s" />
        <widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
        <widget name="key_strip_rule" position="30,990" size="1860,2" backgroundColor="%(RULE)s" />
        """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    def __init__(self, session, manager, config):
        Screen.__init__(self, session)
        self.session = session
        self.manager = manager
        self.config = config

        self["header_bg"] = Label("")
        self["content_bg"] = Label("")
        self["title"] = Label("EPG MANAGER  /  SOURCES")
        self["hint"] = Label("YELLOW toggles whether a source runs on auto/manual 'Update All' at all "
                              "(persistent). This is different from the per-run checklist on the main dashboard.")
        self["list"] = SourceStatusList()

        self["footer_bg"] = Label("")
        self["key_strip_rule"] = Label("")
        self["key_red_bar"] = Label("")
        self["key_red"] = Label("Close")
        self["key_green_bar"] = Label("")
        self["key_green"] = Label("Update This Source Now")
        self["key_yellow_bar"] = Label("")
        self["key_yellow"] = Label("Enable / Disable")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "green": self.update_selected,
                "yellow": self.toggle_selected,
                "ok": self.update_selected,
            },
            -1,
        )

        self._timer = eTimer()
        (self._timer.callback.append(self._refresh) if hasattr(self._timer, "callback")
         else self._timer.timeout.get().append(self._refresh))
        self._timer.start(1500, False)
        self._last_list_fingerprint = None
        self._refresh()

    def _refresh(self):
        statuses = self.manager.get_status_all()
        enabled_map = {st["id"]: self.config.is_source_enabled(st["id"]) for st in statuses}
        # No per-run "selected" checkbox concept on this screen - the
        # marker here instead reflects the PERSISTENT enabled flag.
        enabled_ids = set(sid for sid, en in enabled_map.items() if en)

        fingerprint = (
            tuple(sorted(enabled_map.items())),
            tuple((st["id"], st["status"], st["program_count"], st["duration_seconds"])
                  for st in statuses),
        )
        if fingerprint != self._last_list_fingerprint:
            self["list"].update_rows(statuses, enabled_ids, enabled_map)
            self._last_list_fingerprint = fingerprint

    def _selected_source_id(self):
        row = self["list"].get_selected_status_dict()
        return row["id"] if row else None

    def update_selected(self):
        sid = self._selected_source_id()
        if not sid:
            return
        if self.manager.is_busy():
            self.session.open(MessageBox, "An update is already running.", MessageBox.TYPE_INFO)
            return
        self.manager.update_source_async(sid)

    def toggle_selected(self):
        sid = self._selected_source_id()
        if not sid:
            return
        current = self.config.is_source_enabled(sid)
        self.config.set_source_enabled(sid, not current)
        self._refresh()


class LogsAboutScreen(Screen):
    LOG_LINE_LIMIT = 80

    _KEY_BARS = theme.key_bar_skin("key_red", 30, 1008, 420, 54, theme.BTN_RED)

    skin = ("""
    <screen name="LogsAboutScreen" position="0,0" size="1920,1080" title="EPG Manager - Logs / About" backgroundColor="%(BG)s">
        <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
        <widget name="content_bg" position="30,125" size="1860,865" backgroundColor="%(PANEL)s" />
        <widget name="title" position="55,26" size="1500,60" font="Regular;39" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="subtitle" position="60,145" size="1800,65" font="Regular;24" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="text" position="60,220" size="1800,720" font="Regular;22" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="hint" position="480,1008" size="1410,54" font="Regular;24" halign="left" valign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="0" />
        <widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
        <widget name="key_strip_rule" position="30,990" size="1860,2" backgroundColor="%(RULE)s" />
        """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    def __init__(self, session):
        Screen.__init__(self, session)
        self["header_bg"] = Label("")
        self["content_bg"] = Label("")
        self["title"] = Label("EPG MANAGER  /  LOGS & ABOUT")

        log_path = get_log_path()
        total_lines = self._count_lines(log_path)
        shown = min(total_lines, self.LOG_LINE_LIMIT)
        self["subtitle"] = Label(
            "EPG Manager v%s   |   Log file: %s   |   showing last %d of %d line(s)" %
            (__version__, log_path, shown, total_lines))

        self["text"] = ScrollLabel(self._build_text(log_path))
        self["hint"] = Label("UP/DOWN: scroll one page   0: top   9: bottom")

        self["footer_bg"] = Label("")
        self["key_strip_rule"] = Label("")
        self["key_red_bar"] = Label("")
        self["key_red"] = Label("Close")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions", "NumberActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "up": self._scroll_up,
                "down": self._scroll_down,
                "0": self._scroll_top,
                "9": self._scroll_bottom,
            },
            -1,
        )


    def _scroll_call(self, names):
        """Call the first ScrollLabel method available on this Enigma2 image."""
        widget = self["text"]
        for name in names:
            fn = getattr(widget, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                return

    def _scroll_up(self):
        self._scroll_call(("pageUp", "up"))

    def _scroll_down(self):
        self._scroll_call(("pageDown", "down"))

    def _scroll_top(self):
        self._scroll_call(("goTop", "home", "top", "firstPage"))

    def _scroll_bottom(self):
        self._scroll_call(("goBottom", "end", "bottom", "lastPage"))

    def _count_lines(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _tail_log(self, path, n):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-n:]) if lines else "(log is empty)"
        except Exception as e:
            return "(could not read log: %s)" % e

    def _build_text(self, log_path):
        about = (
            "EPG Manager v%s\n"
            "Automatic, cron-free EPG updates for Medi1TV, 2M/Chada, SNRT, "
            "beIN Sports, Almajd and Arryadia.\n\n"
            "==================== LAST %d LOG LINES ====================\n\n"
        ) % (__version__, self.LOG_LINE_LIMIT)

        return about + self._tail_log(log_path, self.LOG_LINE_LIMIT)
