# Source quality review — 2 November 2026

The 14 primary sources are retained. Each contributes real programmes, unique
services or an exact-ID backup. In particular, AlKass remains valuable as the
official source and backup for services also present in operator packages.
Similar channel names alone were not used to merge identities.

## Remove, replace and retain

All 117 generic catalogue URLs were checked with bounded HTTP GET requests and
archive signatures. Eight returned HTTP 404 twice. The publisher's own index
confirmed three replacements, whose XMLTV samples contained future programmes:

| Catalogue entry | Old file | Replacement |
|---|---|---|
| Belgium | epg_ripper_BE1.xml.gz | epg_ripper_BE2.xml.gz |
| Canada | epg_ripper_CA1.xml.gz | epg_ripper_CA2.xml.gz |
| USA | epg_ripper_US1.xml.gz | epg_ripper_US2.xml.gz |

The five confirmed dead EPGShare links for Estonia, Iceland, Slovenia, China and
Venezuela are removed from the bundled catalogue. This removes those particular
links, not every possible source for those countries. No replacement was guessed.
Existing Belgium/Canada/USA source IDs remain stable. Installed definitions with
the old URLs resolve to their replacements and deduplicate. Saved settings are
not rewritten. Confirmed retired URLs are excluded from the source list.

109 other archive endpoints remain available as optional fallback sources.
Together with the three replacements, the generic catalogue now has 112 entries.
An HTTP response and archive signature establish availability, not correctness
of every future programme. These feeds retain their fallback priority.

Detailed URL/status and replacement evidence:
`reports/source-review-external-catalogue.json`.

Expired `others` and `vuplus-test` publications and diagnostic test feeds are
archived under `docs/archived-feeds/`. Neither expired bundle contained any future
event: their latest stops were 30 September and 5 October respectively. The active
publication directory now contains the 14 direct feeds plus derived MENA.

## Remove filler, retain the actual programmes

| Feed | Channel-label / filler programme rows removed | Unavailable descriptions removed |
|---|---:|---:|
| OSN | 135 | 0 |
| STARZPLAY | 343 | 0 |
| STC | 1,466 | 132 |
| Total | 1,944 | 132 |

The cleanup removes explicit generated filler, unavailable-guide titles and
long exact channel labels masquerading as programme names. A real bilingual
title is retained when only the other language contains filler. Genuine repeated
news/shows, French 2M titles, real descriptions, programme times and channel IDs
are preserved. The 132 removed descriptions contain explicit unavailable-info
messages; no replacement synopsis is invented.

The regular source packer now applies the same cleanup before publication.
Derived MENA also filters old last-known-good inputs, so a provider outage cannot
reintroduce old filler. Sources remain visible for manual mapping when they have
no usable programme.

Each published repair is pinned to its reviewed input SHA, validates original
counts/IDs/intervals, preserves the provider's original `generated_utc`, and records
the transformation separately. Re-running it is a byte-identical no-op. Freshly
computed reports match the repaired output SHA; repacking never refreshes the
provider age. Proofs are the three `*-quality-cleanup.json` reports.

## Measured usable coverage after cleanup

| Primary source | Catalogue IDs | IDs with future EPG |
|---|---:|---:|
| Morocco | 15 | 9 |
| AlKass | 8 | 8 |
| Tabie | 4 | 4 |
| Tunisia TV | 2 | 2 |
| Al Jazeera | 5 | 2 |
| Dubai+ | 9 | 9 |
| Shahid | 71 | 71 |
| Rotana | 9 | 7 |
| Sport24 | 9 | 7 |
| OSN | 100 | 79 |
| beIN | 68 | 66 |
| ElCinema | 97 | 79 |
| STARZPLAY | 120 | 117 |
| STC | 149 | 124 |

These dated counts can decrease with elapsed time or filler removal. Lower counts
are not hidden by synthetic coverage. No whole primary source is empty. The
post-cleanup audit finds zero placeholder-title rows and zero unavailable-info
descriptions in the 14 direct feeds, and zero publication-integrity blocks.
Other coverage gaps, short horizons, repetition suspicions and missing synopses
remain explicit in the reports; these findings do not certify every programme.

`reports/source-review-decisions.json` records the per-source decisions and exact
published SHA values. `source-review-current-health.json` and
`source-review-health-audit.json` contain the measured coverage and remaining issues.

Validation: 229 tool tests and 6 migration tests pass locally on Python 3.13;
CI runs Python 3.13 and 3.14. All 14 gzip/JSON/TXT bundles and the derived MENA
snapshot pass structural validation. No receiver settings, mappings, plugin
installation or Enigma2 process were changed.
