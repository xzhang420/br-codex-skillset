---
name: godzilla-nci-operando-processing-workflow
description: Run, debug, and extend downstream NCI (neutron capture imaging) operando processing workflows built around operando_pipeline.ipynb, operando_config.ini, reconstructed NCI TIFF folders, electrochemistry files, and the C:\Software\toolbox\operando Python modules. Use for NCI notebook execution, OBINT/reference/background correction, bad TIFF handling, and potential/thickness/event plots when the user explicitly refers to NCI or neutron capture imaging.
---

# NCI - Operando Processing - Workflow

## Workflow

This skill applies only to NCI, meaning neutron capture imaging, and only after the detector event stream has already been reconstructed into NCI TIFF images or image folders used by the operando notebooks. Do not use this skill for neutron radiography data or conventional transmission radiography workflows. If the task is about acquisition or the upstream event-reconstruction layer, work directly with the TPX3/Lumacam acquisition and reduction scripts instead of using this workflow.

1. Establish the active workspace and experiment before editing:
   - Notebook path, usually `operando_pipeline.ipynb`.
   - Config path, usually `operando_config.ini`.
   - Toolbox modules, commonly `C:\Software\toolbox\operando\operando_data.py`, `operando_gui.py`, and `operando plots.py`.
   - Experiment id such as `exp601`, `exp701`, or `exp702`.
   - Raw data, corrected output, reference image, and electrochemistry file paths.
2. Inspect the notebook GUI setup cell and config together. Prefer updating config values over hard-coding paths in notebook cells.
3. Before changing shared toolbox modules, search for existing helpers and call sites. Preserve backwards-compatible signatures when notebooks may already import those helpers.
4. After code edits, validate with Python syntax/import checks when the local Python environment is available. Tell the user to restart the notebook kernel or rerun the environment/setup cell when imported modules changed.
5. For long-running corrections, distinguish between the code on disk and the already-loaded notebook state. A traceback showing an old call signature usually means the kernel needs a restart.

## Common Tasks

### Experiment Path Setup

- For simple NCI experiment switching, beam-history matching, and path validation before running the notebook, use `godzilla-nci-operando-processing-prep` first.
- Read the GUI setup cell and `operando_config.ini`.
- Update the config to point at the requested experiment first, rather than editing many notebook cells.
- Check for related paths: raw image directory, OB/open-beam images, dark/background images, corrected output directory, electrochemistry file, and plot output directory.
- If the user changes from one experiment to another, update all matching experiment-specific paths together and report the exact experiment now configured.

### TIFF and Correction Failures

- When `tifffile.TiffFileError`, `KeyError: b'  '`, empty image datetime errors, or bad-image crashes occur, identify the specific image filename from the traceback or processing log.
- Prefer making collection/correction loops skip unreadable TIFFs with a concise warning summary instead of stopping the full run.
- Track skipped files in processing metadata when possible, for example `skipped_tiff_files` and `skipped_tiff_count`.
- Ensure the same skip handling applies at every stage that reads the image, not only the first collection pass.

### OBINT, Reference, and Background Correction

- Confirm whether the user is running OBINT correction, reference division, background correction, or a chained workflow.
- Avoid assuming a currently running notebook callback can be modified live. Explain when a change only affects future runs after module reload.
- If adding automatic chaining between steps, keep manual controls usable and report the stopping condition.
- Preserve summaries of expected, corrected, uncorrected, skipped, and failed image counts.

### Plotting and Event Figures

- Reuse existing plotting helpers and notebook patterns for potential, thickness, event images, color bars, and subplot labels.
- Check whether labels should start at a specific letter, such as `(c)`, and keep labels consistent across panels.
- For event-time plots, verify time columns and units before labeling. Common columns include `Time (s)`, `Time elapsed (s)`, voltage/potential, capacity, and thickness.
- When changing figure layout, inspect the affected notebook cell and plotting helper together so the notebook and module behavior stay aligned.

## Safety Checks

- Do not delete raw NCI images or original electrochemistry data.
- Make shared toolbox edits narrowly, because multiple notebooks may import the same modules.
- Before bulk output cleanup, confirm the resolved target directory is the intended corrected/output folder.
- If a user asks why a patch did not take effect, check whether the notebook kernel has stale imports before editing again.
