---
name: godzilla-archive-tpx3-experiments
description: Safely audit and archive immediate LumaCam/NCI experiment directories under a tpx3Files folder as one verified .tar.gz per experiment, with exact target selection, recent-write checks, full archive read-back, and source deletion only after verification. Use when asked to compress, archive, zip, or free space from TPX3 experiment folders on the godzilla workstation, including while excluding an ongoing acquisition.
---

# Archive TPX3 Experiment Folders

Use `scripts/archive_tpx3_experiments.sh` for deterministic audit and execution. Treat source deletion as permanent.

## Workflow

1. Resolve the absolute `tpx3Files` root and enumerate its immediate directories, existing archives, partial archives, sizes, file counts, and newest writes.
2. Resolve an exact inclusion list. Never use globs or partial experiment names. Explicitly omit ongoing or user-excluded experiments.
3. Run the bundled script without `--execute`. Report empty/nearly empty folders, recent activity, source sizes, archive conflicts, and free space before changing data.
4. Stop for user direction when a folder is empty or scientifically suspicious. For removal of an empty experiment across the proposal layout, use `godzilla-remove-lumacam-experiment`; do not remove only its `tpx3Files` directory.
5. Refuse any source grouping over 1,000,000,000,000 bytes. Split it only after reviewing the immediate run-directory boundaries as described below.
6. After the user approves compression, launch the script detached with reduced CPU and I/O priority. Each source is deleted only after the archive is synced, fully read back with `tar -tzf`, and its entry count and source metrics still match.
7. Monitor the log until `ALL COMPLETE`. Report every `ARCHIVE COMPLETE`, error, remaining source, partial archive, final archive, and released space.

## Audit

Run with exact immediate directory names:

```bash
<skill-dir>/scripts/archive_tpx3_experiments.sh \
  --root /absolute/proposal/data/experiments/tpx3Files \
  -- experiment_one experiment_two
```

The default mode is read-only. It refuses symlinked roots or targets, paths outside the immediate root, empty execution targets, archive conflicts, recent writes, and oversized sources.

## Execute

Choose priorities according to acquisition state:

- No live acquisition on the filesystem: `ionice -c 2 -n 7 nice -n 10`, normally 16 `pigz` threads.
- Live acquisition on the same filesystem: exclude it by exact name and use `ionice -c 3 nice -n 15`, normally 8 threads. Warn that compression can still reduce acquisition headroom.

Example:

```bash
screen -dmS tpx3_archive_job bash -lc \
  'exec ionice -c 2 -n 7 nice -n 10 <skill-dir>/scripts/archive_tpx3_experiments.sh \
    --execute --threads 16 \
    --root /absolute/proposal/data/experiments/tpx3Files \
    --log /absolute/proposal/compression_tpx3_experiments.log \
    -- experiment_one experiment_two'
```

Use `--replace-partials` only after confirming no archive process owns the stale partial. Never move a newly appearing final archive until its `ARCHIVE COMPLETE` line is present.

During `VERIFY START`, the `.tar.gz.partial` file stops growing while the complete archive is decompressed and listed. This is expected. Do not interrupt it merely because its size is stationary.

## Oversized experiments

Keep each source grouping at or below 1 TB for PSI/SciCat handling:

1. Require that the oversized experiment contains only immediate run directories and no loose files.
2. Sort run directories chronologically by their fixed-width names.
3. Partition them into contiguous groups whose uncompressed file totals are each at most 1,000,000,000,000 bytes.
4. Name archives with explicit part and run ranges, for example `exp004_part1_00000-00035.tar.gz`.
5. Store original parent/run paths inside every archive so extracting all parts reconstructs the original experiment directory.
6. Verify and sync each part before deleting only the run directories contained in that part. Remove the experiment parent with `rmdir` only after all parts pass and it is empty.

Use a reviewed proposal-specific wrapper for split execution; the bundled exact-folder script deliberately refuses oversized experiments rather than guessing scientific boundaries.

## Safety invariants

- Preserve `.gitkeep`, proposal documentation, metadata, logs, software, processing parameters, and every non-target experiment.
- Archive all files inside a selected experiment directory; never alter `.tpx3` contents.
- Do not infer that a folder is disposable from size alone.
- Keep source data whenever creation, sync, verification, or consistency checks fail.
- Treat the final archive and deleted source as recoverable only from backups or filesystem snapshots.
