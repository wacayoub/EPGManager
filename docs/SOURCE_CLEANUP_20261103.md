# Source cleanup — 3 November 2026

All 14 direct sources were inspected individually against their published
XMLTV, JSON checksum/count metadata and TXT channel inventories. All 112
generic fallback URLs returned HTTP 200 with a recognized gzip/XZ/XML prefix.
There were no duplicate generic URLs or newly confirmed dead links to remove.
The fallback check is bounded availability/signature evidence; it does not
certify the complete archives or their programme accuracy/future coverage.

## Corrections

- **2M:** Telerama's dated grid starts with a programme carried in from the
  previous evening, then early-morning rows, then the dated evening. The
  leading segment now belongs to the previous day. This removes the false
  same-day collision while preserving the existing provider-clock conversion.
  Schedule cards are scoped to the channel grid, excluding navigation links.
- **Chada:** structured Piisas list rows take precedence over full-page text.
  The introductory sentence containing a clock no longer creates an extra
  programme such as `avec` alongside the actual show.
- **Shahid:** thirteen rows duplicated the same start, programme and all metadata,
  but had different stops. Keep the shortest observed provider interval;
  discard only the redundant copy. Different titles, descriptions, episode
  metadata, languages and starts are never silently merged or selected.
  Category order is ignored for identity comparison, while the selected
  programme retains its original metadata/order. Nine duplicates were removed
  in the first pass and four category-order variants in the final pass.
  The shared packer applies the same rule to future collections.
- **Morocco scheduling:** the legacy `--scheduled` CLI no longer rejects
  07:07 or delayed runs using its obsolete 06:00/18:00 hour filter. The workflow
  remains the owner of the once-daily Morocco-local schedule.
- **Quality:** unresolved overlaps now produce explicit warnings/degraded
  current health. Candidates with overlapping programmes cannot be qualified
  in derived routing/readiness. Provider rows with ambiguous timing remain
  visible for review; no universal time shift or guessed replacement is applied.

No channel IDs, direct providers or available fallback sources were removed.
The Shahid cleanup preserves provider generation time and records both byte
hashes. A second cleanup is an exact byte no-op. The aggregate was rebuilt
from the resulting verified source snapshot, preserving its complete ID set.

## Snapshot after cleanup and verified Morocco recollection

| Source | IDs with future EPG / catalogue | Remaining overlapping rows |
|---|---:|---:|
| Morocco | 12 / 15 | 0 |
| AlKass | 8 / 8 | 0 |
| Tabie | 4 / 4 | 0 |
| Tunisia TV | 2 / 2 | 0 |
| Al Jazeera | 2 / 5 | 0 |
| Dubai+ | 9 / 9 | 1 |
| Shahid | 71 / 71 | 0 |
| Rotana | 8 / 9 | 0 |
| Sport24 | 9 / 9 | 0 |
| OSN | 79 / 100 | 0 |
| beIN | 65 / 68 | 0 |
| ElCinema | 95 / 97 | 0 |
| STARZPLAY | 117 / 119 | 0 |
| STC | 124 / 149 | 0 |

Values are dated observations, not permanent coverage promises. Missing
descriptions are reported without manufacturing text. Channels with no guide
remain in the catalogue. Morocco's three empty IDs are AFLAM, Arryadia HD1 and
local TNT, awaiting genuine listings. HD Smart/Botola and HD2/HD3 Grand Format
remain explicitly labelled and unchanged by these parser fixes.

Morocco recollection [41649556916](https://github.com/wacayoub/EPGManager/actions/runs/41649556916)
passed collection and exact-producer verification. Its publication commit is
`883610e517c423a55103014ff07bec1280a3eb85`, with 1,838 programmes; all seven
previous 2M/Chada overlaps disappeared. There are no remaining future Shahid
overlaps after the conservative duplicate cleanup.

One genuine provider timing conflict remains on Noor Dubai: its JSON ends a
programme 20 minutes after the next programme's announced start. Neither
programme is deleted or arbitrarily retimed. Dubai+ is reported DEGRADED and
that overlapping candidate cannot qualify as a healthy source. An independently
confirmed corrected provider stop is still required to resolve that interval.

Exact hashes, preserved ages and per-source decisions:
[`source-cleanup-review-20261103.json`](../reports/source-cleanup-review-20261103.json).
Fallback HTTP observations:
[`generic-source-cleanup-20261103.json`](../reports/generic-source-cleanup-20261103.json).
The pre-cleanup sources remain recoverable at `archive/pre-source-cleanup-20261103`.

Local validation: 300 tool tests plus 6 migration/publication tests passed.
The existing Python 3.13/3.14 CI also validates source bundles and reconstruction.
Receiver modified: **false**. Canonical `mena-data` remains unchanged and Vu+
release remains **NO_GO**.
