# OSN 19 zero-guide IDs: RC80 safe Best Match evidence bridge

**18 October 2026 — REVIEW ONLY, NO-GO for receiver installation.**

## Confirmed source state

Public `wacayoub/EPG-Scrapers` official OSN guide at publication
`2026-10-17T10:51:33.524041+00:00`:
- `101` catalogue IDs and `6,320` programmes; `82` IDs with programmes and `19` with **no future programmes**.
- Source EPG audit: no invalid start/stop or orphan programme records.
- Per-ID SHA-locked source classification: **73 VERIFIED / 9 REVIEW / 19 NO_EPG**.
- Exact 19 zero-EPG Alfa/Pinoy/Legacy IDs retain their canonical XMLTV identities and remain manual-map-visible; no synthetic programme rows are permitted.

The previously audited OSN site / official Alfa API did not provide verified new schedules for these 19. Independent 12 active SHA-verified direct-source feeds supplied **zero exact donor feeds**. No third-party, name-only or unauthorized recovery programme has been added. This bridge is **not a claim that 19 programmes are recovered**.

## New optional RC80 source-side evidence

New accepted OSN publications run `EPG-Scrapers/tools/osn_per_id_health.py` and produce `reports/osn-per-id-health.json` (PR #33), whose byte-cohort SHA must equal the published `feeds/osn.json` and whose 101-ID catalogue must match compressed XMLTV.

On this review branch:
- `core/verified_epg_refresh.py` consumes the **optional** source-side report, checks exact gzip SHA, programme count, 101 identity set, emission provenance, non-synthetic declaration, zero-EPG and category consistency, then **intersects** source-side green IDs with already verified **actual current XMLTV rows**, *never adds green from metadata alone*.
- Missing optional report keeps the prior strict current-XMLTV + per-ID 48h evidence path, since the report only appears after OSN next accepted source refresh. An available report with a mismatched SHA, stale cohort, duplicate/missing channel or invented zero-ID classification fails closed.
- `core/verified_epg_match.py` surfaces a no-guide ID as `NO_EPG` instead of a generic 48h-failure alert, never as an auto-eligible candidate.
- Saved/manual ownership remains top of preview and is **not modified**; all 19 IDs remain visible for human selection.

## Tests & safety status

GitHub Actions 39090159364 passed **221 synthetic/offline tests per Python version** (3.13 and 3.14), including exact-SHA source checks and negative cases, original OSN absence fallback, saved-owner precedence, and no network/config modifications on GUI thread.

The reviewed Git blob identities replacing the prior PR #16 preview are:
- `ui/channel_mapping.py`: `26165496d9c530058dbdbc858c34794c0a4fb1a2` (**unchanged**)
- `core/verified_epg_match.py`: `7181615a9bb10eeb8d8d892929c26c4513c6025d`
- `core/verified_epg_refresh.py`: `78546faeefcbf2d879b6c17d1ebd773480eb83c0`

Both strict public manifest validators and the non-installable offline one-shot package builder must use **these exact pins together**. CI verified the public blobs from their contents. The private RC79.1 rescue remains required offline; **no private baseline or installable IPK is published**. No change to receiver, installed RC79.1, mappings, GitHub updater or Morocco / ElCinema / beIN schedules.

**Unresolved:** availability/rights for original official Alfa and Pinoy programmes; real Vu+ native import, GUI mapping and 24h scheduler acceptance under R80. RC80 remains a **DRAFT/NO-GO**, not final.
