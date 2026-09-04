---
name: godzilla-lumacam-git-management
description: >-
  Safely manage the Godzilla LumaCam development-to-production Git workflow.
  Use when the user wants to start, inspect, save, test, push, promote, compare,
  or plan rollback of lumacam_measurementcontrol changes; asks which LumaCam
  directory or branch to edit or run experimentally; or wants Git guidance for
  /home/localadmin/Programs/lumacam_measurementcontrol and
  lumacam_measurementcontrol_dev.
---

# Manage LumaCam Git Workflow

## Overview

Guide the user through one simple route: edit the dev worktree on `develop`,
validate and push it, then fast-forward the production worktree on `main` only
after explicit promotion approval. Tell the user what to do next after every
operation. The user should never need to remember Git commands.

Production experiment code:
`/home/localadmin/Programs/lumacam_measurementcontrol`

Development code:
`/home/localadmin/Programs/lumacam_measurementcontrol_dev`

Never synchronize these directories by copying files. Git commits connect them.

## Dependencies

- `uv`: run the deterministic helper with an isolated Python runtime. If `uv`
  is unavailable, use the installed `uv` skill before continuing.
- Git: both LumaCam directories must remain worktrees of the same repository.

No external API is used. API rate limiting does not apply.

## Quick Start

User says:

1. `Start a LumaCam dev change: <description>`
2. After editing: `Save my LumaCam dev change`
3. After acceptance: `Promote LumaCam dev to production without hardware test`

The agent runs all Git commands. After each step, read the JSON report and give
the user its `next_step` in plain language.

If dev already has uncommitted work, do not run `start`. Run `status`, explain
the existing changes, and continue that work or ask whether it should be saved.

## Utility Scripts

Set the skill directory before each command:

```bash
SKILL_DIR=/home/localadmin/.codex/skills/godzilla-lumacam-git-management
```

### Inspect status

```bash
uv run "$SKILL_DIR/scripts/lumacam_git.py" status \
  --output /tmp/lumacam-git-status.json
```

Use for `Show LumaCam Git status`, branch comparisons, or before uncertain
actions. Fetches remote refs unless `--skip-fetch` is passed.

### Start a change

```bash
uv run "$SKILL_DIR/scripts/lumacam_git.py" start \
  --change "<short description>" \
  --output /tmp/lumacam-git-start.json
```

Requires clean `main` and `develop`, fetches GitHub, and runs the safe equivalent
of switching to `develop`, pulling with `--ff-only`, and checking status. It
never edits production code.

### Test dev

```bash
uv run "$SKILL_DIR/scripts/lumacam_git.py" test \
  --output /tmp/lumacam-git-test.json
```

Runs unit tests, shell syntax checks, and tracked parameter JSON validation.
This does not use detector hardware.

### Save dev

First inspect `git diff` and explain all changes to the user. Then pass every
reviewed changed path explicitly:

```bash
uv run "$SKILL_DIR/scripts/lumacam_git.py" save \
  --message "<concise commit message>" \
  --path <reviewed-path> [--path <reviewed-path> ...] \
  --reviewed \
  --output /tmp/lumacam-git-save.json
```

This validates dev, stages only the listed files, commits, and pushes
`develop`. It refuses unlisted changes or pre-staged files.

### Promote to production

Get exact candidate SHA from `status` or `save`. Promotion requires a clean,
fully pushed `develop`, clean and current `main`, passing software validation,
and no detected acquisition process.

If user confirms hardware testing:

```bash
uv run "$SKILL_DIR/scripts/lumacam_git.py" promote \
  --source-sha <exact-develop-sha> \
  --hardware-tested \
  --output /tmp/lumacam-git-promote.json
```

If hardware testing is intentionally skipped, require the user's exact phrase
`Promote LumaCam dev to production without hardware test`, then run:

```bash
uv run "$SKILL_DIR/scripts/lumacam_git.py" promote \
  --source-sha <exact-develop-sha> \
  --skip-hardware-test \
  --confirmation "Promote LumaCam dev to production without hardware test" \
  --output /tmp/lumacam-git-promote.json
```

Promotion uses fast-forward only, creates the next
`production-YYYY-MM-DD.N` annotated tag, and atomically pushes `main` plus tag.
Never promote merely because dev was saved.

### Plan rollback

```bash
uv run "$SKILL_DIR/scripts/lumacam_git.py" rollback-plan \
  --tag <production-tag> \
  --output /tmp/lumacam-git-rollback.json
```

This is read-only. Report its plan and request separate confirmation before any
recovery action. Never use force-push, `git reset --hard`, or a detached
production checkout.

## Workflow

1. **Start:** Prepare clean, synchronized `develop`; tell user to edit only dev.
2. **Edit:** Make requested changes only inside dev. Preserve ignored local
   acquisition settings and calibration paths unless explicitly requested.
3. **Review:** Show concise diff summary and flag changes to tracked experiment
   parameters such as `parameterSettings.json`.
4. **Save:** Validate, commit reviewed paths, and push `develop`.
5. **Accept:** Ask whether candidate should remain in dev or enter production.
6. **Promote:** Require hardware-tested status or exact skip phrase; promote the
   exact saved SHA.
7. **Run:** Tell user experiments always run from production directory on
   `main`, never from dev.

Detailed policy: read `references/management-plan.md` when diagnosing a failure,
explaining branch roles, or planning recovery.

## Error Handling

- On dirty worktrees, branch mismatch, remote divergence, failed validation,
  active acquisition, merge conflict, or authentication failure: stop and show
  the shortest decisive error plus report path.
- Preserve all user changes. Never stash, discard, reset, delete, force-push,
  or auto-resolve conflicts.
- A failed save may leave a safe local commit; inspect status before retrying.
- A failed promotion push may leave local `main` and a local production tag
  ahead of GitHub; inspect and recover without creating new history blindly.
- Never edit live files to repair a promotion problem.

## Common Mistakes

- Editing or experimentally testing new code in production directory.
- Copying dev over production instead of promoting a commit.
- Treating software tests as hardware validation.
- Running `git add -A`, which may include unrelated work.
- Assuming ignored machine-local configuration will synchronize through Git.
