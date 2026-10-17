# Arryadia: six-month archive availability and recurring-programme evidence

**Audit date:** 2026-10-17 | **Requested window:** 2026-04-17 to 2026-10-17 inclusive | **Mode:** read-only.  
**Editorial principle:** historical recurrence is useful for source validation and ranking, not a licence to emit fabricated future XMLTV programmes.

## Outcome — do not claim six full months of TV schedules

The official SNRT page `https://www.snrt.ma/fr/node/4070` is currently stuck on **21–26 September 2026**, containing **97 individually timed programme rows over six dates**. An actual GitHub-runner sweep on 2026-10-17 of **16 offsets** (`?limit=5&start=0,5,10,20,35,50,70,90,110,130,150,170,190,220,260,300`) returned **those same six dates and 97 rows every time**. Counted once only. An attempted external TV archive returned the current 17 October page regardless of six distinct historic `dt` values. Therefore these are *not* 184 days of measured TV schedules. A separately indexed SNRT snapshot for **12–17 August 2026** also shows recurring morning Arryadia Chabab blocks, but that snapshot was not accessible as a distinct historical feed in today's direct downloads.

Available historic **live football match** listings for HD1 and HD2 contain individually dated matches in May–July and October, but they are NOT a daily programming schedule. Such records must never be copied to another Arryadia channel or used to predict unannounced fixtures.

## Archive coverage evidence by requested month

| 2026 month | Evidence recovered | Can support regular TV-slot inference? |
| --- | --- | --- |
| Apr 17–30 | No complete dated Arryadia TV schedule recovered | No |
| May | Individually dated HD1 Coupe du Trône football matches, 16–17 May | No; match-only |
| June | HD1 and HD2 football listings (e.g. 12, 17, 21, 25, 28 June on HD1; 17 and 28 June on HD2) | No; match-only |
| July | HD1 football on 2, 5 and 21 July | No; match-only |
| August | Indexed SNRT 12–17 August historical grille: morning `Arryadia Chabab` programmes on several days | Qualitative corroboration, not a statistical census |
| September | **97 dated SNRT programmes on 21–26 Sep**; detailed distribution below | Yes, for this *one sampled week*, no wider extrapolation |
| Oct 1–17 | Recorded HD1 match 8 Oct; HD3 source contains match history up to 14 Oct | No; match-only |

### Actual recurring show counts from 21–26 September official source

Deduplicated by (programme date, clock time, exact title), excluding `Programmes Arryadia` and equivalent placeholder blocks.

| Family | Real listings | Distinct observed broadcast dates | Observed start hours | Interpretation |
| --- | ---: | ---: | --- | --- |
| Arryadia Chabab — football, incl. women's youth football | 16 | 5 | Usually 07:00–11:00 | Strong sign of a *morning block in this one week*; exact episode and daily start unconfirmed |
| Arryadia Chabab — reportage | 8 | 5 | Usually 08:00 and 11:00 | Recurs within same morning block; never fix a daily exact start solely from this |
| Flash info | 15 | 6 | Often near 04:00, 13:00, 22:00 | Recurrent bulletin with a 6-day sample; precise clock varies |
| Documentaire | 5 | 3 | 12:00–13:00 or around 20:00 | Variable; special production and event dependent |
| Studio / direct analysis (`Studio Al Botola`, `Studio Tahlil`) | 3 | 2 | 17:00, 19:00, 21:00 | Event/match-driven, not weekly guaranteed |
| Football/matches | 15 | 6 | Variable | Never predict opponent, competition, transmission or LIVE from past weeks |
| Billard World Championship Marrakech 2026 | 7 | 5 | Often 22:25–22:30 | Specific historical event; **not** an ongoing regular show |
| Tennis / Coupe Davis | 5 | 4 | Variable | Competition dependent, not regular |
| Futsal | 5 | 4 | Variable | Competition dependent, not regular |

### Confidence for use in a future editorial matcher

- **Medium (programme-family recurrence only):** Chabab football/reportage has a recognizable morning block in September, corroborated by a separate August indexed grid. *No confidence in today's precise hour from archive alone.*
- **Medium (recurrent programme family):** Flash info was present on six sampled September dates, but observed three dayparts; no guaranteed exact schedule for October.
- **Low (fixed scheduling):** Studio Al Botola / Studio Tahlil and documentaries; context-driven and event-dependent.
- **None for future XMLTV publication:** any opponent, kickoff, TNT/HD1/HD2/HD3 assignment, `LIVE:` status, or a guessed rerun based on old programmes.
- **Not a six-month frequency estimate:** April–July and October lack daily SNRT schedules; August snapshot is partial.

## Mandatory integration safeguards

1. Use historical show patterns only as **read-only candidate-scoring hints** (preferred family, approximate daypart). They are not actual TV listings.
2. Future XMLTV `<programme>` entries require a trustworthy broadcast-date-specific current source, with timestamp, title and channel identity. Do not reuse old entries with shifted dates.
3. Explicitly separate **Arryadia HD**, **Arryadia TNT** and **HD1/HD2/HD3**; sports rights and match feeds vary. Do not duplicate HD3 live matches onto HD1/HD2 on historical probability.
4. Keep Arabic-original programme names where supplied by the broadcaster, descriptions Arabic when real; preserve named clubs and competitions as required by the project's editorial rules.
5. Remove the belief that three-hour `Programmes Arryadia ...` fillers are `fresh` guide coverage: classify as synthetic/unknown, never as validated real events.
6. An independent/current match listing is corroboration, not sole authority for LIVE unless explicitly announced for the same channel and day.
7. A six-month *statistically validated* recurring schedule needs dated weekly snapshots from **at least several distinct months**, not six months of fallback boilerplate.

## Evidence

- Official historical 21–26 Sep SNRT Arryadia grid: https://www.snrt.ma/fr/node/4070
- Independently indexed historical August SNRT snapshot: https://www.snrt.ma/fr/node/4070?limit=5&start=130 (retrieval now redirects to the September data, so count only as qualitative archive)
- Dated HD1 match archive: https://www.livefootballtv.info/channel/arryadia-hd1
- Dated HD2 match archive: https://www.livefootballtv.info/channel/arryadia-hd2
- HD3 football match history (not an EPG): https://www.livefootballtv.info/channel/moroccan-sports
- Unusable generic daily segments (3h repetition): https://epg.pw/channels/55898/20261002.html?lang=en
- GitHub read-only collector and successful 2026-10-17 run on development branch `fix/morocco-arryadia-verified-sources-20261017`: `tools/arryadia_6month_archive_audit.py`, Actions `Arryadia six-month historical programme audit`.

**This document does not claim complete six-month programme availability, does not change EPG mappings, and does not authorize publishing inferred events.**
