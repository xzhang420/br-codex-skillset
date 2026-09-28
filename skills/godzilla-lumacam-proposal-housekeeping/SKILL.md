---
name: godzilla-lumacam-proposal-housekeeping
description: Audit LumaCam/NCI proposal folders for empty or unfinished runs and experiments, report sizes, delete only after the user confirms the reviewed targets, and group test/focus experiments consistently across all data sections under test_focus. Use for proposal housekeeping or cleanup on godzilla.
---

# LumaCam Proposal Housekeeping

Keep the existing experiment-removal and TPX3-archiving skills available independently. This skill adds proposal-wide discovery, per-run cleanup, and consistent grouping. Creating or editing this skill is not permission to clean a proposal.

## Audit first

Resolve the exact proposal root; accept exactly one `data/experiment` or `data/experiments`. Do not rename the layout. Run the bundled **read-only** auditor:

```bash
python3 <skill-dir>/scripts/audit_proposal.py /absolute/proposal
python3 <skill-dir>/scripts/audit_proposal.py /absolute/proposal --json --inventory > /tmp/proposal-housekeeping-before.json
```

Use a unique local report filename per proposal/session; keep reports out of the shared skill repository. The JSON includes every run, exact paths, section sizes, archive paths, proposed grouping moves, and optional per-entry stat snapshots. The human-readable output lists all experiments and runs needing review. Inspect errors and unmapped-entry warnings; never treat an incomplete scan as an empty directory.

The auditor discovers identities in `.work`, `derived`, `final`, `logs`, `metadata`, `tpx3Files`, `rawFiles`, `photonFiles`, and `eventFiles`, then includes exact identities in other immediate sections. It handles both ungrouped experiments and `<section>/test_focus/<experiment>`. Preserve shared directories such as `logs/serval`, `logs/monitor`, `.work/batch_focus_cache`, `.trash`, and section-level `.gitkeep` files. Resolve unfamiliar layouts or unmapped experiment-owned artifacts before claiming all associated data is covered. Do not move/delete unmatched entries by guessing a prefix.

Interpret evidence conservatively:

- `completed` and `processed` are successful statuses. `acquired` means acquisition succeeded but processing is unfinished; these runs may contain valuable data.
- Other recognized incomplete statuses are listed in the helper's `UNFINISHED` set. Unknown or missing/invalid manifests mean **unknown**, not failed. Cross-check per-run `acquisition.json`, exposure manifests, and logs when experiment metadata is inconsistent or unavailable; report the source of any revised conclusion.
- `no_local_payload` means no nonzero file outside `metadata`/`logs`, ignoring `.gitkeep`. It is a review flag, not proof of a useless acquisition. `.work` incoming/recovery files count as data. Nonzero log/metadata files still contribute to deletion sizes. Unknown custom sections count conservatively as payload.
- `zero_byte_tree` describes the audited filesystem tree, not scientific validity. Missing planned runs with no paths are reportable but offer nothing to delete.
- A completed run without local payload is inconsistent or migrated/archived, not an empty-run deletion candidate. Do not classify emptiness from `tpx3Files` alone. Experiment archives can hide run data; the helper does not inspect archive contents or attribute archive bytes to runs. Check external/migrated data if evidence points there.
- An experiment containing unfinished runs can also contain completed runs. Preserve completed runs by default; never translate a request to delete unfinished runs into deletion of their parent experiment. Explicit whole-experiment confirmation must disclose the completed runs and their data.

Present a review table with exact experiment/run IDs, manifest status and evidence, reason flagged, completed/expected counts, file counts, apparent bytes, allocated bytes, and recent activity. Include section sizes and exact targets for the proposed deletions. Separate empty/no-data, unfinished-with-data, and unknown/inconsistent candidates. Experiment totals include their run totals: do not sum both. Hard links, sparse files, and snapshots mean allocated sizes are estimates, not promised recovered space. Keep test/focus grouping in a separate move list.

Before any mutation, inspect global processes read-only for acquisition, reconstruction, aggregation, recovery, and archival work involving the proposal/targets. Command-line matching alone is insufficient when scripts use a configured root: inspect their cwd/open files/configured roots as needed. Exclude active targets and recent writes (default audit window: 10 minutes). Never stop acquisition on the user's behalf without authorization. A stale `running` status is evidence to investigate, not proof a process still runs.

## Delete only after confirmation

**Never delete during the initial audit. Show the exact proposed deletion list and sizes, ask the user to confirm that list or a subset, then end the turn.** An initial cleanup request or permission to group folders is not deletion confirmation. Suggested reply: `DELETE the listed targets for <proposal>` or an explicit list of experiment/run IDs. A clear equivalent confirmation is sufficient; ambiguity must be resolved. No timeout, silence, or prior skill-creation request counts as confirmation.

After confirmation:

1. Re-audit and repeat activity checks immediately. Compare the confirmed targets' identities, paths, manifests, file lists, sizes, inode/device, and mtime/ctime snapshots. Changed targets, new files, newly discovered archives, or changed activity require a new report and confirmation. Changes only in unrelated experiments do not invalidate the selected scope. Pause for filesystem errors, symlinks (including ancestor paths), special files, unsafe mounts, or incomplete ownership mapping.
2. Derive a concrete list of exact absolute paths from the reviewed report. Reject paths outside the selected layout, the layout/section/group roots, shared folders, symlinks, and parent/child duplicates. Validate every target before deleting any. Keep a durable copy of the reviewed selection and necessary manifest provenance outside the deletion targets; do not imply that a manifest copy backs up the data.
3. For a **run**, remove its exact run directories across every section and its individually attributed final/derived files, including files under image variants. Never delete an image-variant directory, the experiment's `experiment.json`, or experiment-wide sums/aggregation metadata merely because one run is selected. Do not rewrite acquisition history to pretend deleted runs never existed. Record the cleanup in a separate proposal-local housekeeping journal. Existing experiment sums may include deleted runs: preserve and flag them for separate review/recomputation.
4. For an **experiment**, remove only its exact experiment directories and explicitly listed experiment-owned archives across all sections, including grouped locations. A subset of runs inside an archive cannot be deleted by removing the entire archive; archive rewriting is outside this workflow and requires a separately reviewed plan. `godzilla-remove-lumacam-experiment` remains suitable for a single ungrouped experiment in its supported layout; its legacy auditor does not cover `test_focus`, so do not use it blindly on grouped experiments.
5. Execute only the confirmed list using explicit paths (Python path operations or commands with `--`; no shell globs). Stop on the first failure, record completed/pending operations, and re-audit before any retry. Do not recursively clean parent folders after deleting selected runs.
6. Verify the selected paths are absent and non-target/completed runs remain. Report removed targets, pre-deletion sizes, failures, remaining candidates, and any aggregates needing review. Permanent deletion has no undo without backups/snapshots.

The bundled helper is deliberately audit-only and has no deletion switch; Codex performs the reviewed exact operations only after this confirmation boundary.

## Group test and focus experiments

Match experiment names containing **`test` OR `focus`**, case-insensitively. Show literal matches for review; do not require both words or infer a test from run contents. Keep the original experiment/run names. Place the grouping folder **inside every data section**:

```text
data/experiment/final/test_focus/<experiment>/...
data/experiment/tpx3Files/test_focus/<experiment>/...
data/experiment/metadata/test_focus/<experiment>/...
data/experiment/logs/test_focus/<experiment>/...
data/experiment/.work/test_focus/<experiment>/...
```

Do the same for `derived`, `rawFiles`, `photonFiles`, `eventFiles`, and any discovered section with exact experiment-owned entries. Move entire experiment trees, preserving all metadata, run names, TIFF variants, summed outputs, and hidden files. Move identified experiment archive files alongside their experiment folders into that section's `test_focus`; preserve archive names and internal contents. Investigate unmapped split archives or sidecars before proceeding. Do not create unused section trees or nest `test_focus/test_focus`.

If the user requested grouping/housekeeping, that authorizes grouping after presenting the concrete move plan and satisfying checks; **it never authorizes deletion**. If the user asked only for an audit or deletion, report grouping candidates without executing moves. For a combined request, present deletion and grouping plans together, finish the deletion-confirmation turn first, then apply the confirmed deletions and re-audit before grouping. Deletion approval must remain tied to the audited paths; never reuse a pre-move deletion list after grouping.

Before moving, ensure all selected experiments are inactive and stable; identify notebooks, processing/ingestion configuration, and manifests that refer to their old paths. Grouping changes paths expected by flat-layout acquisition/recovery/ingestion tools. Explain concrete affected consumers; keep original manifest experiment/run IDs. Do not blindly replace strings in historical metadata or create compatibility symlinks. If active consumers need the original layout, defer those experiments until resolved. Include any requested config changes as explicit reviewed edits.

Use `group_moves` from a fresh JSON audit to construct an exact move list, with source/destination sizes. Validate the entire list before the first move. Refuse existing destinations, merges, overwrites, symlinked parents, ambiguous duplicates, and cross-filesystem moves. Confirm parent directories reside on the source filesystem before using rename. Create only necessary `test_focus` directories; use a no-overwrite rename operation (on Linux, `renameat2` with `RENAME_NOREPLACE`) or a reviewed equivalent that cannot clobber a newly appearing destination. A plain check followed by `os.rename` is not a no-overwrite guarantee.

Keep a durable proposal-local move journal recording source, destination, pre-move inventory, and each successful move so a partial failure can be reconciled. Stop on failure; do not merge or retry blindly. An interrupted grouping may have sections already moved and others pending: re-audit, compare inventories, and move only the verified remaining sources. Existing source and destination for the same experiment/section is a conflict, never an invitation to merge.

After grouping, verify each source is absent, destination inventories/file counts/bytes match (allow directory ctime changes caused by rename), all data sections agree on placement, and nonmatching experiments are unchanged. Re-running grouping should yield no further moves for completed targets. Report exact grouped experiments, total files/bytes, any skipped targets, and affected downstream paths.

For compression rather than grouping, use `godzilla-archive-tpx3-experiments` and its archive verification workflow. Do not compress or remove TPX3 sources as an implicit housekeeping step.
