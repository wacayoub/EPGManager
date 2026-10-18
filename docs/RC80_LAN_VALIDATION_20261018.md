# RC80 actual LAN validation — 18 October 2026

**Installation: NO GO. Receiver modified: false.** One consolidated candidate:
[draft PR #38](https://github.com/wacayoub/EPGManager/pull/38), branch
`codex/rc80-final-qualification-20261018`, based on `948ca148`.
This supersedes the earlier SSH_NOT_TESTED report.

## Actual receiver evidence

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

First five trials ranking 500 synthetic candidates: median **40.62 ms**, range
40.47–64.92 ms on Vu+/Python 3.14.7. This is CPU ranking, not GUI rendering or
a measured overall speedup. Final consolidated-code results are recorded
separately in the receiver evidence before completion.

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

## Offline tests and single private source candidate

Windows/Python 3.13.2: **192 tests: 170 pass, 7 failures, 12 errors, 3 skips**.
Remaining failures involve POSIX modes, shell/paths and symlink privileges.
They are not passing tests. All eight new MENA/legacy-health tests pass.
Linux CI for the previous PR code passed 184 suite + four probe tests on
Python 3.13/3.14; that does not validate the newly consolidated commit.
Final consolidated Linux results must be read before claiming success.

One private SOURCE-ONLY archive was assembled from the actual 138-module
snapshot, two public RC80 modules and SHA-locked deltas. Schema remains 15.
The structural gate confirms 140 modules, zero unapproved modifications and
four changed original paths: version, health, diagnostics, preview UI.
Snapshot/archive stay on the owner's PC outside Git. It is not an IPK and
must not be copied over the operational plugin.

The specifically pinned rescue IPK was absent from bounded known locations.
No rescue was created remotely. Permanent IPK install blocks remain intact.
A real opkg rollback test is waived by the owner, not an outstanding gate.

## Remaining gates

1. Final Linux CI on consolidated public code.
2. Prepare/inspect a complete package with unchanged application assets.
3. Separate explicit authorization for a precise receiver deployment/test
   before any remote write, import execution or GUI restart.
4. Actual GUI Auto/Manual Mapping, Best Match, persistence, native EPGImport,
   unavailable-source retention, GMT+0/local display, scheduler and UI latency.
   Reboot/restart remains separately authorized.

RC79.1 remains installed. IPToSatPro and IPAudio Pro were untouched. No final
release has been published.
