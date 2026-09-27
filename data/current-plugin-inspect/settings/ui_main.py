# -*- coding: utf-8 -*-
"""EPG Manager dashboard - Matrix/OpenATV inspired Full-HD UI."""
import os
import time

from enigma import eTimer, gRGB, eSize
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label

from .widgets import SourceStatusList
from . import theme
from ..core import plugin_launcher
from ..core.logger import get_logger
from ..core.health import evaluate_health, summarize_health
from ..version import __version__

log = get_logger(__name__)


class EPGManagerMainScreen(Screen):
    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 994, 420, 60, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 510, 994, 420, 60, theme.BTN_GREEN) +
        theme.key_bar_skin("key_yellow", 990, 994, 420, 60, theme.BTN_YELLOW) +
        theme.key_bar_skin("key_blue", 1470, 994, 420, 60, theme.BTN_BLUE)
    )

    skin = ("""
    <screen name="EPGManagerMainScreen" position="0,0" size="1920,1080"
            title="EPG Manager" backgroundColor="%(BG)s" flags="wfNoBorder">
        <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
        <widget name="title" position="55,30" size="340,62" font="Regular;42"
                 foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="version" position="402,35" size="270,54" font="Regular;31"
                 foregroundColor="%(ACCENT_PRIMARY)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="date" position="1430,20" size="180,62" font="Regular;22"
                 halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="clock_divider" position="1630,22" size="2,58" backgroundColor="%(MUTED_TEXT)s" />
        <widget name="clock" position="1650,18" size="230,62" font="Regular;45"
                 halign="right" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="header_rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />

        <widget name="main_panel" position="55,122" size="1180,850" backgroundColor="%(PANEL)s" />
        <widget name="status" position="82,142" size="710,44" font="Regular;30"
                 foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="selection_count" position="800,142" size="390,44" font="Regular;26"
                 halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="last_update" position="82,190" size="1112,34" font="Regular;23"
                 foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="next_update" position="82,225" size="760,34" font="Regular;23"
                 foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="progress_text" position="842,225" size="352,34" font="Regular;21"
                 halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="progress_track" position="82,271" size="1112,8" backgroundColor="%(PANEL_ROW_ALT)s" />
        <widget name="progress_fill" position="82,271" size="1,8" backgroundColor="%(ACCENT_PRIMARY)s" />
        <widget name="list_rule" position="82,299" size="1112,2" backgroundColor="%(MUTED_TEXT)s" />
        <widget name="col_source" position="174,311" size="294,34" font="Regular;20"
                 foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="col_status" position="479,311" size="167,34" font="Regular;20"
                 foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="col_programs" position="656,311" size="145,34" font="Regular;20"
                 foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="col_days" position="808,311" size="162,34" font="Regular;20"
                 foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="col_duration" position="982,311" size="190,34" font="Regular;20"
                 foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="sources_list" position="82,349" size="1112,510" backgroundColor="%(PANEL)s" />
        <widget name="list_hint_bg" position="82,850" size="1112,48" backgroundColor="%(NAV_PANEL)s" />
        <widget name="list_hint" position="105,857" size="1065,34" font="Regular;21"
                 foregroundColor="%(TEXT)s" backgroundColor="%(NAV_PANEL)s" transparent="1" />
        <widget name="nav_bg" position="82,906" size="1112,42" backgroundColor="%(NAV_PANEL)s" />

        <widget name="side_panel" position="1260,122" size="620,850" backgroundColor="%(PANEL_BLUE)s" />
        <widget name="side_label" position="1300,175" size="540,34" font="Regular;20"
                 halign="center" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="side_title" position="1300,225" size="540,54" font="Regular;34"
                 halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="side_subtitle" position="1300,282" size="540,38" font="Regular;22"
                 halign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="side_rule" position="1360,350" size="420,2" backgroundColor="%(ACCENT_PRIMARY)s" />
        <widget name="side_stats" position="1300,382" size="540,320" font="Regular;24"
                 halign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="health_box" position="1350,721" size="440,56" backgroundColor="%(PANEL_ALT)s" />
        <widget name="side_health" position="1360,729" size="420,42" font="Regular;22"
                 halign="center" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
        <widget name="side_hint" position="1300,785" size="540,120" font="Regular;21"
                 halign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />

        <widget name="hint" position="95,913" size="1080,28" font="Regular;20"
                 foregroundColor="%(WHITE)s" backgroundColor="%(NAV_PANEL)s" transparent="1" />
        <widget name="footer_bg" position="0,972" size="1920,108" backgroundColor="%(FOOTER_PANEL)s" />
        <widget name="key_strip_rule" position="55,972" size="1825,2" backgroundColor="%(RULE)s" />
        """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    def __init__(self, session, manager, config):
        Screen.__init__(self, session)
        self.session = session
        self.manager = manager
        self.config = config
        self._selected = set()
        self._last_list_fingerprint = None
        self._last_statuses = []
        self._was_busy = False
        self._ask_restart_when_done = False
        self._restart_prompt_open = False

        self["header_bg"] = Label("")
        self["title"] = Label("EPG MANAGER")
        self["version"] = Label("v.%s" % __version__)
        self["date"] = Label("")
        self["clock_divider"] = Label("")
        self["clock"] = Label("")
        self["header_rule"] = Label("")
        self["main_panel"] = Label("")
        self["status"] = Label("Status: READY")
        self["selection_count"] = Label("")
        self["last_update"] = Label("")
        self["next_update"] = Label("")
        self["progress_text"] = Label("")
        self["progress_track"] = Label("")
        self["progress_fill"] = Label("")
        self["list_rule"] = Label("")
        self["col_source"] = Label("SOURCE")
        self["col_status"] = Label("STATUS")
        self["col_programs"] = Label("PROGRAMMES")
        self["col_days"] = Label("DAYS")
        self["col_duration"] = Label("DURATION")
        self["sources_list"] = SourceStatusList()
        self["list_hint_bg"] = Label("")
        self["list_hint"] = Label("UP/DOWN  Navigate     OK  Select / unselect     9  Quick update")
        self["nav_bg"] = Label("")

        self["side_panel"] = Label("")
        self["side_label"] = Label("SOURCE DETAILS")
        self["side_title"] = Label("Automatic EPG Import")
        self["side_subtitle"] = Label("EPG Manager")
        self["side_rule"] = Label("")
        self["side_stats"] = Label("")
        self["health_box"] = Label("")
        self["side_health"] = Label("")
        self["side_hint"] = Label("")
        self["hint"] = Label("0 Sources    •    1 Native Import    •    2 Smart Mapping    •    7 Health Check    •    8 Online Update    •    EXIT Close")

        # Create footer background first.  Some OpenATV skins use widget creation
        # order as z-order; creating it after the key labels can hide the whole
        # action bar even though the skin XML is correct.
        self["footer_bg"] = Label("")
        self["key_strip_rule"] = Label("")
        for key, text in (("key_red", "Update Selected"),
                          ("key_green", "Select All / None"),
                          ("key_yellow", "Settings"),
                          ("key_blue", "Logs / About")):
            self[key] = Label(text)
            self[key + "_bar"] = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "NumberActions", "DirectionActions"],
            {"cancel": self.close,
             "ok": self.toggle_selected_row,
             "red": self.update_or_cancel,
             "green": self.toggle_select_all,
             "yellow": self.open_settings,
             "blue": self.open_logs,
             "1": self.open_native_import,
             "2": self.open_smart_mapping,
             "0": self.open_sources_screen,
             "9": self.update_highlighted_now,
             "7": self.open_health_check,
             "8": self.open_online_update,
             "up": self._nav_up,
             "down": self._nav_down}, -1)

        self.onClose.append(self._cleanup)

        self._clock_timer = eTimer()
        cb = self._clock_timer.callback if hasattr(self._clock_timer, "callback") else self._clock_timer.timeout.get()
        cb.append(self._refresh_clock)
        self._clock_timer.start(1000, False)

        self._poll_timer = eTimer()
        cb2 = self._poll_timer.callback if hasattr(self._poll_timer, "callback") else self._poll_timer.timeout.get()
        cb2.append(self._refresh)
        self._poll_timer.start(1000, False)
        self._refresh_clock()
        self._refresh()

    def _refresh_clock(self):
        now = time.localtime()
        self["clock"].setText(time.strftime("%H:%M", now))
        self["date"].setText(time.strftime("%A\n%d %b", now))

    def _current_status(self):
        try:
            idx = self["sources_list"].getCurrentIndex()
        except Exception:
            idx = -1
        if 0 <= idx < len(self._last_statuses):
            return self._last_statuses[idx]
        return None

    def toggle_selected_row(self):
        st = self._current_status()
        if not st:
            return
        sid = st.get("id")
        if not self.config.is_source_enabled(sid):
            self["status"].setText("Status: SOURCE DISABLED")
            return
        if sid in self._selected:
            self._selected.remove(sid)
        else:
            self._selected.add(sid)
        self._refresh()

    def toggle_select_all(self):
        ids = set(st.get("id") for st in self.manager.get_status_all()
                  if self.config.is_source_enabled(st.get("id")))
        self._selected = set() if ids and self._selected.issuperset(ids) else ids
        self._refresh()

    def _nav_up(self):
        self["sources_list"].up()
        self._refresh_side_panel(self._last_statuses, self.manager.is_busy())

    def _nav_down(self):
        self["sources_list"].down()
        self._refresh_side_panel(self._last_statuses, self.manager.is_busy())

    def update_or_cancel(self):
        if self.manager.is_busy():
            self.manager.cancel_update()
            return
        valid = [sid for sid in sorted(self._selected) if self.config.is_source_enabled(sid)]
        if not valid:
            self["status"].setText("Status: SELECT AT LEAST ONE SOURCE")
            return
        self._ask_restart_when_done = True
        if hasattr(self.manager, "update_selected_async"):
            started = self.manager.update_selected_async(valid)
        else:
            started = self.manager.update_source_async(valid[0])
        if started is False:
            self._ask_restart_when_done = False

    def update_highlighted_now(self):
        st = self._current_status()
        if not st or self.manager.is_busy():
            return
        sid = st.get("id")
        if not self.config.is_source_enabled(sid):
            self["status"].setText("Status: SOURCE DISABLED")
            return
        self._ask_restart_when_done = True
        started = self.manager.update_source_async(sid)
        if started is False:
            self._ask_restart_when_done = False

    def _ask_restart(self):
        if self._restart_prompt_open:
            return
        self._restart_prompt_open = True
        try:
            from Screens.MessageBox import MessageBox
            self.session.openWithCallback(
                self._restart_answered, MessageBox,
                "EPG update finished.\n\nRestart Enigma2 now?",
                MessageBox.TYPE_YESNO, default=False)
        except Exception:
            self._restart_prompt_open = False

    def _restart_answered(self, answer):
        self._restart_prompt_open = False
        if not answer:
            return
        try:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)
        except Exception as exc:
            log.exception("Could not restart Enigma2: %s", exc)

    def open_settings(self):
        from .settings import EPGManagerSettingsScreen
        self.session.openWithCallback(self._settings_closed, EPGManagerSettingsScreen, self.config)

    def _settings_closed(self, *args):
        self._last_list_fingerprint = None
        self._refresh()

    def open_health_check(self):
        from .health_check import EPGHealthCheckScreen
        self.session.open(EPGHealthCheckScreen, self.manager, self.config)

    def open_logs(self):
        try:
            from .screens import LogsAboutScreen
            self.session.open(LogsAboutScreen)
        except Exception as exc:
            log.exception("Unable to open Logs / About screen: %s", exc)
            try:
                from Screens.MessageBox import MessageBox
                self.session.open(
                    MessageBox,
                    "Unable to open Logs / About.\n\n%s" % exc,
                    MessageBox.TYPE_ERROR,
                    timeout=8,
                )
            except Exception:
                pass

    def open_native_import(self):
        from .native_import import NativeImportScreen
        self.session.open(NativeImportScreen, self.config)

    def open_smart_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen, self.config, True, self.manager)

    def open_online_update(self):
        from .online_update import OnlineUpdateScreen
        self.session.open(OnlineUpdateScreen)

    def open_channel_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen, self.config, True, self.manager)

    def open_sources_screen(self):
        from .screens import SourcesScreen
        self.session.openWithCallback(self._sources_closed, SourcesScreen, self.manager, self.config)

    def _sources_closed(self, *args):
        # Drop disabled sources from the manual selection list immediately.
        self._selected = set(sid for sid in self._selected if self.config.is_source_enabled(sid))
        self._last_list_fingerprint = None
        self._refresh()

    def _set_side_health(self, text, color):
        self["side_health"].setText(text)
        try:
            self["side_health"].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

    def _refresh_side_panel(self, statuses, busy=False):
        st = self._current_status()
        if st:
            sid = st.get("id")
            enabled = self.config.is_source_enabled(sid)
            label = {
                "SUCCESS": "UPDATED", "RUNNING": "UPDATING...",
                "FAILED": "FAILED", "WARNING": "WARNING",
                "CANCELLED": "CANCELLED", "COOLDOWN": "WAIT",
                "IDLE": "IDLE",
            }.get(st.get("status"), st.get("status", "IDLE"))
            self["side_title"].setText(st.get("name", "EPG Source"))
            self["side_subtitle"].setText(label if enabled else "DISABLED")
            programs = st.get("program_count") or 0
            days = st.get("days_covered") or 0
            duration = st.get("duration_seconds")
            duration_text = "-" if duration is None else "%.1f sec" % float(duration)
            days_text = "-" if not days else "%d day%s" % (days, "" if days == 1 else "s")
            selected_text = "Selected for update" if sid in self._selected else "Not selected"
            date_from = st.get("date_from") or "-"
            date_to = st.get("date_to") or "-"
            coverage_range = "%s → %s" % (date_from, date_to) if date_from != "-" and date_to != "-" else "No date range yet"
            error = (st.get("error_message") or "").strip()
            if error and len(error) > 72:
                error = error[:69] + "..."
            lines = [
                "%d programmes" % programs,
                "%s EPG coverage" % days_text,
                coverage_range,
                "%s last duration" % duration_text,
                "",
                selected_text,
            ]
            if error:
                lines.extend(["", "Issue: %s" % error])
            self["side_stats"].setText("\n".join(lines))
            if not enabled:
                self._set_side_health("SOURCE DISABLED", theme.STATUS_GREY)
            elif st.get("status") == "SUCCESS" and programs > 0:
                self._set_side_health("HEALTH: GOOD", theme.STATUS_GREEN)
            elif st.get("status") in ("FAILED", "WARNING"):
                self._set_side_health("HEALTH: NEEDS ATTENTION", theme.STATUS_RED)
            elif busy:
                self._set_side_health("UPDATE IN PROGRESS", theme.STATUS_YELLOW)
            else:
                self._set_side_health("READY", theme.STATUS_GREEN)
            self["side_hint"].setText("OK  Select / unselect\n9  Quick update\nRED  Update selected\nGREEN  Select all / none")
            return

        enabled = sum(1 for item in statuses if self.config.is_source_enabled(item.get("id")))
        success = sum(1 for item in statuses if item.get("status") == "SUCCESS")
        failed = sum(1 for item in statuses if item.get("status") in ("FAILED", "WARNING"))
        self["side_title"].setText("Automatic EPG Import")
        self["side_subtitle"].setText("EPG Manager")
        self["side_stats"].setText("%d sources configured\n%d sources enabled\n%d updated successfully\n%d need attention" %
                                   (len(statuses), enabled, success, failed))
        if failed:
            self._set_side_health("HEALTH: CHECK SOURCES", theme.STATUS_ORANGE)
        elif busy:
            self._set_side_health("UPDATE IN PROGRESS", theme.STATUS_YELLOW)
        else:
            self._set_side_health("SYSTEM READY", theme.STATUS_GREEN)
        self["side_hint"].setText("OK  Select / unselect\n9  Quick update\nRED  Update selected\nGREEN  Select all / none")

    def _format_next_update(self):
        mode = self.config.get_schedule_mode()
        last = self.config.get_last_update()
        if mode == "daily":
            hh, mm = self.config.get_daily_update_time()
            return "Next Update: daily at %02d:%02d (Casablanca)" % (hh, mm)
        if mode == "weekly":
            hh, mm = self.config.get_daily_update_time()
            from ..core.config import WEEKDAY_NAMES
            day = WEEKDAY_NAMES[self.config.get_weekly_update_day()]
            return "Next Update: %s at %02d:%02d (Casablanca)" % (day, hh, mm)
        if mode == "monthly":
            hh, mm = self.config.get_daily_update_time()
            return "Next Update: day %d at %02d:%02d (Casablanca)" % (
                self.config.get_monthly_update_day(), hh, mm)
        interval = self.config.get_update_interval_hours()
        if not last:
            return "Next Update: waiting for first scheduled run"
        remaining = last + interval * 3600 - time.time()
        if remaining <= 0:
            return "Next Update: due now"
        return "Next Update: in %dh %02dm" % (
            int(remaining // 3600), int((remaining % 3600) // 60))

    def _refresh(self):
        busy = self.manager.is_busy()
        # Manual updates can optionally be followed by an Enigma2 restart. The
        # question is shown from this GUI timer (never from the worker thread).
        if self._was_busy and not busy and self._ask_restart_when_done:
            self._ask_restart_when_done = False
            self._ask_restart()
        self._was_busy = bool(busy)
        cancelling = self.manager.is_cancelling()
        if cancelling:
            status_text, color = "Status: CANCELLING...", theme.STATUS_ORANGE
        elif busy:
            status_text, color = "Status: UPDATING...", theme.STATUS_YELLOW
        else:
            status_text, color = "Status: READY", theme.STATUS_GREEN
        self["status"].setText(status_text)
        try:
            self["status"].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

        self["key_red"].setText("Cancel Update" if busy else "Update Selected")
        self["last_update"].setText("Last Update: %s" % self.config.last_update_human())
        self["next_update"].setText(self._format_next_update())
        progress = self.manager.get_job_progress()
        if progress.get("active") and progress.get("total"):
            self["progress_text"].setText("%d/%d  •  %d%%  •  %ds" % (
                progress.get("completed", 0), progress.get("total", 0),
                progress.get("percent", 0), progress.get("elapsed_seconds", 0)))
            fill_width = max(1, int(1112 * progress.get("percent", 0) / 100.0))
        else:
            self["progress_text"].setText("Ready")
            fill_width = 1
        try:
            if self["progress_fill"].instance is not None:
                self["progress_fill"].instance.resize(eSize(fill_width, 8))
        except Exception:
            pass

        statuses = self.manager.get_status_all()
        self._last_statuses = statuses
        ids = set(st.get("id") for st in statuses)
        # Remove stale/disabled selections automatically.
        self._selected = set(sid for sid in self._selected
                             if sid in ids and self.config.is_source_enabled(sid))
        selected = len(self._selected)
        enabled_map = {st.get("id"): self.config.is_source_enabled(st.get("id")) for st in statuses}
        enabled = sum(1 for value in enabled_map.values() if value)
        self["selection_count"].setText("%d selected  •  %d enabled" % (selected, enabled))

        fingerprint = (
            tuple(sorted(self._selected)), tuple(sorted(enabled_map.items())),
            tuple((st.get("id"), st.get("status"), st.get("program_count"),
                   st.get("duration_seconds"), st.get("days_covered")) for st in statuses)
        )
        if fingerprint != self._last_list_fingerprint:
            self["sources_list"].update_rows(statuses, self._selected, enabled_map)
            self._last_list_fingerprint = fingerprint
        self._refresh_side_panel(statuses, busy)

    def _cleanup(self):
        for timer_name in ("_clock_timer", "_poll_timer"):
            try:
                getattr(self, timer_name).stop()
            except Exception:
                pass
