# Codex Skill Sync Bootstrap

Use these scripts when adding a new computer to the shared Codex skill repo.

## Windows

Install Git for Windows, then clone the repo:

```powershell
New-Item -ItemType Directory -Path "$env:USERPROFILE\dev" -Force
git clone https://github.com/xzhang420/br-codex-skillset.git "$env:USERPROFILE\dev\br-codex-skillset"
cd "$env:USERPROFILE\dev\br-codex-skillset"
```

Run the bootstrap script with the device prefix you want that computer to own:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -DevicePrefix personal
```

If prompted for GitHub authentication, complete it. If the script reports an authentication failure, run `gh auth login` or sign in through Git Credential Manager, then rerun the script.

## Linux

Install Git, then clone the repo:

```bash
mkdir -p ~/dev
git clone https://github.com/xzhang420/br-codex-skillset.git ~/dev/br-codex-skillset
cd ~/dev/br-codex-skillset
```

Run the bootstrap script with the device prefix you want that computer to own:

```bash
bash scripts/bootstrap-linux.sh godzilla
```

If GitHub rejects the push, run `gh auth login` or configure a personal access token, then rerun the script.

## What The Scripts Do

- Pull the latest shared repo.
- Merge existing local `~/.codex/skills` into the repo without overwriting repo files.
- Back up the old local skills folder.
- Link `~/.codex/skills` to the shared repo's `skills` folder.
- Commit and push any newly imported local skills.
- Print the ownership line to add to that computer's biweekly skill-review automation prompt.

Restart Codex after the script finishes.
