# EPGManager RC80 → stable 1.0 — release gates

Status: **NO-GO** (14 October 2026). This is a release checklist, **not an installation instruction**.

## Baseline to preserve
- Vu+ Zero 4K / OpenATV 8.0.1 / receiver Python 3.14.7.
- Installed package: `enigma2-plugin-extensions-epgmanager` `2026.10.08-rc79.1`.
- 138 receiver Python modules, configuration schema 15; manual/automatic mappings, source preferences, Rytec, GitHub-direct links, previous EPG data, and selected native theme must remain unchanged unless the user explicitly edits them.
- Receiver source snapshots and mappings are **private**; never push receiver exports to the public repo.

## P0 — required before publishing a production IPK

| Gate | Acceptance evidence | Status |
| --- | --- | --- |
| Reproducible build | Build *exact* audited rc79.1 receiver Python tree + reviewed rc80 overlays; no rc70 release archive. All 138 files compared. Signed/hashed output, no private data shipped. | **BLOCKED** |
| Backup/recovery | Check a receiver-private previous-version IPK against all 138 modules and other assets; rehearse both `opkg` database recovery and Enigma2 startup after failed upgrade. Code-only tar restoration does not meet this. | **BLOCKED** |
| Package hooks | No XMLTV/cache/mapping/theme deletions. Preinstall snapshot validated, failure aborts before overwrite, postinstall performs only static checks. Unsupervised installs blocked. | Offline **PASS**, hardware **BLOCKED** |
| GUI acceptance | On real receiver: Overview, Sources, Smart Mapping, duplicates, zero-EPG, settings, Online Update, System Check and Logs; all keys and footer labels responsive; no crash/soft-lock. | **BLOCKED** |
| Import pipeline | Read source XMLTV directly from GitHub, fallback last-known-good only if valid/fresh; Native EPGImport count/progress changes live; mapping IDs persist after restart. | **BLOCKED** |
| Mapping reliability | For 20 named SAT channel cases test best candidate from online source rather than stale local cache; prefer real non-empty, current programmes; preview on YELLOW; manual mapping overrides automated selection; no accepted stale or generated EPG. | **BLOCKED** |
| Scheduling | Verify all source triggers, Casablanca DST/offsets and a full 24-hour daily cycle; no overlapping jobs, no unnecessary receiver scraping, clear diagnostics. | **BLOCKED** |
| Source integrity | JSON/gzip SHA + size parity, 48h coverage, actual programme start/stop, nonempty titles, live-now coverage, 5 sample programmes/source and language policy. | 11/14 **PASS**, 3 **BLOCKED** |
| CI | Python 3.13 + 3.14; all synthetic tests pass; no secret export; publish path disabled until other gates certified. | **PASS: 54 tests per Python** |
| Release | Only after all gates pass: pinned immutable release asset, checksum/size, `update.json` manifest updated atomically and receiver update/recovery tested. | **BLOCKED** |

## Known blocking feed defects (published XMLTV, 2026-10-14 20:07 UTC)

- ElCinema: 97 IDs nominally active, but only 7 channels have a programme at audit time; two programme titles are empty.
- Shahid: three empty programme titles.
- Tabie: feed generated ~214 h ago, 5/9 IDs with events and **0 channels with a current programme**.

Trace: [XMLTV audit](https://github.com/wacayoub/EPGManager/actions/runs/38629384553), [source remediation issue](https://github.com/wacayoub/EPG-Scrapers/issues/1).

## Product scope and simplification before v1.0
- Keep a single responsive native Enigma2 dashboard (no WebView), existing skin dimensions and selectable theme.
- Keep only useful primary navigation: Overview, Sources, Smart Mapping, Duplicates/Zero EPG, Settings, Logs/Diagnostics, Online Update. Merge repeated maintenance actions.
- Source detail should disclose feed ID, actual source, last successful import, freshness and 48h EPG presence. A source entry displaying a channel with 0 EPG must not outrank a valid feed.
- Show real import progress as current imported-channel count rather than a generic system clock or fake percentage.
- Background operations must never block remote navigation; avoid expensive mapping rescan on each keypress.
- Manual mapping must remain accessible even if automatic matching found a candidate; save/cancel semantics stable.
- Preserve user-defined theme and color; don't reset theme on upgrade.

## Links and commands for maintainers (read-only until release gates pass)

- Draft [plugin-only finalization PR](https://github.com/wacayoub/EPGManager/pull/8).
- [54 synthetic tests on Python 3.13/3.14](https://github.com/wacayoub/EPGManager/actions/runs/38629592321).
- In CI use `python3 -m unittest discover -s tests -p 'test_*py' -v` and `python3 tools/audit_published_xmltv.py --strict --output xmltv-audit.json`.
- **Do not** run the legacy main release workflow, force reinstall an older `update.json`, set `EPGMGR_RC80_SUPERVISED` without a tested recovery plan, or install a generated `LOCAL_RESCUE_UNTESTED.ipk` on the live receiver.

## Decision

Keep RC79.1 installed and PR #8 as DRAFT. Do not merge/publish a stable IPK while any P0 release gate is open.
