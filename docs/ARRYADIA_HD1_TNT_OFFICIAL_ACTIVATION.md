# HD1 and local TNT: activate EPG from explicit SNRT announcements

Update, 6 November 2026: the user subsequently requested an explicitly labelled
automatic fallback for HD1, TNT and Aflam. See [the current fallback policy](MOROCCO_AUTO_EPG.md).
The official-source states below still describe SNRT availability; fallback
blocks are reported separately and never become official programmes.

HD1 and TNT catalogue IDs remain present even with zero programmes. At each Morocco collection, they acquire EPG automatically when an explicitly dated, still-future SNRT listing identifies their own service. This is EPG availability, not a claim that the transmitter is currently on air, and it performs no receiver wake-up or import.

TNT is the local terrestrial service. Generic Arryadia/satellite programmes are not copied onto it. Explicit TNT, Arabic terrestrial markers such as `البث الأرضي` or `القناة الأرضية`, French terrestrial markers, and typed local service metadata can identify the TNT listing. An incidental mention of a local match does not establish a TNT broadcast. Typed service metadata takes precedence over incidental satellite words in a title or synopsis.

The collector checks **both Arabic and French official SNRT grids** on every attempt. A current Arabic main-channel grid no longer stops collection before an FR-only HD1 or local TNT announcement can be found. Arabic rows keep priority in overlapping same-service slots; other services and non-overlapping slots can be added from French. Different services are never collapsed into one row.

`reports/morocco-arryadia-source.json` exposes independent states for HD1 and TNT:

- `OFFICIAL_EPG_AVAILABLE`: current explicit future SNRT programmes.
- `OFFICIAL_LKG_AVAILABLE`: exact still-future retained programmes from the prior native guide.
- `WAITING_FOR_OFFICIAL_EPG`: no such official programmes; preserve the ID. The separately labelled automatic fallback may fill its uncovered intervals.

Smart/Botola remains restricted to the main HD service. Grand Format remains restricted to HD2/HD3. Neither is assigned to TNT or HD1. The seven-day Morocco window and daily 07:07 Morocco-local collection remain in effect. GitHub delivery can be delayed; this is checked at collection time rather than continuous real-time monitoring.

Regression tests cover multilingual partial-guide merging, terrestrial identification, metadata precedence, no generic satellite copying, seven-day announcements and exact future LKG. Vu+ remains **NO_GO**.
