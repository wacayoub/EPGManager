# Source clock on the Vu+

The receiver checks the configured Africa/Casablanca targets every minute.
It sends a small Git request to the dedicated `vuplus-clock` branch when a task
is due. GitHub then runs the existing collector from `main`.
The 14 provider collections, postprocessing at 08:37 and watchdog at 14:15 use
the targets in `config/source-schedules.json`.

The native GitHub schedules provide fallback delivery. The source workflow
checks the committed publication-attempt receipt inside its existing
per-source concurrency group. A PUBLISH or KEEP_LKG attempt already recorded
on the local day causes a scheduled duplicate to skip collection.
An explicit manual refresh still runs. Exact publication verification runs
when the clock actually collects a source.

## Receiver installation

The managed directory is `/etc/epgmanager-clock`. It contains the controller,
its policy copy, workflow template, transport runtime, state and dedicated
repository deploy key. Its root-only key grants access to EPGManager.
The SSH host keys are pinned to GitHub's published keys.
The pure-Python Git runtime is pinned by URL and SHA-256 in
`config/vuplus-clock-runtime.json`.

The managed crontab entry runs `vuplus_clock.py tick` every minute.
The controller serializes concurrent ticks and sends at most one due task per
tick. Successful delivery is recorded atomically. A failed delivery retries on
the next tick. A deterministic commit makes a retry of the same task idempotent.
After downtime, unsent tasks for the current Morocco day are dispatched in
target-time order.

Read `state.json` to inspect `last_tick_utc`, `last_delivery`, `last_error`
and the tasks accepted today. A policy or workflow-template change should be
deployed to this directory along with its matching controller.

The controller starts GitHub jobs independently of the Enigma2 GUI. A receiver
shutdown or network failure delays dispatch. GitHub can also delay processing a
push event. The recorded request and run timestamps expose these delays.

## Measured scheduling fault

On 4–6 November, the native GitHub scheduler created source runs hours after
their configured targets. Collection then began in approximately 15–60 seconds.
For example, AlKass's 6 November target was 00:17 and its run was created at
06:55. OSN's target was 01:40 and its run was created at 08:28.
Morocco's 5 November target was 07:07 and its run was created at 14:32.

The trigger audit now distinguishes the primary clock from native fallback,
reports trigger delay separately from setup time, and flags the latest due
request when it has not been observed. A historical successful run cannot hide
a missed current target.
