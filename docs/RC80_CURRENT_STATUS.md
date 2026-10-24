# RC80 status and cleanup — 24 October 2026

## What actually runs

The fresh read-only SSH probe still measures 2026.10.20-rc80, schema 15,
142 Python modules and Python 3.14.7. The last completed native import inserted
394,472 events with zero insertion failures. The daily slot remains 13:15 GMT+0,
without purge. See [original measured trial](RC80_LIVE_UTC_VALIDATION_20261024.md).

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

## Performance candidate

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
on the full 68-source import. No receiver file was changed for this benchmark.

## Single candidate and repository cleanup

The guarded offline IPK changes only core/direct_native_import.py and
ui/settings.py; the latter corrects Casablanca-local wording to GMT+0 (UTC).
The other 215 application files and their modes remain unchanged: 217 payload
files, 142 modules, same schema and no packaged user settings. Input identity,
installation consent, exact baseline and verified backup guards remain active.
The 712,314-byte candidate SHA256 is
5888e63c3f4f96e531939663014a3d72161af8b86aa81a29a567b36ca8172e23.
It has not yet been installed or tested as a real import.

README now has an explicit current-status banner separating legacy instructions
from qualified RC80 evidence. Private directories and IPKs are ignored to avoid
accidental commits. The existing retired publisher remains disabled. Its proposed
simplification and a full README rewrite were rejected by automatic policy;
only the safe additive README correction was applied. No historical source ZIP,
active dependency branch, release, receiver cache or useful backup was deleted.
Work is consolidated in PR #45 rather than creating another dependency PR.

## Remaining gate

The candidate needs Linux Python 3.13/3.14 CI and an explicitly approved guarded
installation/restart followed by actual GUI/native-import qualification before
receiver performance improvements are claimed. Missing external guides,
editorial quality and ambiguous comparisons against non-owner donor feeds remain
separate data-quality issues. No general public final release is promoted.
