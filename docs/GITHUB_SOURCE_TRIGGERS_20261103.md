# GitHub source-trigger audit — 3 November 2026

The live GitHub API was checked against the maintained workflow files on
`main`. All 14 source workflows are registered and active. Repository Actions
are enabled and actions are allowed. Default token permissions are read-only;
the source publishers explicitly request `contents: write`, so changing the
repository-wide default is unnecessary.

No current source collection was queued or running during the initial audit.
The most recent scheduled Tabie, OSN and ElCinema executions contain a successful
actual source-refresh step. Their skipped provider-specific setup steps do not
mean collection was skipped. No scheduled execution was observed for the other
11 source wrappers at that point; this alone does not prove a disabled trigger.

## Cleanup

- Retired workflow registrations whose files no longer exist on `main` are
  disabled through the GitHub API. Each mutation first verifies the remote
  default-branch tree and checks for running/queued work. Run history is kept.
- Remove `refresh-starzplay-ma.yml`: it required a private
  `[self-hosted, linux, starzplay-ma]` runner, but the repository has zero
  registered private runners. Normal STARZPLAY remains on the hosted runner
  through `refresh-starzplay.yml` at 03:07 Morocco local time.
- The daily watchdog now audits all 14 source registrations and the actual
  collection steps of their latest scheduled runs. It distinguishes missing
  evidence, skipped collection, execution failure and active/disabled workflows.
  API failures produce UNVERIFIED, never a clean trigger result.
- Audit diagnostics run after any needed MENA rebuild dispatch. A failed audit
  remains visible without blocking that valid rebuild. JSON evidence is saved
  as a seven-day Actions artifact and its status appears in the job summary.
  The watchdog is bounded to ten minutes, with thirty-second API request limits.

The audit itself is read-only: it does not dispatch, enable, cancel, retry or
repeatedly scrape sources. It adds no new cron and preserves all 16 Morocco-local
source/postprocessing/watchdog schedule entries. Manual source launches remain.

## Limits of the evidence

GitHub supports IANA `timezone` alongside `cron`, including Africa/Casablanca.
Scheduled events run from the default branch and can be delayed or dropped
under load. See [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule)
and [scheduled-event behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

An API dispatch response or green workflow alone is not proof that programmes
were collected or published. The audit checks the collection step; exact source
publication is checked separately with the producer-run receipt. The next
automatic delivery is not certified before it occurs.

No repository-wide permissions were broadened. Receiver release remains
**NO_GO**; no receiver action, plugin release or canonical `mena-data` update is
part of this trigger cleanup.
