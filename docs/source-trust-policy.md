# EPG source trust policy

This repository must never publish a channel merely because an upstream catalogue contains an XMLTV ID.

## Tier A — direct / broadcaster / platform source
Eligible for primary use only after live validation of the exact channel schedule.

Current approved primary families:
- beIN / beIN SPORTS direct adapters
- OSN official guide
- Shahid / MBC live-channel data when the exact public guide/API is validated
- Morocco direct adapters already validated in this repository
- Sport24 only for channels whose Arabic schedule was manually validated (currently Abu Dhabi / Dubai Sports coverage)
- ElCinema only for channels whose real schedule is returned and passes validation

## Tier B — reputable guide/operator source
Examples can include operator TV guides or well-known programme guides.
These are not promoted automatically. They may be used only after comparing the exact programmes against a direct/native source and confirming the channel identity.

## Tier C — fallback only
- EPGShare
- generic mirrors / copied XMLTV feeds
- sources with unclear origin
- sources that expose IDs but no current verifiable programme grid

Tier C can never replace a healthy Tier A or Tier B source.

## Mandatory live validation before adding a new channel
A candidate channel must pass all of these checks:
1. Exact channel identity verified (no regional/feed mismatch).
2. Real current/future programmes returned; not placeholder text.
3. start < stop for every accepted event.
4. At least 24 hours future coverage; target 48 hours when the source provides it.
5. Titles/descriptions follow the repository language policy.
6. No copied/shifted schedule from another channel.
7. No duplicate canonical XMLTV ID in the final published set.
8. Source URL/API remains reachable on a clean scheduled run.
9. If the source fails, Last Known Good remains published.
10. The candidate is reported for review before first production promotion.

## Language policy
- Native Arabic channels: Arabic title + Arabic description.
- Foreign movies/series/subtitled programming: English/original title + Arabic description.
- Non-Arabic channels: English title + English description.

## Discovery is not publication
Files such as `reports/arab-epg-gaps.json` are discovery inventories only.
A discovered ID is NOT considered valid EPG until it passes the mandatory live validation above.


## Explicitly blocked sources
- sat.tv — do not use for scraping or production. Previously tested and blocks scraping; keep only as discovery evidence if it appears in external catalogues.
