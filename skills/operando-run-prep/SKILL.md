---
name: operando-run-prep
description: Use when preparing the operando notebook for a new experiment, updating operando_config.ini paths, validating experiment/reference/beamhistory inputs, or reporting TPX time ranges before downloading beam-history data.
---

# Operando Run Prep

Use this skill for the repetitive setup work around the operando workflow in `Leon_BOA_Dec2025`-style campaign directories.

## Use this skill when

- The user wants to switch `operando_config.ini` to a new experiment
- The user wants the config paths updated without touching stable processing parameters
- The user wants to validate that experiment, reference, beam-history, and echem paths exist
- The user wants earliest/latest `.tpx3` times for experiment or reference folders before downloading beam-history data

## Do not use this skill for

- Choosing `background_n_images`
- Choosing `selected_image_indices`
- Deciding whether a reference is scientifically appropriate
- Interpreting suspicious output or artifacts

## Workflow

1. Identify the campaign root and the `operando_config.ini` path.
2. Confirm or infer the experiment switch details before editing:
   - which new `exp_dir` should be used
   - whether to reuse the same experiment `beamhistory_path`; if not, find the matching experiment beamhistory in `beamhistory/` by matching the experiment folder name or experiment number
   - whether to reuse the same `reference_dir`
   - whether to reuse the same `reference_beamhistory_path`; if the reference changed, find the matching reference beamhistory in `beamhistory/` by matching the reference folder name, reference experiment number, or obvious reference keywords
3. Prefer the bundled script instead of manually rewriting the config.
4. If the user is switching experiments, usually update only:
   - `base_dir` when the whole campaign moved
   - `exp_dir`
   - `beamhistory_path`
   - `reference_dir` only if the reference changed
   - `reference_beamhistory_path` only if the reference changed
5. Validate the referenced paths after updating.
6. If helpful, print TPX time ranges for `experiment/` and `reference/` before the user downloads beam-history data.
7. In the final response, state exactly which keys changed and which keys were intentionally left unchanged, including whether experiment/reference beamhistory paths were reused or changed.

## Commands

Dry-run showing current config plus TPX time ranges:

```bash
python3 ~/.codex/skills/operando-run-prep/scripts/prepare_operando_run.py   --config /path/to/operando_config.ini   --print-config --show-ranges --dry-run
```

Update a config for a new experiment:

```bash
python3 ~/.codex/skills/operando-run-prep/scripts/prepare_operando_run.py   --config /path/to/operando_config.ini   --exp-dir experiment/exp1301_LiMetal_final   --beamhistory-path beamhistory/Leon_BOA_Dec2025_exp1301_LiMetal_final.csv   --reference-dir reference/exp004_Ref1um_NewCell_40x20NA_wmetal   --reference-beamhistory-path beamhistory/Leon_BOA_Dec2025_Ref.csv
```

Show TPX time ranges only:

```bash
python3 ~/.codex/skills/operando-run-prep/scripts/prepare_operando_run.py   --config /path/to/operando_config.ini   --show-ranges --dry-run
```

## Notes

- The script preserves the rest of the config file instead of rewriting it through `configparser`.
- It updates only keys you explicitly pass.
- It validates relative paths against `base_dir`.
- If the user does not explicitly say whether the experiment beamhistory or reference beamhistory is the same, inspect the current config and `beamhistory/` folder first. If there is exactly one plausible match, use it and report the inference. If there are multiple plausible matches, ask the user before editing.
- For this workflow, the user still makes manual decisions in the notebook after setup.
