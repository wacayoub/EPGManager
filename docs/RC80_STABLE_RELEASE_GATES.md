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


## Beta4 private source-provenance repair and 14-source read-only audit

**Private, non-installable RC80 Beta4** was assembled offline from the exact installed RC79.1 recovery package. SHA256: `a475d085c381fb4c1740df7c8fee2d90ed25d98b1eb2b13fc05e312583040863`. 138 Python files and 213 application assets, all file permissions and private configuration intact; only the same five application files differ from the RC79.1 baseline. The `preinst` exits 90 unconditionally, including with supervised flags. Beta4 includes an additional private fix in `core/github_match_health.py`: require the actual published `feeds/<source>.json` to have a fresh, matching generation with the separate 48h and quality reports. Missing or mismatched quality must return `UNKNOWN`, never `EPG_OK`. Reject stale reports from a new candidate while the previous last-known-good XMLTV remains published. Synthetic/duplicate programme labels remain demoted, manual mappings preserved. This is a **timestamp-cohort and metadata check**, NOT cryptographic verification of the XMLTV contents, real-time on-air evidence, or proof of receiver import.

**19 private Beta4 offline tests passed**, including missing quality, stale Tabie LKG, future-only/empty guide, wrong audit publisher, mismatched quality source, repeated/generative titles, incorrect SHA metadata, strict installation refusal, exact file counts/modes and reproducible package build. None of the private source code has been published to GitHub or installed on receiver.

**Public non-deploying source provenance validator:** `tools/audit_rc80_public_feed_provenance.py --online`, validated with 10 synthetic tests plus standard CI. [Public CI 79 tests per Python 3.13/3.14](https://github.com/wacayoub/EPGManager/actions/runs/38648570003), [real read-only 14-source online run](https://github.com/wacayoub/EPGManager/actions/runs/38648620494).

Actual public metadata check 2026-10-14 22:35 UTC:

- Morocco: **PUBLICATION_METADATA_COHERENT**, source-reported 14/14 on-air at publication.
- Tabie: **STALE_OR_INVALID_PUBLISHED_FEED**, still published Oct 5. New Oct 14 reports were produced from an unaccepted candidate and must never certify that old feed.
- beIN, ElCinema, OSN, Sport24, Rotana, AlJazeera: **48H_AUDIT_FAIL**. This means the strict 48h standard is unmet; it does **not** mean every channel is blank.
- Dubai+, Starzplay, Shahid, STC TV, AlKass, TunisiaTV: **PUBLICATION_COHORT_OK_LIVE_NOW_UNKNOWN**; matching publication/audit timestamps but no direct current-on-air channel evidence in metadata.

**Remaining P0 release gates:** real `opkg` recovery transaction and Enigma2 restart, native GUI/remote navigation and skin acceptance, real native GitHub import, 20 real-channel Smart Mapping + persisted manual overrides, 24h scheduler observation, and XMLTV repairs with actual on-air validation for sources. Hardware remains on RC79.1; the private Beta4 is not approved for installation. Do not publish the blocked private IPK as an Online Update asset.


## 14-source verified XMLTV bytes + live-now audit (2026-10-14 23:22 UTC)

A read-only GitHub action [verified all 14 public compressed XMLTV feeds](https://github.com/wacayoub/EPGManager/actions/runs/38653630557) against their *actual* published SHA256 + size (14/14), then parsed real start/stop/title to count genuinely on-air programmes. The source JSON's `currently_on_air_channels` only measures its publication time and is **never** taken as current proof, even if it says 100%.

At that observation UTC: Morocco **14/14**, beIN **54/66**, ElCinema **70/97**, OSN **81/82**, Sport24 **8/8**, Dubai+ **9/9**, Starzplay **116/116**, Shahid **71/71**, Rotana **7/7**, STC TV **149/149**, AlJazeera **3/3**, AlKass **8/8**, TunisiaTV **2/2**, Tabie **0/5**. The denominator is actual XMLTV active programme IDs, not necessarily the satellite bouquet size. ElCinema retains **2 empty programme titles** and Shahid **3**; Tabie old 5-Oct published guide remains unusable. Earlier ElCinema 7/97 at 20:07 UTC differs sharply from 70/97 at 23:22 UTC: investigate time-of-day holes, not simply source-wide EPG absence.

**Only 6/14** full-source release cohorts pass the combination of real-content, no missing titles, >=60% live-now coverage and strict 48h auditing: Morocco, Dubai+, Starzplay, STC TV, AlKass, TunisiaTV. A 48h release failure is *not proof of zero current EPG* (e.g. beIN 54/66, OSN 81/82); preserve mapping for genuine partial current programmes, display degraded/short-horizon warning, and never prefer generated content to verified live programming.

Auditor `tools/audit_rc80_public_feed_provenance.py` now offers `--online --verify-xmltv`, checks actual published gzip hash via the independent bounded parser, reports live-now counts and errors, and never promotes JSON-only statistics to `EPG_OK`. The CI [full public feed run](https://github.com/wacayoub/EPGManager/actions/runs/38653630557) succeeded; 16 synthetic provenance tests passed, previous 79 public application regression tests remained green. **No private device data, no opkg operation, no receiver installation.** The six remaining hardware/recovery gates still require separate evidence.


## 14 Oct 2026 23:35 UTC — SHA-locked title repairs and channel-level candidate evidence

Two real EPG-Scrapers published-source repairs were applied safely through [PR #3](https://github.com/wacayoub/EPG-Scrapers/pull/3), [run 38654529351](https://github.com/wacayoub/EPG-Scrapers/actions/runs/38654529351). Each input SHA was pinned; **only five originally untitled programmes were removed** (Shahid three, ElCinema two), with real titles/descriptions/dates preserved, no generated programmes, unchanged ID membership, new gzip hash and accurate counts. Publication retained the original `generated_utc` and declared a separate `repacked_utc`, so old upstream data cannot be falsely laundered as freshly scraped. Shahid **4543 programmes, 71/71 IDs**; ElCinema **3988, 97/97**.

Independent public [SHA-verified XMLTV audit](https://github.com/wacayoub/EPGManager/actions/runs/38654665180) now shows Shahid **71/71 with programmes currently airing, zero blank titles**, satisfying strict 48h release criteria. ElCinema **78/97 with programmes airing, zero blank titles**, but still 26 failed channel-horizon cases: **17 TOO_SHORT**, **8 NO_FUTURE_EPG**, **1 TOO_SHORT+INTERNAL_GAP**. The earlier 7/97 and 70/97 ElCinema observations are time-of-day differences; investigate actual gaps instead of inventing titles. Complete eligible provider count improved **6/14 → 7/14**; Tabie still expired and 0/5 now.

The public provenance script now handles **verified title-only repack cohorts** while keeping the upstream freshness timestamp authoritative. It refuses old sources even if recently repacked, and requires real downloaded gzip hash+programme evidence before `EPG_OK`.

To avoid the false-negative “whole source has no EPG” mapping bug, the read-only actual-XMLTV auditor now returns `current_real_xmltv_ids`, `future_real_xmltv_ids`, `past_only_xmltv_ids`, `observed_zero_epg_ids` and `per_id_evidence_observed_utc`. Invalid timestamps, blank titles and orphan IDs are excluded from the “real now” set. These per-channel facts remain usable when *other* channels of beIN/OSN/ElCinema make the **whole-source 48h gate FAIL**. This is public offline audit scaffolding, **not yet live device Best EPG Match integration**; source-level strict NO-GO is not weakened.

[Plugin CI run](https://github.com/wacayoub/EPGManager/actions/runs/38654937524): **91/91 tests passed on Python 3.13 and 91/91 on Python 3.14**. [Strict public-source release gate](https://github.com/wacayoub/EPGManager/actions/runs/38654937494) still returns failure by design until all problematic XMLTV providers pass (the evidence artifact remains available).

A possible separate official Qatar TV schedule source was located at `https://www.qtv.qa/transmissionscheduled`, but its machine-readable current-day programme data has not been verified; never publish guessed Qatar/Tabie schedules in its place.

**Installation remains unauthorized**: exact old version rescue payload is fully content-identical, but live `opkg` downgrade/recovery, Enigma2 GUI/startup, import, mapping and full scheduler observation were NOT tested. Keep installed RC79.1, schema 15, mappings/themes untouched.


## 15 Oct 2026 00:03 UTC — live ElCinema exact-ID donor extension published

A new [source-only PR #4](https://github.com/wacayoub/EPG-Scrapers/pull/4) was merged after double Python 3.13/3.14 real-feed shadow testing. The [guarded publication workflow](https://github.com/wacayoub/EPG-Scrapers/actions/runs/38657713720) finished successfully on main. It pinned SHA256 to the exact **Shahid**, **Dubai+** and the previous **ElCinema** feed; it accepted only matching literal XMLTV IDs and only non-overlapping authentic published programme nodes.

**Live published result:** ElCinema has **4208** programmes (previous **3988**) from **220 actual donor events for 12 channels**, with exactly the original **97 catalogue IDs** and all 3988 existing programme nodes preserved. No fabricated text, altered broadcast instant, or receiver files. Source original scrape timestamp still `2026-10-14T11:53:08.608823Z`, a separate `repacked_utc=2026-10-15T00:02:59.436635Z` and SHA/hash/donor provenance now declare the augmentation accurately. Published feed SHA: `caa863a39766f1c7ab30c6de5122f14c28908e462e772b0087d9cd6377a3074e`.

At the source publication instant **88/97 channels contained a genuine current programme**. Strict 36h-per-channel audit failures fell from **81 → 70** (eleven failures recovered). Important: at 23:59 UTC the same shadow test measured **26 → 14**; the sharp midnight difference is the strict **36h horizon boundary**, *not* loss of a large number of currently airing programmes. EPG quality status still **WARN**, entire ElCinema 48h gate still **FAIL**, and 70 remaining channel duration failures must not be painted green. The result has its original provider age and still needs future scrapes to avoid decaying.

No permanent cross-source automation has been attached to the scheduled ElCinema production scraper; this was a guarded one-time repack, which future provider refreshes can replace. Before automating, verify a dedicated regression and per-ID provenance to avoid wrong regional feeds. Receiver RC79.1 remains installed; RC80 not authorized. Real opkg recovery, Enigma2 native imports, GUI and scheduling acceptance remain blocked.


## 15 Oct 2026 — Casablanca automated source scheduling

Merged and activated **EPG-Scrapers source-only schedule PR #5**, [link](https://github.com/wacayoub/EPG-Scrapers/pull/5), commit `47a65a35fe45a28bec0fe32bb5a7efc79f1313f5`. Local timezone via native GitHub Actions `timezone: Africa/Casablanca`: **07:15** 14-source chain, **14:15** watchdog, **15:00** targeted retry of stale/rejected/low-current-coverage ElCinema, OSN, beIN and Shahid (skip active chain, fail-closed on unavailable run status), **19:30** beIN/Sport24/AlKass, **20:30** Morocco/Arryadia. Targeted and sports source jobs use `max-parallel: 1` to reduce overlapping common publish-mutex risks. Watchdog no longer blindly dispatches all 14 source scrapers.

Safety: 13 offline tests on each Python 3.13 and 3.14 plus actionlint YAML validation green: [workflow run](https://github.com/wacayoub/EPG-Scrapers/actions/runs/38791811949). No receiver changes, no private IPK upload, no opkg operation. Native receiver-side 21:30 EPGImport is **not** connected/scheduled by these public source workflows. Live clock-triggered run timing and full-day stability are not yet confirmed; GitHub Actions historical delays were large (15 Oct previous 06:07 UTC scheduled chain started 11:20:40 UTC). **No RC80 install authorization** from these source scheduling improvements.


## 15 Oct 2026 20:00 UTC — ElCinema daily extension now persistent

Verified production regression: the 15-Oct daily ElCinema scrape replaced the one-off 14-Oct donor enhancement; ElCinema's actual published guide fell from **4208 → 3952 events**. Fixed in source-only [EPG-Scrapers PR #6](https://github.com/wacayoub/EPG-Scrapers/pull/6), commit `13484cf1a4abeef0225b7369b5fe9590fd60bf48`. A reusable `elcinema-daily-donors.yml` is now called after all 14 source refreshes **and** after targeted retry completion, before the derived MENA build. It appends only genuine real title-bearing **exact matching ID** Shahid/Dubai+ rows when they do not overlap existing ElCinema, verifies all three input gzip SHA256 checksums and same Casablanca-local-day actual provider generation, preserves all original XMLTV nodes and timestamps, and refuses stale upstream feeds, degraded horizon or altered catalogues. Original provider generation date stays original; additional `repacked_utc` and donor hashes make the provenance explicit. No receiver deployment, no synthetic EPG.

Real GitHub CI dry-run on fresh 15-Oct source feeds: **3952 → 4633** programmes (**+681** authentic programmes for 12 IDs), strict 36h per-ID failures **19 → 8** at the time observed; Python 3.13/3.14 each passed **13 integration/safety tests and current actionlint**: [CI](https://github.com/wacayoub/EPG-Scrapers/actions/runs/38798214028). The [one-time guarded bootstrap](https://github.com/wacayoub/EPG-Scrapers/actions/runs/38798332203) completed both source extension and derived MENA rebuild, independently confirmed on main: ElCinema **4633**, MENA **31221** programmes generated **2026-10-15T20:00:04Z**. MENA gains are not expected to be +681 because canonical routing, higher-priority sources and programme deduplication apply.

Daily scheduling is enabled but its next genuinely clock-triggered run has not yet been observed on the new local-clock configuration. ElCinema still has eight short/missing-horizon channel failures. Tabie upstream old 2026-10-05 guide remains stale, and all RC80 opkg rollback / live GUI / native import / mapping tests remain **NO-GO**.


## RC80 Step: preview-only verified XMLTV EPG ranking (2026-10-15)

Implemented on the **DRAFT** feature branch, not on the live receiver:

- `tools/audit_rc80_public_feed_provenance.py` produces explicit `evidence_schema=1` and `checked_utc`, with SHA256/size-verified programme-level lists and source/cohort checks. Metadata alone cannot certify `REAL_CURRENT_EPG`.
- New `source/overlay/core/verified_epg_match.py` consumes only an optional precomputed **local** JSON snapshot at `/tmp/epgmanager_verified_evidence.json`. It never performs HTTP/XMLTV parsing/import/mapping writes, caps input at 2 MiB, refuses wrong schema, and rejects per-ID proofs older than 20 minutes.
- `source/overlay/ui/channel_mapping.py` now consults the optional bridge **before choosing one suggestion per source** and also reorders the bounded preview list. REAL CURRENT EPG is preferred over future-only/unknown/suspect or zero-EPG IDs when source/identity restrictions allow; a stored mapping still stays at the top. A real current programme is not automatically assumed from programme times alone when source quality flags fail. The UI remains importable if the new bridge is missing.
- Existing manual ownership / Smart Mapping save flows and `_auto_batch_match` are deliberately **unchanged**. This is a suggestion/ranking feature, not a certified automatic-mapping rollout.
- CI: [113 offline tests, Python 3.13 + 3.14](https://github.com/wacayoub/EPGManager/actions/runs/38802378818) passed, including source-ID boundaries, generated placeholders, stale reports, missing schema, manual-owner preservation, actual producer/output compatibility and lossless UI/snapshot parity.

**Outstanding gates before any receiver install:** (1) generate/refresh the actual verified report in the approved receiver runtime without blocking navigation or downloading on every keypress; (2) private package assembly must account for any newly added Python module and maintain a verified complete installed-tree manifest instead of blindly asserting the original 138 count; (3) run receiver-specific UI/EPGImporter/scheduler and full opkg recovery acceptance, with no mappings or config mutated by diagnostic steps; (4) only after all checks should the verified selection policy be considered for Auto Repair. Current rc79.1 remains installed, release remains **NO-GO**.


## RC80 next milestone: bounded automatic evidence refresh (2026-10-15)

**Code staged on the draft feature branch; no receiver installation performed.**

- `core/verified_epg_refresh.py`: bounded background worker, one public source at a time, global cooldown 120s, per-source cooldown 600s, HTTP 10-second timeout and strict response limits (JSON 320 KiB, gzip 4 MiB, expanded XML 24 MiB). XMLTV is streamed through the parser, not loaded as one expanded blob. Reports contain actual current/future per-ID event evidence, declared XMLTV counts, SHA-256/size checks, authenticated publication cohort, excluded placeholders and proper source age.
- The only local write is an atomic mode-0600 temporary report at `/tmp/epgmanager_verified_evidence.json`. Path/symlink guards prevent writes to `/etc/enigma2` or arbitrary other directories. The Enigma2 UI only queues the worker; it neither downloads nor parses XMLTV on the remote navigation thread.
- The UI gate is **OFF BY DEFAULT**, requiring `EPGMGR_RC80_EVIDENCE_BG=1` to opt into background refreshing after a controlled receiver test. This is not an install instruction or a tested acceptance flag.
- Partial 48h provider failures are now treated **per channel**. For a well-formed, channel-complete provider audit showing no invalid times/orphan events, only IDs with `FAIL` are excluded from automatic-quality evidence; genuinely current `PASS/WARN` sibling IDs remain eligible for PREVIEW priority. `48H_FAILED_ID_REVIEW` is shown instead of silently discarding every channel in that source. `_auto_batch_match` and automatic mapping writes are unchanged.
- **130 Python offline tests passed under both 3.13 and 3.14**: [non-deploying CI](https://github.com/wacayoub/EPGManager/actions/runs/38806389547). Synthetic tests cover hash tampering, stale origination timestamps, missing titles, invalid metadata, oversized XML, symlink escapes, backpressure, partial 48h and old/manual owner behavior.
- **Real public gzip smoke**, with NO receiver access: [GitHub workflow](https://github.com/wacayoub/EPGManager/actions/runs/38806282762) at 2026-10-15 20:59 UTC: Shahid **71/71 actual-now IDs verified**; Morocco **14/15 actual-now IDs verified** (one failed source ID, 2M, not auto-approved). The Morocco `morocco-48h-audit.json` reported 2M last programme at 2026-10-15 15:25 UTC and only 3.66h horizon. Upstream fix tracked by [EPG-Scrapers #7](https://github.com/wacayoub/EPG-Scrapers/issues/7).

**Still NO-GO** for production: standalone background code not CPU/timer-tested on Vu+ Zero 4K / OpenATV 8.0.1, no real on-box evidence refresh, Enigma2 GUI/EPGImport acceptance, complete opkg rollback, or validated package manifest combining the original 138-module private receiver tree with new core modules. Remain draft; do not merge, install, alter the update manifest, or change mappings to justify these data-only tests.


## 15 Oct 2026 — Historical baseline restored; strict RC80 package source-inventory gate

The previous RC80 preview UI experiment inadvertently replaced the `data/release-inspect/channel_mapping.py` **historical rc78 baseline** with the new overlay. This broke the non-deploying `rc78-plugin-staging.yml` check (`Expected verified rc78 baseline differs: ui/channel_mapping.py`). The reference has now been restored byte-for-byte from the unchanged audited original (Git blob `d97aa4fed4b4619824efe16668cd9e82d4e1718d`). The enhanced RC80 UI remains solely in `source/overlay/ui/channel_mapping.py`.

The safety test now compares Python ASTs across the baseline and development GUI: only `_cross_source_suggestion_candidates` and `_refresh_cross_source_suggestions` may change; the actual `_auto_batch_match` and all other class methods must remain unchanged. The legacy non-deploying [staging workflow](https://github.com/wacayoub/EPGManager/actions/runs/38809919827) passes again. It tests *old rc78 experimental staging*, **not** a shippable RC80 candidate or the actual private rc79.1 receiver.

New `tools/audit_rc80_package_manifest_readonly.py` is a fail-closed local *source tree* inventory checker for a future **privately assembled** candidate. It requires a SHA256/size-locked exact 138-file rc79.1 receiver manifest, the exact reviewed RC80 preview GUI, and both exact public bridge modules `core/verified_epg_match.py` and `core/verified_epg_refresh.py`: **140 Python files total**. Other original module changes require individually reviewed private SHA256 hashes on a four-path allowlist; extra/missing modules, symlinks, schema downgrade or source drift are rejected. No receiver settings, mapping or private Python need be published. No package is built/installed by this checker and it always reports `authorized_to_install=false`.

[142 non-deploying offline tests on each Python 3.13 and 3.14](https://github.com/wacayoub/EPGManager/actions/runs/38809807501) passed, covering historical baseline, AST non-regression, verified EPG preview, real-feed snapshot fixtures and the strict 138+2 manifest. **Still blocked:** actual 138-file receiver-private manifest from audited rc79.1, private runtime fix digests, assembled package validation, true Vu+ performance and native EPGImport/GUI/scheduler tests, exact opkg recovery. No production merge, no release IPK, no update manifest change.
