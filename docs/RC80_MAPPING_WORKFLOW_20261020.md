# RC80 Mapping workflow: qualified candidate preparation

The owner approved the Mapping usability study and has continuing authorization in place for necessary EPGManager work, with mappings/settings/programmes protected. This follow-up implements the accepted study without changing ownership/scoring/guide-quality policy.

## Changes

- More has five ordinary actions: Search current reception, Filter channels, Change EPG guide, Undo last change, Advanced tools. Pending replacement can still be cancelled. Native ChoiceBox numbering is no longer duplicated in labels.
- Existing filters are centralized. The four new ordinary views remain; Other filters retains mapped/unmapped/conflicts/ignored/auto/legacy mapping-issue views. The old attention-menu entry redirects to the same filter menu.
- Apply, remove and ignore open a preview/confirmation defaulting to No. Apply shows old/new source/ID and affected-service count. Changing channel, candidate, mapping revision or linked-service scope before confirmation refuses dispatch. Existing mutation bodies are preserved byte-for-byte behind these wrappers.
- Removal/ignore/reset are in Advanced; removal is no longer the first ordinary menu action. Full-reset confirmation defaults to No. Failure to open the reset menu shows an error rather than falling through to the broader reset dialog.
- Candidate percentages say Match. The green button in candidate focus says Apply selected guide, including explicit linked-service change mode. Existing optional Auto Mapping remains unchanged elsewhere.
- INFO gives compact guide details: match score, separate guide status, source/language, last source event ending in GMT+0 and the stored ranking reason. Missing scores are Not scored. Missing/expired guide horizons are Unknown. No claim of continuous EPG coverage or measured gaps is made. The previous technical diagnostics are retained in Advanced.
- The cached-health helper adds only a read-only snapshot API. Existing health classification, readiness gates and refresh/fetch functions are unchanged; no new HTTP requests, timers, imports or programme synthesis.
- Review rows show a reason (unmapped, unknown guide, conflict, excluded, degraded/suspect guide). Other views retain their normal current-programme subtitle. Global counters explicitly say Receiver and unknown ID totals do not appear as 0/0.

## Verification before deployment

17 new unit tests pass for safe menus, preview/no-action cancellation, accepted/stale context dispatch, unknown/expired/malformed guide horizons, report immutability and explanatory text. Actual candidate AST/render checks verify original mutation bodies and technical diagnostics preserved, every existing health function unchanged, review/default subtitle rendering, four green-button cases, Match labels and a failed reset-menu opening that cannot reach reset logic.

The IPK changes only ui/channel_mapping.py and core/github_match_health.py. All other 215 application files/modes, native importer, scheduler, source priority and mapping storage are unchanged. Version 2026.10.20-rc80/schema 15/142 Python modules/75 resources remain. Package consent, exact baseline guard, verified backup and passive removal hooks are retained. Actual owner mappings must not be changed during GUI validation: open/cancel confirmations rather than executing apply/remove/ignore/reset/undo.

Linux Python 3.13/3.14 CI and actual read-only preflight must pass before installation. Actual native GUI evidence is still pending at this preparation stage.
