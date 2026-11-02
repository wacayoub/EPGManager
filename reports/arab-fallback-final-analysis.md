# Arab fallback final analysis

Active fallback rows: **1527**  
Zero-EPG IDs blacklisted: **929**  
Canonical groups: **1273**  
Duplicate groups: **210**  
Extra duplicate rows collapsed: **254**  
New IDs after zero/LatAm filtering: **150**

## Source blacklist

| Provider | Source | Reason | Detail |
|---|---|---|---|
| epgshare | AE1 | SOURCE_QUARANTINED_BAD_CHANNEL_PROGRAMME_MAPPING | 411 cloned channels / 813 total |
| epgshare | EG1 | ZERO_ACTIVE_CHANNELS | 238 channels, 0 active |
| epgshare | SA1 | ZERO_ACTIVE_CHANNELS | 97 channels, 0 active |
| epgshare | SA2 | SOURCE_QUARANTINED_BAD_CHANNEL_PROGRAMME_MAPPING | 23 cloned channels / 41 total |
| epgshare | AR1 | ARGENTINA_LATAM_NOT_ARAB |  |
| epgshare | BEIN1 | SOURCE_QUARANTINED_BAD_CHANNEL_PROGRAMME_MAPPING | 28 cloned channels / 80 total |
| openepg | arabiapremiumar | INVALID_SOURCE_XML | no element found: line 1, column 0 |

## Integration rule

Healthy direct sources are never replaced. Fallback winners are only candidates for channels that still have zero EPG. Duplicate alternatives remain available as failover; zero-programme IDs stay dynamically blacklisted until they recover future programmes.
