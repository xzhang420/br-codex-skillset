## Codex Skill Ownership

This repository is the shared source of truth for Codex skills across the user's devices.

Use top-level skill folders only. Do not create nested skill roots such as `skills/device/personal/foo`.

Device-owned skill prefixes:

- `skills/personal-*`: skills created or maintained from the personal computer.
- `skills/workstation-*`: skills created or maintained from the workstation computer.
- `skills/godzilla-*`: skills created or maintained from the godzilla computer.
- `skills/shared-*`: manually promoted skills intended to be generally useful on every device.

When creating or editing skills from an automated workflow review, only create or edit skill folders owned by the current device prefix unless the user explicitly approves editing another folder.

Before changing skills, run `git pull --rebase --autostash` in this repo.

After changing skills, stage only portable files such as `skills`, `config.toml`, and `AGENTS.md`; commit with a concise message; run `git pull --rebase`; then push.

If there is a merge conflict, authentication failure, or uncertainty about overwriting a skill, stop and report it. Never force-push.

Never stage or commit Codex auth files, sessions, logs, SQLite state files, caches, sandbox files, plugin caches, or local backups.
