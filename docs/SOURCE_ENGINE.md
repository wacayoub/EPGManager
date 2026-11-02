# EPGManager source engine

Production XMLTV scrapers for MENA channels.

## Sources
- `morocco`: Morocco/2M/SNRT adapters
- `elcinema`: Arabic entertainment channels
- `bein`: beIN MENA
- `osn`: OSN
- `sport24`: sports fallback/auxiliary source
- `starzplay`: STARZPLAY Live public schedule metadata, hybrid AR/EN when accessible
- `aljazeera`: Al Jazeera Network official Arabic schedules
- `alkass`, `tabie`, `tunisiatv`, `dubaiplus`, `shahid`, `rotana`, `stctv`

### Hybrid language policy
For STARZPLAY international/foreign channels, EPGManager keeps the English programme title/subtitle and the Arabic description. Arabic-native channels keep Arabic title/subtitle/description. STARZPLAY is published only after normal feed validation and last-known-good checks; blocked or empty fetches never overwrite a healthy feed.

## Safety rules
Each source is scraped independently. A candidate feed is validated before publication. If validation fails, the previous known-good `.xml.gz` remains untouched.

Validation includes:
- valid XMLTV
- unique channel IDs
- `start < stop`
- no exact duplicate programme rows
- minimum future programmes
- gzip integrity
- per-channel zero-EPG and future-zero monitoring

## Schedule
All automatic source schedules use **fixed GMT+0** (`timezone: Etc/GMT`), once daily.
There is no seasonal or Ramadan shift. Times below are requested start times,
not publication deadlines: GitHub may delay a scheduled workflow.

| Source | GMT+0 |
|---|---|
| Tabie | 00:30 |
| ElCinema | 01:20 |
| OSN | 01:40 |
| beIN | 02:22 |
| Sport24 | 03:05 |
| Shahid | 03:39 |
| Rotana | 03:50 |
| Al Jazeera | 04:12 |
| Dubai Plus | 04:44 |
| STARZPLAY | 05:12 |
| STC | 05:55 |
| AlKass | 06:30 |
| Tunisia TV | 06:35 |
| Morocco | 07:07 |

The 07:15 orchestration may extend ElCinema from verified donors, but this repository no longer builds or publishes a receiver-wide MENA aggregate. `wacayoub/EPGManager` is the only owner of final merge, canonical receiver IDs, premium/MENA split, global LKG and Vu+ publication. Manual source refresh remains available.

Receiver imports must follow **actual publication**, not just the requested cron.
On 23 October the MENA rebuild completed at 12:39 GMT+0; a 10:00 import therefore
could not include every source's new publication. 13:15 would cover that observed
day with a margin, but is not a guarantee against future GitHub delays. Check source
SHA/generation/remaining guide before calling a cycle up to date. Never purge real
receiver EPG because a provider is late.

## Outputs
- `feeds/<source>.xml.gz`
- `reports/latest.json`
- `reports/latest.md`

The repository is the source engine. It publishes only validated per-source feeds for EPGManager consumption. It must not publish a final receiver MENA aggregate or own receiver canonical IDs.


## Guide quality

STARZPLAY is active on its own 05:12 GMT+0 workflow. A successful Actions run or a
MAIN/B1/B2 role does not certify guide quality. Review `<source>-current-health.json` and the source-specific quality reports: expired IDs stay visible but never green;
placeholder suspicions remain explicit, and generated Arryadia/AFLAM filler is
excluded from new derived publications. A degraded genuine exact-ID backup may
extend a shorter guide without falsely turning the result green.

## Multinational language policy

International/multinational channels use the original English programme title/sub-title and Arabic description. Arabic-native channels remain Arabic title + Arabic description. National Geographic Abu Dhabi is an explicit Arabic-native exception. STC and ElCinema enforce this policy for overlapping channels such as Discovery, CNN, Star Action, Star Movies, Star World and National Geographic Middle East.
