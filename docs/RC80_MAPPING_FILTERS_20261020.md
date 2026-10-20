# RC80 Mapping filters: prepared, not installed

The owner requested easier filtering after the readable UI installation. Receiver interaction in this follow-up was read-only: the installed Mapping SHA256 matches the pinned readable UI baseline. No mappings, settings, programme data or installed code changed.

## Interface

BLUE / More → Filter channels opens four choices for the current reception list:

- All channels: restores the full original reception view.
- No future EPG: includes only the active owner's freshly confirmed NO_EPG state in resident GitHub health reports. Missing/expired/failed/unsupported reports are unknown, never empty. This concerns source guide availability; existing receiver programmes may still be present.
- Needs review: unmapped, conflicted, ignored or primary-source health other than EPG_OK. Uncertainty is not treated as a reliable guide.
- Manual mappings: exposes the existing locked/manual predicate directly, without changing lock semantics.

The filter name remains in the receiver-column heading. The selected service is kept if still visible; otherwise selection safely falls back to the first row. Empty lists can return to All. Filtering is an in-memory view, not a saved owner preference. The health snapshot is evaluated when applying the filter or refreshing the reception view; reapply to consume newly warmed reports. No new data download, programme scan, report-refresh timer or storage write is introduced by these predicates.

## Review and verification

Only ui/channel_mapping.py changes. The other 216 application files/modes, native importer, scheduler, theme, mapping storage/save/undo logic and source priorities remain identical. Existing legacy filters retain their semantics. The row cache is bypassed for health-dependent views so a freshly applied filter does not reuse an obsolete row set.

Ten unit tests cover confirmed-empty versus unknown/healthy/degraded/suspect/generated states, active-owner lookup, missing/error data, read-only menu choices, cancellation, source pinning and conservative review on backend lookup errors. Actual candidate refresh_channels/set_channel_filter methods were exercised with synthetic services: expected membership, selection retention, empty view and return-All passed. AST comparison confirms all other methods unchanged. Reproduce: `python tools/audit_rc80_mapping_filters.py <private-staging-directory>`.

The consolidated package retains 142 Python modules, 75 resources, schema 15, and version 2026.10.20-rc80. It preserves package-specific consent, verified backup, exact baseline checks and passive removal hooks. No import/restart occurs in its hooks. Linux Python 3.13/3.14 CI and read-only package preflight must pass before seeking specific installation/restart approval. Actual native GUI interaction of these new filters remains NOT TESTED until an approved deployment.

The actual receiver read-only package preflight passes (exit 0); missing consent refuses installation. The isolated synthetic mode/selection/empty-view tests also pass in memory on receiver Python 3.14.7. This does not certify native GUI interaction or real-owner filter counts. No receiver file writes occurred.
