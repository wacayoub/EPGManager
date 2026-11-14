# Automatic fallback for Aflam, Arryadia HD, HD1 and local TNT

At the user's request on 6 November 2026, the Morocco daily collector fills
uncovered intervals on these three exact services over its rolling seven-day
window. Aflam uses three-hour blocks, as in the former automatic guide; HD1 and
local TNT use two-hour blocks. Titles begin with `[Auto]`. Descriptions explicitly
say these are automatic fallback blocks, without a confirmed film, match, or
on-air claim. No satellite guide is copied onto local TNT.

On 14 November 2026 the main Arryadia HD channel was added with two-hour
`[Auto] برامج الرياضية` blocks. Its Smart estimates and Botola matches still come
first; the blocks fill only the hours they leave empty. SNRT's own grid has
not been updated since September 2026, so the main channel otherwise had an
8-9 hour hole every day.

Fresh official programmes and exact still-future last-known-good programmes
take priority on their own service. Blocks are split around those intervals.
Old generated blocks are discarded and recreated each collection, so an official
listing can replace them immediately on the next daily collection or manual run.

Arryadia HD continues to use labelled Smart recurrence and estimated Botola
broadcasts. HD2/HD3 continue their viewer-reported hourly Grand Format loop.
Morocco's 07:07 Africa/Casablanca collection target is unchanged.

XMLTV category `Automatic fallback schedule` survives packing and derived feeds.
Health and seven-day reports count `automatic_fallback_programmes` separately
from source programmes. `reports/morocco-auto-fallback.json` records the requested
window and blocks per service. The generated guide remains degraded and cannot
turn Best Match green or certify an Enigma2 release. No receiver restart, wake-up
or EPG import is part of this change.
