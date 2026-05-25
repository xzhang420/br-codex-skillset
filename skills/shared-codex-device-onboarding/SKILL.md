---
name: shared-codex-device-onboarding
description: Use when the user says they have a new computer/device, asks how to synchronize Codex skills or settings across computers, asks to set up Codex on another Windows/Linux/Ubuntu machine, or asks to automate future Codex skill sync setup. Provides the shared br-codex-skillset bootstrap workflow and device-prefix ownership rules.
---

# Shared Codex Device Onboarding

Use this skill when adding a new computer to the user's Codex skills/settings sync setup.

## Source Of Truth

The shared private repo is:

```text
https://github.com/xzhang420/br-codex-skillset
```

Preferred local paths:

```text
Windows: %USERPROFILE%\dev\br-codex-skillset
Linux/Ubuntu: ~/dev/br-codex-skillset
```

Codex should read skills through:

```text
Windows: %USERPROFILE%\.codex\skills
Linux/Ubuntu: ~/.codex/skills
```

That path should link to the repo's `skills` folder.

## Device Prefixes

Use top-level skill folders only.

Known devices:

```text
personal-*     personal computer
workstation-*  workstation computer
godzilla-*     Ubuntu/Linux computer
shared-*       manually promoted cross-device skills
```

For any new computer, ask for or choose a lowercase device prefix, for example `travel-laptop`, then use skill folders named `skills/travel-laptop-*`.

## Bootstrap A Future Computer

First make sure Git is installed.

Windows:

```powershell
New-Item -ItemType Directory -Path "$env:USERPROFILE\dev" -Force
git clone https://github.com/xzhang420/br-codex-skillset.git "$env:USERPROFILE\dev\br-codex-skillset"
cd "$env:USERPROFILE\dev\br-codex-skillset"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -DevicePrefix DEVICE-PREFIX
```

Linux/Ubuntu:

```bash
mkdir -p ~/dev
git clone https://github.com/xzhang420/br-codex-skillset.git ~/dev/br-codex-skillset
cd ~/dev/br-codex-skillset
bash scripts/bootstrap-linux.sh DEVICE-PREFIX
```

Replace `DEVICE-PREFIX` with the chosen prefix.

The bootstrap script:

- pulls the latest repo
- merges existing local Codex skills into the repo without overwriting repo files
- backs up the old local skills folder
- links `~/.codex/skills` or `%USERPROFILE%\.codex\skills` to the repo
- commits and pushes imported skills
- prints the ownership line to add to that computer's biweekly workflow-review automation prompt

If push fails, guide the user through GitHub authentication (`gh auth login` or Git Credential Manager), then rerun the script.

## Automation Prompt Line

After bootstrap, add this to that computer's biweekly workflow-review automation prompt:

```text
This computer is DEVICE-PREFIX. It owns skill folders whose names start with skills/DEVICE-PREFIX-. When creating or editing skills from this device's workflow review, only create or edit skills with that prefix unless the user explicitly approves editing another folder.
```

Also include:

```text
Before changing skills, run git pull --rebase --autostash in the shared br-codex-skillset repo. After creating or editing skills, run git add skills config.toml AGENTS.md, commit with a concise message, run git pull --rebase, and push. If there is a conflict or authentication failure, stop and report it. Never force-push.
```

## Safety

Never sync the whole `.codex` directory. Never commit auth files, sessions, logs, SQLite state files, caches, sandbox files, plugin caches, or local backups.
