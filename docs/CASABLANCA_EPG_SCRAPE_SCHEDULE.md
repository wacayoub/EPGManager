# EPG-Scrapers — All source schedules on fixed GMT+0 (18 October 2026)

**Definition:** User explicitly requests **GMT+0 fixed clock**, not
`Africa/Casablanca` civil wall-clock. For scheduling, **GMT+0 equals
UTC+0**. The GitHub Actions native IANA timezone is now `Etc/GMT`
(fixed +00:00, no Moroccan/Ramadan/DST shifting). At present
(18 October 2026), Casablanca local civil time is GMT+1, so a
04:44 GMT+0 trigger appears as 05:44 in Casablanca.

This is a **GitHub source scraper schedule only**. It does not change
Vu+ / Enigma2 / EPGImport receiver scheduling, local receiver timezone,
installed EPGManager RC79.1, blocked R80, or private mappings.

## The effective automatic schedule

| Fixed GMT+0 trigger | Action | Workflow |
| --- | --- | --- |
| **00:30 daily** | **Tabie/QMC — sole source scrape; only genuinely active IDs** | **`refresh-tabie.yml`** |
| **01:20 daily** | ElCinema — one source scrape | `refresh-elcinema.yml` |
| **01:40 daily** | OSN — one source scrape | `refresh-osn.yml` |
| **02:22 daily** | beIN MENA — one source scrape | `refresh-bein.yml` |
| **03:05 daily** | Sport24 — one source scrape | `refresh-sport24.yml` |
| **03:39 daily** | Shahid/MBC — one source scrape | `refresh-shahid.yml` |
| **03:50 daily** | **Rotana — sole source scrape** | **`refresh-rotana.yml`** |
| **04:12 daily** | **Al Jazeera / Al Jazeera English — one source scrape** | **`refresh-aljazeera.yml`** |
| **04:44 daily** | Dubai+ — one source scrape | `refresh-dubaiplus.yml` |
| **05:12 daily** | **STARZPLAY — sole daily source scrape** | **`refresh-starzplay.yml`** |
| **05:55 daily** | **STC TV — sole daily source scrape** | **`refresh-stctv.yml`** |
| **06:30 daily** | **AlKass — sole automatic source scrape** | **`refresh-alkass.yml`** |
| **06:35 daily** | **Tunisia TV — sole automatic source scrape** | **`refresh-tunisiatv.yml`** |
| **07:07 daily** | **Morocco — sole automatic source scrape** | **`refresh-morocco.yml`** |
| **07:15 daily** | ElCinema authentic donor postprocessing and derived MENA rebuilding **only; no source scrapers** | `daily-epg-chain.yml` |
| **14:15 daily** | Watchdog health checks; may rebuild derived MENA only, does not source-scrape | `epg-watchdog.yml` |

All automatic `schedule` entries above specify exactly
`timezone: "Etc/GMT"`. A `cron: "44 4 * * *"` with `Etc/GMT`
always targets 04:44 GMT+0, including when Morocco changes legal UTC offset.
Execution on GitHub may be delayed by queueing or shared
`epg-source-publish` concurrency. **Cron time is a trigger target,
not an exact guaranteed completion time**.

## Duplicate source protections

- Dubai+ dedicated daily 04:44 is the **sole automatic Dubai+ scraper**.
  Its 07:15 chain job and the old `push` trigger are removed;
  `workflow_dispatch` is preserved.
- All **14 direct source families** have their sole dedicated automatic daily
  clock, including Morocco **07:07 GMT+0**; no 07:15 source duplicates.
  Morocco’s previous 20:30 cron and code-change push trigger are removed.
  The historical evening Morocco/AlKass workflow remains **manual only**.
- Al Jazeera's dedicated schedule is `12 4 * * *` at `Etc/GMT`:
  **04:12 fixed GMT+0**, no seasonal shift. Remove its old 07:15
  upstream job; STARZPLAY no longer runs in the morning chain either.
  Keep `workflow_dispatch` for deliberate manual Al Jazeera refreshes.
- STARZPLAY's sole fixed GMT+0 schedule is **05:12 daily** (`12 5 * * *`,
  `timezone: "Etc/GMT"`). Its former job in the 07:15 chain is removed,
  and Tunisia TV becomes the first job; all subsequent sources keep their
  dependencies. The former automatic code-change `push` trigger of
  `refresh-starzplay.yml` is removed. Manual `workflow_dispatch` remains.
- STC TV runs once at **05:55 fixed GMT+0** (`55 5 * * *`,
  `timezone: "Etc/GMT"`) in `refresh-stctv.yml`. The old STC job was
  removed from the 07:15 chain, so AlKass follows Tunisia TV directly.
  No automatic `push` trigger exists; manual dispatch is retained.
  The full STC catalogue and per-ID quality checks remain unchanged.
- AlKass **06:30**, Tunisia TV **06:35**, Rotana **03:50** and Tabie **00:30**
  are the only automatic scrapes of each respective source. All four
  use `Etc/GMT`, retain explicit manual `workflow_dispatch`, remove
  `push` triggers and leave the 07:15 chain. The derived-only 07:15
  orchestration preserves ElCinema genuine donor postprocessing and
  MENA rebuilding.
- No automatic 19:30 AlKass refresh remains; `workflow_dispatch
  group=sports` remains an explicitly manual exception. The
  **20:30 Morocco** cron is removed in favor of Morocco **07:07**.
- STC / STARZPLAY / beIN / Morocco guide identity and XMLTV mappings
  are unchanged. This schedule change cannot fabricate missing
  Rotana Clip / Al Resalah programmes, fill genuine AlKass short
  guide horizon or Tabie QMC stale-provider dates.
- Shahid targeted retry is still **manual only**, with no automatic 15:00.
- The shared legacy `refresh-source-core.yml` "06:00 scheduled" guard
  has also been moved to fixed GMT+0. Normal dedicated source workflows
  pass `scheduled: false` and keep publisher integrity checks.
- Other `workflow_dispatch` and explicitly configured `push` triggers
  are on-demand/event-based, **not** additional daily clock schedules.
  No source item is manufactured by schedule changes.

## Dubai+ EPG quality and caveats

At last audited publication (2026-10-17 10:51 UTC/GMT), 9/9 Dubai+
channels carried guides with 423 programmes and a ~48h horizon.
Dubai One has a genuine 185-minute internal gap, Dubai TV 120 minutes,
and Dubai Zaman 180 minutes. **350 of 413 future programmes lacked
authentic descriptions**. The separate Dubai+ quality PR adds support
for native ar-AE provider-localized synopsis fields but does not invent
any missing descriptions or fill unrelated channel gaps.

Only DubaiTV.ae@SD may be complemented by the strict exact-ID OSN
48h PASS genuine donor, preserving all Dubai+ primary programmes.
Sport24's three exact sports donors remain independent.

## Acceptance

Regress `gmt0-all-sources-0444-ci.yml` and `four-sources-fixed-gmt0-ci.yml` on both Python 3.13 and 3.14,
in addition to the pre-existing per-source schedule checks. Confirm the
fixed GMT+0 offset even on winter, summer and Ramadan examples.

**After merge**, first 04:44 GMT+0 Dubai+ source run is not yet proof
of health until actual publication/LKG/guide audit is observed. Nothing
in this change installs or edits anything on the receiver.
