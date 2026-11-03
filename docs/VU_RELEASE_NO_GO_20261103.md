# Vu+ release: NO-GO — 3 November 2026

Checked at 08:03 UTC. No receiver connection, refresh, install, mapping change or
Enigma2 restart was performed or authorized. `mena-data` was not changed.

## Scope of the GitHub results

- [Run 41451046826](https://github.com/wacayoub/EPGManager/actions/runs/41451046826)
  failed at step 25, OSN bilingual enrichment. Its final canonicalization,
  release checks and atomic publication steps were skipped. The reported
  missing `OSNtv Now.sa` remains an upstream/data blocker; it was not removed
  from the required contract or filled with invented events.
- [Run 41451680469](https://github.com/wacayoub/EPGManager/actions/runs/41451680469)
  is marked success but skipped 39 steps, including publication. This is not
  a successful receiver publication.
- [Run 41504663624](https://github.com/wacayoub/EPGManager/actions/runs/41504663624)
  validates the unified source diagnostics on Python 3.13/3.14. It does not
  replace the receiver release process.

The legacy MENA Cloud workflow is archived outside `.github/workflows/`. No
active workflow promotes the new source aggregate to `mena-data`. The old
package builder remains disabled. The surviving source postprocessing and
watchdog may rebuild source diagnostics; they cannot establish receiver release
eligibility or authorize a refresh.

## Compressed publication checked directly

The exact public `mena-data` commit inspected is
`f02d3be8c7bf5bb9d9022cedce3212fbf1116e67`. Its manifest is dated
17 October 2026, 17:39 UTC. The gzip files were extracted from that Git snapshot,
decompressed, parsed and measured locally without rewriting them.

| Artifact | IDs | Programmes | Gzip bytes | XML bytes | Future programmes |
|---|---:|---:|---:|---:|---:|
| mena | 491 | 23,905 | 847,825 | 9,625,459 | 0 |
| premium | 60 | 1,762 | 85,014 | 867,194 | 0 |
| mena-arabic | 551 | 25,667 | 932,258 | 10,492,515 | 0 |

For these exact bytes, SHA-256 and sizes match the three manifest entries.
The actual XML channel sets are disjoint across MENA/Premium and their union is
the combined set. The actual programme multisets also have an exact union.
These are snapshot-specific structural results, not freshness certification.
All 551 combined IDs now lack future EPG; measured median future horizon is 0h.

The stored ADM audit says FAIL with nine missing required IDs. Re-running the
current identity policy on the final compressed ADM bytes instead finds the
required IDs and returns REVIEW. That policy's historical schedule-span values
are not fresh UTC coverage. Its output must not be combined with the stored
audit as if they were one coherent validation cohort. The expired aggregate
and unvalidated receiver remain blocked regardless of either identity result.

## False PASS fixed

Replaying the former `receiver_release_gate.py` against this expired archive
returned PASS: it trusted `manifest.coverage.median_future_hours`, measured in
October. The guard now calculates horizons and future IDs from final XMLTV
programmes at one current UTC reference, checks invalid intervals and fails on
published receiver IDs without future EPG. The same archive now returns FAIL.

By default, the command also fails closed when receiver validation and refresh
authorization are not established. `--artifact-checks-only` explicitly scopes an
offline artifact check; even a passing result reports `receiver_release_status:
NO_GO`, hardware not validated and authorization false. It is not a release
override. No OSN/ADM required-ID or language threshold was weakened.

## Current source aggregate is not the receiver release

The source data snapshot inspected on `main` is
`5ca5e1d525ac4a7eca43bf5efff90db31f47dca2`. Its diagnostic aggregate still contains
548 IDs and 30,767 rows from 2 November, but the Tabie source changed on 3 November
after that aggregate was built. The current diagnostic therefore reports
`MENA_SOURCE_SNAPSHOT_OUTDATED` and `INVALID`, with 99 IDs lacking future EPG at
this check. The earlier 74-ID observation was time-specific.

The aggregate expands to **14,087,720 bytes**, exceeding the configured static
Vu+ XML budget of **12,582,912 bytes (12 MiB)**. This size check is not native
Enigma2/runtime validation. It is a further reason not to promote this source
artifact as a receiver payload.

Source readiness now has an explicit diagnostic scope, `receiver_release_status:
NO_GO`, release blockers and the uncompressed byte count. Source CI and source
rebuild workflows write the same scope into their GitHub summaries. A green
source job cannot serve as evidence that the receiver release passed.

Still unmet or untested: a fresh canonical publication from the current sources,
all required provider quality gates, idempotence of that new publication,
complete Arabic regression, actual Enigma2 EPGImport, Vu+ runtime/GUI safety,
and explicit receiver-refresh authorization.

Machine-readable observations and exact hashes:
[`vu-release-no-go-20261103.json`](../reports/vu-release-no-go-20261103.json).

Validation of the correction: 234 tool tests plus 6 migration tests passed on
Python 3.13. Five new regression cases cover stale XML versus a positive manifest,
invalid intervals, fresh offline artifacts without hardware certification,
default release refusal, and READY source diagnostics remaining NO-GO.
