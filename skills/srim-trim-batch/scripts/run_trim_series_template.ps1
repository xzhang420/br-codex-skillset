param(
    [string]$Root = $PSScriptRoot,
    [string[]]$WidthLabels = @("3.5", "3", "2.5"),
    [string]$LayerName = "V2O5",
    [string]$OutputPrefix = "Tritium_through_V2O5",
    [int]$IonCount = 10000,
    [switch]$EnableTransmitOutput = $true,
    [switch]$ClearPreviousBatchOutputs
)

$ErrorActionPreference = "Stop"
$rootPath = Resolve-Path -LiteralPath $Root
$trimInPath = Join-Path $rootPath "TRIM.IN"
$trimAutoPath = Join-Path $rootPath "TRIMAUTO"
$trimDatPath = Join-Path $rootPath "TRIM.DAT"
$trimExePath = Join-Path $rootPath "TRIM.exe"
$outputRoot = Join-Path $rootPath "SRIM Outputs"
$summaryPath = Join-Path $outputRoot "$($OutputPrefix)_transmission_summary.csv"
$logPath = Join-Path $outputRoot "$($OutputPrefix)_series.log"

function Write-Log {
    param([string]$Message)
    Add-Content -LiteralPath $logPath -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

function Backup-File {
    param([string]$Path)
    $backup = Join-Path $outputRoot ("{0}.backup.{1}" -f (Split-Path -Leaf $Path), (Get-Date -Format "yyyyMMdd_HHmmss"))
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    return $backup
}

function Set-TrimInput {
    param([string]$WidthLabel)

    $widthUm = [double]::Parse($WidthLabel, [System.Globalization.CultureInfo]::InvariantCulture)
    $widthAngstrom = [int][math]::Round($widthUm * 10000.0)
    $lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $trimInPath)

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^Ion:') {
            $parts = $lines[$i + 1] -split '\s+' | Where-Object { $_ -ne "" }
            $parts[4] = [string]$IonCount
            $parts[6] = [string][math]::Min(10000, $IonCount)
            $lines[$i + 1] = ("{0,6}{1,8}{2,12}{3,8}{4,8}{5,9}{6,9}" -f [int]$parts[0], [int]$parts[1], [int]$parts[2], [int]$parts[3], [int]$parts[4], [int]$parts[5], [int]$parts[6])
        }

        if ($EnableTransmitOutput -and $lines[$i] -match '^Diskfiles \(0=no,1=yes\):') {
            $lines[$i + 1] = "                          0       0           1       0               0                               0"
        }

        if ($lines[$i] -match '^PlotType \(0-5\); Plot Depths:') {
            $parts = $lines[$i + 1] -split '\s+' | Where-Object { $_ -ne "" }
            $lines[$i + 1] = ("{0,8}{1,26}{2,16}" -f [int]$parts[0], [int]$parts[1], $widthAngstrom)
        }

        if ($lines[$i] -match ('^\s*\d+\s+"{0}"' -f [regex]::Escape($LayerName))) {
            $layerWidthRegex = [regex]('("{0}"\s+)\d+' -f [regex]::Escape($LayerName))
            $lines[$i] = $layerWidthRegex.Replace($lines[$i], { param($m) $m.Groups[1].Value + $widthAngstrom }, 1)
        }
    }

    Set-Content -LiteralPath $trimInPath -Value $lines -Encoding ASCII
}

function Get-TransmitCount {
    param([string]$TransmitPath)
    $lines = Get-Content -LiteralPath $TransmitPath
    $count = 0
    foreach ($line in $lines) {
        if ($line.Trim() -match '^\d+(\s+[-+]?\d+(\.\d+)?([Ee][-+]?\d+)?){2,}') {
            $count++
        }
    }
    return $count
}

function Get-NewTxtFiles {
    param([datetime]$StartedAt)
    $slop = $StartedAt.AddSeconds(-5)
    @(
        Get-ChildItem -LiteralPath $outputRoot -File -Filter "*.txt" -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath $rootPath -File -Filter "*.txt" -ErrorAction SilentlyContinue
    ) | Where-Object {
        $_.LastWriteTime -ge $slop -and $_.Name -notin @("TRIMAUTO.TXT", "notes.txt")
    } | Sort-Object FullName -Unique
}

foreach ($required in @($trimInPath, $trimAutoPath, $trimDatPath, $trimExePath)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing required SRIM file: $required" }
}
if (-not (Test-Path -LiteralPath $outputRoot)) { New-Item -ItemType Directory -Path $outputRoot | Out-Null }

if ($ClearPreviousBatchOutputs) {
    $safeRoot = (Resolve-Path -LiteralPath $outputRoot).Path
    $targets = @()
    $targets += Get-ChildItem -LiteralPath $outputRoot -File -Force | Where-Object { $_.Name -like "$OutputPrefix*" -or $_.Name -in @("TRANSMIT.txt", "TRIMOUT.txt") }
    $targets += Get-ChildItem -LiteralPath $outputRoot -Directory -Force | Where-Object { $_.Name -like "$OutputPrefix*" }
    foreach ($target in $targets) {
        $resolved = Resolve-Path -LiteralPath $target.FullName
        if (-not $resolved.Path.StartsWith($safeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to delete outside output folder: $resolved"
        }
        Remove-Item -LiteralPath $resolved.Path -Recurse -Force
    }
}

$trimInBackup = Backup-File -Path $trimInPath
$trimAutoBackup = Backup-File -Path $trimAutoPath
Set-Content -LiteralPath $summaryPath -Value "width_um,input_ions,transmitted_ions,transmitted_percent,output_folder,transmit_file" -Encoding ASCII

try {
    foreach ($width in $WidthLabels) {
        Set-TrimInput -WidthLabel $width
        Set-Content -LiteralPath $trimAutoPath -Value @("1", "") -Encoding ASCII

        $destination = Join-Path $outputRoot ("{0}_{1}" -f $OutputPrefix, $width)
        if (-not (Test-Path -LiteralPath $destination)) { New-Item -ItemType Directory -Path $destination | Out-Null }

        Write-Log "Starting width $width with $IonCount ions."
        $startedAt = Get-Date
        $process = Start-Process -FilePath $trimExePath -WorkingDirectory $rootPath -PassThru -Wait
        Write-Log "TRIM.exe finished for width $width with exit code $($process.ExitCode)."

        foreach ($file in @(Get-NewTxtFiles -StartedAt $startedAt)) {
            Move-Item -LiteralPath $file.FullName -Destination (Join-Path $destination $file.Name) -Force
        }

        $transmit = Get-ChildItem -LiteralPath $destination -File | Where-Object { $_.Name -like "TRANSMIT*.txt" } | Select-Object -First 1
        if ($null -eq $transmit) {
            Add-Content -LiteralPath $summaryPath -Value ("{0},{1},,,{2}," -f $width, $IonCount, $destination)
            continue
        }

        $transmitted = Get-TransmitCount -TransmitPath $transmit.FullName
        $percent = [math]::Round(($transmitted / [double]$IonCount) * 100.0, 6)
        Add-Content -LiteralPath $summaryPath -Value ("{0},{1},{2},{3},{4},{5}" -f $width, $IonCount, $transmitted, $percent, $destination, $transmit.FullName)
    }
}
finally {
    Copy-Item -LiteralPath $trimInBackup -Destination $trimInPath -Force
    Copy-Item -LiteralPath $trimAutoBackup -Destination $trimAutoPath -Force
    Write-Log "Restored TRIM.IN and TRIMAUTO from backups."
}
