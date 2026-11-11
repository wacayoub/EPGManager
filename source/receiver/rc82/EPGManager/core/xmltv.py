# -*- coding: utf-8 -*-
"""
core/xmltv.py

One shared XMLTV engine used by all six sources, replacing five different
hand-rolled generators (raw string concatenation in 2M/Chada, ElementTree in
Medi1TV/Almajd, lxml.etree in SNRT/Arryadia/beIN).

Responsibilities (per spec section 15-18):
  - Build valid, UTF-8 XMLTV with correct entity escaping (ElementTree does
    this correctly by construction - no more manual html.escape() calls that
    can miss an edge case).
  - Deduplicate programmes (channel + start + title).
  - Sort chronologically per channel.
  - Compute stop times from the next programme's start when not supplied,
    falling back to a configurable default duration for the last slot.
  - Validate before ever touching the real output file (core/validation.py).
  - Atomic write with a .bak of the previous good file (core/utils.py),
    and refuse to overwrite a good file with an empty/broken one.
"""

from xml.etree import ElementTree as ET

from .logger import get_logger
from .utils import indent_xml, atomic_write, backup_previous
from .validation import validate_xmltv_string

log = get_logger(__name__)

DEFAULT_DURATION_MINUTES = 60
MAX_CYCLIC_GAP_HOURS = 12


class Channel(object):
    def __init__(self, channel_id, display_names, icon=None):
        """display_names: list of (text, lang) tuples, or a single string."""
        self.channel_id = channel_id
        if isinstance(display_names, str):
            display_names = [(display_names, None)]
        self.display_names = display_names
        self.icon = icon


class Programme(object):
    __slots__ = ("channel_id", "start", "stop", "title", "desc", "lang",
                 "title_lang", "desc_lang", "category", "icon")

    def __init__(self, channel_id, start, title, desc="", stop=None,
                 lang="ar", title_lang=None, desc_lang=None, category=None,
                 icon=None):
        self.channel_id = channel_id
        self.start = start        # aware datetime
        self.stop = stop          # aware datetime or None (auto-computed)
        self.title = title or ""
        self.desc = desc or ""
        self.lang = lang
        self.title_lang = title_lang or lang
        self.desc_lang = desc_lang or lang
        self.category = category
        self.icon = icon


class XMLTVBuilder(object):
    def __init__(self, generator_name="EPG Manager", tzm=None,
                 default_duration_minutes=DEFAULT_DURATION_MINUTES):
        self.generator_name = generator_name
        self.tzm = tzm
        self.default_duration_minutes = default_duration_minutes
        self._channels = {}
        self._programmes = []

    def add_channel(self, channel_id, display_names, icon=None):
        self._channels[channel_id] = Channel(channel_id, display_names, icon)

    def add_programme(self, programme):
        self._programmes.append(programme)

    def add_programmes(self, programmes):
        self._programmes.extend(programmes)

    # -- core build steps ---------------------------------------------------
    def _dedupe_and_sort(self):
        seen = set()
        unique = []
        for p in self._programmes:
            key = (p.channel_id, p.start, p.title.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        unique.sort(key=lambda p: (p.channel_id, p.start))
        return unique

    def _compute_stop_times(self, programmes):
        """Build one continuous EPG timeline per channel.

        beta48 rule: whenever the next programme start is known, it is the
        authoritative stop of the current programme.  This removes artificial
        holes at midnight and at broadcaster-day boundaries, regardless of
        whether a source supplied a shorter explicit stop.

        For the final event in the downloaded horizon, reuse the first event's
        wall-clock time as the next broadcaster-cycle boundary when it is at
        most 12 hours away.  Example: Medi1 23:00 -> next cycle 05:15, or SNRT
        06:xx -> 07:00.  No filler programme is created.
        """
        from datetime import timedelta
        by_channel = {}
        for p in programmes:
            by_channel.setdefault(p.channel_id, []).append(p)

        max_wrap = timedelta(hours=MAX_CYCLIC_GAP_HOURS)
        for ch_progs in by_channel.values():
            ch_progs.sort(key=lambda p: p.start)
            if not ch_progs:
                continue
            for i, p in enumerate(ch_progs):
                if i + 1 < len(ch_progs):
                    candidate_stop = ch_progs[i + 1].start
                    if candidate_stop > p.start:
                        # One source of truth: the current event ends exactly
                        # when the next known event begins (no gap/overlap).
                        p.stop = candidate_stop
                        continue

                # Last event: a source may know an authoritative boundary
                # even when the next programme page is unavailable (e.g.
                # Medi1 23:00 -> 05:00 or SNRT 06:xx -> 07:00). Preserve it.
                if p.stop is not None and p.stop > p.start:
                    continue

                # Otherwise, if the guide clearly exposes a repeating daily
                # cycle, bridge only a short overnight gap to the next cycle.
                first_start = ch_progs[0].start
                candidate = first_start
                guard = 0
                while candidate <= p.start and guard < 32:
                    candidate = candidate + timedelta(days=1)
                    try:
                        normalizer = getattr(candidate.tzinfo, "normalize", None)
                        if normalizer:
                            candidate = normalizer(candidate)
                    except Exception:
                        pass
                    guard += 1
                gap = candidate - p.start if candidate > p.start else None
                if gap is not None and timedelta(0) < gap <= max_wrap:
                    if p.stop is None or p.stop < candidate:
                        p.stop = candidate
                    continue

                if p.stop is None or p.stop <= p.start:
                    p.stop = p.start + timedelta(minutes=self.default_duration_minutes)
        return programmes

    def get_date_range(self):
        """Return (num_distinct_days, earliest_date, latest_date) covered
        by the programmes added so far - used by the dashboard to show
        "X days downloaded" per source, independent of the raw programme
        count (which varies wildly by source density)."""
        dates = set(p.start.date() for p in self._programmes)
        if not dates:
            return 0, None, None
        return len(dates), min(dates), max(dates)

    def to_xml_string(self):
        programmes = self._compute_stop_times(self._dedupe_and_sort())

        tv = ET.Element("tv", {
            "generator-info-name": self.generator_name,
            "source-info-name": "EPG Manager",
        })

        # Channels the caller registered explicitly...
        emitted_channel_ids = set()
        for ch_id, ch in self._channels.items():
            self._emit_channel(tv, ch)
            emitted_channel_ids.add(ch_id)

        # ...plus a safety net: any channel referenced only by a programme
        # (a source forgot to register it, or - like beIN - channels are
        # discovered dynamically at scrape time) still gets a <channel> tag,
        # because XMLTV consumers require one per referenced channel id.
        for p in programmes:
            if p.channel_id not in emitted_channel_ids:
                self._emit_channel(tv, Channel(p.channel_id, p.channel_id))
                emitted_channel_ids.add(p.channel_id)
                log.debug("Auto-registered channel '%s' from programme data "
                          "(no explicit add_channel call)", p.channel_id)

        for p in programmes:
            self._emit_programme(tv, p)

        indent_xml(tv)
        xml_body = ET.tostring(tv, encoding="unicode")
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n' + xml_body)

    def _emit_channel(self, tv, ch):
        el = ET.SubElement(tv, "channel", id=ch.channel_id)
        for name, lang in ch.display_names:
            dn = ET.SubElement(el, "display-name")
            if lang:
                dn.set("lang", lang)
            dn.text = name
        if ch.icon:
            ET.SubElement(el, "icon", src=ch.icon)

    def _emit_programme(self, tv, p):
        attrs = {
            "start": p.start.strftime("%Y%m%d%H%M%S %z"),
            "stop": p.stop.strftime("%Y%m%d%H%M%S %z"),
            "channel": p.channel_id,
        }
        el = ET.SubElement(tv, "programme", attrs)
        title_el = ET.SubElement(el, "title", lang=p.title_lang)
        title_el.text = p.title
        desc_el = ET.SubElement(el, "desc", lang=p.desc_lang)
        desc_el.text = p.desc
        if p.category:
            cat_el = ET.SubElement(el, "category", lang=p.desc_lang)
            cat_el.text = p.category
        if p.icon:
            ET.SubElement(el, "icon", src=p.icon)

    # -- public "just save it safely" entrypoint -----------------------------
    def save(self, output_path, min_programs=1):
        """Validate-then-atomically-write. Returns a validation.ValidationResult.
        If validation fails, the previous file at output_path is left
        completely untouched (spec section 17/18)."""
        xml_text = self.to_xml_string()
        result = validate_xmltv_string(xml_text, min_programs=min_programs)

        if not result.ok:
            log.error("Refusing to write %s - validation failed: %s",
                      output_path, "; ".join(result.errors))
            return result

        backup_previous(output_path)
        atomic_write(output_path, xml_text)
        log.info("Saved %s: %d programmes across %d channels",
                  output_path, result.program_count, result.channel_count)
        return result
