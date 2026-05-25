#!/usr/bin/env bash
set -euo pipefail

device_prefix="${1:-}"

if ! command -v git >/dev/null 2>&1; then
  echo "Git is not installed. Install git first." >&2
  exit 1
fi

if [[ -n "$device_prefix" && ! "$device_prefix" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "Device prefix must be lowercase letters, numbers, and hyphens only, for example: godzilla or ubuntu-analysis." >&2
  exit 1
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_skills="$repo/skills"
codex_home="${CODEX_HOME:-$HOME/.codex}"
codex_skills="$codex_home/skills"
timestamp="$(date +%Y%m%d-%H%M%S)"
backup="$codex_home/skills.local-backup-$timestamp"

cd "$repo"
git pull --rebase --autostash

mkdir -p "$repo_skills" "$codex_home"

if [[ -e "$codex_skills" || -L "$codex_skills" ]]; then
  if [[ -L "$codex_skills" && "$(readlink -f "$codex_skills")" == "$(readlink -f "$repo_skills")" ]]; then
    echo "Codex skills already point to $repo_skills"
  else
    echo "Merging existing local skills into repo without overwriting existing repo files..."
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --ignore-existing "$codex_skills/" "$repo_skills/"
    else
      cp -an "$codex_skills/." "$repo_skills/"
    fi

    echo "Backing up existing Codex skills folder to $backup"
    mv "$codex_skills" "$backup"
    ln -s "$repo_skills" "$codex_skills"
  fi
else
  ln -s "$repo_skills" "$codex_skills"
fi

if [[ -n "$device_prefix" ]]; then
  echo
  echo "Use this ownership line in this computer's skill-review automation prompt:"
  echo "This computer is $device_prefix. It owns skill folders whose names start with skills/$device_prefix-. When creating or editing skills from this device's workflow review, only create or edit skills with that prefix unless the user explicitly approves editing another folder."
fi

git add skills
if ! git diff --cached --quiet; then
  git commit -m "Add Codex skills from $(hostname)"
  git pull --rebase
  git push
else
  echo "No new local skills to commit."
fi

echo
echo "Done. Restart Codex so it reloads skills from $repo_skills"
