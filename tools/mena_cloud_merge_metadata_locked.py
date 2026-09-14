#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shadow merge wrapper that locks regular-channel metadata to its chosen timeline.

Purpose:
- keep the chosen timeline's title for ordinary MENA channels;
- never replace a title merely because another feed has a close time slot;
- allow Arabic-description enrichment only when the alternate event has the
  exact same normalized title as the chosen timeline event;
- preserve the existing beIN/OSN premium bilingual enrichment policy.

This wrapper is initially used only by the metadata-lock shadow workflow.
"""
from __future__ import annotations

import re

import mena_cloud_merge as base
import mena_cloud_merge_arabic_first as arabic

_previous_choose_event = base.choose_event


def _norm_title(value):
    value = (value or "").casefold()
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def _first_text(programme, role):
    items = base.text_items(programme, role)
    return items[0] if items else ("", "other")


def _remove_role(programme, role):
    for node in list(programme.findall(role)):
        programme.remove(node)


def locked_choose_event(entries, premium):
    if premium:
        return _previous_choose_event(entries, premium)

    timeline_candidate, timeline_programme = entries[0]
    out = base.copy_element(timeline_programme)
    timeline_title, _timeline_lang = _first_text(timeline_programme, "title")
    title_key = _norm_title(timeline_title)

    # The timeline title is authoritative. Only enrich a missing/non-Arabic
    # description from an alternate feed when the programme title is exactly
    # the same after conservative normalization.
    current_desc, current_desc_lang = _first_text(out, "desc")
    needs_ar_desc = not current_desc or (
        current_desc_lang != "ar" and base.language_of(current_desc) != "ar"
    )
    if needs_ar_desc and title_key:
        best = None
        for _candidate, programme in entries[1:]:
            alt_title, _ = _first_text(programme, "title")
            if _norm_title(alt_title) != title_key:
                continue
            desc, desc_lang, score = base.best_text(programme, "desc", "ar")
            if not desc or (desc_lang != "ar" and base.language_of(desc) != "ar"):
                continue
            rank = (score, len(desc))
            if best is None or rank > best[0]:
                best = (rank, desc)
        if best is not None:
            base.replace_role(out, "desc", best[1], "ar")
        elif current_desc and current_desc_lang == "en":
            _remove_role(out, "desc")

    # Defensive: timestamps and title are always those of the selected timeline.
    out.set("start", timeline_programme.get("start") or "")
    if timeline_programme.get("stop"):
        out.set("stop", timeline_programme.get("stop") or "")
    return out


base.choose_event = locked_choose_event


def main():
    return arabic.main()


if __name__ == "__main__":
    raise SystemExit(main())
