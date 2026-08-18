---
name: godzilla-lumacam-new-proposal
description: Prepare a new LumaCam/NCI proposal on the acquisition workstation from NCI_PROPOSAL_TEMPLATE. Use when the user starts a new proposal or beamtime campaign and needs to copy the proposal template to /data01, select a new Timepix3 camera calibration directory, configure monitored-acquisition email recipients, update lumacam_measurementcontrol acquisition settings, repoint the TPX3 batch-focus notebook and its proposal-local cache, update every /data01/data_acquisition tpxAcqPhotonTest.py calibration reference, validate paths and processing settings, or perform a safe detector-conflict preflight before the first acquisition.
---

# Prepare a LumaCam Proposal

Collect three required inputs before changing anything:

- the exact proposal directory name, such as `Harris_BOA_July2026_P20260262`;
- the absolute calibration directory containing one `.bpc` file and its matching `.dacs` file;
- one or more recipient email addresses for monitored-acquisition notifications.

Explicitly ask the user for the recipient list even when the monitor already has
addresses configured. Do not reuse recipients from an earlier campaign or infer
them from the proposal contacts. Accept a comma-separated list or repeated
addresses, remove duplicates while preserving order, and require at least one
syntactically valid address.

Do not infer the proposal name or calibration directory from an earlier
campaign. Use the live template and configuration files rather than storing
campaign copies in this skill.

## Run the setup

Use `scripts/setup_lumacam_proposal.py`. Its workstation defaults are:

- repository: `/home/localadmin/Programs/lumacam_measurementcontrol`;
- template: `<repository>/NCI_PROPOSAL_TEMPLATE`;
- proposal parent: `/data01`;
- photon tests: `/data01/data_acquisition`;
- batch-focus notebook: `/home/localadmin/Programs/tpx3cam-analysis/TPX3_batch_focus.ipynb`.

Run a dry run first, then the real setup:

```bash
python3 scripts/setup_lumacam_proposal.py \
  --proposal <proposal-name> \
  --calibration-dir <absolute-calibration-directory> \
  --email-to <address>[,<address>...] \
  --dry-run

python3 scripts/setup_lumacam_proposal.py \
  --proposal <proposal-name> \
  --calibration-dir <absolute-calibration-directory> \
  --email-to <address>[,<address>...]
```

Request filesystem escalation when `/data01` or the shared workstation files are outside the active writable sandbox. A request to start or prepare a new proposal authorizes these scoped writes.

The script must complete all of these together:

1. Validate the safe proposal name, live version-2 template, calibration pair, processing-parameter JSON, acquisition settings, and photon-test files.
2. Refuse to overwrite an existing `/data01/<proposal-name>` directory.
3. Copy the live `NCI_PROPOSAL_TEMPLATE` to `/data01/<proposal-name>`.
4. Set `LUMACAM_PROPOSAL_DIR` in `acquisitionSettings.sh`.
5. Set `config_pixel_path` and `config_dacs_path` in `python/settings_installation.py`.
6. Replace `DEFAULT_EMAIL_TO` in `monitor_dataAcq_sumImages.py` with exactly the requested deduplicated recipient list. This is a workstation-wide monitor default for subsequent acquisitions, not proposal metadata; it intentionally replaces the preceding campaign's list. Do not change the sender address unless the user separately requests it.
7. Update the pixel and DAC calibration URLs in every `tpxAcqPhotonTest.py` below `/data01/data_acquisition`.
8. Set `series_dir` in `/home/localadmin/Programs/tpx3cam-analysis/TPX3_batch_focus.ipynb` to `/data01/<proposal-name>/data/experiments/tpx3Files`. The notebook derives its cache from this path and must therefore resolve it to `/data01/<proposal-name>/data/experiments/.work/batch_focus_cache`, never to the Programs directory.
9. Validate the resulting paths and report the active `parameterSettings.json`; do not change processing thresholds unless the user explicitly requests new values.
10. Report any process holding detector UDP port 8192. Never terminate a process without first identifying the exact owner and obtaining authorization when needed.

If a validation or write fails, report the failing path. Do not substitute an old calibration, redirect acquisition to a fallback directory, or delete experiment data.

## Verify and hand off

Independently confirm that:

- `/data01/<proposal>/data/experiments/metadata/layout.json` exists and declares layout version 2;
- all configured calibration files resolve inside the requested calibration directory;
- `DEFAULT_EMAIL_TO` in `monitor_dataAcq_sumImages.py` contains exactly the requested deduplicated recipients;
- both photon-test copies use the same calibration pair;
- `TPX3_batch_focus.ipynb` points at `/data01/<proposal>/data/experiments/tpx3Files` and its derived cache location is `/data01/<proposal>/data/experiments/.work/batch_focus_cache`;
- `parameterSettings.json` remains the active processing-parameter file;
- no Sophy, Serval, or acquisition process unexpectedly owns UDP port 8192.

If `TPX3_batch_focus.ipynb` was already open while the setup ran, tell the user to reload it from disk before running it.

Test monitored email delivery without starting an acquisition:

```bash
cd -P /home/localadmin/Programs/lumacam_measurementcontrol
./monitor_dataAcq_sumImages.py --send-test-email
```

Report the recipients shown by the monitor and ask the user to confirm that at
least one requested recipient received the test before relying on alerts.

Give the user the physical repository command and a minimal first-run example:

```bash
cd -P /home/localadmin/Programs/lumacam_measurementcontrol
./dataAcq_single.sh 5 test001
```

Also mention that Sophy and `dataAcq_*` cannot control the detector simultaneously. On interruption, preserve `.work`; use the repository recovery workflow for non-empty incoming TPX3 files rather than deleting them.
