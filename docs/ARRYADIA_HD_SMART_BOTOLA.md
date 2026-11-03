# Arryadia HD Smart EPG with Botola Pro

The user requested enabling Smart EPG on **Arryadia HD**, including published Botola Pro match dates. HD2/HD3 keep the separately authorized hourly Grand Format schedule. TNT and HD1 are unchanged.

Priority is: current official SNRT programmes, exact genuine LKG, published Botola kickoff slots with estimated HD assignment, then non-overlapping learned recurring programmes. Simultaneous matches are left unassigned rather than putting two broadcasts on the same service. Estimated matches removed by a later extraction are not replayed from the previous guide.

## Recurring programmes

Smart learns recurring youth-programme families, reportage and news bulletins from explicit official SNRT dates. A template needs at least three distinct observed dates and bounded clock variation. It retains the generic programme family, never an old episode's opponents or match details. Generated rows cannot teach the model. The model is persisted in `data/arryadia-smart-model.json` and is not erased by a temporary empty download.

The current sample is the official grid of 21–26 September 2026. It supplies **historical observations**, not current broadcaster confirmation. The diagnostic keeps those dates explicit. Recurring titles are prefixed `[Estimé]` and the Arabic description explains their origin.

## Botola Pro

Only published fixtures from the Botola Pro first-division calendar are considered. SofaScore's unique tournament ID 937 is checked; LiveScore is an independent public fallback. Cancelled, postponed and finished fixtures, unrelated leagues and second-division fixtures are excluded. Denied hosts are not retried or bypassed; requests have a shared time budget.

Kickoff comes from the published fixture timestamp. HD channel assignment and the two-hour slot duration are estimates, labelled `[Diffusion estimée]` and explained in the description. This does not claim confirmed broadcast rights or a live SNRT announcement. No upcoming match is invented when providers return no valid fixture.

A live probe on 3 November returned **FAR Rabat vs Raja**, kickoff **4 November 2026 at 20:00 UTC/Morocco local time**, via the [LiveScore calendar](https://www.livescore.com/en/football/morocco/botola-pro/). The Smart probe produced 15 recurring estimates plus that match within the 48-hour window. Subsequent refreshes use the current provider data.

XMLTV categories distinguish Smart estimates, estimated Botola broadcasts and viewer-reported Grand Format. They survive packing, LKG loading and MENA aggregation. Quality reports count them separately and prevent an official-quality PASS. The resulting Morocco source can have 12/15 channels with future data: nine source guides, HD Smart and HD2/HD3 recurrence. This is not a claim of twelve official verified schedules.

Regression tests cover template evidence, model persistence, published kickoff preservation, collisions, official priority, removal of stale estimated matches, competition/status filters, producer defaults, and provenance through final packing/aggregation. Vu+ remains **NO_GO**; no receiver or frozen canonical release is modified.
