# LumaCam Branch Management Plan

## Roles

- `develop`: editable candidate code in
  `/home/localadmin/Programs/lumacam_measurementcontrol_dev`.
- `main`: production experiment code in
  `/home/localadmin/Programs/lumacam_measurementcontrol`.
- `production-YYYY-MM-DD.N`: immutable label identifying a production release.
- `origin/develop` and `origin/main`: GitHub copies at
  `https://github.com/xzhang420/lumacam_measurementcontrol.git`.

The production directory is the only directory used for image-acquisition
experiments. Development and review happen in the dev directory.

## User Instructions

For a normal change, the user only needs these natural-language requests:

1. `Start a LumaCam dev change: <description>`
2. Ask Codex to edit or review code.
3. `Save my LumaCam dev change`
4. Review the saved commit and software-test result.
5. `Promote LumaCam dev to production without hardware test`, or state that
   hardware testing passed and request promotion.

Codex must state the current phase and next user action after each request.
Manual Git commands are not required.

## Strict Invariants

1. Never make planned code changes in production worktree.
2. Never copy one repository directory over another.
3. Never rewrite shared history or force-push.
4. Never discard, stash, or overwrite unexplained changes.
5. `main` promotion must be a fast-forward to the exact pushed `develop` SHA.
6. Push production branch and annotated production tag atomically.
7. Stop promotion while acquisition-related processes are active.
8. Software validation is mandatory. Hardware validation may be skipped only
   after explicit user confirmation.
9. Ignored machine-local proposal paths, calibration paths, logs, data, and
   runtime state remain local and are not promotion content.

## Normal State Sequence

```text
clean main + clean develop
        |
        v
edit develop working tree
        |
        v
software validation
        |
        v
commit and push develop
        |
        v
explicit acceptance
        |
        v
fast-forward main to exact develop SHA
        |
        v
tag and atomic push
```

## Failure Recovery

- **Dirty dev before start:** report files; continue existing work or ask user
  whether it belongs to the new change. Do not pull over it.
- **Remote branch moved:** fetch and report ahead/behind counts. Do not merge or
  rebase without explaining the situation.
- **Validation failed:** preserve edits, report failed check, fix only when user
  requested implementation.
- **Commit succeeded but push failed:** keep commit, inspect authentication and
  branch state, then retry only the push when safe.
- **Local promotion prepared but atomic push failed:** local `main` and tag may
  be ahead. Inspect exact SHAs and tag targets before retrying. Do not generate
  another production tag blindly.
- **Bad production release:** use `rollback-plan` first. Prefer reviewed revert
  commits or a new fixed forward commit. Never reset or force-push `main`.

## Archive Policy

Historical pre-workflow copies remain under
`/home/localadmin/Programs/archive_lumacam_measurementcontrol`. This skill does
not move, modify, or delete those archives.
