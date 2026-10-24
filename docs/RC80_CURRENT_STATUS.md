# RC80 status and cleanup — 24 October 2026

## What actually runs

The approved optimized package is now installed and verified: 2026.10.20-rc80,
schema 15, 142 Python modules (no count change), Python 3.14.7. Actual native
EPGImport inserted 395,527 events with zero insertion failures across 4,134
written services and 69 ready sources. The daily slot remains 13:15 GMT+0,
without purge. [Installed evidence](evidence/rc80-cleanup-installed-20261024.json)
and [earlier scheduler trial](RC80_LIVE_UTC_VALIDATION_20261024.md) distinguish
the new manual import from the previously exercised scheduled trigger.

## Six partial intervals: explained by the actual source

The six cases concern three revised time slots repeated across two La 2 services.
Their uncovered portions are 60, 300 and 300 seconds per service. The actual
Koala Spain 15-day XMLTV contains exactly those same gap intervals. Ninety-six
native title/start/duration fingerprints per service match that donor. These
six cases are source schedule revisions, with no additional importer-created
gap observed. They must not be filled with invented programmes or by extending
an obsolete event. This closes those six bounded investigations; it does not
certify every channel or simulate an unavailable-source import.

Donor checked: public koala15.xml.xz, SHA256
5cb15b2400394843b79b650ec1917e008768822a663f2d79e25fa2c6e219f973.
The earlier Morocco/2M hypothesis did not match these services and was rejected.

## Installed performance correction

The original full plan needed 423.98 seconds, with 138.084 seconds in ownership
and 261.136 seconds in winner expansion. The candidate preserves the same
lexicographic ranking: source priority first, name similarity only on equal
priority. It caches name scores by their actual text inputs within one job and
removes the much larger per-claim composite-score cache. Caches are not persisted
and no mapping or source policy changes.

Differential tests cover 60 deterministic generated cases, manual/automatic and
stale saved owners, aliases, different shared maps, source-specific labels,
zero/error scores and stable ties. Eight new tests pass on workstation Python.

A synthetic read-only benchmark actually executed on the Vu+ compares the
installed arbitration function with the candidate on fictional data:

| 54 sources / 40 services | Before | Candidate | Name-score calls |
| --- | ---: | ---: | ---: |
| Different source priorities | 0.767749 s | 0.096136 s | 2,160 to 0 |
| Equal-rank shared maps | 0.766813 s | 0.144059 s | 2,160 to 40 |

Decisions match exactly. These are synthetic CPU timings, not a measured gain
on the full import. No receiver file was changed for this benchmark.

The subsequent real import reduced preparation from 423.98 to 193.16 seconds
(54.44%). Ownership took 47.975 seconds and winner expansion 115.816 seconds.
The complete cycle took 600.37 seconds (10m00s), versus the earlier 1237.18
seconds (20m37s): an observed 51.47% reduction. These are two production runs,
not identical-input experiments: ready sources increased from 68 to 69 and
feed/cache/network conditions differed. The whole-cycle change is not solely
attributed to this patch.

## Single candidate and repository cleanup

The guarded offline IPK changes only core/direct_native_import.py and
ui/settings.py; the latter corrects Casablanca-local wording to GMT+0 (UTC).
The other 215 application files and their modes remain unchanged: 217 payload
files, 142 modules, same schema and no packaged user settings. Input identity,
installation consent, exact baseline and verified backup guards remain active.
The 712,314-byte candidate SHA256 is
5888e63c3f4f96e531939663014a3d72161af8b86aa81a29a567b36ca8172e23.
It was installed after explicit owner approval. The recovery archive was
read back and verified against all 217 previous application files. All 16
protected configuration hashes matched during installation. Exactly one
Enigma2 restart completed in 77.07 seconds, with no receiver reboot.
The installed 217 file bytes/modes and compilation of 142 modules passed.
Six mapping/learning/SRP/source-preference/native-settings files still matched
their pre-install hashes after the import. IPAudioPro and SatIPTVBridge Python
file hashes remained unchanged.

The bounded 32-service sample retained all 3,899 nonexpired events identically
across restart. Across the complete trial, five of the initial 3,900 expired;
3,782 remained identical, 110 revised intervals were fully covered, three
were partially covered and none disappeared entirely. The three partial
Eurosport1.int intervals exactly match Rytec Misc schedule gaps of 180, 240
and 360 seconds. Seventy-nine native event fingerprints match that public
feed, SHA256 74fb30a8bb92a464a6202a59adbbc0de4cd7f3377c8053f8cc192d265a0ab159.
No additional importer-created gap was observed in this sample. No obsolete
event was extended to fabricate continuity.

README now has an explicit current-status banner separating legacy instructions
from qualified RC80 evidence. Private directories and IPKs are ignored to avoid
accidental commits. The existing retired publisher remains disabled. Its proposed
simplification and a full README rewrite were rejected by automatic policy;
only the safe additive README correction was applied. No historical source ZIP,
active dependency branch, release, receiver cache or useful backup was deleted.
Work is consolidated in PR #45 rather than creating another dependency PR.

## Validation and remaining limits

Linux CI passed 310 tests plus four probe tests on each Python 3.13 and 3.14.
The real GUI opened after restart, displayed the corrected GMT+0 help and
13:15 slot, and returned to READY after the actual EPGImport completed.
The dashboard still reports unavailable external guides and unresolved matches;
these are not certified as fixed by a performance patch. No mappings were
rewritten, no unavailable-source simulation was performed, and the 32-service
retention sample is not an exhaustive channel audit. Missing external guides,
editorial quality and ambiguous comparisons against non-owner donors remain
separate data-quality issues.

Verdict: **GO to keep this exact candidate on the qualified receiver.**
This is not a claim that every upstream channel has a complete guide.
No general public final release is promoted. The public historical update
channel has not been repointed.
