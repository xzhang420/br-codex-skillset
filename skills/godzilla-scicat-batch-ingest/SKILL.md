---
name: godzilla-scicat-batch-ingest
description: >-
  Prepare, validate, ingest, recover, archive, and monitor standard
  LumaCam/Timepix3 proposal datasets from the Godzilla workstation into PSI
  SciCat. Use for raw-only, remaining-only, or complete proposal workflows at
  any PSI instrument, including later archival of processed/supporting content.
---

# Godzilla SciCat Batch Ingest

Use this workflow only for a standard LumaCam proposal with
`data/experiment/tpx3Files` or `data/experiments/tpx3Files`. It does not infer
arbitrary instrument-specific directory layouts.

## Start every workflow

Ask the user to choose exactly one scope, even if a previous proposal used a
different choice:

- `all`: compressed raw TPX3 datasets first, wait for tape confirmation, then
  one remaining-content derived dataset.
- `raw`: compressed raw TPX3 datasets only.
- `remaining`: one non-raw derived dataset for a proposal whose raw datasets
  are already confirmed on tape.

Also identify the proposal root. Discover the proposal PID, owner group,
instrument location, raw count, PIDs, archive jobs, PSI username, and dataset
version at runtime. Never copy a proposal number, owner group, instrument,
dataset PID, or job ID from an earlier proposal into a new plan.

If immediate experiment directories still need verified `.tar.gz` archives,
use `godzilla-archive-tpx3-experiments` first. Do not modify the standalone
`/home/localadmin/Programs/scicat_batch_ingest` process while it is running.

If verified raw archives were moved to a separate disk, keep `--proposal-root`
pointed at the canonical proposal tree so its metadata remains authoritative,
and pass the archive folder with `--raw-source-dir`. Do not copy multi-terabyte
archives back merely to recreate the standard layout. Explicit split archive
names such as `exp004_part1_00000-00035.tar.gz` map to only that run range in
the base experiment metadata.

## Credential and safety rules

- Request the SciCat token through hidden terminal input. Keep it in memory
  only; never paste it into chat, command logs, plans, shell history, or files.
- Confirm Kerberos with `klist`; credentials are independent of the current
  directory. Use the token username for `<username>@D.PSI.CH` unless the user
  supplies another principal.
- Never delete source data or SciCat records. Never recreate a failed dataset.
  Stop on the first ingest, copy, lifecycle, or archive-submission error; retain
  the plan, state, PID, and logs, inspect, then resume the same plan.
- Preserve actual acquisition timestamps from each `experiment.json`. Do not
  edit the official proposal schedule to make those times match.
- For an interrupted acquisition that has `started_at` but no `ended_at`, the
  helper may derive `endTime` from the latest `.tpx3` member mtime inside the
  verified archive. It must record this explicitly as a plan warning; never
  substitute the planned duration or official proposal schedule.
- Treat archive-job submission as incomplete. Physical tape completion requires
  lifecycle `archiveStatusMessage` `datasetOnArchive` (or PSI's historical
  `datasetOnAchive`) and `retrievable: true`.
- Direct API requests have a conservative ceiling of one per second. Monitoring
  uses one combined PID query every 600 seconds. This is a client safeguard, not
  a published PSI quota.

Read [references/psi-scicat-workflow.md](references/psi-scicat-workflow.md) when
preparing or executing a production workflow, diagnosing a partial transfer, or
interpreting lifecycle state.

## Run the helper

Use `uv run`; do not invoke the script with `python` or `python3`.

```bash
SKILL_DIR=/home/localadmin/dev/br-codex-skillset/skills/godzilla-scicat-batch-ingest
PLAN=/data01/scicat_ingest/P########/batch_plan.json

UV_CACHE_DIR=/tmp/uv-cache-godzilla-scicat uv run \
  "$SKILL_DIR/scripts/scicat_batch_ingest.py" prepare \
  --mode raw \
  --proposal-root /data01/<proposal-folder> \
  --output "$PLAN"
```

Replace `raw` with the user's chosen `all` or `remaining`. Keep control files
under `/data01/scicat_ingest/P########`, outside the proposal root.
For raw archives stored elsewhere, add
`--raw-source-dir /media/.../tpx3Files_..._P########` to `prepare`.

Validate without changing SciCat:

```bash
UV_CACHE_DIR=/tmp/uv-cache-godzilla-scicat uv run \
  "$SKILL_DIR/scripts/scicat_batch_ingest.py" validate \
  --plan "$PLAN" \
  --output "${PLAN%/*}/validation_report.json"
```

Summarize the plan, sizes, counts, warnings, existing datasets, exclusions, and
report path. Only after every dry run passes, let the helper request the exact
production confirmation:

```bash
UV_CACHE_DIR=/tmp/uv-cache-godzilla-scicat uv run \
  "$SKILL_DIR/scripts/scicat_batch_ingest.py" execute \
  --plan "$PLAN" \
  --output "${PLAN%/*}/execution_report.json"
```

The terminal shows live rsync progress by replacing one status line instead of
printing a new line every second; full rsync/scicat-cli details go into
per-dataset log files. At successful completion it prints a short proposal,
dataset-count, size, elapsed-time, report-path, and tape-pending summary. A
successful `execute` means jobs were submitted, not that data are on tape.

For a one-shot check:

```bash
UV_CACHE_DIR=/tmp/uv-cache-godzilla-scicat uv run \
  "$SKILL_DIR/scripts/scicat_batch_ingest.py" status \
  --plan "$PLAN" \
  --output "${PLAN%/*}/status.json"
```

For confirmation monitoring, use the agreed ten-minute interval. Choose and
state an explicit timeout; `0` means no deadline:

```bash
UV_CACHE_DIR=/tmp/uv-cache-godzilla-scicat uv run \
  "$SKILL_DIR/scripts/scicat_batch_ingest.py" monitor \
  --plan "$PLAN" \
  --output "${PLAN%/*}/monitor.json" \
  --interval-seconds 600 \
  --timeout-seconds 0
```

## Complete mode transition

For `all`, the first `validate`/`execute` cycle handles raw datasets. Monitor
until all raw datasets are confirmed on tape. Then run `validate` again: it
creates the frozen remaining-content inventory, links every raw PID through
`inputDatasets`, chooses the next `vNNN` name, and dry-runs the derived dataset.
Run `execute` again and accept the separate `P######## REMAINING` confirmation.
Monitor again until that derived dataset is also on tape.

For `remaining`, `prepare` first proves that every local raw archive maps to one
matching SciCat raw dataset already on tape. It then creates the same derived
inventory immediately.

The remaining dataset includes proposal content except raw `tpx3Files` archives
and transient `.trash`, `.work`, cache, temp/partial/lock files, and empty
`.gitkeep` placeholders. It is one dataset. Symbolic links require manual review.
If processed files appear later, prepare a new `vNNN` derived dataset; never
modify an archived dataset.

## Finish criteria

Report completion only when every dataset in the selected scope has both the
on-archive lifecycle state and `retrievable: true`. Include the plan, state,
validation/execution/monitor reports, per-dataset PIDs, and any archive job IDs.
If only submission succeeded, say exactly that and direct the user to `monitor`.
