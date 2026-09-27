# -*- coding: utf-8 -*-
"""EPG Health Check - source quality overview for Enigma2/OpenATV."""
from enigma import eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_VALIGN_CENTER, gRGB, eTimer
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText

from . import theme
from ..core.health import evaluate_health, summarize_health

ROW_H = 72


def _fmt_duration(st):
    v = st.get("duration_seconds")
    if v is None:
        return "-"
    try:
        return "%.1fs" % float(v)
    except Exception:
        return "-"


def _health_color(level):
    return {
        "GOOD": theme.STATUS_GREEN_INT,
        "WARNING": theme.STATUS_ORANGE_INT,
        "FAILED": theme.STATUS_RED_INT,
        "UPDATING": theme.STATUS_YELLOW_INT,
        "DISABLED": theme.STATUS_GREY_INT,
        "NOT CHECKED": theme.STATUS_GREY_INT,
    }.get(level, theme.STATUS_GREY_INT)


class HealthList(MenuList):
    def __init__(self):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, gFont("Regular", 27))
        self.l.setFont(1, gFont("Regular", 22))
        self.l.setItemHeight(ROW_H)

    def update_rows(self, statuses, enabled_map):
        try:
            idx = self.getCurrentIndex()
        except Exception:
            idx = 0
        rows = []
        for i, st in enumerate(statuses):
            enabled = enabled_map.get(st.get("id"), True)
            health = evaluate_health(st, enabled)
            level = health.get("level", "NOT CHECKED")
            bg = theme.PANEL_ROW_HEX_INT if i % 2 == 0 else theme.PANEL_ROW_ALT_HEX_INT
            color = _health_color(level)
            programs = st.get("program_count") or 0
            days = st.get("days_covered") or 0
            coverage = "%d day%s" % (days, "" if days == 1 else "s") if days else "-"
            rows.append([
                (st, health),
                MultiContentEntryText(pos=(14, 4), size=(300, ROW_H-8), font=0,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=st.get("name", "Source"),
                    color=theme.TEXT_INT, color_sel=theme.WHITE_INT,
                    backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(320, 4), size=(190, ROW_H-8), font=1,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=level,
                    color=color, color_sel=color, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(515, 4), size=(150, ROW_H-8), font=0,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=str(programs) if programs else "-",
                    color=theme.TEXT_INT, color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(670, 4), size=(160, ROW_H-8), font=0,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=coverage,
                    color=theme.TEXT_INT, color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(835, 4), size=(165, ROW_H-8), font=0,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=_fmt_duration(st),
                    color=theme.TEXT_INT, color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
            ])
        self.setList(rows)
        if rows:
            try:
                self.instance.moveSelectionTo(max(0, min(idx, len(rows)-1)))
            except Exception:
                pass

    def current_data(self):
        cur = self.getCurrent()
        return cur[0] if cur else (None, None)


class EPGHealthCheckScreen(Screen):
    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 1006, 420, 54, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 510, 1006, 510, 54, theme.BTN_GREEN) +
        theme.key_bar_skin("key_blue", 1080, 1006, 510, 54, theme.BTN_BLUE)
    )
    skin = ("""
    <screen name="EPGHealthCheckScreen" position="0,0" size="1920,1080" title="EPG Health Check" backgroundColor="%(BG)s" flags="wfNoBorder">
        <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
        <widget name="title" position="55,30" size="1150,58" font="Regular;42" foregroundColor="%(WHITE)s" backgroundColor="%(BG)s" transparent="1" />
        <widget name="summary" position="1150,35" size="730,46" font="Regular;23" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
        <widget name="rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />

        <widget name="left_panel" position="55,122" size="1180,868" backgroundColor="%(PANEL)s" />
        <widget name="intro" position="82,145" size="1112,55" font="Regular;23" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="h_source" position="96,218" size="290,32" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="h_health" position="402,218" size="185,32" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="h_programs" position="597,218" size="145,32" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="h_days" position="752,218" size="150,32" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="h_duration" position="917,218" size="175,32" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="list" position="82,257" size="1112,560" backgroundColor="%(PANEL)s" />
        <widget name="legend" position="82,835" size="1112,92" font="Regular;21" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />

        <widget name="right_panel" position="1260,122" size="620,868" backgroundColor="%(PANEL_BLUE)s" />
        <widget name="detail_title" position="1300,176" size="540,50" font="Regular;32" halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="detail_health" position="1330,245" size="480,48" font="Regular;25" halign="center" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="detail_rule" position="1360,315" size="420,2" backgroundColor="%(ACCENT_PRIMARY)s" />
        <widget name="detail_text" position="1308,348" size="524,410" font="Regular;23" halign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="detail_hint" position="1300,795" size="540,110" font="Regular;20" halign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />

        <widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
        <widget name="key_rule" position="55,990" size="1825,2" backgroundColor="%(RULE)s" />
        """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    def __init__(self, session, manager, config):
        Screen.__init__(self, session)
        self.manager = manager
        self.config = config
        self._last_fp = None

        self["header_bg"] = Label("")
        self["title"] = Label("EPG HEALTH CHECK")
        self["summary"] = Label("")
        self["rule"] = Label("")
        self["left_panel"] = Label("")
        self["intro"] = Label("Instant overview of missing, expired or unhealthy EPG data")
        self["h_source"] = Label("SOURCE")
        self["h_health"] = Label("HEALTH")
        self["h_programs"] = Label("PROGRAMMES")
        self["h_days"] = Label("DAYS")
        self["h_duration"] = Label("DURATION")
        self["list"] = HealthList()
        self["legend"] = Label("GOOD = valid EPG   •   WARNING = low/partial data   •   FAILED = update error or expired EPG\nUP/DOWN Navigate   •   GREEN Update sources needing attention")
        self["right_panel"] = Label("")
        self["detail_title"] = Label("")
        self["detail_health"] = Label("")
        self["detail_rule"] = Label("")
        self["detail_text"] = Label("")
        self["detail_hint"] = Label("GREEN  Update attention sources\nBLUE  Update highlighted source\nRED / EXIT  Close")
        self["footer_bg"] = Label("")
        self["key_rule"] = Label("")
        for key, text in (("key_red", "Close"), ("key_green", "Update Attention"), ("key_blue", "Update This Source")):
            self[key] = Label(text); self[key+"_bar"] = Label("")

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "cancel": self.close, "red": self.close, "green": self.update_attention,
            "blue": self.update_current, "ok": self.update_current,
            "up": self._up, "down": self._down,
        }, -1)
        self._timer = eTimer()
        (self._timer.callback.append(self._refresh) if hasattr(self._timer, "callback") else self._timer.timeout.get().append(self._refresh))
        self._timer.start(1000, False)
        self.onClose.append(self._cleanup)
        self._refresh()

    def _up(self):
        try: self["list"].up()
        except Exception: pass
        self._refresh_detail()

    def _down(self):
        try: self["list"].down()
        except Exception: pass
        self._refresh_detail()

    def _refresh(self):
        statuses = self.manager.get_status_all()
        enabled_map = {st.get("id"): self.config.is_source_enabled(st.get("id")) for st in statuses}
        summary = summarize_health(statuses, enabled_map)
        self["summary"].setText("%d good  •  %d warning  •  %d failed" % (
            summary.get("GOOD", 0), summary.get("WARNING", 0), summary.get("FAILED", 0)))
        fp = (tuple(sorted(enabled_map.items())), tuple((st.get("id"), st.get("status"), st.get("program_count"), st.get("days_covered"), st.get("date_to"), st.get("error_message")) for st in statuses))
        if fp != self._last_fp:
            self["list"].update_rows(statuses, enabled_map)
            self._last_fp = fp
        self._refresh_detail()

    def _refresh_detail(self):
        st, health = self["list"].current_data()
        if not st:
            self["detail_title"].setText("No source")
            self["detail_health"].setText("")
            self["detail_text"].setText("")
            return
        level = health.get("level", "NOT CHECKED")
        color = {
            "GOOD": theme.STATUS_GREEN, "WARNING": theme.STATUS_ORANGE,
            "FAILED": theme.STATUS_RED, "UPDATING": theme.STATUS_YELLOW,
            "DISABLED": theme.STATUS_GREY, "NOT CHECKED": theme.STATUS_GREY,
        }.get(level, theme.STATUS_GREY)
        self["detail_title"].setText(st.get("name", "Source"))
        self["detail_health"].setText("HEALTH: %s" % level)
        try: self["detail_health"].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception: pass
        programs = st.get("program_count") or 0
        days = st.get("days_covered") or 0
        date_from, date_to = st.get("date_from") or "-", st.get("date_to") or "-"
        self["detail_text"].setText("%d programmes\n%d day%s coverage\n%s → %s\n%s last duration\n\n%s" % (
            programs, days, "" if days == 1 else "s", date_from, date_to, _fmt_duration(st), health.get("message", "")))

    def _attention_ids(self):
        ids = []
        for st in self.manager.get_status_all():
            sid = st.get("id")
            if not self.config.is_source_enabled(sid):
                continue
            level = evaluate_health(st, True).get("level")
            if level in ("WARNING", "FAILED", "NOT CHECKED"):
                ids.append(sid)
        return ids

    def update_attention(self):
        if self.manager.is_busy(): return
        ids = self._attention_ids()
        if ids: self.manager.update_selected_async(ids)

    def update_current(self):
        if self.manager.is_busy(): return
        st, _health = self["list"].current_data()
        if st and self.config.is_source_enabled(st.get("id")):
            self.manager.update_source_async(st.get("id"))

    def _cleanup(self):
        try: self._timer.stop()
        except Exception: pass
