# Morocco: seven-day guide where available

The Morocco source requests **168 hours (seven days)** and keeps all 15 catalogue IDs. Other sources retain their existing 48-hour windows. The normal 07:07 Morocco local collection now requests seven days and its packer preserves the 168-hour window, even if an older manual caller still passes 48 hours.

The horizon is applied through collection, exact-LKG selection, Arryadia Smart/Botola generation, hourly HD2/HD3 recurrence and final XMLTV packing. The coverage audit target is also 168 hours. Grand Format uses a rolling seven-day horizon, including partial first/last calendar days, rather than stopping at midnight before the seventh full day has elapsed.

## Provider limits remain explicit

- SNRT: retain explicit provider dates throughout the requested window.
- 2M: request up to seven distinct weekday pages, preserving their date order without requesting the same week again or copying a day forward. Existing programme clock/language rules remain unchanged.
- Medi1 Arabic/Maghreb: request the dated provider endpoints over seven days.
- Chada: retain the available today/tomorrow pages and genuine dated LKG; do not repeat its daily grid to manufacture seven days.
- Arryadia HD: generate labelled Smart recurrence and published Botola Pro kickoff estimates over the full window; current broadcaster announcements take priority.
- Arryadia HD2/HD3: preserve the user-authorized hourly Grand Format recurrence and its viewer-reported provenance.
- Aflam, TNT and HD1 keep their IDs and any genuine available programmes; no recurrence has been authorized for them.

An empty or shorter provider guide remains empty or shorter. Seven days requested is not a promise of seven complete days from every provider.

`reports/morocco-7day-coverage.json` is measured from the **final accepted gzip bytes**, including when a candidate is rejected and LKG retained. It records the source SHA, all 15 IDs, programme counts by local start date, native/viewer-reported/Smart counts, final available stop and actual uncovered minutes in the requested 168 hours. `FULL_REQUESTED_WINDOW` requires continuous interval coverage, not merely a last programme seven days away.

Regression tests check a genuine day-six event and exact LKG, rejection outside seven days, the rolling Grand Format horizon and writer validation, a day-six Botola fixture, distinct 2M page requests and the real Morocco shell branch/packer. The integration test verifies that the output still contains all IDs, preserves seventh-day data and reports unavailable IDs without manufacturing rows.

Vu+ release remains **NO_GO**. This source publication does not contact a receiver or replace the frozen canonical release.
