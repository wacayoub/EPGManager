# Per-source audit and Morocco schedules — 3 November 2026

All times below are **Morocco local time (`Africa/Casablanca`)** and follow seasonal changes. The previous schedules used fixed GMT+0. Programme timestamps remain in their source-defined time zones and XMLTV UTC; changing scraping time does not shift broadcasts.

| Source | Daily local target | Future channels / catalogue at review | Individual review |
| --- | --- | ---: | --- |
| morocco | 07:07 | 9 / 15 | SNRT/2M/Medi1: Arryadia exposes an old grid; AFLAM has no verified current guide. Keep missing IDs explicit. |
| alkass | 06:30 | 8 / 8 | Direct official TV guide; all eight channels have future data. |
| tabie | 00:30 | 4 / 4 | Direct provider schedules; four channels have future data. |
| tunisiatv | 06:35 | 2 / 2 | Official programme pages; both channels have future data. |
| aljazeera | 04:12 | 2 / 5 | Official GraphQL Arabic/English work. AJ2 returns no rows; Mubasher/Documentary lack verified schedule endpoints. |
| dubaiplus | 04:44 | 9 / 9 | Official channel API; all nine channels have future data. |
| shahid | 03:39 | 71 / 71 | Official adapter; all 71 channels have future data. |
| rotana | 03:50 | 7 / 9 | Official streams page plus verified STC fallback; AlResalah and RotanaClip still lack genuine rows. |
| sport24 | 03:05 | 9 / 9 | UTC timestamps retained; verified donor extension recovers all nine channels. |
| osn | 01:40 | 79 / 100 | Android/Legacy batches, blank GUID filtering and English/Arabic merge. Raw OSNtv Now works; canonical receiver identity remains unvalidated. |
| bein | 02:22 | 65 / 68 | Arabic/English discovery, midnight anchor correction and final sidecar repack. Three catalogue channels still lack rows. |
| elcinema | 01:20 | 95 / 97 | Midnight rollover and authentic short-horizon retry; two catalogue IDs still lack rows. |
| starzplay | 05:12 | 117 / 119 | Official API bilingual merge; two catalogue entries have no future rows. |
| stctv | 02:37 | 124 / 149 | Official channel/schedule APIs; channel-name loops removed rather than published as genuine programmes. |

Each of the 14 files was checked independently for gzip SHA256/size, XMLTV counts, unique IDs, exact TXT catalogue, valid programme intervals and real future coverage. All have useful future data, but partial guides remain DEGRADED. Missing provider programmes are not fabricated or silently removed from the catalogue. Exact gaps and programme counts are in [the machine report](../reports/per-source-review-20261103.json).

Scheduling fixes: remove the obsolete 06:00 hour gate, record each source target and observed delay, and move postprocessing from 07:15 to 08:37 local time. This gives the 07:07 Morocco collection its 70-minute job allowance plus a 20-minute margin; arbitrary GitHub scheduler delay can still exceed this. The watchdog at 14:15 local time checks for changed source bytes requiring another derived rebuild.

The [manual sequential workflow](../.github/workflows/audit-sources-one-by-one.yml) allows exactly one source refresh-and-verification at a time, continues after an individual failure, and rebuilds the diagnostic aggregate at the end. No schedule is duplicated. Accepted candidates keep per-run receipts; a rejected candidate keeps its last valid feed and cannot pass as a newly published source.

Recent scheduled-run history is attached separately in [the observed schedule report](../reports/source-schedule-observations-20261103.json). Only three of the 14 newly consolidated workflow files had scheduled runs at review; absence of a run is not proof of on-time execution. The observed Tabie/ElCinema/OSN starts were many hours after their configured fixed-GMT targets. Retiming cannot promise exact delivery: [GitHub documents scheduler delays and possible dropped jobs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Vu+ remains **NO_GO**; no receiver was modified and the frozen canonical release is unchanged.

STC moved from 05:55 to 02:37 local time so its verified Rotana and STARZPLAY consumers can use the latest donor publication. The 73-minute lead over Rotana covers the 70-minute job allowance subject to scheduler delays.
