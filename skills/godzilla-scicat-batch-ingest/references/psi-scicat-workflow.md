# PSI SciCat workflow reference

## Scope and data model

This skill targets a standard LumaCam proposal tree at any PSI instrument:

```text
<proposal-root>/
└── data/
    └── experiment/ or experiments/
        ├── tpx3Files/*.tar.gz
        └── metadata/<experiment>/experiment.json
```

Each verified raw `.tar.gz` is one raw SciCat dataset. The optional remaining
dataset is a single derived dataset whose `inputDatasets` lists every raw PID.
SciCat documents `inputDatasets` as provenance links from derived data to its
inputs:

- https://www.scicatproject.org/documentation/Development/v4.x/Data_Model.html
- https://www.scicatproject.org/scitacean/generated/classes/scitacean.model.UploadDataset.html

PSI recommends datasets between roughly 1 GB and 1 TB. The documented hard
limits are 50 TB and 400,000 files. The helper warns outside the recommended
range and refuses hard-limit violations.

PSI ingestor manual:
https://data-catalog-services.pages.psi.ch/ingestorManual/

## Proposal identifiers and groups

The visible proposal number, full proposal PID, and owner group are different:

- Human proposal number: `P########`
- SciCat proposal PID: normally `20.500.11935/########`
- Owner group: a PSI access group such as `p#####`

Always query the proposal record. Do not assume the owner group is derived from
the proposal number or that the logged-in user is the main proposer/PI. The user
must belong to the returned owner group and be able to see the proposal.

Discover `creationLocation` from `MeasurementPeriodList.instrument`. If the
proposal contains zero or multiple distinct instrument paths, require an
explicit location instead of defaulting to BOA.

## What each phase proves

1. `prepare`
   - Locates the standard layout.
   - Queries proposal PID, owner group, and instrument.
   - Freezes raw archive size/mtime and actual run time range.
   - For remaining-only, proves every local raw archive has one matching raw
     SciCat dataset already confirmed on tape.
   - Writes metadata, file lists, and a plan outside the proposal root.
2. `validate`
   - Rechecks immutable size/mtime inventory.
   - Runs `datasetIngestor` without `--ingest` for new datasets.
   - Makes no SciCat mutations.
   - Records the exact validated plan hash.
3. `execute`
   - Requires the matching validated plan and typed phase confirmation.
   - Creates one dataset at a time using `--copy --ingest`.
   - If the initial rsync breaks after PID creation, resumes against that PID.
   - Marks the lifecycle archivable only after a successful copy.
   - Submits an archive job with the queried owner group.
   - Stops immediately on failure and does not delete/recreate anything.
4. `status`
   - Performs a single combined read-only PID query and writes a report.
5. `monitor`
   - Performs the same combined query every 600 seconds.
   - Confirms physical archive only when `archiveStatusMessage` is
     `datasetOnArchive` (or historical `datasetOnAchive`) and `retrievable` is
     Boolean `true`.

An archive job UUID proves submission only. `archivable: true` proves files are
ready to be archived, not that tape writing finished.

## Copy recovery layout

For a created PID `PREFIX/UUID` and source folder `/data01/.../tpx3Files`, PSI's
cache destination is:

```text
<username>@pb-archive.psi.ch:archive/UUID/data01/.../tpx3Files
```

The helper uses the generated `filelisting.txt`, `--partial`, and SSH keepalive
options. It first requires a manually verified host key in `known_hosts` and a
valid Kerberos ticket. Never accept a new host fingerprint automatically.

## Remaining-content policy

Include all regular files below the proposal root except:

- the complete `data/experiment[s]/tpx3Files` subtree;
- `.trash`, `.work`, common cache directories, `tmp`, and `temp` directories;
- temporary, partial, editor-swap, and lock files;
- empty `.gitkeep` placeholders.

Reject symbolic links for review because their target may lie outside the
proposal or change independently. The inventory stores relative path, size, and
nanosecond mtime. Any change invalidates the plan.

The derived name is
`P########_remaining_processed_and_supporting_content_vNNN`. Query existing
datasets and increment `vNNN`. If analysis produces additional files later,
create another version rather than editing an archived dataset.

## Request policy and errors

No numeric API quota is published in the PSI ingestion manual or official
SciCat documentation reviewed for this workflow. The helper therefore uses a
conservative cross-process ceiling of one direct API request per second. This
ceiling does not throttle rsync. Monitoring uses one combined request for all
PIDs every ten minutes.

- HTTP 429 raises a dedicated error and stops; retry later.
- HTTP 5xx and network failures use bounded exponential backoff.
- Other HTTP failures include the response body and stop immediately.
- The token is read with hidden input and is never persisted.

## Production checklist

- Correct proposal root and chosen mode.
- Raw `.tar.gz` files already verified by the archive skill.
- Plan/control directory outside the proposal root.
- Proposal PID, owner group, and instrument reviewed.
- Every dry run passed after the final plan change.
- Kerberos ticket valid; archive host fingerprint previously verified.
- No other rsync to the archive host is running.
- Exact phase confirmation entered.
- Submission report saved.
- Tape monitor completed for every PID in scope.
