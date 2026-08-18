---
name: godzilla-remove-lumacam-experiment
description: Safely audit and remove all data belonging to one LumaCam/NCI experiment across the proposal data-layout subfolders. Use when a user asks to delete, remove, clean, reset, or start over an NCI experiment or experiment ID. Always report completed-run counts, file counts, and total size, then end the turn and require a new explicit confirmation before permanent deletion.
---

# Remove NCI Experiment Data

Use a strict two-turn workflow. Never combine the audit and deletion in one turn.

## 1. Resolve and audit

1. Resolve the proposal root and the exact experiment directory name. Reject partial, glob, or ambiguous matches.
2. Check global processes read-only for the exact experiment name. If acquisition, reconstruction, aggregation, or recovery is active, do not delete; report the process and require the user to stop it safely first.
3. Run the bundled read-only auditor:

   ```bash
   python3 <skill-dir>/scripts/audit_nci_experiment.py <proposal-root> <exact-experiment-name>
   ```

4. Report:

   - exact proposal and experiment;
   - completed runs versus expected runs, plus all other manifest status counts;
   - each matched layout directory;
   - combined file count, apparent size, and allocated size;
   - whether the experiment appears active.

If the manifest is absent or invalid, state that the completed-run count is unavailable. Do not silently infer it from filenames.

## 2. Request confirmation and stop

Ask the user to reply with `REMOVE <exact-experiment-name>` to confirm permanent deletion. End the turn without deleting anything.

The initial deletion request never counts as this confirmation, even if it says “delete now,” “remove it,” or similar. A commentary update is not a confirmation boundary.

## 3. Recheck after confirmation

After receiving the new confirmation:

1. Require the exact experiment name in the confirmation. Resolve ambiguity before proceeding.
2. Repeat the process check and audit immediately.
3. If the experiment is active, the target list changed, new files appeared, or the size increased materially, stop and present the new audit for confirmation again.
4. Refuse to follow symlinked experiment targets or paths outside the recognized proposal layout.

## 4. Delete exact targets

Delete only the exact experiment directories reported by the auditor under recognized sections such as `.work`, `derived`, `final`, `logs`, `metadata`, `tpx3Files`, `rawFiles`, and `photonFiles`.

- Use explicit absolute paths and `--`; never use globs, partial names, unresolved variables, or recursive deletion at the proposal/layout root.
- Do not remove shared locations such as `logs/serval`, proposal documentation, analysis notebooks, or other experiments.
- Request filesystem escalation when required.

## 5. Verify and report

Run the auditor again and verify that no exact experiment targets remain. Report what was removed, the space represented by the pre-deletion audit, whether any matching paths remain, and that recovery requires a backup or filesystem snapshot.
