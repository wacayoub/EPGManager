# RC80 actual LAN validation — 18 October 2026

**Current follow-up: the owner reported trigger/synchronization discrepancies.
A GMT+0 timing/receipt correction is prepared and tested offline, but not installed.
Its actual receiver trigger/GUI acceptance is pending specific installation and
restart approval. The earlier successful 141-module installation below is
historical evidence, not proof that this new correction is receiver-validated.**
Evidence: [prepared timing correction](evidence/rc80-sync-timing-prepared-20261020.json).

**GO for the final RC80 installed on this qualified Vu+ Zero 4K. The owner-
authorized `2026.10.20-rc80` IPK is now installed with opkg: 141 modules,
schema 15. Enigma2 restarted in 45.22 seconds without a receiver reboot.
A new real native import processed/imported 16,044 events, zero failures.
All 496 sampled pre-install events survived installation, restart and import.
Mappings/preferences and EPGManager settings are preserved. Runtime metadata
rewrites during restart are documented below. No public release or merge.**
Current evidence: [final installed acceptance](evidence/rc80-final-installed-20261020.json).
The dated sections below retain the earlier qualification history.

One consolidated candidate:
[draft PR #38](https://github.com/wacayoub/EPGManager/pull/38), branch
`codex/rc80-final-qualification-20261018`, based on `948ca148`.
This supersedes the earlier SSH_NOT_TESTED report.

## Actual receiver baseline before authorized installation

Read AGENTS.md and codex/CODEX_SSH_TASK.md before the probe. Alias `epg-vu`
fails name resolution: no SSH alias configuration exists on this PC. Direct
SSH to the owner's previously verified LAN receiver succeeds with existing
session credentials. No credential is requested in chat or stored in Git.

| Observation | Actual result |
| --- | --- |
| EPGManager | 2026.10.08-rc79.1 |
| Schema / Python modules | 15 / 138 |
| Python | 3.14.7 |
| Image | OpenATV 8.0.1, build 20261003 |
| Enigma2 package | 8.0.1+git35583+57b7a510+57b7a5127d-r1 |
| EPGImport package | 1.1+git314+a32929f0+a32929f2d0-r0 |
| Enigma2 process / EPGImport extension | Active / present |
| Syntax on receiver | 138 modules parsed, zero syntax errors |
| Seven protected module hashes | All match known RC79.1 |
| Mapping/preferences files | Present; contents never read |
| Already tuned satellite service cache | 27 valid events: one current, 26 future |

Dependency discovery in standalone SSH Python found requests, urllib3, bs4,
lxml, twisted, SSL, gzip/zlib, bz2/lzma and both pytz/zoneinfo. The optional
cloudscraper package is absent. Enigma2's embedded modules and GUI path are
not discoverable in this standalone interpreter; this is not proof that the
running Enigma2 process lacks them. Standalone Python reports UTC (offset 0).
Actual GUI local-time display and dependency imports were not exercised.

The cache check used read-only localhost OpenWebif GETs. It does not certify a
new import or GUI operation. No channel name, service reference, private URL,
programme text, mapping or settings content is published. Source capture and
AST parsing took 7.27 seconds including capture overhead, not GUI latency.
Evidence: `docs/evidence/rc80-receiver-20261018.json`.

## Detected defects and candidate corrections

1. **RC79.1 false green reproduced on hardware.** Its classifier returns EPG_OK
   for a PASS slot report without a per-ID quality denominator. The SHA-locked
   private patch returns EPG_WARN. Qualified MENA metadata cannot promote
   UNKNOWN; absent/expired MENA cannot grant green in the legacy health path.
   Malformed numeric quality now returns UNKNOWN instead of raising into UI.
2. **MENA exact owner/ID vetoes.** NO_FUTURE_EPG/zero future programmes block
   green; degraded guides require review. MAIN/B1/B2 never certify quality.
   The legacy health worker downloads one bounded MENA report per batch and
   builds an index once; UI lookups are O(1), with no network on UI callbacks.
3. **Preview consistency.** Empty/degraded candidates lose automatic preview
   eligibility and show NO EPG/EPG REVIEW. Only four reconciled preview methods
   change. Original auto/manual/undo writers, class configuration and key
   actions pass AST parity; exact source SHA gates remain enforced.
4. **Preserved existing PR fixes.** Explicit occasional beIN INFO rows no
   longer invalidate healthy siblings. Empty MAX/XTRA/AFC IDs receive no fake
   programmes. Optional local XML absence reports WARN for direct GitHub
   sources, not false FAIL or proof of a successful native import.

No live defect in persistence, scheduler, source offsets or import execution
is claimed fixed: those functional cases remain untested. No language,
source priority, ID, programme content, mapping or time offset was rewritten.

## Actual candidate execution on Vu+ CPU

Candidate evidence/health modules ran via SSH stdin under an isolated
in-memory package, without importing/replacing installed plugin modules.
Synthetic logic fixtures are separate from the real cache check above.

| Synthetic case | RC79.1 | RC80 candidate |
| --- | --- | --- |
| Missing per-ID quality | EPG_OK | EPG_WARN |
| Qualified owner + quality | — | EPG_OK |
| Degraded owner | — | EPG_WARN |
| No future EPG | — | NO_EPG |
| Malformed numeric quality | — | UNKNOWN |

Final consolidated-code run: all six expected classification results matched.
Five trials ranking 500 synthetic candidates: median **42.48 ms**, range
42.33–69.57 ms on Vu+/Python 3.14.7. This is CPU ranking, not GUI rendering or
a measured overall speedup. Python uses optimization level 1 on the receiver;
the final test uses explicit exceptions, independent of disabled assertions.
The initial consolidated test script had an invalid future-import concatenation;
a subsequent probe exposed a sub-microsecond fixture timestamp rounding issue.
Neither attempt is counted as a successful final test. The corrected final
probe exited successfully and rechecked all seven unchanged protected hashes.

## Actual public-source tests (PC)

Evidence: `docs/evidence/rc80-public-20261018.json`, checked 19:55 UTC.
Before/after share downloaded bytes and observation time; baseline `948ca148`.
**14/14 compressed XMLTV hashes verified.** MENA: 517 IDs, 398 qualified at
build, 93 degraded, 26 without future EPG (publication-time classifications).

- beIN independently trusted current IDs: **0 → 65** at this observation time.
- Degraded selected-owner guides lose positive certification: Morocco 15 → 14,
  Shahid 71 → 43, Starzplay 110 → 74, Elcinema 56 → 44,
  Aljazeera 3 → 2, Alkass 8 → 7, Dubaiplus 9 → 7.
- Sport24 remains rejected for content quality; Tabie remains stale.
- Comparison: **8.08 s on PC**, two workers, one shared readiness GET.
  This is not receiver speed or proof that all 14 sources are healthy.

```sh
python tools/qualify_rc80_public_sources.py --output qualification-public.json --compare-ref 948ca14813bb1b47893af8fbd47be59196e82cc6
python -m unittest codex.test_receiver_readonly_probe -v
python -m unittest discover -s tests -v
```

## Offline tests and consolidated private candidate

Windows/Python 3.13.2: **192 tests: 170 pass, 7 failures, 12 errors, 3 skips**.
Remaining failures involve POSIX modes, shell/paths and symlink privileges.
They are not passing tests. All eight new MENA/legacy-health tests pass.
Linux CI for the previous PR code passed 184 suite + four probe tests on
Python 3.13/3.14; that does not validate the newly consolidated commit.
**Final consolidated Linux CI passed and logs were read:** 192/192 suite tests
plus 4/4 probe fixture tests, with no skips, on each of Python 3.13 and 3.14.
Tested code: `470ad45d634002a7d9eb992bf0f501b7be290579`.
[Successful run](https://github.com/wacayoub/EPGManager/actions/runs/39161235131).
Later report-only updates do not alter that tested code.

One private SOURCE-ONLY archive was assembled from the actual 138-module
snapshot, two public RC80 modules and SHA-locked deltas. Schema remains 15.
The structural gate confirms 140 modules, zero unapproved modifications and
four changed original paths: version, health, diagnostics, preview UI.
Snapshot/source archive stay on the owner's PC outside Git. They must not be
copied over the operational plugin. A complete inspection IPK has now also
been assembled as described below.

Archive: `EPGManager-RC80-candidate-SOURCE-ONLY.private.zip`.
SHA256: `3bd1a9368008eea922a6deef90bf40e6ff2e948221c6d86364658cd8449f932b`.

The specifically pinned rescue IPK was absent from bounded known locations.
No rescue was created remotely. Permanent IPK install blocks remain intact.
A real opkg rollback test is waived by the owner, not an outstanding gate.

## Complete offline inspection IPK

Following the owner's instruction to prepare the complete package, read-only
SSH inventory found exactly 138 Python modules and 75 application resources.
All 138 source hashes were rechecked before the resource snapshot. No mapping,
preferences or settings file under /etc was read, copied or modified. Private
application resources remain local and are never uploaded to GitHub.

`tools/build_rc80_snapshot_ipk.py` reconstructs the same reviewed source
candidate from these observations, applies the existing exact preimage,
mapping parity and source inventory gates, and adds the 75 unchanged resources
with their observed permissions. It does not substitute a different rescue
hash into the historical rescue builder. That builder is unchanged.

Package: `EPGManager_RC80_COMPLETE_OFFLINE_BLOCKED.ipk`, **670584 bytes**.
Version: `2026.10.18-rc80-candidate`, schema 15; **140 modules + 75 resources**.
SHA256: `d693dee2289eb2f907e0dd18b9616b3ee914603085a032d12d80628a0bb5b5b7`.

- Two independently serialized packages are byte-identical.
- All 215 payload bytes/modes survive archive read-back exactly.
- The 140 modules are byte-identical to the already tested private source ZIP.
- Installed package identity/dependencies are preserved; runtime/control
  versions agree. Resources are declared as conffiles to retain local edits
  in a separately reviewed deployment. Actual opkg conffile behavior is untested.
- On the PC, all four embedded scripts pass shell syntax checks. Preinst
  exits **90**, including with EPGMGR_RC80_SUPERVISED=1; the other hooks exit 0
  without an action. No script or opkg command was run on the receiver.
- Five new regressions test exact asset preservation, deterministic archives,
  unconditional install block, malformed metadata, path escapes, missing
  modules, changed asset bytes, duplicate assets and mode drift.

**Package-builder Linux CI passed:** 197/197 suite tests plus 4/4 probe tests
on each of Python 3.13 and 3.14, no skips. Job logs were read for tested commit
`45bd0091f5859c4c899540185369cccfd714cd7c`.
[Successful package qualification](https://github.com/wacayoub/EPGManager/actions/runs/39163452728).
Public count/hash evidence: `docs/evidence/rc80-package-20261018.json`.

This is a complete **inspection** IPK with an unconditional installation
block. It is not an approved deployment package. Preparing this artifact did
not authorize or perform an installation, restart, import or cache change.

## Supervised installation candidate

The owner authorized improving/finalizing preparation, not a receiver install
or restart. A separate supervised test artifact is now prepared while the
permanently blocked inspection artifact remains unchanged. Both contain the
same application payload (140 modules and 75 resources); this is one source
candidate, not a second RC80 release.

`EPGManager_RC80_SUPERVISED_TEST.ipk`: **708464 bytes**.
SHA256: `00bb8cfc6cfc22df2fdf348c1524f6b7123e9e37fe86c79d47fdffbb4434499f`.
Version/schema remain `2026.10.18-rc80-candidate` / 15.

The new preinst refuses an ordinary installation without package-specific
consent. It checks original hashes/permissions, requires passive or absent old
removal hooks, checks backup space, then writes and reads back one private
recovery archive containing the working plugin and EPGManager configuration.
Only EPGManager settings lines are copied; settings for other plugins are
excluded. The postinst verifies the expected 140-module payload and performs
no settings/cache reset, scheduler operation, EPG import or Enigma2 restart.

A read-only preflight must run **before** any opkg invocation, because old
removal hooks participate in upgrades. Its exact code was sent through SSH
stdin and **passed on the actual receiver**, with no backup/write/install.
Installed prerm is passive; postrm is absent. The installed legacy postinst
contains cache overwrites and EPG-file deletion, observed in code only and
never executed in this task. The supervised candidate does not carry those
legacy postinst operations; its new postinst only verifies file integrity.

Eight new synthetic tests pass locally: consent refusal before any write,
rejection of active old hooks, source/resource drift, verified backup while
preserving original mappings/EPG, exclusion of other-plugin settings, low-space
refusal, hook syntax/compilation and postinstall verification of 140 modules.
All embedded hooks pass local shell syntax checks; the actual embedded preinst
was executed on the PC without consent and refused before receiver-path I/O.
At that preparation stage backup creation/restoration had not been tested on
the receiver. The authorized installation below subsequently tested backup
creation/read-back, but no restoration was exercised.

Private baseline manifests, scripts and both IPKs stay outside Git. Public
summary evidence: `docs/evidence/rc80-supervised-20261018.json`.
Final Linux CI for the new guard **passed: 205/205 suite tests plus 4/4 probe
tests on each of Python 3.13 and 3.14, with no skips**. Job logs were read.
Tested code commit: `b992b4c7c10923affa087bfae3a91978f2041a52`.
[Successful supervised-candidate CI](https://github.com/wacayoub/EPGManager/actions/runs/39167039826).

The owner subsequently explicitly confirmed the installation with verified
backup, without import/restart. Installation alone does not reload the GUI;
Enigma2 restart remains a separately authorized operation.

## Authorized installation actually executed

The installed artifact has the same 215-file application payload as the tested
inspection IPK. Its control metadata corrects the inherited `python3` dependency
to OpenATV's installed `python3-core` 3.14.7 package. A dedicated regression
checks that only that dependency token changes. The nine targeted guard tests
passed before installation. Current artifact:

- Version `2026.10.18-rc80-candidate`, schema 15, **140 modules + 75 resources**.
- IPK SHA256 `a38b0cbd635611472d58112e8e13cb35b6ce137c97b7a9565d7406cbdbb60b69`.
- Bytes: **708468**. The earlier 00bb8cfc artifact is superseded for deployment;
  the permanent inspection package remains unchanged.

Pre-install attempts stopped without any receiver write because this opkg has
different option/status-query behavior. The installer then verified each of
the 13 required installed dependency packages individually and used opkg's
`--nodeps` to isolate this upgrade from other package/dependency changes. No
dependency was installed to work around a missing runtime.

The actual authorized `opkg install` returned **0**. Its preinst created one
private RC79 backup, then read it back and verified all archived bytes/modes
before replacing the package payload:

`/etc/epgmanager_epg/backups/rc79-before-rc80-1792358774825996001-5701.tar.gz`

Backup SHA256: `13640001a06ce505823a5f3758fedf1f44a7a4703410169c5942aaa83f7a128d`
(1193540 bytes). No real restore/rollback was executed.

Post-install results:

- opkg reports `install ok installed`, version `2026.10.18-rc80-candidate`.
- Every candidate file matches the expected content/mode; 140 modules parse
  without syntax errors on the receiver.
- Configuration hashes, EPG file metadata and the Enigma2 process are unchanged.
- No import or GUI restart was requested. The running GUI has not been reloaded;
  RC80 GUI/native import behavior remains unvalidated.

**Recorded opkg warning:** the upgrade attempted to remove an obsolete `/.`
entry inherited from the previous package; unlinking the root directory failed
with "Is a directory". No root directory was removed and no manual registry
repair was performed. Subsequent read-only inspection confirms the current
registry has **215 entries, zero missing files, zero foreign paths and no root
entry**. This opkg stores `path<TAB>mode`, accounted for by the final probe.
The warning is retained in the installation evidence, not hidden by exit code 0.

Linux CI for the dependency correction passed **206/206 suite tests + 4/4 probe
tests on each of Python 3.13 and 3.14**, no skips; logs inspected. Code commit
`273efb55e0de3c66f7cebb7f100a8e4b97a2fd9f`.
[Successful run](https://github.com/wacayoub/EPGManager/actions/runs/39167932586).
Evidence: `docs/evidence/rc80-installation-20261018.json`.

## Remaining gates

1. Separate explicit authorization for the actual native EPG import test.
2. Actual GUI Auto/Manual Mapping, Best Match, persistence, native EPGImport,
   unavailable-source retention, GMT+0/local display, scheduler and UI latency.
   Reboot/restart remains separately authorized.

RC80 candidate is installed on disk; a verified RC79.1 recovery archive is
preserved. IPToSatPro and IPAudio Pro were not touched. No final release has
been published.

## Authorized GUI restart actually executed

The owner separately authorized the Enigma2 restart. One localhost OpenWebif
`powerstate?newstate=3` request was sent, following the
[official API documentation](https://github.com/E2OpenPlugins/e2openplugin-OpenWebif/wiki/OpenWebif-API-documentation).
The HTTP connection closed during the restart; the command was not retried.
Measured recovery: **51.85 seconds**, Enigma2 PID **5904 → 7195**, with three
consecutive successful OpenWebif power-state samples. The receiver boot ID
remained unchanged: this was a GUI restart, not a receiver reboot.

The comparison included 95 tracked plugin/configuration files. Two hashes
changed: `epgmanager_sat_registry.json` and `epgmanager_mapping_view_cache.json`.
These are registry/view cache files used by startup warming, not saved mappings.
A separate read-only comparison against the verified RC79 recovery archive
confirmed **13 of 15 archived configuration entries identical**, including
`epgmanager_mappings.json`, plugin settings, native settings, source preferences,
learning and SRP mappings. The only differences were the same two cache files.
No recent crash file was found in the inspected top-level log directories;
this limited check does not prove absence of every possible runtime error.

No manual import, mapping edit, cache reset, or further installation was issued.
The installed candidate and successful GUI process recovery do **not** certify
opening EPGManager screens or native EPGImport behavior. These acceptance gates
remain pending. Evidence: `docs/evidence/rc80-gui-restart-20261018.json`.

## Owner GUI confirmation and authorized import attempt — 20 October

The owner confirmed the TV picture returned and EPGManager opens. This is actual
owner-observed GUI opening evidence; Auto/Manual Mapping and Best Match screen
behavior remain separate acceptance checks.

The owner explicitly authorized a native EPG import test with no deletion of
saved mappings or existing programmes. **No import was executed in this attempt.**
Direct SSH reached authentication, but the tool's automatic approval policy
rejected interactive input (`sandbox_approval` disabled). No credential was
requested or disclosed in chat. Alias `epg-vu` still fails name resolution.
A read-only direct LAN OpenWebif request was refused on TCP port 80
(Windows error 10061); the earlier successful localhost API does not establish
current LAN accessibility. SSH key access has been requested as the prerequisite.

Review of the captured native import source identifies the installed OE-Alliance
engine bridge as the appropriate test path. The manual screen passes the saved
clear-before-import preference, so the authorized conservation test must explicitly
enforce `clear_before_import=False` before starting; it must not blindly press
Manual Import with an unknown setting. Standalone SSH Python is insufficient to
prove a write into Enigma2's embedded cache. No installed code/configuration,
mapping, cache, service, or scheduler was changed by this attempt.

Final release remains **NO GO** pending actual native import, programme retention,
time correctness and remaining mapping/scheduler acceptance. Evidence:
`docs/evidence/rc80-native-import-attempt-20261020.json`.

## Actual native-import GUI attempt through owner SSH tunnel — 20 October

The owner established a Windows SSH local forward to the receiver's localhost
OpenWebif. HTTP power-state and OSD capture succeeded. Remote-control API
navigation opened EPGManager, Main Settings and Native Import options on the real
GUI. The displayed installed version is `2026.10.18-rc80-candidate`.

**First concrete problem:** the saved **Delete current EPG before import** option
was **Yes**. In accordance with the owner's explicit conservation instruction,
it was changed to **No** before selecting Manual Import and accepting the prompt.
The screen's normal save operation persisted the setting. No mapping editing,
source-selection change, purge, installation or restart was requested.

**Actual test result: NOTHING TO IMPORT.** Preparation found **zero local XMLTV
files** under the configured `/etc/epgmanager_epg`. This manual screen uses the
local-only bridge, whereas GitHub-direct feeds are imported by the dashboard's
Import All/direct runner. The native engine did not start; this is not an import
success. The screen's suggestion to use Import All was not followed blindly.

**Second concrete code defect:** `DirectEPGImportRunner` constructs
`FirstWriteFlushProxy(..., enabled=not self.clear_before_import)` in both the
OE-Alliance engine and fallback paths, and runs migration sanitation before the
engine. Consequently `clear_before_import=False` still permits a per-service
purge, losing existing programmes when a replacement feed is partial or the
subsequent event write fails. This was found by inspection of the SHA-verified
receiver source and reproduced offline, not by destructive testing on the Vu+.

The source-locked corrective candidate disables both implicit first-write
flushes, makes the proxy default safe and skips migration sanitation during
non-destructive imports. Explicit `clear_before_import=True` remains a separate
destructive request and was not used. Five targeted tests pass: partial feeds,
failed writes in both paths, safe proxy default, reproduction of old loss and
source-pattern drift rejection. The full private 140-module candidate assembles;
75 resource bytes/modes are preserved. Its inspection-only IPK is permanently
installation-blocked (670606 bytes, SHA256
`81cff2c29ef3c7716bb1a6d88ad6f7a5996103c17570f2261fecb9a954ece1ee`).
It is **not installed or hardware-qualified**.

Cache conservation sampling queried 32 receiver services, of which six had EPG:
**543 event fingerprints before, 543 after, all 543 retained**, zero query errors.
This does not certify the entire cache or mapping-file hash identity. No saved
mapping edit was requested; native import preparation only consumed maps.

The initial Windows full-suite attempt hit sandbox temporary-directory errors.
Repeating with a workspace temporary directory ran 211 tests but still produced
7 failures, 12 errors and 3 skips involving POSIX modes, symlink permissions,
Linux paths and shell commands. This is explicitly **not a passing full suite**.
Probe tests then ran four, zero failures, one Windows skip. Linux CI for the new
code is required; the earlier 206+4 successful run does not certify these changes.

Evidence: `docs/evidence/rc80-native-import-20261020.json`. **Final release NO GO**
pending a separately authorized corrective deployment and actual non-destructive
direct native import, cache retention and the other acceptance gates.

Subsequent Linux CI for code commit `907a56f318bb2b23826aa19b3899293ed8b30159`
passed **211/211 suite tests + 4/4 probe tests on each Python 3.13 and 3.14**,
zero skips. Both job logs were inspected. This resolves the full-suite execution
gate for the new code without concealing the Windows limitations above.
[Successful run](https://github.com/wacayoub/EPGManager/actions/runs/39346462328).
It does not resolve corrective deployment or native-import hardware acceptance.

## Corrective deployment and actual native import success — 20 October

The owner explicitly authorized installing the retention correction, restarting
Enigma2 and repeating native import while retaining mappings/programmes. The
owner executed the reviewed, SHA-verified installer in the existing SSH terminal.
It patched only `core/direct_native_import.py`; no opkg command was run. The
installed version remains `2026.10.18-rc80-candidate`, schema 15, 140 modules.

The installer created and read back a complete private recovery archive before
atomic replacement, verified every archived byte/mode and confirmed all **15
tracked configuration files unchanged**. Archive:

`/etc/epgmanager_epg/backups/rc80-before-native-retention-1792456904621317787.tar.gz`

1203661 bytes, SHA256
`afb1dbb622b034a5e248aa327aabae195b03544af2139509042548b778862a14`.
Its hash and corrected module hash were independently checked through the owner
SSH tunnel. The module matches
`c8dc4d68712006f8fe86e64e698852d6dee8601667b9d880cff4dd3873d929f7`.
No restore was attempted.

One authorized OpenWebif GUI restart request was sent. The connection closed,
the API became unavailable, then three consecutive successful responses confirmed
recovery after **47.18 seconds**. Receiver uptime progressed **03:12 → 03:13**.
Boot ID could not be read through the HTTP file controller (procfs reports a
zero-length file), so it is not claimed as verified for this restart. The six
protected mapping/preferences/native-settings hashes remained identical across
restart, and the corrective module hash still matched.

After reopening EPGManager, **Import All was invoked once**. This exercises the
direct native runner instead of the local-only Manual Import screen. Actual
persisted activity advanced to a new import timestamp and reported:

| Receiver measurement | Result |
| --- | --- |
| Native engine | `Plugins.Extensions.EPGImport.EPGImport` |
| Cycle | DONE, successful |
| Events processed / imported | 15793 / 15793 |
| Failed events | 0 |
| Services written | 738 |
| Native sources / mapped IDs / mapped refs | 12 / 283 / 751 |
| Native engine time | 19.322 seconds |
| Full cycle time | 26.19 seconds |
| Reboot requested by import | false |

Fourteen sources remain selected; twelve were used by this native import. This
does not establish that every selected feed supplied events. No claim is made
about which two were absent from the final plan without per-source evidence.

**Conservation after actual import:** the six protected files retain identical
SHA256 hashes, including saved mappings, source preferences, SRP maps/master,
learning and native settings. The saved clear-before-import setting is false.
A fresh pre-import baseline sampled 32 receiver services: **542 event fingerprints
before, 542 after, all 542 retained**, zero query errors. This fresh baseline is
distinct from the earlier 543-event local-only attempt. These samples do not
certify every event in the receiver cache; no explicit purge was requested.

Read-only parity subsequently compared the installed plugin to the consolidated
corrective inspection IPK: **215/215 SHA256 matches**, comprising **140 Python
modules and 75 resources**, zero mismatches. This HTTP parity check does not
recheck Unix file modes. The installer did preserve and verify the changed
module's mode. The consolidated inspection IPK remains permanently blocked for
installation and is not a final release.

Installer code `ab08f68d4c972aa3fc10d6d9cd075530488a1a2a` passed **217 suite + 4
probe tests on each Linux Python 3.13 and 3.14**, no skips, both logs inspected.
[Successful run](https://github.com/wacayoub/EPGManager/actions/runs/39348916349).

**Native-import gate: PASS on actual hardware. Final release: still NO GO**
pending remaining mapping save/undo, Best Match GUI quality-state scenarios,
donor-versus-GUI time display, scheduled trigger and a live unavailable-source
scenario. Existing mapping persistence across the authorized restart is verified;
new mapping edits were not attempted. No IPToSatPro/IPAudio Pro operation or final
release publication was performed.

Evidence: `docs/evidence/rc80-native-import-success-20261020.json`.

## Remaining acceptance and new stale-profile defect — 20 October

Live access was recovered through the owner's existing SSH tunnel on PC port
18880 to the receiver's loopback port 80. The owner's diagnostic established
that port 18081 belongs to a Python process; it was not treated as OpenWebif.
No SSH key was added and no unrelated process was changed.

Actual receiver GUI tests opened Smart Mapping, displayed three cached
suggestions for a selected service, opened/cancelled the manual source selector,
and completed an Auto Mapping dry run on 7.0W. The result was 313 scanned,
1 SAFE proposal, 16 review, zero ambiguous and 296 without a match. The APPLY
question was answered **No**. All six protected file hashes remained unchanged.
Three OpenWebif responses during the search took 0.091, 0.106 and 0.104 seconds;
these measure API responsiveness, not GUI frame latency. New mapping save/undo
was deliberately not exercised against the owner's live store.

A targeted read-only time check parsed generated channel maps privately and
queried 158 services, 139 populated, with zero query errors. Fourteen public
XMLTV payloads were SHA-verified and all used GMT+0. Across 4,955 sampled event
rows there were **3,267 exact title/start/duration matches** with public donor
programmes. Matches covered 101 services and 11 sources. Repeated programme
titles/durations also produce matches at adjacent hourly slots, so this is
bounded positive evidence, not an all-channel offset or mapping certification.
An earlier untargeted 32-service sample matched none of the public donors and
was not counted as a successful timing test. No programme text or private
service references are published.

**New real defect:** the Zero EPG GUI shows NO EPG alongside positive future
programme metadata. The beIN complete quality profile was checked at epoch
1791973623, while the live monitor observation was 1792458184: the older profile
wrongly wins even when a newer observation contradicts it. A replay of the
SHA-verified installed functions and privately captured metadata reproduces
64 contradictory NO_EPG verdicts on 64 positive-monitor rows. The correction
returns review for these 64 cases, zero NO_EPG and zero automatic green. This
does not certify that all 64 public guides are usable.

The reviewed correction changes only `core/source_quality.py` and
`core/github_direct_sync.py`, adding `core/quality_profile_freshness.py`. It
retains caches and user data. A complete profile must be timestamped, within
24 hours, and at least as recent as the monitor before it is authoritative.
Positive monitor metadata alone now remains WARNING. The correction is
**prepared offline, not installed**; installed preimage hashes were freshly
verified through OpenWebif. `tools/install_rc80_stale_profile.py` requires
separate explicit owner authorization, verifies all downloads and preimages,
creates and reads back a recovery archive, then changes those three files.
It does not execute opkg, restart Enigma2 or run an import. Four installer tests
exercise real temporary filesystem backup/conservation, drift rejection and
backup failure; six freshness tests cover the measured regression and invalid
timestamps.

Current code commit `f3df5d3a1b4ea8fada1efec5fd231e68d0af0870` passed **227 suite
tests + 4 probe tests on each Linux Python 3.13 and 3.14**, zero failures/skips,
both logs inspected. [CI run](https://github.com/wacayoub/EPGManager/actions/runs/39352229509).
Additional isolated workstation tests exercised the SHA-verified receiver
MappingStore, scheduler and timezone source: 18 checks passed, including
manual-owner protection, save/reload, undo, GMT+0 roundtrips, timer registration,
boot grace, success de-duplication and bounded failure retries. Its timers are
stubs, not real Enigma2 timer evidence. A fresh public-data Best Match replay
checked 518 MENA IDs, including 108 degraded and 24 no-future IDs, with zero
false green in those degraded/no-future groups. Fourteen XMLTV hashes verified;
six sources passed the source-level readiness gate.

One consolidated **source-only, non-installable** pending-validation archive is
prepared locally: 141 Python modules, 75 unchanged resources, schema 15, 216
files; every source parses and the ZIP payload roundtrip matches. SHA256:
`40ab3b63c455a40a42517b84be9515322de97506f33d4695fe6a7dd846dc5cb9`
(791751 bytes). It supersedes the previous candidate for further review; it
has not replaced the installed 140-module plugin. The older blocked IPK
remains historical evidence of the receiver's retention correction.

Scheduler results and restoration are recorded in
`docs/evidence/rc80-remaining-acceptance-20261020.json`. Final release remains
NO GO until the new stale-profile correction is separately approved, installed
and checked in the real Mapping/Best Match GUI. Live mapping writes/undo and
a forced unavailable-source scenario remain unperformed; their isolated tests
are reported separately. No final release/tag/merge was published.

### Actual scheduler trigger and restored settings

The owner separately authorized a temporary scheduled import and restoration.
The original enablement/time were recorded privately from the fresh GUI,
superseding an earlier disabled-state assumption. Clear-before-import remained
No. Only the import time was temporarily moved to 01:15, with the five-minute startup guard
left intact. No manual Import All command was sent during this test.

The real Enigma2 scheduler logged start at **01:15:45 UTC**, 45 seconds after
the configured slot. Persisted activity recorded successful DONE at epoch
1792458969, a **24.16-second cycle**, with **15,793 events processed/imported,
zero failed events, 738 services written, 12 native sources, 283 mapped IDs and
751 mapped references**. Engine: `Plugins.Extensions.EPGImport.EPGImport`,
17.496 seconds; no reboot requested. The scheduler logged successful completion
at 01:16:45 and its last-success timestamp was read back from saved settings.
This is actual timer/native-import evidence, distinct from the isolated tests.
The shorter time than the earlier import is not attributed to a code speedup: the
runner code is unchanged and cache conditions differ.

After completion the original scheduler enablement/time were restored, saved
and read back; private owner schedule values are not published.
All original explicit EPGManager configuration values were unchanged except the
expected new last-success timestamp. All six protected files, including native
settings and mapping/preferences/SRP/learning files, retained their initial
SHA256 hashes. No code was installed or GUI restarted during this round.

The cache baseline contained 542 event fingerprints across 32 queried services.
After scheduled import, 541 remained; the missing event had naturally expired.
**All 541 baseline events still valid at the after-sample time were retained**,
zero query errors. This is bounded conservation evidence, not a whole-cache
comparison or proof of behavior under a deliberately broken live source.

**Scheduler hardware gate: PASS. Final release gate: NO GO pending the prepared
stale-profile correction and real GUI retest.**

## Final stale-profile deployment, receiver retest and consolidated IPK — 20 October

The owner separately authorized this exact three-file correction, GUI restart
and Mapping/Best Match retest, then executed the pinned installer in the
existing SSH session. It changed source_quality.py and github_direct_sync.py,
added quality_profile_freshness.py, and verified all **15 protected configuration
files unchanged**. No opkg, restart or import was performed by that installer.
The backup was read back and independently hashed through the existing tunnel:
`/etc/epgmanager_epg/backups/rc80-before-stale-profile-1792459290041161680.tar.gz`,
SHA256 `4daf86c9f69d2b0177b902941ceebb73233a0492d720b9039e2b2b72387cf3b1`.
All three correction hashes and the earlier native-retention module hash matched.

One authorized GUI restart recovered after **47.61 seconds**, with observed API
down/up, three stable responses and increasing receiver uptime. Boot ID was not
checked. Independent full payload parity now matches **216/216 files: 141 Python
modules and 75 resources**, with zero mismatches. This HTTP comparison checks
bytes, not modes; the installer's verified backup records original modes.
The installed runtime label remains `2026.10.18-rc80-candidate`, schema 15.

Actual post-restart GUI evidence:

- Dashboard NO_EPG count changed from 659 to **31**. Positive-monitor cases no
  longer inherit obsolete empty-profile verdicts. The 633 remaining uncertain
  or review cases did not automatically turn green; dashboard green count is 0.
- The Zero EPG beIN group changed from 74 rows to **4**, each visibly reporting
  zero programmes. This is a correction of the displayed verdicts, not a claim
  that missing programmes were generated or every remaining guide is reliable.
- Best Match opens with three cached candidates and a current programme preview.
  Candidate identity percentages are not treated as programme-quality proof.
- Auto Mapping was rerun because the health classifier changed: 313 scanned,
  1 safe proposal, 16 review, 0 ambiguous, 296 no match. APPLY was declined.
  No additional mappings were created, deleted or reset.
- All six independently tracked mapping/preferences/native-setting files retain
  their pre-install SHA256 hashes. The 32-service cache sample contains **541
  events before and after, all 541 fingerprints retained**, zero query errors.
- The native-import activity timestamp is unchanged from the successful earlier
  scheduled import. No new native import was requested in this correction round.

### One prepared final candidate

`tools/prepare_rc80_qualified_final.py` builds the single private final candidate
`enigma2-plugin-extensions-epgmanager_2026.10.20-rc80_all.ipk` from the exact
qualified payload, pinned recovery archive and previous inspection metadata.
It contains **141 modules + 75 unchanged resources**, schema 15. **215/216
application files are byte-identical to the qualified receiver**; only version.py
branding changes to 2026.10.20-rc80. AST comparison verifies all other executable
behavior and schema remain unchanged. Python dependencies are preserved, with
the tested OpenATV python3-core substitution. No user configuration or EPG cache
is packaged.

The new preinst adapts the reviewed recovery guard to this exact 141-module
baseline, requires package-specific explicit consent, rejects source/mode or
removal-hook drift, and verifies a full recovery archive before unpacking.
Postinst verifies 216 payload files. Removal hooks are passive; no hook imports
EPG or restarts Enigma2. Guard adaptation, missing-consent refusal, a real
141-module temporary backup fixture, branding and dependency conservation have
five additional passing offline tests. The IPK was deterministically built and
its payload, hooks and Unix modes roundtrip-verified. It was **not installed with
opkg and not uploaded as a GitHub release**; any such operation needs its own
explicit owner authorization.

Final IPK: **701058 bytes**, SHA256
`9a8f90832b1101a80584737c97301d959409bedaedcdd6128f139f1fef573e8e`.
The previous source ZIP and inspection IPKs remain historical evidence, not
additional final candidates. The functional candidate is already active on the
receiver; the final IPK adds the final version label and package bookkeeping.

Code commit `30326c438f91dee3f81e2587d862d90cac03a9af` passed **232 suite + 4 probe
tests on each Linux Python 3.13 and 3.14**, no failures/skips, both job logs
inspected. [Final CI run](https://github.com/wacayoub/EPGManager/actions/runs/39353668123).

**Verdict: GO for controlled final installation on this exact qualified receiver.**
This is not certification for other images or every channel/language. New live
mapping edits/undo and a deliberately broken live source were not exercised
against owner data; their isolated tests are separate. Existing mappings survive
restart and imports. Source gaps and unresolved mappings are preserved for
explicit review, never replaced with fabricated programmes. IPToSatPro and
IPAudio Pro were untouched. No final package installation, merge, tag or public
release was performed.

Current evidence: `docs/evidence/rc80-final-qualified-20261020.json`. Earlier NO_GO
statements above document the historical gates before this authorized repair;
they do not describe the present controlled-installation verdict.

## Final IPK installed and accepted — 20 October 2026

The owner explicitly authorized this final IPK installation, Enigma2 restart
and non-destructive native import. Direct SSH key authentication succeeded
on the owner's LAN receiver. No public SSH endpoint was used by Codex.
The exact existing package was installed without changing its consent guard.

- Package: `enigma2-plugin-extensions-epgmanager_2026.10.20-rc80_all.ipk`,
  701058 bytes, SHA256
  `9a8f90832b1101a80584737c97301d959409bedaedcdd6128f139f1fef573e8e`.
- Read-only exact baseline/hash/mode preflight passed before installation.
  The preinst created and read back a verified recovery archive; SHA256
  `2542cb2bb4bc6c789247b96a298916ffce5a53e5b4f74bf4e580696c8d73fc88`.
  opkg exit was 0, status `install ok installed`, version `2026.10.20-rc80`.
  All 15 protected configuration hashes were identical before/after opkg.
- The postinst verified all 216 application files/modes. A separate receiver
  syntax check parsed 141 modules with zero errors; schema remains 15.
- One Enigma2 restart recovered in **45.22 seconds**. The Enigma2 PID changed,
  receiver boot ID stayed identical, and three consecutive API checks passed.
- Strict all-file configuration hashes did **not** remain identical across
  restart: 12/15 stayed identical. Global Enigma2 `settings` was rewritten,
  but the EPGManager settings lines match the recovery archive exactly.
  The satellite registry changed only `signature`; the mapping view cache
  changed only `created` and `signature`. Their remaining data are identical.
  All six protected mapping/preferences/native files remained byte-identical
  across installation, restart, import and final GUI navigation. No assertion
  is made that every other plugin's global settings line is unchanged.
- Actual GUI: final version displayed, dashboard and Smart Mapping opened,
  three Best Match candidates and a current programme preview visible,
  source selector opened/cancelled. No mapping save or automatic APPLY.
- After verifying the clear-before-import option is false (default false),
  a real native import completed through `Plugins.Extensions.EPGImport.EPGImport`:
  **16,044 processed/imported events, zero failures, 739 services, 13 native
  sources, 285 channel IDs and 753 service references**. Engine time **20.80 s**,
  entire cycle **35.63 s**, stage DONE, no reboot requested. Fourteen selected
  source feeds do not imply fourteen sources wrote usable events.
- Conservation sample: 32 services, 496 events before and after; **496/496
  non-expired event fingerprints retained**, zero query errors. This bounded
  sample does not certify every programme on every service.

The actual scheduler trigger, 3,267 exact timing correspondences and Auto
Mapping dry run were observed earlier in this same qualification; they were
not needlessly repeated after the final IPK's branding-only application change.
The previously inspected Linux Python 3.13/3.14 CI remains 232 suite + 4 probe
tests per interpreter, zero failures/skips; 18 isolated mapping/time/scheduler
checks are separate from actual GUI and native import evidence.

**Verdict: GO for the installed RC80 on this receiver.** Source gaps (31 IDs
without future EPG), uncertain guides and unresolved user mappings remain
explicit. New live mapping save/undo, a forced live source outage and other
receiver images remain outside the measured acceptance. No public stable
release, Git tag or merge was performed. IPToSatPro and IPAudio Pro were not
modified by this installation. Evidence contains only counts, hashes and
validation outcomes; no credentials, mappings or programme text.

## Trigger and synchronization follow-up — prepared, not installed

The Sources table's LAST REFRESH column was observed on the receiver. It shows
upstream publication time, which can predate the receiver's latest check.
UTC and the requested GMT+0 are the same offset. Source-generated times are
not synchronization completion receipts.

Two code defects were reproduced using the exact SHA-verified installed
source in isolated fixtures: saving settings creates a new scheduler with a
fresh five-minute startup delay; metadata-only refresh replaces source rows
and discards prior successful local XML synchronization timestamps. No claim
is made that a missed timed trigger was reproduced on live hardware.
The legacy source-generator scheduler is retired intentionally and remains off.

The correction changes five existing modules (plugin.py, core/scheduler.py,
core/github_direct_sync.py, ui/settings.py and ui/native_sources.py), adding
core/sync_timing.py. It uses an explicit UTC clock for the active native
scheduler and labels the configured time GMT+0 without changing that value.
Only genuine Enigma2 startup retains its five-minute quiet period. Settings
apply does not invent another boot period. Existing busy/failed-job retry and
single-run completion rules remain intact. A scheduled import additionally
refreshes GitHub control-plane metadata asynchronously with force=False;
this does not start local scrapers, prepare mappings or download full XML.

Source rows display publication and receiver-check timestamps separately in
GMT+0, parsing real ISO offsets before formatting. Metadata checks preserve
successful local receipts and never claim that a GET is an XML sync or import.
A failed synchronization cannot advance the last-success timestamp.

The one consolidated corrected IPK retains version 2026.10.20-rc80/schema 15:
142 modules + 75 unchanged resources, 702188 bytes, SHA256
`89d6202cbec8d9a8ad10bc2d7b2f25c4b0c8afbab9d18d515a490396d76f999a`.
The preinst still requires package-specific consent and an exact installed
141-module baseline, followed by verified backup; postinst checks 142 modules.
The native import engine, user settings, mapping writers, IDs, priorities,
languages and programme offsets are unchanged. Same-version installation
requires an explicitly approved opkg --force-reinstall operation; no such
operation or restart was performed for this follow-up.

Validation: 15 new public unit tests pass with zero skips; 18 checks execute
installed/corrected source in isolation. All 142 candidate modules parse.
The exact new preflight ran on the actual Vu+ over SSH in read-only check mode
and passed. The actual embedded preinst also refused missing consent on the
PC before receiver-path I/O. Deterministic serialization and all payload/hook
bytes and modes roundtrip-verified. Corrected live trigger and GUI acceptance remain pending. Linux qualification
of code commit `16b4505f` passed **247 suite + 4 probe tests on each Python 3.13
and 3.14**, zero failures/skips, with both logs inspected.
[Successful timing correction CI](https://github.com/wacayoub/EPGManager/actions/runs/39394353322).
No receiver code or owner data was changed by this preparation.
