# Arryadia collector correction — 3 November 2026

Update: the user-authorized hourly Grand Format schedule on HD2/HD3 is restored separately from official-grid availability. See [restoration and provenance](ARRYADIA_GRAND_FORMAT_RESTORED.md). The official-grid observations below remain timestamped evidence.

The collector is repaired; **current Arryadia programme availability is not recovered**. The live [Arabic](https://www.snrt.ma/ar/node/4070) and [French](https://www.snrt.ma/fr/node/4070) SNRT endpoints both returned HTTP 200 and 97 explicitly dated grid rows for 21–26 September 2026. Both have zero future programmes on 3 November. A current date elsewhere in the HTML does not make this an updated guide. The [recorded live probe](../reports/morocco-arryadia-source.json) distinguishes successful HTTP from stale guide data.

Corrections:

- Read each programme's explicit calendar date, including after midnight. The real DOM lists 22 September 00:20 with class `20260922`; it must not become 23 September. Remove the competing flat-text day-tab alignment that could move rows between days.
- Keep same-clock, same-title rows for different services distinct. Infer end times from the next start on the same service only; isolated/terminal events are capped at one hour, explicitly reported as inferred.
- Assign explicit SAT, TNT, HD1, HD2 and HD3 markers separately. An unmarked main-channel programme is not evidence that the four other services simulcast it. All catalogue IDs remain present.
- Prefer the Arabic official grid and try its French counterpart if no current dated events are available. Keep actual title/description language.
- Preserve exact still-future LKG rows with their original dates; classify them as retained LKG rather than a fresh scrape. Never shift September rows into November or copy an unverified fixture calendar.
- Publish `reports/morocco-arryadia-source.json` on every Morocco attempt, with HTTP results, programme dates, current-event counts and retained-LKG counts. Other Morocco collectors remain independent.

The direct Arryadia landing pages contain programme descriptions without a dated broadcast grid. The former `arryadia.snrt.ma/grilles-des-programmes` link redirects to a malformed SNRT URL and returned 404. None supplies a verified current timetable in this check.

Validation covers calendar midnight, ISO-dated cards, exact channel variants, deduplication, header-versus-grid date confusion, Arabic/French fallback, provider outage, LKG provenance and placeholders. Full repository CI runs on Python 3.13 and 3.14. The Morocco source can remain useful at 9/15 channels while all five Arryadia IDs remain explicitly missing.

Vu+ release remains **NO_GO**. No receiver or canonical `mena-data` publication is changed. Morocco's daily collection stays at **07:07 Morocco local time**; changing its launch time cannot refresh a frozen upstream grid.
