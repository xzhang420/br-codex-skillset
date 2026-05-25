param(
    [string]$DevicePrefix = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-RobocopyNoOverwrite {
    param(
        [string]$Source,
        [string]$Destination
    )

    & robocopy $Source $Destination /E /XC /XN /XO
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or not available in PATH. Install Git for Windows first."
}

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$repo = $repo.Path
$repoSkills = Join-Path $repo "skills"
$codexHome = Join-Path $env:USERPROFILE ".codex"
$codexSkills = Join-Path $codexHome "skills"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $codexHome "skills.local-backup-$timestamp"

if ($DevicePrefix -and ($DevicePrefix -notmatch '^[a-z0-9][a-z0-9-]*$')) {
    throw "DevicePrefix must be lowercase letters, numbers, and hyphens only, for example: personal or windows-lab."
}

Set-Location $repo
Invoke-Git pull --rebase --autostash

New-Item -ItemType Directory -Path $repoSkills -Force | Out-Null
New-Item -ItemType Directory -Path $codexHome -Force | Out-Null

if (Test-Path $codexSkills) {
    $item = Get-Item $codexSkills
    $isLinked = (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)

    if ($isLinked -and $item.Target -contains $repoSkills) {
        Write-Host "Codex skills already point to $repoSkills"
    }
    else {
        Write-Host "Merging existing local skills into repo without overwriting existing repo files..."
        Invoke-RobocopyNoOverwrite $codexSkills $repoSkills
        Write-Host "Backing up existing Codex skills folder to $backup"
        Rename-Item $codexSkills $backup
        New-Item -ItemType Junction -Path $codexSkills -Target $repoSkills | Out-Null
    }
}
else {
    New-Item -ItemType Junction -Path $codexSkills -Target $repoSkills | Out-Null
}

if ($DevicePrefix) {
    Write-Host ""
    Write-Host "Use this ownership line in this computer's skill-review automation prompt:"
    Write-Host "This computer is $DevicePrefix. It owns skill folders whose names start with skills/$DevicePrefix-. When creating or editing skills from this device's workflow review, only create or edit skills with that prefix unless the user explicitly approves editing another folder."
}

Invoke-Git add skills
$cached = git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    Invoke-Git commit -m "Add Codex skills from $env:COMPUTERNAME"
    Invoke-Git pull --rebase
    Invoke-Git push
}
else {
    Write-Host "No new local skills to commit."
}

Write-Host ""
Write-Host "Done. Restart Codex so it reloads skills from $repoSkills"
