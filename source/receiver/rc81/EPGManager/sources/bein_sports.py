# -*- coding: utf-8 -*-
"""beIN Sports MENA EPG source.

beta93 strategy
---------------
1) Primary: current beinsports.com Opta TV-guide JSON API, MENA catalogue.
   This gives one event object with title/description/startDate/endDate and
   removes the legacy HTML parser's positional-shift risk completely.
2) Arabic-first sports normalization. Arabic broadcaster text is preserved;
   English-only MENA feeds are translated locally using sports-aware rules.
3) Fallback: the proven legacy bein.com Arabic AJAX scraper is retained so a
   temporary API/catalogue failure never leaves the receiver without beIN EPG.

The source intentionally keeps EN/FR simulcast channels separate from the Arab
MENA set. AFC/XTRA/MAX/4K feeds remain distinct identities and are never folded
onto beIN SPORTS 1..9.
"""

from __future__ import unicode_literals

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .base import EPGSource
from ..core import dependencies
from ..core.parser import get_soup, SourceWarning
from ..core.xmltv import Programme
from ..translators.hygiene import is_mixed_sentence
from ..translators.sports import (
    extract_metadata, get_league_arabic, translate_sports_text,
    translate_sports_title,
)

DOHA_TZ_NAME = "Asia/Qatar"
MODERN_CHANNELS_URL = "https://www.beinsports.com/api/opta/tv-channel"
MODERN_EVENTS_URL = "https://www.beinsports.com/api/opta/tv-event"
MODERN_REFERER = "https://www.beinsports.com/ar-mena/%D8%AC%D8%AF%D9%88%D9%84-%D8%A7%D9%84%D8%A8%D8%AB"
LEGACY_REFERER = "https://www.bein.com/ar/epg/"

# beta93 retains beta90 hard provider firewall. These are the current beIN MENA Opta channel
# UUIDs, kept separate from France/EN simulcasts. If beIN rotates UUIDs, the
# modern path fails closed and fetch() uses the Arabic legacy guide instead of
# guessing from a duplicated display name such as "beIN SPORTS 1".
MODERN_MENA_SITE_IDS = {
    "beINSPORTS1": "7836FEA9-6B39-4A1A-8352-DC5FCB97A16C",
    "beINSPORTS2": "FD1DD7DD-1E7B-4AA2-8682-BFA17338E653",
    "beINSPORTS3": "8AEA2426-D451-4BA5-BF48-114A1F04B1A8",
    "beINSPORTS4": "DB9361E8-B3EB-4D6F-9A82-75B5F09E2F92",
    "beINSPORTS5": "964E6246-CA95-410B-82C4-EA75DD979435",
    "beINSPORTS6": "E24D9C11-A8B4-4C7F-AD3E-B3364FB6D5A2",
    "beINSPORTS7": "A892063B-A5D9-4199-95AC-6A214515FA6B",
    "beINSPORTS8": "0F8D20A4-D46C-4B18-9242-8E7B3E978FF8",
    "beINSPORTS9": "5C08D9D3-C713-4F1F-947E-87C761428B9B",
    "beINSPORTSNews": "7B558284-F996-4123-9584-1E5D01844270",
    "beINSPORTSNBA": "2F518547-2269-4C07-93D5-2733397472BD",
    "beINSPORTS": "84F7E0C1-D47A-4444-BBA8-C50BB8D2C5A8",
    "beIN4K": "67DD49E9-E3A2-4B3A-94B8-88C620A4DFB1",
    "beINSPORTSMAX1": "2FB43094-3598-43C1-A3BA-44BFB40092E0",
    "beINSPORTSMAX2": "008F0EA9-FCD9-4E8C-849A-913979E7450A",
    "beINSPORTSMAX3": "7783DC02-4527-4094-9EE3-CDA8E093E4EB",
    "beINSPORTSMAX4": "5B15611A-5F9D-4EF0-89A6-677C9CA2BD5D",
    "beINSPORTSMAX5": "F7215920-CCF9-4DBB-8B9D-152E232FA549",
    "beINSPORTSMAX6": "DC04009D-7E18-4D1C-BA7F-7269B8F8D065",
    "beINSPORTSXTRA1": "E3B37FA0-E582-45B2-BB8E-516E1A714EF6",
    "beINSPORTSXTRA2": "27E67022-B943-4913-9AF3-AFD3DAC9854B",
    "beINSPORTSXTRA3": "CDF1A4C8-26DD-4C33-A239-F729A3B09295",
    "beINSPORTSXTRA4": "7A8040D9-7BAF-477E-B9F7-8BAB88F677E8",
    "beINSPORTSXTRA5": "51D28C47-7B79-4007-81A3-BFDF9BC65A3B",
    "beINSPORTSXTRA6": "9ABD32F9-C6D9-4DD5-B936-2C7E6546292E",
    "beINSPORTSXTRA7": "1752F091-A114-4629-BED4-46E0BB488A24",
    "beINSPORTSXTRA8": "CD634732-20D1-4137-94E7-939DE93D056D",
    "beINSPORTSXTRA9": "522050CE-EBD3-43EA-B636-42B034FDC05C",
    "beINSPORTSAFC": "10A2A142-F98C-4706-9FD0-2D3C36045D63",
    "beINSPORTSAFC1": "0CB3E227-4376-4545-AB64-D6C390F644D8",
    "beINSPORTSAFC2": "EEB3E4E8-0F9D-4735-943C-AEA3E39C87DE",
    "beINSPORTSAFC3": "A2D36A21-00D5-4001-A443-81CF2C06553F",
    "beINSPORTSAFC4": "42680C3C-580F-43DA-BDD8-02651BD10F32",
    "beINSPORTSAFC5": "B0DBD19A-9F44-4197-BD09-2B6A5F315F3B",
    "beINSPORTSAFC6": "2BF668DF-1B76-4199-88CE-8691FD86AD8C",
}

class BeinSportsSource(EPGSource):
    id = "bein_sports"
    name = "beIN Sports MENA"
    output_filename = "bein.xml"
    default_days = 3
    max_days = 3
    min_programs = 5
    # Keep beta86 behaviour for maximum compatibility with older receiver
    # images/certificate bundles; the modern API itself supports normal TLS.
    verify_ssl = False

    def fetch(self, downloader, tzm, days, config, builder):
        days = max(1, min(int(days or self.default_days), self.max_days))

        modern_entries = self._fetch_modern(downloader, days, builder, config=config)
        if len(modern_entries) >= self.min_programs:
            self.log.info("beIN modern MENA API: %d programme(s)", len(modern_entries))
            return modern_entries

        if modern_entries:
            self.log.warning(
                "beIN modern API returned only %d programme(s); using legacy Arabic fallback",
                len(modern_entries),
            )
        else:
            self.log.warning("beIN modern MENA API unavailable/empty; using legacy Arabic fallback")

        legacy_entries = self._fetch_legacy(downloader, days, builder, config=config)
        if legacy_entries:
            self.log.info("beIN legacy Arabic fallback: %d programme(s)", len(legacy_entries))
            return legacy_entries

        raise SourceWarning("beIN Sports MENA: no events found from modern or legacy guide")

    # ------------------------------------------------------------------
    # Modern beinsports.com JSON API
    # ------------------------------------------------------------------
    def _fetch_modern(self, downloader, days, builder, config=None):
        """Fetch beIN MENA with bounded parallel channel requests.

        beta95 fixes the 600s-class refresh stalls seen when the Opta endpoint
        is slow.  The receiver-wide timeout/retry values (often 20s x 2) are
        appropriate for ordinary sources but disastrous when multiplied by
        20+ beIN channel requests.  beIN therefore uses a short one-attempt
        per-channel budget, independent sessions, and a core-channel circuit
        breaker before optional MAX/XTRA/AFC requests.
        """
        channels = self._discover_modern_channels(downloader)
        if not channels:
            return []

        now_utc = datetime.now(timezone.utc)
        range_start = now_utc - timedelta(minutes=5)
        range_end = now_utc + timedelta(days=days)
        params_common = {
            "startBefore": self._api_datetime(range_end),
            "endAfter": self._api_datetime(range_start),
        }
        headers = {
            "Referer": MODERN_REFERER,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ar,en;q=0.8",
        }

        try:
            configured = int(config.get_effective_parallel_workers()) if config is not None and hasattr(config, "get_effective_parallel_workers") else 2
        except Exception:
            configured = 2
        # Zero 4K safety: keep this source deliberately quiet. The source itself
        # already runs in a background worker; more HTTP workers only increase
        # memory/CPU pressure and rate-limit risk.
        workers = max(1, min(2, configured))

        core_ids = set(["beINSPORTS%d" % n for n in range(1, 10)] + ["beINSPORTSNews", "beINSPORTS"])
        core = [c for c in channels if c.get("channel_id") in core_ids]
        extra = [c for c in channels if c.get("channel_id") not in core_ids]

        entries = []
        channel_names = {}
        failed_channels = 0

        core_result = self._fetch_modern_group(
            downloader, core, params_common, headers, now_utc, workers
        )
        entries.extend(core_result["entries"])
        channel_names.update(core_result["channel_names"])
        failed_channels += core_result["failed"]

        # If the primary endpoint is broadly unreachable, stop immediately.
        # Old beta94 continued through every MAX/XTRA/AFC UUID and could spend
        # ~20s x 2 retries on each one, producing the reported ~641s runtime.
        core_requests = max(1, len(core))
        endpoint_unhealthy = (
            core_result["ok_requests"] == 0 or
            (len(core_result["entries"]) < self.min_programs and
             core_result["failed"] >= max(3, (core_requests + 1) // 2) and
             core_result["ok_requests"] < 3)
        )
        if endpoint_unhealthy:
            self.log.warning(
                "beIN modern API circuit breaker: %d/%d core request(s) failed; skipping optional channels",
                core_result["failed"], core_requests,
            )
            return []

        if extra:
            extra_result = self._fetch_modern_group(
                downloader, extra, params_common, headers, now_utc, workers
            )
            entries.extend(extra_result["entries"])
            channel_names.update(extra_result["channel_names"])
            failed_channels += extra_result["failed"]

        # Deduplicate after the parallel merge.  channel + start + title is
        # stable and avoids any cross-worker shared mutable set.
        deduped = []
        seen = set()
        for programme in entries:
            key = (programme.channel_id, programme.start.isoformat(), programme.title)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(programme)
        entries = deduped

        if len(entries) >= self.min_programs:
            for cid, display_name in sorted(channel_names.items()):
                builder.add_channel(cid, [(display_name, "ar")])

        if failed_channels:
            self.log.warning("beIN modern API: %d channel request(s) failed", failed_channels)
        self.log.info("beIN modern fast fetch: %d programme(s), %d worker(s)", len(entries), workers)
        return entries

    def _fetch_modern_group(self, downloader, channels, params_common, headers, now_utc, workers):
        result = {"entries": [], "channel_names": {}, "failed": 0, "ok_requests": 0}
        if not channels:
            return result

        def one(channel):
            # One independent Session per worker avoids sharing requests.Session
            # across threads.  One attempt and max 6s caps a dead endpoint.
            child = downloader.fork(retries=1, timeout=min(6, int(getattr(downloader, "timeout", 6) or 6)), backoff_base=1.0)
            try:
                params = dict(params_common)
                params["channelIds"] = channel["site_id"]
                resp = child.get_or_none(MODERN_EVENTS_URL, params=params, extra_headers=headers)
                if resp is None:
                    return channel, [], False
                rows = self._json_rows(resp.text)
                if rows is None:
                    return channel, [], False
                programmes = []
                unique = set()
                for item in rows:
                    programme = self._build_modern_programme(item, channel, now_utc, unique)
                    if programme is not None:
                        programmes.append(programme)
                return channel, programmes, True
            finally:
                child.close()

        with ThreadPoolExecutor(max_workers=min(max(1, int(workers)), len(channels))) as executor:
            futures = [executor.submit(one, channel) for channel in channels]
            for future in as_completed(futures):
                try:
                    channel, programmes, request_ok = future.result()
                except Exception as exc:
                    self.log.debug("beIN parallel channel request failed: %s", exc)
                    result["failed"] += 1
                    continue
                if not request_ok:
                    result["failed"] += 1
                    continue
                result["ok_requests"] += 1
                result["entries"].extend(programmes)
                if programmes:
                    result["channel_names"][channel["channel_id"]] = channel["name"]
        return result

    def _discover_modern_channels(self, downloader):
        """Return current MENA channel UUIDs with a strict 4-second probe.

        This function is called only when the beIN source itself is refreshed.
        It is never imported/called by Enigma2 session-start or Smart Mapping.
        One short ar-mena discovery request may update rotated UUIDs; the pinned
        table remains the complete fail-safe catalogue.
        """
        discovered = {}
        headers = {
            "Referer": MODERN_REFERER,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ar,en;q=0.8",
        }
        child = downloader.fork(retries=1, timeout=4, backoff_base=1.0)
        try:
            resp = child.get_or_none(
                MODERN_CHANNELS_URL,
                params={"region": "ar-mena"},
                extra_headers=headers,
            )
        finally:
            child.close()
        if resp is not None:
            rows = self._json_rows(resp.text)
            if rows is not None:
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    site_id = str(item.get("id") or item.get("channelId") or "").strip()
                    name = self._clean_text(item.get("name") or item.get("title") or "")
                    if not site_id or not name:
                        continue
                    profile = self._channel_profile(name)
                    if profile is None:
                        continue
                    local_id, display_name = profile
                    if local_id not in discovered:
                        discovered[local_id] = {
                            "site_id": site_id,
                            "channel_id": local_id,
                            "name": display_name,
                        }

        live_count = len(discovered)
        # Pinned IDs are still authoritative fallbacks and currently match the
        # public MENA channel catalogue. Fill only gaps from the live probe.
        for local_id, site_id in MODERN_MENA_SITE_IDS.items():
            if local_id in discovered:
                continue
            name = self._display_name_for_local_id(local_id)
            if not name:
                continue
            discovered[local_id] = {
                "site_id": site_id,
                "channel_id": local_id,
                "name": name,
            }
        channels = list(discovered.values())
        channels.sort(key=lambda c: self._channel_sort_key(c["name"]))
        self.log.info("beIN MENA catalogue: %d live + %d pinned = %d channel(s)",
                      live_count, max(0, len(channels) - live_count), len(channels))
        return channels

    @staticmethod
    def _display_name_for_local_id(local_id):
        if local_id == "beINSPORTSNews": return "beIN SPORTS NEWS"
        if local_id == "beINSPORTSNBA": return "beIN SPORTS NBA"
        if local_id == "beINSPORTS": return "beIN SPORTS"
        if local_id == "beIN4K": return "beIN 4K"
        m = re.fullmatch(r"beINSPORTS([1-9])", local_id)
        if m: return "beIN SPORTS %s" % m.group(1)
        m = re.fullmatch(r"beINSPORTSMAX([1-9])", local_id)
        if m: return "beIN SPORTS MAX %s" % m.group(1)
        m = re.fullmatch(r"beINSPORTSXTRA([1-9])", local_id)
        if m: return "beIN SPORTS XTRA %s" % m.group(1)
        if local_id == "beINSPORTSAFC": return "beIN SPORTS AFC"
        m = re.fullmatch(r"beINSPORTSAFC([1-9])", local_id)
        if m: return "beIN SPORTS %s AFC" % m.group(1)
        return None

    @staticmethod
    def _json_rows(text):
        try:
            payload = json.loads(text or "")
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        rows = payload.get("rows")
        return rows if isinstance(rows, list) else None

    def _build_modern_programme(self, item, channel, now_utc, unique):
        if not isinstance(item, dict):
            return None
        try:
            # The Opta endpoint normally honours channelIds, but never trust a
            # pooled/misrouted response. If channelId is present it MUST equal
            # the exact MENA UUID requested for this local channel.
            response_channel_id = str(item.get("channelId") or "").strip()
            if response_channel_id and response_channel_id.upper() != str(channel.get("site_id") or "").upper():
                self.log.warning(
                    "beIN provider firewall: event channel mismatch for %s: got %s, expected %s",
                    channel.get("name"), response_channel_id, channel.get("site_id"),
                )
                return None

            start = self._parse_iso_utc(item.get("startDate"))
            stop = self._parse_iso_utc(item.get("endDate"))
            if start is None or stop is None or stop <= start or stop <= now_utc:
                return None

            raw_title = self._clean_text(item.get("title"))
            if not raw_title:
                nested = item.get("data") if isinstance(item.get("data"), dict) else {}
                raw_title = self._clean_text(nested.get("title"))
            if not raw_title:
                return None

            raw_desc = self._clean_text(item.get("description"))

            # Some feeds put the competition only in a nested/category field.
            nested = item.get("data") if isinstance(item.get("data"), dict) else {}
            competition = self._clean_text(
                item.get("category") or item.get("competition") or nested.get("competition") or ""
            )

            # Keep the visible EPG subject deliberately short: for matches it
            # contains only Team A vs Team B + competition. Season, round/week,
            # live marker and other metadata are moved to the description.
            is_live = item.get("live") is True or item.get("isLive") is True
            title, desc = self._format_epg_text(
                raw_title, raw_desc, competition_hint=competition, is_live=is_live,
                news_channel=(channel.get("channel_id") == "beINSPORTSNews")
            )

            event_key = (channel["channel_id"], start.isoformat(), raw_title)
            if event_key in unique:
                return None
            unique.add(event_key)

            return Programme(
                channel_id=channel["channel_id"],
                start=start,
                stop=stop,
                title=title,
                desc=desc,
                lang="ar",
                title_lang="ar",
                desc_lang="ar",
            )
        except Exception as exc:
            self.log.debug("beIN modern event ignored: %s", exc)
            return None

    @classmethod
    def _format_epg_text(cls, raw_title, raw_desc="", competition_hint="", is_live=False, news_channel=False):
        """Build a compact beIN subject and a richer description.

        Match subject policy (beta93):
          Live : Team A vs Team B - Mini Match - Competition
          Live : Team A vs Team B - Competition
          Team A vs Team B - Competition                (when not live)

        LIVE is a high-value on-screen status and stays in the subject. Explicit
        season/year, matchday/week and round/leg metadata remain in desc. This
        keeps Enigma2 channel lists compact without hiding whether an event is live.
        """
        raw_title = cls._clean_text(raw_title)
        raw_desc = cls._clean_text(raw_desc)
        competition_hint = cls._clean_text(competition_hint)

        base_title, metadata = extract_metadata(raw_title)
        has_mini_match = bool(re.search(r"\bMini Match(?:es)?\b", raw_title, flags=re.I))

        # Calendar years are removed from compact *match* subjects only.
        # For magazine/news titles such as "FIFA World Cup 2026 Stats", 2026
        # is part of the programme identity and must stay visible.
        is_match_subject = bool(re.search(r"\s+(?:vs\.?|v\.?)\s+|\s*ضد\s*", base_title, flags=re.I))
        standalone_years = re.findall(r"\b20\d{2}\b", base_title) if is_match_subject else []
        if standalone_years:
            base_title = re.sub(r"\b20\d{2}\b", " ", base_title)
            base_title = re.sub(r"\s+", " ", base_title).strip(" -|/")

        # A standalone LIVE marker is deliberately treated as status, not as
        # translatable programme text.  Keep the word boundary so Liverpool can
        # never be mistaken for Live.  "Live Studio" is a programme/format label,
        # not a standalone live prefix unless the API explicitly sets is_live.
        raw_live_prefix = bool(re.match(
            r"^\s*(?:Live\b(?!\s+Studio\b)|مباشر\b)\s*(?:[:\-–—]\s*)?",
            raw_title, flags=re.I
        ))
        live_in_subject = bool(is_live or raw_live_prefix)

        # beIN SPORTS NEWS uses recurring studio/bulletin programmes.  On this
        # channel the feed's LIVE flag is transport/status metadata rather than
        # useful programme branding, so never expose a ``Live :`` prefix in the
        # EPG subject.  Numbered sports channels keep the beta93 live-subject
        # policy unchanged.
        if news_channel:
            live_in_subject = False

        title = translate_sports_title(base_title or raw_title)

        # translate_sports_title may render a source LIVE prefix as Arabic.
        # Remove any leading status marker here and re-add one canonical English
        # `Live :` prefix after the compact subject has been assembled.
        title = re.sub(r"^\s*(?:Live\b|مباشر\b)\s*(?:[:\-–—]\s*)?", "", title, flags=re.I).strip()

        # Remove redundant generic sport labels from match subjects.  They add
        # no value once the competition is shown and are a major source of
        # clutter on narrow Enigma2 channel-list skins.
        generic_labels = {
            "كرة القدم", "كرة السلة", "التنس", "كرة اليد", "الكرة الطائرة",
            "Football", "Basketball", "Tennis", "Handball", "Volleyball",
        }
        if re.search(r"\s+vs\s+", title, flags=re.I):
            parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", title) if p.strip()]
            match_part = ""
            other_parts = []
            for part in parts:
                if not match_part and re.search(r"\s+vs\s+", part, flags=re.I):
                    match_part = part
                else:
                    other_parts.append(part)

            if match_part:
                # Prefer explicit category metadata, then only the portions of
                # the raw title *outside* the ``Team A vs Team B`` segment.
                # Searching the full match string classified AFC Wimbledon as
                # the tennis tournament Wimbledon.  Team names are never a
                # competition signal.
                raw_parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", base_title or raw_title) if p.strip()]
                raw_non_match = " - ".join(
                    p for p in raw_parts
                    if not re.search(r"\s+(?:vs\.?|v\.?)\s+|\s*ضد\s*", p, flags=re.I)
                )
                league = (get_league_arabic(competition_hint) or
                          get_league_arabic(raw_non_match) or
                          get_league_arabic(raw_desc))
                if league in generic_labels:
                    league = ""

                if not league:
                    skip_labels = set(generic_labels) | {
                        "ملخص مختصر للمباراة", "ملخص المباراة", "ملخص موسع",
                        "مباريات مختصرة", "قبل المباراة", "بعد المباراة",
                        "الاستوديو المباشر", "فردي الرجال", "فردي السيدات",
                    }
                    for part in other_parts:
                        if part in skip_labels:
                            continue
                        # Skip orphaned metadata that survived an unusual feed form.
                        if re.fullmatch(r"20\d{2}\s*[/\-]\s*(?:20)?\d{2,4}", part):
                            continue
                        if re.match(r"^(?:الجولة|دور الـ|ربع النهائي|نصف النهائي|النهائي|الذهاب|الإياب|المجموعة)\b", part):
                            continue
                        league = part
                        break

                if has_mini_match:
                    # Keep the broadcaster's useful format label in English in
                    # the compact subject, per user preference. Do not duplicate
                    # it in desc.
                    title = "%s - Mini Match" % match_part
                    if league:
                        title += " - %s" % league
                else:
                    title = match_part if not league else "%s - %s" % (match_part, league)

        # LIVE belongs in the subject (user-facing ChannelSelection line), not
        # in description.  Keep the literal English label as requested.
        if live_in_subject and title and not re.match(r"^Live\s*:\s+", title, flags=re.I):
            # RC17 user-facing live marker: keep one canonical literal prefix.
            title = "Live : %s" % title

        # Description gets the remaining metadata intentionally removed from
        # the subject (season, round/week, leg, etc.).
        details = []

        # Broadcast-format qualifiers belong in the description, not beside
        # the teams in the ChannelSelection subject.
        format_markers = (
            (r"\bExtended Highlights\b", "ملخص موسع"),
            (r"\bMatch Highlights\b", "ملخص المباراة"),
            (r"\bPre[- ]Match\b", "قبل المباراة"),
            (r"\bPost[- ]Match\b", "بعد المباراة"),
            (r"\bLive Studio\b", "استوديو مباشر"),
        )
        if not news_channel:
            for pattern, label in format_markers:
                if re.search(pattern, raw_title, flags=re.I) and label not in details:
                    details.append(label)

        # Calendar-year season precedes round/leg details.
        for year in standalone_years:
            token = "الموسم: %s" % year
            if token not in details:
                details.append(token)

        for token in [t.strip() for t in (metadata or "").split("|") if t.strip()]:
            season = re.fullmatch(r"(20\d{2})\s*[/\-]\s*(20?\d{2,4})", token)
            if season:
                y1, y2 = season.groups()
                if len(y2) == 2:
                    y2 = y1[:2] + y2
                token = "الموسم: %s/%s" % (y1, y2)
            if token not in details:
                details.append(token)

        # NEWS descriptions need channel-aware copy. Generic phrase replacement
        # can create mixed sentences such as ``Latest news from the beIN أخبار
        # الرياضية room``.  For the news service prefer clean, deterministic
        # Arabic descriptions tied to the actual programme identity.
        if news_channel:
            news_desc = cls._news_description(raw_title, title, raw_desc)
            if news_desc and news_desc not in details:
                details.append(news_desc)
            desc = " | ".join(details).strip()
            return title, desc

        translated_desc = translate_sports_text(raw_desc)
        if translated_desc:
            # Promotional beIN boilerplate repeats the same competition/season
            # already visible in title + metadata; keeping it would recreate
            # clutter.  More importantly, token-by-token translation of an
            # arbitrary English paragraph can leave an unreadable mixed-language
            # description.  Only keep text that is genuinely Arabic-first;
            # otherwise generate a short factual Arabic description from the
            # structured event title.
            boilerplate = bool(re.match(
                r"^(?:exclusive\s+|live\s+)?coverage\s+of\b.*\bbein\s+sports\b",
                raw_desc, flags=re.I
            ))
            if not boilerplate:
                ar_chars = len(re.findall(r"[\u0600-\u06FF]", translated_desc))
                latin_chars = len(re.findall(r"[A-Za-z]", translated_desc))
                # beta111: catch short hybrid fragments too (e.g. "ملخصات of
                # the match"), while still allowing Latin team/proper names in
                # an otherwise Arabic sentence.
                mixed_or_english = bool(
                    (latin_chars >= 8 and ar_chars == 0) or
                    is_mixed_sentence(translated_desc, min_latin_chars=6)
                )
                if not mixed_or_english and translated_desc not in details:
                    details.append(translated_desc)
                elif mixed_or_english:
                    fallback_desc = cls._structured_arabic_description(
                        title, raw_title, competition_hint
                    )
                    if fallback_desc and fallback_desc not in details:
                        details.append(fallback_desc)
        elif raw_desc:
            fallback_desc = cls._structured_arabic_description(
                title, raw_title, competition_hint
            )
            if fallback_desc and fallback_desc not in details:
                details.append(fallback_desc)

        desc = " | ".join(details).strip()
        return title, desc

    @classmethod
    def _structured_arabic_description(cls, title, raw_title="", competition_hint=""):
        """Build a safe Arabic description when provider prose is English-heavy.

        Team names remain Latin, matching the user's subject preference. The
        sentence itself is Arabic and is derived only from structured facts, so
        it cannot invent statistics or mistranslate a long Opta paragraph.
        """
        clean_title = cls._clean_text(title)
        clean_title = re.sub(r"^Live\s*(?:-|:|–|—)\s*", "", clean_title, flags=re.I).strip()
        match = re.search(r"^(.+?)\s+vs\s+(.+?)(?:\s+-\s+|$)", clean_title, flags=re.I)
        league = (get_league_arabic(raw_title) or
                  get_league_arabic(competition_hint) or
                  get_league_arabic(clean_title))

        if match:
            team_a = match.group(1).strip()
            team_b = match.group(2).strip()
            if not team_a or not team_b:
                return ""
            is_mini = "Mini Match" in clean_title
            # Keep both Latin team names as one LTR island inside the Arabic
            # sentence. This avoids Enigma2/Pango bidi reordering such as moving
            # Sunderland before the Arabic label or flipping Team A/Team B.
            match_label = "\u200e%s vs %s\u200e" % (team_a, team_b)
            if is_mini:
                sentence = "ملخص مختصر لمباراة %s" % match_label
            else:
                sentence = "مباراة %s" % match_label
            if league:
                sentence += " ضمن %s" % league
            return sentence + "."

        # RC15 Qatar 1 / native beIN shared policy: when English provider prose
        # is unsafe to translate token-by-token, build a concise Arabic sentence
        # from the already-normalized programme title.  This covers magazine,
        # review/highlights and motorsport packages without inventing facts.
        if "فورمولا 1" in clean_title or "موتو جي بي" in clean_title:
            return "تغطية رياضية ل%s." % clean_title
        if "ملخصات" in clean_title or "ملخص المباراة" in clean_title or "ملخص موسع" in clean_title:
            if league:
                return "ملخصات لأبرز أحداث %s." % league
            return "ملخصات لأبرز أحداث البرنامج الرياضي."
        if "المراجعة" in clean_title or re.search(r"(?:^|\s)مراجعة(?:\s|$)", clean_title):
            if league:
                return "مراجعة لأبرز أحداث %s." % league
            return "مراجعة لأبرز الأحداث والنتائج الرياضية."
        if "مجلة" in clean_title:
            if league:
                return "مجلة رياضية تقدم الأخبار والتحليلات المتعلقة ب%s." % league
            return "مجلة رياضية تقدم الأخبار والتحليلات وأبرز المستجدات."
        if "تقديم" in clean_title or "قبل المباراة" in clean_title:
            if league:
                return "تقديم وتحليل لأبرز أحداث %s." % league
            return "تقديم وتحليل للحدث الرياضي."
        if re.search(r"[\u0600-\u06FF]", clean_title):
            return "برنامج رياضي من beIN SPORTS: %s." % clean_title
        return ""

    @classmethod
    def _news_description(cls, raw_title, normalized_title, raw_desc=""):
        """Return a concise Arabic description for beIN SPORTS NEWS.

        The Opta feed often supplies an English generic newsroom sentence.
        Translating isolated tokens inside that sentence produces unreadable
        mixed Arabic/English text, so NEWS uses a small semantic catalogue and
        a safe Arabic fallback. Unknown proper names are preserved.
        """
        raw = cls._clean_text(raw_title)
        norm = cls._clean_text(normalized_title)
        low = raw.lower().strip()

        exact = {
            "the issue of the day": "برنامج الشوط الثالث يناقش أبرز قضايا وأحداث اليوم الرياضي مع التحليل الفني وآراء الخبراء.",
            "nine o'clock bulletin": "نشرة رياضية مسائية تقدم آخر الأخبار والنتائج والتطورات وتقارير مراسلي beIN SPORTS من مختلف أنحاء العالم.",
            "nine o’clock bulletin": "نشرة رياضية مسائية تقدم آخر الأخبار والنتائج والتطورات وتقارير مراسلي beIN SPORTS من مختلف أنحاء العالم.",
            "six o'clock bulletin": "نشرة رياضية تقدم آخر الأخبار والنتائج والتطورات من مختلف البطولات والملاعب.",
            "six o’clock bulletin": "نشرة رياضية تقدم آخر الأخبار والنتائج والتطورات من مختلف البطولات والملاعب.",
            "news bulletin": "نشرة إخبارية رياضية تستعرض آخر الأخبار والنتائج والتطورات من مختلف أنحاء العالم.",
            "al jawla": "برنامج الجولة يستعرض أبرز مباريات ونتائج وأحداث اليوم مع المتابعة والتحليل.",
            "al jawla - live studio": "برنامج الجولة يستعرض أبرز مباريات ونتائج وأحداث اليوم مع المتابعة والتحليل.",
            "al hassad": "برنامج الحصاد يقدم خلاصة أبرز الأخبار والنتائج والمباريات والأحداث الرياضية خلال اليوم.",
            "al hassila": "برنامج الحصيلة يلخص أبرز النتائج والأحداث والأخبار الرياضية خلال اليوم.",
        }
        if low in exact:
            return exact[low]

        # Arabic/normalized programme families visible in the current NEWS grid.
        if "نشرة التاسعة" in norm:
            return exact["nine o'clock bulletin"]
        if "نشرة السادسة" in norm:
            return exact["six o'clock bulletin"]
        if "نشرة الأخبار" in norm:
            return exact["news bulletin"]
        if norm == "الجولة" or norm.startswith("الجولة "):
            return exact["al jawla"]
        if "الشوط الثالث" in norm:
            return exact["the issue of the day"]
        if norm == "الحصاد" or norm.startswith("الحصاد "):
            return exact["al hassad"]
        if norm == "الحصيلة" or norm.startswith("الحصيلة "):
            return exact["al hassila"]
        if "موجز" in norm:
            return "موجز سريع لأهم الأخبار الرياضية والنتائج وآخر التطورات."
        if "مقابلة خاصة" in norm or "مقابلة حصرية" in norm:
            return "لقاء خاص مع أحد نجوم أو شخصيات الرياضة حول أبرز القضايا والأحداث."
        if "تقرير خاص" in norm:
            return "تقرير خاص يتناول إحدى القضايا أو القصص الرياضية البارزة بمزيد من التفاصيل."
        if norm.startswith("تاريخ النادي"):
            club = norm.split(":", 1)[1].strip() if ":" in norm else ""
            if club:
                return "حلقة تستعرض تاريخ نادي %s وأبرز محطاته وإنجازاته." % club
            return "حلقة تستعرض تاريخ النادي وأبرز محطاته وإنجازاته."

        # If the source itself already contains a predominantly Arabic
        # description, keep it; otherwise avoid leaking a half-translated
        # English sentence into the Arabic EPG.
        desc = cls._clean_text(raw_desc)
        if desc:
            ar = len(re.findall(r"[\u0600-\u06FF]", desc))
            latin = len(re.findall(r"[A-Za-z]", desc))
            if ar >= 20 and ar >= latin * 2:
                return desc

        return "برنامج من beIN SPORTS NEWS يقدم تغطية لأبرز الأخبار والأحداث الرياضية."

    @staticmethod
    def _parse_iso_utc(value):
        value = str(value or "").strip()
        if not value:
            return None
        try:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                continue
        return None

    @staticmethod
    def _api_datetime(dt):
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    @staticmethod
    def _clean_text(value):
        value = str(value or "")
        value = re.sub(r"<[^>]+>", " ", value)
        value = value.replace("&nbsp;", " ").replace("\u200f", "").replace("\u200e", "")
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _channel_profile(name):
        """Map current MENA catalogue names to stable local XMLTV IDs.

        FR/EN simulcasts are intentionally excluded from the Arabic source.
        Numbered channels whose *event text* happens to be English are NOT
        excluded; they are MENA numbered services and are translated locally.
        """
        clean = re.sub(r"\s+", " ", str(name or "")).strip()
        upper = clean.upper()
        if not upper.startswith("BEIN"):
            return None
        if re.search(r"\b(?:SPORTS\s+)?(?:EN|FR)\s*[12]\b", upper):
            return None

        m = re.fullmatch(r"BEIN\s+SPORTS\s+MAX\s*0*([1-9])", upper)
        if m:
            n = int(m.group(1))
            return "beINSPORTSMAX%d" % n, "beIN SPORTS MAX %d" % n

        m = re.fullmatch(r"BEIN\s+SPORTS\s+XTRA\s*0*([1-9])", upper)
        if m:
            n = int(m.group(1))
            return "beINSPORTSXTRA%d" % n, "beIN SPORTS XTRA %d" % n

        m = re.fullmatch(r"BEIN\s+SPORTS\s+([1-9])\s+AFC", upper)
        if m:
            n = int(m.group(1))
            return "beINSPORTSAFC%d" % n, "beIN SPORTS %d AFC" % n

        if re.fullmatch(r"BEIN\s+SPORTS\s+AFC", upper):
            return "beINSPORTSAFC", "beIN SPORTS AFC"

        m = re.fullmatch(r"BEIN\s+SPORTS\s+([1-9])", upper)
        if m:
            n = int(m.group(1))
            return "beINSPORTS%d" % n, "beIN SPORTS %d" % n

        if re.fullmatch(r"BEIN\s+SPORTS\s+NEWS", upper):
            return "beINSPORTSNews", "beIN SPORTS NEWS"
        if re.fullmatch(r"BEIN\s+SPORTS\s+NBA", upper):
            return "beINSPORTSNBA", "beIN SPORTS NBA"
        if re.fullmatch(r"BEIN\s+SPORTS", upper):
            return "beINSPORTS", "beIN SPORTS"
        if re.fullmatch(r"BEIN\s+4K", upper):
            return "beIN4K", "beIN 4K"
        if re.fullmatch(r"BEIN\s+SPORTS\s+4K(?:\s+HDR)?", upper):
            return "beINSPORTS4KHDR", "beIN SPORTS 4K HDR"
        return None

    @staticmethod
    def _channel_sort_key(name):
        upper = name.upper()
        m = re.fullmatch(r"BEIN SPORTS ([1-9])", upper)
        if m:
            return (0, int(m.group(1)), name)
        if upper == "BEIN SPORTS NEWS":
            return (1, 0, name)
        if upper == "BEIN SPORTS":
            return (1, 1, name)
        if "4K" in upper:
            return (2, 0, name)
        if "MAX" in upper:
            m = re.search(r"(\d+)$", upper)
            return (3, int(m.group(1)) if m else 99, name)
        if "XTRA" in upper:
            m = re.search(r"(\d+)$", upper)
            return (4, int(m.group(1)) if m else 99, name)
        if "AFC" in upper:
            m = re.search(r"SPORTS\s+(\d+)\s+AFC", upper)
            return (5, int(m.group(1)) if m else 0, name)
        return (6, 0, name)

    # ------------------------------------------------------------------
    # Legacy bein.com Arabic AJAX fallback (beta86-compatible)
    # ------------------------------------------------------------------
    def _fetch_legacy(self, downloader, days, builder, config=None):
        """Legacy Arabic fallback, parallelised per day with short HTTP budget."""
        doha_tzm = _DohaTZ()
        now_doha = doha_tzm.now()
        tasks = [(now_doha + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

        def fetch_day(week):
            child = downloader.fork(retries=1, timeout=min(6, int(getattr(downloader, "timeout", 6) or 6)), backoff_base=1.0)
            day_entries = []
            day_channels = {}
            unique_events = set()
            try:
                for idx in range(8):
                    url = (
                        "https://www.bein.com/ar/epg-ajax-template/?action=epg_fetch"
                        "&category=sports&serviceidentity=bein.net&offset=00&mins=00"
                        "&cdate=%s&language=AR&postid=25344&loadindex=%d" % (week, idx)
                    )
                    resp = child.get_or_none(url, extra_headers={"Referer": LEGACY_REFERER})
                    if resp is None or "li" not in resp.text:
                        break
                    events = self._parse_events(resp.text)
                    if not events:
                        break
                    for ev in events:
                        prog = self._build_legacy_programme(ev, week, now_doha, doha_tzm, unique_events)
                        if prog:
                            day_entries.append(prog)
                            day_channels[prog.channel_id] = self._legacy_display_name(prog.channel_id)
                return day_entries, day_channels
            finally:
                child.close()

        entries = []
        channels_seen = {}
        workers = min(3, max(1, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_day, week) for week in tasks]
            for future in as_completed(futures):
                try:
                    day_entries, day_channels = future.result()
                    entries.extend(day_entries)
                    channels_seen.update(day_channels)
                except Exception as exc:
                    self.log.debug("beIN legacy day fetch failed: %s", exc)

        # Stable ordering after parallel days.
        entries.sort(key=lambda p: (p.start, p.channel_id, p.title))
        for ch_id in sorted(channels_seen):
            builder.add_channel(ch_id, [(channels_seen[ch_id], "ar")])
        self.log.info("beIN legacy fast fallback: %d programme(s), %d day worker(s)", len(entries), workers)
        return entries

    def _parse_events(self, data):
        # Structural parse first in beta87: one <li> yields one complete event,
        # so a missing description/format can never shift later programmes.
        soup = get_soup(data)
        if soup is not None:
            events = self._parse_events_bs4(soup)
            if events:
                return events
        return self._parse_events_regex(data)

    def _parse_events_bs4(self, soup):
        events = []
        for li in soup.find_all("li"):
            data_img = li.get("data-img")
            if not data_img:
                img_el = li.find(attrs={"data-img": True})
                data_img = img_el.get("data-img") if img_el else None
            time_p = li.find("p", class_="time")
            title_p = li.find("p", class_="title")
            if not (data_img and time_p and title_p):
                continue
            match = re.search(r"/([^/]+?)\.png", data_img)
            channel_raw = match.group(1) if match else None
            if not channel_raw:
                continue
            format_p = li.find("p", class_="format")
            desc_p = li.find("p", class_="description")
            events.append({
                "channel": channel_raw,
                "time": time_p.get_text(strip=True),
                "title": title_p.get_text(strip=True),
                "format": format_p.get_text(strip=True) if format_p else "",
                "desc": desc_p.get_text(strip=True) if desc_p else "",
                "live": str(li.get("live") or "0"),
            })
        return events

    def _parse_events_regex(self, data):
        channels = re.findall(r"data-img=['\"].*?/([^/]+?)\.png['\"]", data)
        time_slots = re.findall(r"<p\s+class=['\"]?time['\"]?>(.*?)</p>", data, flags=re.I | re.S)
        times = [re.sub(r"\s+", "", t.replace("&nbsp;-&nbsp;", "-")) for t in time_slots]
        titles = re.findall(r"<p\s+class=['\"]?title['\"]?>(.*?)</p>", data, flags=re.I | re.S)
        formats = re.findall(r"<p\s+class=['\"]?format['\"]?>(.*?)</p>", data, flags=re.I | re.S)
        live_events = re.findall(r"li\s+[^>]*live=['\"](\d)['\"]", data, flags=re.I)
        descriptions = re.findall(r"<p\s+class=['\"]?description['\"]?>(.*?)</p>", data, flags=re.I | re.S)
        min_len = min(len(titles), len(times), len(channels)) if channels and times and titles else 0
        events = []
        for j in range(min_len):
            events.append({
                "channel": channels[j], "time": times[j], "title": titles[j],
                "format": formats[j] if j < len(formats) else "",
                "desc": descriptions[j] if j < len(descriptions) else "",
                "live": live_events[j] if j < len(live_events) else "0",
            })
        return events

    def _build_legacy_programme(self, ev, week, now_doha, doha_tzm, unique_events):
        try:
            time_raw = re.split(r"\s*-\s*", self._clean_text(ev.get("time")))
            if len(time_raw) != 2:
                return None

            raw_title = self._clean_text(ev.get("title"))
            raw_desc = self._clean_text(ev.get("desc"))
            raw_format = self._clean_text(ev.get("format"))
            if not raw_title:
                return None

            is_live = str(ev.get("live") or "0") == "1"
            legacy_channel_token = str(ev.get("channel") or "")
            is_news_channel = self._legacy_display_name(legacy_channel_token) == "beIN SPORTS NEWS"
            title, desc = self._format_epg_text(
                raw_title, raw_desc, competition_hint=raw_format, is_live=is_live,
                news_channel=is_news_channel
            )

            start_t = datetime.strptime(time_raw[0], "%H:%M")
            end_t = datetime.strptime(time_raw[1], "%H:%M")
            event_date = datetime.strptime(week, "%Y-%m-%d")
            end_date = event_date + timedelta(days=1) if end_t < start_t else event_date
            starttime = doha_tzm.localize(event_date.replace(hour=start_t.hour, minute=start_t.minute))
            endtime = doha_tzm.localize(end_date.replace(hour=end_t.hour, minute=end_t.minute))
            if endtime < now_doha:
                return None

            channel_id = (str(ev.get("channel") or "").split("?")[0]
                          .replace("_Digital_Mono", "").replace("_DIGITAL_Mono", "")
                          .replace("-1", ""))
            if not channel_id:
                return None
            event_key = (channel_id, starttime.isoformat(), raw_title)
            if event_key in unique_events:
                return None
            unique_events.add(event_key)

            return Programme(
                channel_id=channel_id,
                start=starttime,
                stop=endtime,
                title=title,
                desc=desc,
                lang="ar",
                title_lang="ar",
                desc_lang="ar",
            )
        except Exception as exc:
            self.log.debug("beIN legacy event ignored: %s", exc)
            return None

    @staticmethod
    def _legacy_display_name(channel_id):
        compact = re.sub(r"[^a-z0-9]+", "", str(channel_id or "").lower())
        m = re.search(r"beinsportsxtra0*([1-9])", compact)
        if m:
            return "beIN SPORTS XTRA %d" % int(m.group(1))
        m = re.search(r"beinsportsmax0*([1-9])", compact)
        if m:
            return "beIN SPORTS MAX %d" % int(m.group(1))
        if "news" in compact and "bein" in compact:
            return "beIN SPORTS NEWS"
        if "4k" in compact and "bein" in compact:
            return "beIN SPORTS 4K HDR"
        m = re.search(r"beinsports0*([1-9])", compact)
        if m and "xtra" not in compact and "max" not in compact:
            return "beIN SPORTS %d" % int(m.group(1))
        return str(channel_id or "beIN Sports")


class _DohaTZ(object):
    """Doha-time helper for the legacy AJAX fallback only."""
    def __init__(self):
        if dependencies.check_module("pytz"):
            import pytz
            self._tz = pytz.timezone(DOHA_TZ_NAME)
            self._backend = "pytz"
        else:
            self._tz = timezone(timedelta(hours=3))
            self._backend = "fixed"

    def now(self):
        if self._backend == "pytz":
            import pytz
            return datetime.now(pytz.utc).astimezone(self._tz)
        return datetime.now(timezone.utc).astimezone(self._tz)

    def localize(self, naive_dt):
        if self._backend == "pytz":
            return self._tz.localize(naive_dt)
        return naive_dt.replace(tzinfo=self._tz)
