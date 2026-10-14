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


## Private runtime correction evidence (not public source)

On 2026-10-14, the receiver-owner's private rc79.1 rescue package (SHA256 `117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69`) was inspected **offline**. A private patch bundle was prepared for exactly three modules, preserving the 138-module count:

- `core/system_check.py`: native GitHub Direct mode no longer falsely fails solely because local XML is absent. Dynamic provider age over 72h still fails; missing status is warning, and 0 live-now channels is visible.
- `core/github_match_health.py`: no `EPG_OK` without a fresh, matching quality audit; `NO_EPG`, `SYNTHETIC` and `UNKNOWN` remain distinct.
- `core/github_direct_sync.py`: parse real currently-on-air coverage metadata, safely ignoring invalid values.

**11 private mocked/offline Python 3.13.5 tests passed.** Seven tests fail or error against the unchanged rc79.1 baseline as expected (5 failures + 2 errors). The patch is a **private review ZIP**, not an installable IPK; the original package was not changed and private receiver code/configuration was **not committed to GitHub**.

**Still blocked:** integrating into audited full rc80 package, safe actual opkg downgrade/rollback, receiver GUI + EPG import/scheduler smoke tests. The existence of private patched source must not change NO-GO release status.


## Private RC80 beta2 package and recovery rehearsal (2026-10-14)

A receiver-private **non-installable** IPK (`2026.10.14-rc80-beta2-private`) was built entirely offline from the owner's SHA-256-locked rc79.1 local rescue snapshot, incorporating only three private core fixes plus `version.py`. **This artifact is not committed or published here.**

- 213 receiver package assets, 138 Python source files; 209 bytes-and-mode-identical unchanged files. Configuration schema stays 15. No user mapping/settings source is included.
- The candidate's `preinst` **always exits 90** and cannot be overridden by supervised flags; this package is **not installable**, not even for a field beta.
- Reproducible byte-for-byte IPK archive; SHA256 `4740f6ed0f5b2e7c38008c0eeb40849d16a74e52cc2c35064f85445563beb684`.
- **19/19** offline tests on Python 3.13.5: proven source checksum, file/mode parity, total module AST syntax, version/schema consistency, refusal of all installation paths, postinstall static check and expected failure handling, filesystem-only backup/recovery with configuration preserved, tar traversal/symlink rejection and archive reproducibility.
- This is **only a private filesystem rollback simulation** with mocked package-version metadata. No actual `opkg` rollback/repair was performed; no Enigma2 runtime or native import test on hardware. Source-data blockers (ElCinema, Tabie) remain.

**Release gate remains NO-GO.** A production build must be separately constructed and tested on the receiver, with a working exact-version recovery package, package-manager metadata restoration, GUI, scheduler and native importer smoke tests. Do not use the private package name as `update.json` target.
