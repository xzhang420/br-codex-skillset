## Codex Skill Ownership

This repository is the shared source of truth for user-maintained Codex skills across the user's devices. Codex-managed system skills under `skills/.system/` are local runtime state and must not be committed.

Use top-level skill folders only. Do not create nested skill roots such as `skills/device/personal/foo`. Register mirrored third-party skills in `third-party-skills.toml` with their upstream URL and local preserve rules. When several local skills come from one upstream repository, prefer one collection entry over repetitive per-skill entries.

Device-owned skill prefixes:

- `skills/personal-*`: skills created or maintained from the personal computer.
- `skills/workstation-*`: skills created or maintained from the workstation computer.
- `skills/godzilla-*`: skills created or maintained from the godzilla computer.
- `skills/shared-*`: manually promoted skills intended to be generally useful on every device.

When creating or editing skills from an automated workflow review, only create or edit skill folders owned by the current device prefix unless the user explicitly approves editing another folder.

Before changing skills, run `git pull --rebase --autostash` in this repo.

After changing skills, stage only portable files such as `skills`, `config.toml`, `AGENTS.md`, scripts, `.gitignore`, and `third-party-skills.toml`, while excluding `skills/.system/`; commit with a concise message; run `git pull --rebase`; then push.

If there is a merge conflict, authentication failure, or uncertainty about overwriting a skill, stop and report it. Never force-push.

Never stage or commit Codex auth files, sessions, logs, SQLite state files, caches, sandbox files, plugin caches, local backups, or `skills/.system/`.

Third-party skills listed in `third-party-skills.toml` are maintained by the primary update device. That automation may update only registered third-party entries, validate the result, commit, and push. User-maintained skills under `personal-*`, `workstation-*`, `godzilla-*`, and `shared-*` require explicit user confirmation before commit or push. Other devices should pull this shared repo rather than updating third-party upstreams directly.
