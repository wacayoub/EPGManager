# Recovery of all 14 sources — 3 November 2026

Scope: public source data and publication integrity. **Vu+ release: NO_GO. No receiver was modified.**

The [collection run 41602904500](https://github.com/wacayoub/EPGManager/actions/runs/41602904500) published all 14 sources and rebuilt the diagnostic MENA aggregate. Its overall conclusion was failure: the original OSN verification saw a newer scheduled publication instead of the batch receipt. This is a verification race, not a claim that the failed run passed.

The attached [machine evidence](../reports/all-source-recovery-20261103.json) binds each accepted publication to its exact Git commit, workflow run and attempt. Verification reads gzip, JSON metadata and ID index from that same commit, checks SHA256, size, counts, identities and programme intervals, and checks current source health separately. A later successful run cannot certify a failed or retained-LKG attempt. New runs retain separate receipts under `reports/refresh-runs/<run>/<attempt>/`.

| Source | Channels with future EPG / catalogue | Programmes in batch publication |
| --- | ---: | ---: |
| morocco | 9 / 15 | 578 |
| alkass | 8 / 8 | 269 |
| tabie | 4 / 4 | 683 |
| tunisiatv | 2 / 2 | 124 |
| aljazeera | 2 / 5 | 112 |
| dubaiplus | 9 / 9 | 407 |
| shahid | 71 / 71 | 4453 |
| rotana | 7 / 9 | 273 |
| sport24 | 9 / 9 | 156 |
| osn | 79 / 100 | 6029 |
| bein | 65 / 68 | 2618 |
| elcinema | 95 / 97 | 4075 |
| starzplay | 117 / 119 | 8520 |
| stctv | 124 / 149 | 7760 |

Counts are a timestamped observation, not a promise of complete coverage. Catalogue IDs with no verifiable schedule remain visible and flagged; no programmes are invented.

OSN batch publication commit `72968c87b73eb1c81cdc9c88beee481e8c0499e2` contains 6,029 programmes. The scheduled run 41603896208 subsequently published 6,033. The raw source channel `osn.5677` (OSNtv Now) has 68 future programmes at this audit. This does not certify the separate canonical receiver IDs `OSNtv Now.sa` or `OSN.Now.ae`.

Remaining provider gaps include Morocco (9/15 with future EPG; the Arryadia page exposed an old September grid), Al Jazeera (2/5), Rotana (7/9), OSN (79/100), beIN (65/68), elCinema (95/97), Starzplay (117/119), and STCtv (124/149). Sport24 now covers 9/9. Missing provider schedules must be corrected upstream or supported by verified alternative schedules; channel titles and unavailable descriptions are not programme data.

The rebuilt diagnostic aggregate contains 514 channels and 29,992 programmes, with zero orphan programmes, invalid intervals or exact duplicates. It still reports 53 channels without future EPG and 93 degraded guides. Its uncompressed XML is 13,751,577 bytes, above the 12 MiB Vu+ budget. The frozen `mena-data` release and Enigma2 hardware validation are not certified by these source checks.

Validation: repository bundle/workflow checks, complete Python unit suite, and CI on Python 3.13/3.14. A separate verification-only workflow can verify the original producer run without recollecting sources.
