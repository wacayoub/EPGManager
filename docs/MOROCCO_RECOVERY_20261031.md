# Morocco scraper recovery — 31 October 2026

Morocco keeps its sole automatic **07:07 fixed GMT+0** schedule and manual
dispatch. Receiver import timing, mappings, source IDs and feed publication
gates are unchanged.

## Corrections

- Morocco has a separate concurrency group from unrelated source downloads.
  All callers of the shared core with `source: morocco` use that same group,
  retaining same-source serialization and `cancel-in-progress: false`.
- Source-scoped publication keeps bounded rebase/push retries. Rebase autostash
  accommodates generated, unstaged reports when another source publishes first.
  A local two-clone integration test verifies both source commits survive and
  generated shared reports are not accidentally published.
- SNRT requests are capped at two concurrent requests across HTTP clients and
  the broadcaster worker pool is reduced to two.
- Transient SNRT 408/429/500/502/503/504 errors use 3/6-second backoff, with
  Retry-After handling and the existing 90-second provider budget. Requests for
  more than 20 seconds of backoff defer the provider rather than retry early.
  Permanent errors such as 403 are not retried. Other providers retain
  their existing retry timing.
- Failed HTTP responses are closed before retrying.
- Arryadia diagnostics do not reference an uninitialized HTML document after
  a download failure.
- A source timing report records lateness observed after job setup and warns
  after 15 minutes. Manual retries are not reported as scheduled late runs.

GitHub can still delay or drop schedule events before a workflow exists. The
repository changes remove an avoidable shared queue; they cannot guarantee a
07:07 start on GitHub's infrastructure. Timing observations include trigger,
queue and setup delay and do not identify those components individually.

## Guide authenticity

Existing exact-channel, still-future last-known-good events remain eligible.
Expired events, synthetic repeats and fixture-only guesses remain excluded.
No failed provider is labeled healthy merely because a scrape job completes.

A read-only workstation probe on 31 October received HTTP 200 from SNRT and
parsed future official rows for Al Aoula, Arrabiaa and Assadisa. Arryadia's page
still exposed September dates and produced no future guide. This is not evidence
of recovery from the GitHub runner, or a reason to shift those dates forward.

## Validation

- 18 new offline recovery tests passed.
- 7 existing 2M translation tests passed in a separate process.
- 8 publication-cohort tests passed using their supported discovery command.
- Changed Python modules compile; schedule YAML remains 07:07 at Etc/GMT.
- The new CI workflow runs these tests on Python 3.13 and 3.14 without scraping.

Broader pre-existing suites are not claimed green: the current public tree's
scraping-quality suite imports absent fixture modules, and two legacy workflow
assertions still require a `finalize` job removed from the public daily chain.
Those unrelated failures were observed before publication of this correction.
