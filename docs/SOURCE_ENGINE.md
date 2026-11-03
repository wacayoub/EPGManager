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
All automatic source schedules use **Morocco local time** (`timezone: Africa/Casablanca`), once daily.
The civil clock follows seasonal and Ramadan changes. The schedule policy is
`config/source-schedules.json`; workflow verification rejects policy drift. Times below are requested start times,
not publication deadlines: GitHub may delay a scheduled workflow.

| Source | Morocco local time |
|---|---|
| Tabie | 00:30 |
| ElCinema | 04:20 |
| OSN | 01:40 |
| beIN | 02:22 |
| Sport24 | 06:03 |
| Shahid | 03:39 |
| Rotana | 03:50 |
| Al Jazeera | 04:12 |
| Dubai Plus | 04:44 |
| STARZPLAY | 03:07 |
| STC | 02:37 |
| AlKass | 00:17 |
| Tunisia TV | 06:35 |
| Morocco | 07:07 |

STC runs at 02:37 before its local-feed consumer Rotana (03:50).
AlKass runs at 00:17 before OSN (01:40); OSN precedes DubaiPlus (04:44),
which precedes Sport24 (06:03). STARZPLAY runs at 03:07 before ElCinema (04:20).
Each local donor has at least the 70-minute collection allowance before its
consumer. STARZPLAY’s own STC fallback fetches the live API independently;
it does not wait for the published STC feed. GitHub delivery delays can exceed
these margins. Dependency order is enforced by repository verification.

The 08:37 local postprocessing may extend ElCinema from verified donors and
rebuilds the diagnostic MENA source aggregate. This allows the 07:07 Morocco
job its 70-minute timeout plus a 20-minute margin. The 14:15 local watchdog
checks source snapshot changes; GitHub delivery delays can still exceed any
fixed margin. These are requested launch times, not guaranteed publication times.

Every source records its target and observed job-setup delay in
`reports/<source>-run-timing.json`. Manual refreshes have no scheduled delay.
The obsolete 06:00 hour filter is removed so a delayed job can still collect.
Use the manual **Collect and verify sources one by one** workflow to complete
one refresh and exact-run publication verification before the next source.

Source collection success does not certify complete guide coverage. Consult
[the per-source review](PER_SOURCE_SCHEDULE_AUDIT_20261103.md) for individual gaps.
Receiver release remains **NO_GO**; the diagnostic aggregate does not certify
canonical receiver IDs, premium/MENA splitting, hardware validation or import authorization.

## Outputs

Morocco publishes a [seven-day guide where available](MOROCCO_SEVEN_DAY_GUIDE.md),
with per-ID actual coverage in `reports/morocco-7day-coverage.json`.
Other source publication windows remain 48 hours.
- `feeds/<source>.xml.gz`
- `feeds/<source>.json` and `feeds/<source>.txt`
- `reports/<source>-publish-lkg.json`
- `reports/<source>-current-health.json`
- `reports/<source>-run-timing.json`
- `reports/refresh-runs/<run>/<attempt>/<source>.json`

EPGManager owns the source engine and its diagnostic aggregate. EPG-Scrapers is a compatibility mirror with no collection schedules. Receiver publication is a separate guarded contract.


## Guide quality

Arryadia HD2/HD3 retain the user-authorized hourly **Grand Format** recurrence.
It is labelled as viewer-reported in XMLTV and quality reports, survives packing
and aggregation, and yields to announced same-channel programmes. The user also
enabled [HD Smart EPG with published Botola fixtures](ARRYADIA_HD_SMART_BOTOLA.md),
explicitly labelled as estimates. TNT and HD1 remain unchanged.
See [restored Grand Format](ARRYADIA_GRAND_FORMAT_RESTORED.md).

STARZPLAY is active on its own 03:07 Morocco local workflow. A successful Actions run or a
MAIN/B1/B2 role does not certify guide quality. Review `<source>-current-health.json` and the source-specific quality reports: expired IDs stay visible but never green;
placeholder suspicions remain explicit, and generated Arryadia/AFLAM filler is
excluded from new derived publications. A degraded genuine exact-ID backup may
extend a shorter guide without falsely turning the result green.

## Multinational language policy

International/multinational channels use the original English programme title/sub-title and Arabic description. Arabic-native channels remain Arabic title + Arabic description. National Geographic Abu Dhabi is an explicit Arabic-native exception. STC and ElCinema enforce this policy for overlapping channels such as Discovery, CNN, Star Action, Star Movies, Star World and National Geographic Middle East.
