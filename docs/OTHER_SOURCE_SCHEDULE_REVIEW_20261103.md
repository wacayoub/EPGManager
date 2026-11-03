# Other 13 source schedules — 3 November 2026

All 13 sources were rechecked against their exact successful publication from [the sequential collection](https://github.com/wacayoub/EPGManager/actions/runs/41609383152), and their current gzip/JSON/TXT bytes and remaining guide health. This review does not claim a new scrape or prove delivery at the revised daily targets. [Machine evidence](../reports/remaining-source-schedule-review-20261103.json).

Times use Morocco local civil time, `Africa/Casablanca`. ElCinema stays at the requested 04:20.

| Source | Daily local target | Future channels / catalogue | Current guide status |
| --- | --- | ---: | --- |
| alkass | 00:17 | 8 / 8 | AVAILABLE |
| tabie | 00:30 | 4 / 4 | AVAILABLE |
| osn | 01:40 | 79 / 100 | DEGRADED |
| bein | 02:22 | 65 / 68 | DEGRADED |
| stctv | 02:37 | 124 / 149 | DEGRADED |
| starzplay | 03:07 | 117 / 119 | DEGRADED |
| shahid | 03:39 | 71 / 71 | AVAILABLE |
| rotana | 03:50 | 8 / 9 | DEGRADED |
| aljazeera | 04:12 | 2 / 5 | DEGRADED |
| dubaiplus | 04:44 | 9 / 9 | AVAILABLE |
| sport24 | 06:03 | 9 / 9 | AVAILABLE |
| tunisiatv | 06:35 | 2 / 2 | AVAILABLE |
| morocco | 07:07 | 9 / 15 | DEGRADED |

## Dependency corrections

- AlKass: 06:30 to 00:17, before OSN at 01:40 (83-minute lead).
- STARZPLAY: 05:12 to 03:07, before ElCinema at 04:20 (73-minute lead).
- Sport24: 03:05 to 06:03, after OSN and DubaiPlus; DubaiPlus at 04:44 has a 79-minute lead.
- STC stays at 02:37 before Rotana at 03:50 (73-minute lead). STARZPLAY fallback fetches the live STC API independently; the published STC file is not a dependency.
- Other targets are unchanged. Postprocessing stays at 08:37, after each 70-minute collection budget; watchdog stays at 14:15.

Repository verification rejects a local-feed consumer scheduled before its donor finishes its configured collection allowance, or postprocessing scheduled too early. Tests compare the dependency registry with the adapters’ local candidate mappings. GitHub delivery delays can exceed these planned margins.

Eleven dedicated source workflows still have no scheduled execution in the queried GitHub history. The successful manual sequence proves collection/publication, not on-time delivery of the revised schedules.

Partial guides remain explicitly degraded. Programme timestamps and channel IDs are unchanged. No receiver was contacted; Vu+ remains **NO_GO**.
