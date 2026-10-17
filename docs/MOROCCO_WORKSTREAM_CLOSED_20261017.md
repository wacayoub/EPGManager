# Morocco Cloud EPG — Workstream closure (2026-10-17)

**Disposition: ACCEPTED / CLOSED FOR THIS ROUND (with explicit known limitations).**
This closes *Morocco-specific feature development and repeated manual audits*, **not**
the whole EPGManager RC80 release, not the scheduled Morocco source refresh,
and not the user's Vu+ Enigma2 mappings or configuration.

## Verified published baseline

- Latest verified successful deployment: [Morocco EPG Cloud run 39075806281](https://github.com/wacayoub/EPGManager/actions/runs/39075806281).
- Public XMLTV: `https://raw.githubusercontent.com/wacayoub/EPGManager/epg-data/morocco.xml.gz`.
- Public manifest: `https://raw.githubusercontent.com/wacayoub/EPGManager/epg-data/manifest.json`.
- Generated 2026-10-17T22:37:32.867838+00:00; published to `epg-data` commit `c50b98212892be39a659f617d15966012707a056` on 2026-10-17T22:38:09Z.
- Manifest SHA-256 for gzip: `13a5b86ee3043cf1a9bf6626be89999b477908f098a5f857937c9dd150bbe038`.
- **15 stable XMLTV channel IDs; 1,936 programme elements.**
- Production publication status **`partial_with_viewer_reported_slots`**.
  This is a factual *partial* guide, not a claim of fully verified 15-channel coverage.

| XMLTV ID | Published programme elements | Provenance / acceptance |
|---|---:|---|
| 2M | 218 | Actual seven-day source; genuine French-origin show titles preserved where explicitly verified; otherwise Arabic/Darija editorial policy; Arabic descriptions |
| AlAoula | 30 | Official SNRT Arabic daily grid; Arabic title and description |
| Arrabiaa | 162 | SNRT source |
| AlMaghribiya | 359 | SNRT source |
| Assadisa | 432 | SNRT source |
| Tamazight | 242 | SNRT source |
| AFLAM.ma | 52 | SNRT source; some generic show titles may remain |
| Arryadia_HD | 0 | Stable ID retained; no current verified broadcast programme |
| Arryadia_TNT | 0 | Stable ID retained; no current verified broadcast programme |
| Arryadia_HD1 | 0 | Stable ID retained; no current verified broadcast programme |
| Arryadia_HD2 | 168 | **Viewer-reported** Grand Format, one hour per slot, 24/day for seven days; NOT verified SNRT times |
| Arryadia_HD3 | 168 | **Viewer-reported** Grand Format, one hour per slot, 24/day for seven days; NOT verified SNRT times |
| Chada TV | 43 | Only `https://chada.ma/fr/tv/grille/`; NO TeleNews or previous cache as provider |
| MEDI1TV_AR.ma | 48 | Medi1 source |
| MEDI1TV_MAGHREB.ma | 14 | Medi1 source |

## Locked editorial and provenance rules

1. Moroccan-only Morocco Cloud scope, without disturbing other groups or receiver mappings.
2. **2M:** preserve verified French titles, including `Info Soir` and `Eco News`; `Bulletin Météo` in French immediately after Info Soir, otherwise Arabic; Arabic programme descriptions. Do not generalize from Latin script alone.
3. **Al Aoula:** the `/ar/al-aoula` official page; never French titles or last-known-good French entries.
4. **Chada TV:** only official weekly broadcaster programme page; no TeleNews donor or LKG programme substitutions.
5. **Arryadia:** no stale SNRT rows shifted from September to October, no synthetic three-hour `Programmes Arryadia` blocks and no automatic `LIVE:` merely because the variant is HD1/HD2/HD3.
6. **Arryadia HD2 / HD3:** user-observed `Grand Format` hourly slots labeled separately as non-official viewer-reported recurrence; future dated/verified official programmes can replace overlapping hourly slots. HD2 and HD3 excluded from football fixture speculation; HD1 advisory candidates never become XMLTV without verification.
7. Preserve all 15 stable channel IDs even when official/current EPG is unavailable; expose `missing_current_epg` and truthful partial publication status.
8. Daily Morocco refresh remains **enabled**; source freshness and validation gates remain enabled. Closing the workstream does not disable daily cloud generation.

## Explicit accepted limitations — do not misrepresent as fixes

- `Arryadia_HD`, `Arryadia_TNT`, `Arryadia_HD1` currently have no reliably dated, genuine current EPG. They remain in `missing_current_epg` and display zero programme elements.
- HD2 and HD3 hourly slots have viewer-reported provenance; they are *not* independently confirmed broadcaster schedules.
- Some SNRT descriptions and Aflam titles can still be generic. No fabricated refinements should be introduced only to improve counts.
- The six-month Arryadia historical audit did **not** recover six complete months of day-by-day EPG; usable broadcaster rows were primarily one September week with limited August corroboration.
- Receiver-side consumption after the next importer run has **not** been directly verified in this closure. The receiver's RC79.1/R80 plugin state is tracked separately.

## Closure checklist

- [x] Latest public manifest inspected after published pipeline success.
- [x] Programmes/counts and 15 stable IDs recorded.
- [x] 2M, Al Aoula and Chada provenance locked.
- [x] HD2/HD3 Grand Format and remaining Arryadia gaps clearly labeled.
- [x] No Morocco-specific open GitHub PRs or issues at closure inspection.
- [x] All latest relevant Morocco source, candidate and publication tests passed.
- [x] Vu+ settings, mappings and plugin untouched.
- [x] **Feature workstream closed; no additional manual Morocco audits unless new evidence or an explicit user request.**

## Delivery history

- [PR #26](https://github.com/wacayoub/EPGManager/pull/26): 2M French-original editorial rules.
- [PR #27](https://github.com/wacayoub/EPGManager/pull/27): Chada official-only.
- [PR #28](https://github.com/wacayoub/EPGManager/pull/28), [PR #29](https://github.com/wacayoub/EPGManager/pull/29): Al Aoula Arabic source and production checks.
- [PR #30](https://github.com/wacayoub/EPGManager/pull/30): Arryadia archival limitations and recurrent show evidence.
- [PR #31](https://github.com/wacayoub/EPGManager/pull/31): no invented Arryadia programmes.
- [PR #32](https://github.com/wacayoub/EPGManager/pull/32), [PR #33](https://github.com/wacayoub/EPGManager/pull/33): football candidate diagnostics, remove HD3 from fixture search.
- [PR #34](https://github.com/wacayoub/EPGManager/pull/34): Grand Format hourly on HD2+HD3, exclude both from fixture search.

**Next project focus:** EPGManager RC80 overall release preparation, **not** further Morocco feature modifications.
