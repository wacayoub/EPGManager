# RC80 Mapping workflow

**Current acceptance: installed under owner authorization and actual receiver GUI tested. Five-action More menu, direct search, review reasons, INFO guide details and cancel-safe confirmations passed. All six protected mapping/preferences hashes and all EPGManager settings are unchanged; all 482 sampled nonexpired programmes survive. GO for this receiver.** [Installed evidence](evidence/rc80-mapping-workflow-installed-20261020.json).

Preparation statements below retain the chronology and are superseded by the installed acceptance at the end.

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

## Installed and native GUI validated

The accepted study was implemented under the owner's continuing authorization, without another routine approval question. The exact **704394-byte** IPK, SHA256 `99514e7c92ca6333931f469d03577beda5f5b73c890e3beca960adfc9d41cdbd`, was installed with opkg --force-reinstall, exit 0. Its read-back-verified backup preceded changes; all 15 protected configuration hashes were identical across installation. One Enigma2 restart recovered in **48.04 s**, with the same receiver boot ID, changed Enigma2 PID and three stable API samples. All 217 application file bytes/modes match the candidate; 142 modules/schema 15, Python 3.14.7, EPGImport present.

Actual native GUI evidence was inspected privately:

- More shows exactly five ordinary actions, with Search first and no remove/reset action. Search opened its native keyboard and was cancelled; the existing query algorithm was not requalified with a new text search.
- Review showed explanatory subtitles for both unmapped and mapped-but-unknown guides. Other filters retained the legacy views in one place. Return-All restored 574 services and ordinary NOW subtitles.
- Candidate rows say Match; the candidate-focused green button says Apply selected guide. INFO showed a separate match score, confirmed guide state, source language and a real future event ending in GMT+0, with the explicit statement that continuous coverage/gaps are not measured.
- OK opened a one-service old/new-guide preview with No selected; cancelled. Advanced removal also opened a confirmation with No selected; No was chosen. Ignoring an unmapped service similarly required confirmation and No was chosen.
- The this-channel/all-linked change submenu opened and was cancelled. No live apply/remove/ignore/reset/undo or linked-service mutation was executed. Positive dispatch, stale-context refusal and multi-service preview counts are fixture tests; the original mutation bodies remain unchanged.

Preservation: **six protected mapping/preferences/native-setting files retain their before-install hashes**, and no EPGManager setting key changed. Source metadata receipts, activity, and registry/view-cache signature/date fields update normally; full runtime 15-file equality is not claimed. **482/482 nonexpired event fingerprints on 32 sampled services remain**, zero query errors. Final API response samples: 0.084/0.063/0.061 s. This is not a claimed UI speed improvement. Receiver left on the dashboard.

[Linux CI](https://github.com/wacayoub/EPGManager/actions/runs/39409998191) of code commit `ba17641fa296f82d9c76532d0388dcd782da6579` passes **277 suite + 4 probe tests on each Python 3.13 and 3.14**, zero failures/skips; both logs inspected. Existing health classification/fetch functions, native importer, scheduler, source priorities, mapping storage and all 215 other application files/modes are unchanged. No new native import, programme synthesis, IPToSatPro/IPAudio modification, public release or merge occurred. **GO for this installed Mapping workflow on the qualified receiver.**
