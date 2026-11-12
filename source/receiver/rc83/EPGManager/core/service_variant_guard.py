# -*- coding: utf-8 -*-
"""Hard programme-feed firewall.

beta114 generalises the beta113 Al Aoula-only guard to NAT/National, TNT,
International/Inter, Monde/World, MENA, language editions and known numbered
channel families. Technical HD/SD/UHD variants remain interchangeable.
"""
from __future__ import print_function
from . import programme_feed_policy


def receiver_variant(service):
    name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
    family, variant = programme_feed_policy._known_family_variant(name)
    return variant if family else ""


def candidate_variant(candidate_name="", display_name=""):
    family, variant = programme_feed_policy._known_family_variant(display_name or candidate_name)
    return variant if family else ""


def check(service, source=None, candidate_name="", display_name=""):
    ok, reason, _fallback = programme_feed_policy.feed_compatible_with_source(
        service, candidate_name, display_name, source=source)
    return ok, reason


def check_epg_row(service, source=None, row=None):
    row = row or {}
    return check(service, source=source,
                 candidate_name=row.get("channel_id") or "",
                 display_name=row.get("display_name") or "")
