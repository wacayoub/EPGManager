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


## Stage-2 readonly hardware observation (2026-10-14)

The owner executed `tools/audit_rc80_receiver_preflight_readonly.py` on Vu+ Zero 4K. Receiver-reported evidence: `environment_preflight_pass=true`, `preflight_warnings=[]`, native Python `3.14.7`, `opkg status` installed `2026.10.08-rc79.1`, application 138 Python files, config schema 15, all seven protected file hashes matching the audited installed baseline. Both mapping and source-preferences files were **present** (contents not inspected). Free space ~292.9 MiB under `/tmp`, much larger on application/backup partition.

This establishes only *environment readiness*, NOT release authorization; the JSON correctly reported `authorized_to_install=false`.

A second **read-only, non-deploying** receiver observer is now available at `tools/audit_rc80_runtime_readonly.py`, with five offline regression tests. It checks AST syntax of installed modules, process-name-only Enigma2 existence, EPGImport directory presence, static EPG import/mapping markers, and optional SHA-only identification of a locally supplied previous-version IPK. It does **not** read source contents, credentials, user configuration, mappings, EPG XML, process arguments, environment, or log contents. It explicitly marks GUI function, native import and opkg recovery as *not tested*, even when prerequisites appear present.

```sh
wget -qO /tmp/epg-rc80-stage2.py https://raw.githubusercontent.com/wacayoub/EPGManager/feature/plugin-only-consolidation-20261014/tools/audit_rc80_runtime_readonly.py && python3 /tmp/epg-rc80-stage2.py
```

Verified GitHub Actions [stage-2 CI](https://github.com/wacayoub/EPGManager/actions/runs/38644459897): **59/59 tests passed for Python 3.13 and 3.14**. Await actual receiver stage-2 output before changing any runtime gate.

**No R80 installation authorized** without full `opkg` rollback and real GUI/native import validation.


## Actual Vu+ stage-2 report and false-positive correction (2026-10-14 22:01 UTC)

Receiver-provided stage-2 read-only JSON: `2026-10-14T22:01:55.941383+00:00`, installed RC79.1, Python 3.14.7, config schema 15, 138 Python modules, **no syntax errors**, **no missing required files**. GitHub Direct checks, non-destructive import, native import button, scheduler, saved auto owner, manual and background mapping, and cross-source suggestions all have their expected static markers. Native EPGImport directory and /etc/epgimport directory exist; 2 processes named `enigma2` were observed in /proc. These are **static/prerequisite observations only**, not GUI, import, scheduler or recovery success.

Two reported observations:
- `STATIC_FEATURE_MISSING:ui/main.py`: **expected** — existing RC79.1 UI has `open_native_import()`, but the RC80-only public overlay `source/overlay/ui/main.py` introduces `open_diagnostics()`. The missing function is a target feature delta, not evidence that RC79.1 was corrupted. Stage-2 observer now reports `expected_rc80_features_not_in_rc79` separately and does not mislabel this missing RC80-only method as a baseline regression.
- `ROLLBACK_IPK_NOT_VERIFIED`: `provided=false` in the user's call; no old package was supplied for SHA identification. It **does not mean no IPK exists on disk**. A read-only `--scan-local-rollback` option now checks just two exact known rc79.1 basenames in /tmp, /var/volatile/tmp, /media/hdd, and /media/usb, with no recursive reads. Matching hashes prove identity only, *not* an `opkg` recovery rehearsal.

Neither flag authorizes the upgrade. Install authorization remains **FALSE** until certified `opkg` downgrade/recovery and native GUI/import/scheduler tests are complete.


## Hardware verification: rc79.1 rescue payload

The owner ran stage-2 and the full rollback package audit on the Vu+ receiver on October 14, 2026. Stage-2 found no unexpected observations; the absent Diagnostics method is an expected RC80 feature delta.

Stage-3 read-only output: rescue package has exact parity with the installed application: 138/138 Python modules, 75/75 other assets, 213/213 file modes, zero missing/extra files, correct package metadata and passive maintainer hooks. Result: `rescue_content_parity_found=true`, `status=COMPLETE_CONTENT_OPKG_UNTESTED`.

The original candidate is NOT an adequate rescue: 136 Python files, only 133 matching installed Python files, 208 matching file modes of 211 assets, missing two installed files and non-passive scripts. Never downgrade using the original candidate.

**The rescue package is content-perfect but not tested with opkg. This report does not authorize an upgrade.** Live opkg recovery, Enigma2 GUI, real native import, mapping and scheduler validation remain open.


## Stage-3 receiver parity evidence

The receiver's read-only stage-3 report confirms that the local RC79.1 rescue snapshot is an exact application payload match: **138/138 Python modules**, **75/75 other assets**, **213/213 permission modes**, zero unmatched files in either direction, metadata valid, matching SHA256, and four passive maintenance hooks. The report says `complete_rescue_content=true` and `rescue_content_parity_found=true`.

The older original candidate does **not** qualify for recovery (only 136 Python modules, 133 matching; two receiver-only modules, non-passive hooks, incomplete mode parity). It must not be used as the working rollback artifact.

This closes the *recovery-content identity/parity* subcheck, **not** the required real package-manager rollback, Enigma2 restart or GUI checks. `opkg_rollback_tested=false`, `authorized_to_install_rc80=false` remain unchanged. Next, validate package-manager transaction behavior in an isolated test environment without changing the live receiver, then complete GUI, mapping, native importer and scheduler acceptance.


## Private rc80-beta3 five-file integration and seven-gate status

Read-only Vu+ stage-3 verified a **complete and exact** local rc79.1 rescue package: 138/138 installed Python modules, 75/75 additional application assets, 213/213 mode matches, valid opkg metadata, and passive rescue maintainer scripts. The old `AUTOMAPPING_SAFE` package is NOT an eligible full rollback: 136 Python modules (133 matching), 208 mode matches and unsafe old hooks. No opkg recovery performed.

A new **private offline-only, deliberately non-installable** `2026.10.14-rc80-beta3-private` package was assembled from the receiver-owner's exact rescue IPK. SHA256: `af32662d04425c3b83a543b063fa50cd430bae18f9e371e1088200719dae0d96`. The package is held privately and is **NOT a GitHub artifact** or online update target. It has 138 Python modules, 213 application files, unchanged schema 15, and exactly five changed application files: `core/github_direct_sync.py`, `core/github_match_health.py`, `core/system_check.py`, `ui/main.py`, `version.py`; 208 files remain byte-identical to receiver baseline. Private mappings, themes and other assets are unchanged.

UI integration deliberately preserves the working rc79.1 `ui/channel_mapping.py` rather than overwriting it with stale legacy rc70 overlays. It adds an in-place `MORE` Diagnostics choice menu (Zero EPG, Duplicates, System Check, Logs) without changing FHD widget geometry, and corrects the System Check aggregate so **1/14 usable remote feeds cannot be reported as PASS**. Four source-health behaviors remain gated by fresh quality reports. Its package `preinst` **always exits 90**, even when supervision flags are supplied; no installation is possible. Offline postinstall static test is read-only on a temporary extraction.

Private Python 3.13.5 offline regression: **14 test functions passed**, including a loop of **20 synthetic receiver-channel ranking cases**; **six synthetic Casablanca Native Import scheduler cases passed**. These are not 20 live SAT-channel tests, nor a native EPGImport execution, nor a real GUI session. Existing public workflow still passes **69/69 Python 3.13 and 69/69 Python 3.14** at [CI](https://github.com/wacayoub/EPGManager/actions/runs/38645920975); private code was NOT uploaded to CI.

Fresh public feed metadata were inspected for all **14** direct providers: Morocco `2026-10-14T20:30:48Z` 14/14 airing-now channels; Tabie remains `2026-10-05T21:53:55Z` stale (5/9 future catalogue entries; 0 genuine current channels in previous audit). Other historical feed coverage percentages cannot prove a channel is airing NOW. ElCinema/Shahid authenticity and missing-title issues still need verified published repairs.

Seven-gate status: **(1)** rescue content parity verified on hardware; **(2)** opkg recovery NOT proven; **(3)** GUI menu mock PASS, real receiver blocked; **(4)** GitHub Direct structural checks PASS, real import blocked; **(5)** 20 synthetic mapping fixtures PASS, real receiver mapping blocked; **(6)** six synthetic scheduler cases PASS, 24h observation blocked; **(7)** 14 source metadata reviewed, Tabie and others unresolved.

**Hard NO-GO remains.** No live receiver installation, forced update, fallback to old rc70, or replacement of private receiver source is authorized.
