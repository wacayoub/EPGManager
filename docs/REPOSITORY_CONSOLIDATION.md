# Consolidation into EPGManager

EPGManager is the single source-code repository for the plugin, provider
adapters, 14 direct feeds, last-known-good publication, derived MENA routing,
and source health reports. The merge preserves both Git histories; exact
parents and restored files are recorded in `consolidation-provenance.json`.

The latest EPG-Scrapers source engine replaces four older same-name tools.
The derived builder, readiness audit and reusable rebuild workflow were
restored from verified commit `8562218`: upstream had deleted them while its
tests and watchdog still required them. The source chain performs donor extension
followed by derived reconstruction. Current schedules use Morocco local time
and are maintained in [`SOURCE_ENGINE.md`](SOURCE_ENGINE.md).

76 former EPGManager workflows, 117 diagnostic/experimental scraper workflows
and retired generator tests were initially copied into archive folders. They
were removed from the current tree during [repository cleanup](REPOSITORY_CLEANUP_20261103.md)
and remain available at `archive/pre-cleanup-20261103` in Git history.
The current engine uses one CI workflow for its full offline test suite.
The obsolete rc70 release builder was removed while receiver release remains NO_GO.

## Correctness fixes

- The plugin catalogue contains all 14 direct feeds on EPGManager. Existing
  direct source IDs remain stable. Installed old repository URLs are resolved
  to the same feed and deduplicated without modifying saved configuration.
- Publication rejects blank/duplicate channel IDs, exact duplicate programme
  rows and orphan events. It validates gzip SHA, size, counts, source label,
  timestamp timezone and the complete TXT ID index before replacing any
  last-known-good bundle.
- ElCinema donor reports use their verified repack cohort. Original provider
  generation remains the freshness clock; an unverified or stale repack
  cannot make old data look current.
- Reusable workflow dependencies, Morocco local schedules and all 14 published
  gzip/XML/JSON/TXT bundles are checked by `verify_unified_repository.py`.

## Measured source evidence

At the 2 November 2026 audit, all 14 direct gzip SHA/size pairs and catalogue
indices matched. No duplicate channel IDs, exact XML duplicate rows or orphan
programme IDs were found in those published feeds. All 14 require review in
the strict 48-hour/editorial summaries; this does not mean all are unavailable.
Current byte-level coverage is recorded in `reports/consolidation-current-health.json`.

| Source | IDs with future EPG / catalogue |
|---|---:|
| Morocco | 9 / 15 |
| AlKass | 8 / 8 |
| Tabie | 4 / 4 |
| Tunisia TV | 2 / 2 |
| Al Jazeera | 2 / 5 |
| Dubai+ | 9 / 9 |
| Shahid | 71 / 71 |
| Rotana | 7 / 9 |
| Sport24 | 7 / 9 |
| OSN | 81 / 100 |
| beIN | 67 / 68 |
| ElCinema | 81 / 97 |
| STARZPLAY | 120 / 120 |
| STC | 149 / 149 |

STC intentionally exposes its complete 149-ID public catalogue. An obsolete
three-channel sports-only audit limit and publication exemption were removed
to match the actual adapter. Repetition warnings remain explicit; programme
rows are not automatically certified merely because the catalogue is complete.
Morocco has no future guide for AFLAM and five Arryadia service IDs. No
programmes were guessed to fill those holes.

The local derived build produced 548 logical IDs and 32,529 programmes.
Readiness found zero structural errors across 14 verified source snapshots:
354 qualified IDs, 151 degraded IDs with EPG and 43 without usable future EPG.
These values are a dated observation, not permanent quality guarantees.

## Validation and migration

Python 3.13 offline validation: 219 tool tests plus 6 migration/publication
tests passed. CI also runs Python 3.14. Rebuild and readiness checks succeed
with explicit editorial warnings. Direct feed bytes were preserved from the
upstream source snapshot; only derived feeds and observation reports were built.

On 3 November the owner explicitly chose one repository after being informed
that historical receiver packages still contain EPG-Scrapers URLs. The former
compatibility mirror is therefore archived. Permanent deletion was refused by
GitHub (HTTP 403; the token lacks `delete_repo`). All 55 original branch tips
are preserved as `archive/epg-scrapers/*` tags in EPGManager, with a complete
local Git bundle and an export of GitHub ticket/PR metadata. See the
[archive manifest and migration boundary](../archives/epg-scrapers/README.md).
The current source overlay rewrites saved legacy feed URLs when loading them;
this does not change an already installed rc78 binary or saved receiver files.
Deletion of the old repository makes its raw URLs unavailable. No new plugin
release or receiver refresh is authorized by this cleanup.

Receiver modified: **false**. GUI, native EPGImport and installed plugin parity
were **not tested** in this repository consolidation. This does not publish a
new plugin package or change mappings, imports, services or receiver settings.
