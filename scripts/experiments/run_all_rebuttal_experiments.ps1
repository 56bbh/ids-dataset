param(
    [ValidateSet("validation", "formal")]
    [string]$Mode = "validation",

    [ValidateRange(1, 100)]
    [int]$Runs = 10,

    [ValidateRange(1, 500)]
    [int]$Epochs = 50,

    [ValidateRange(1, 100)]
    [int]$Patience = 8,

    [string]$BatchName = "rebuttal",

    [string]$Python = "python",

    [string]$TemporalData = "",

    [string]$NonTemporalData = "",

    [string]$OutputRoot = "",

    [switch]$Resume,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ModelRoot = Join-Path $RepoRoot "src\models"
if (-not $TemporalData) {
    $TemporalData = Join-Path $RepoRoot "data\model_inputs\current_release\temporal"
}
if (-not $NonTemporalData) {
    $NonTemporalData = Join-Path $RepoRoot "data\model_inputs\current_release\non_temporal"
}
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot "outputs"
}
$ResultRoot = Join-Path $OutputRoot "experiment_results\$BatchName\$Mode"
$LogRoot = Join-Path $OutputRoot "experiment_logs\$BatchName\$Mode"
$Suffix = "ratio0p6_T10_S1"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $TemporalData)) {
    throw "Temporal input directory was not found: $TemporalData"
}
if (-not (Test-Path -LiteralPath $NonTemporalData)) {
    throw "Non-temporal input directory was not found: $NonTemporalData"
}

New-Item -ItemType Directory -Force -Path $ResultRoot, $LogRoot | Out-Null

$ValidationArgs = @()
if ($Mode -eq "validation") {
    $ValidationArgs = @("--validation-only")
}

$Jobs = @(
    [PSCustomObject]@{
        Name = "01_mlp_all"
        Script = Join-Path $ModelRoot "train_mlp_baseline.py"
        Arguments = @(
            "--data-dir", $NonTemporalData,
            "--output-dir", (Join-Path $ResultRoot "mlp"),
            "--representation", "all",
            "--runs", $Runs,
            "--seed", 42,
            "--seed-step", 1,
            "--epochs", $Epochs,
            "--batch-size", 128,
            "--lr", 0.001,
            "--weight-decay", 0.0001,
            "--patience", $Patience,
            "--hidden-dims", 128, 64,
            "--dropout", 0.2,
            "--device", "cuda"
        ) + $ValidationArgs
    },
    [PSCustomObject]@{
        Name = "02_lstm_ordered"
        Script = Join-Path $ModelRoot "train_lstm_baseline.py"
        Arguments = @(
            "--data-dir", $TemporalData,
            "--suffix", $Suffix,
            "--out-dir", (Join-Path $ResultRoot "lstm"),
            "--model", "lstm",
            "--runs", $Runs,
            "--seed", 42,
            "--seed-step", 1,
            "--epochs", $Epochs,
            "--batch-size", 128,
            "--lr", 0.0008,
            "--weight-decay", 0.0001,
            "--patience", $Patience,
            "--hidden-dim", 256,
            "--num-layers", 2,
            "--dropout", 0.25,
            "--no-class-weight",
            "--device", "cuda",
            "--temporal-order", "ordered"
        ) + $ValidationArgs
    },
    [PSCustomObject]@{
        Name = "03_lstm_shuffled"
        Script = Join-Path $ModelRoot "train_lstm_baseline.py"
        Arguments = @(
            "--data-dir", $TemporalData,
            "--suffix", $Suffix,
            "--out-dir", (Join-Path $ResultRoot "lstm"),
            "--model", "lstm",
            "--runs", $Runs,
            "--seed", 42,
            "--seed-step", 1,
            "--epochs", $Epochs,
            "--batch-size", 128,
            "--lr", 0.0008,
            "--weight-decay", 0.0001,
            "--patience", $Patience,
            "--hidden-dim", 256,
            "--num-layers", 2,
            "--dropout", 0.25,
            "--no-class-weight",
            "--device", "cuda",
            "--temporal-order", "shuffled"
        ) + $ValidationArgs
    },
    [PSCustomObject]@{
        Name = "04_resnet1d_ordered"
        Script = Join-Path $ModelRoot "train_resnet1d_baseline.py"
        Arguments = @(
            "--data-dir", $TemporalData,
            "--suffix", $Suffix,
            "--output-dir", (Join-Path $ResultRoot "resnet1d"),
            "--runs", $Runs,
            "--seed", 42,
            "--seed-step", 1,
            "--epochs", $Epochs,
            "--batch-size", 128,
            "--lr", 0.001,
            "--weight-decay", 0.0001,
            "--patience", $Patience,
            "--dropout", 0.2,
            "--base-channels", 64,
            "--num-blocks", 3,
            "--kernel-size", 3,
            "--class-weight", "none",
            "--device", "cuda",
            "--temporal-order", "ordered"
        ) + $ValidationArgs
    },
    [PSCustomObject]@{
        Name = "05_resnet1d_shuffled"
        Script = Join-Path $ModelRoot "train_resnet1d_baseline.py"
        Arguments = @(
            "--data-dir", $TemporalData,
            "--suffix", $Suffix,
            "--output-dir", (Join-Path $ResultRoot "resnet1d"),
            "--runs", $Runs,
            "--seed", 42,
            "--seed-step", 1,
            "--epochs", $Epochs,
            "--batch-size", 128,
            "--lr", 0.001,
            "--weight-decay", 0.0001,
            "--patience", $Patience,
            "--dropout", 0.2,
            "--base-channels", 64,
            "--num-blocks", 3,
            "--kernel-size", 3,
            "--class-weight", "none",
            "--device", "cuda",
            "--temporal-order", "shuffled"
        ) + $ValidationArgs
    },
    [PSCustomObject]@{
        Name = "06_transformer_ordered"
        Script = Join-Path $ModelRoot "train_transformer_baseline.py"
        Arguments = @(
            "--data-dir", $TemporalData,
            "--suffix", $Suffix,
            "--out-dir", (Join-Path $ResultRoot "transformer"),
            "--runs", $Runs,
            "--seed", 42,
            "--seed-step", 1,
            "--epochs", $Epochs,
            "--batch-size", 128,
            "--lr", 0.0008,
            "--weight-decay", 0.0001,
            "--patience", $Patience,
            "--dropout", 0.2,
            "--d-model", 128,
            "--num-heads", 4,
            "--num-layers", 2,
            "--dim-feedforward", 256,
            "--pooling", "cls",
            "--device", "cuda",
            "--temporal-order", "ordered"
        ) + $ValidationArgs
    },
    [PSCustomObject]@{
        Name = "07_transformer_shuffled"
        Script = Join-Path $ModelRoot "train_transformer_baseline.py"
        Arguments = @(
            "--data-dir", $TemporalData,
            "--suffix", $Suffix,
            "--out-dir", (Join-Path $ResultRoot "transformer"),
            "--runs", $Runs,
            "--seed", 42,
            "--seed-step", 1,
            "--epochs", $Epochs,
            "--batch-size", 128,
            "--lr", 0.0008,
            "--weight-decay", 0.0001,
            "--patience", $Patience,
            "--dropout", 0.2,
            "--d-model", 128,
            "--num-heads", 4,
            "--num-layers", 2,
            "--dim-feedforward", 256,
            "--pooling", "cls",
            "--device", "cuda",
            "--temporal-order", "shuffled"
        ) + $ValidationArgs
    }
)

if (-not ("IdsExecutionState" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IdsExecutionState {
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
}

$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]0x00000001
$sleepStateSet = $false

try {
    if (-not $DryRun) {
        $stateResult = [IdsExecutionState]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
        $sleepStateSet = $stateResult -ne 0
        if (-not $sleepStateSet) {
            Write-Warning "Windows sleep prevention could not be enabled. Keep the computer awake manually."
        }
    }

    Write-Host "Batch: $BatchName"
    Write-Host "Mode: $Mode"
    Write-Host "Runs per condition: $Runs (seeds 42 through $([int](41 + $Runs)))"
    Write-Host "Maximum epochs: $Epochs; patience: $Patience"
    Write-Host "Results: $ResultRoot"
    Write-Host "Logs: $LogRoot"

    foreach ($Job in $Jobs) {
        $markerPath = Join-Path $LogRoot "$($Job.Name).completed"
        $logPath = Join-Path $LogRoot "$($Job.Name).log"

        if ($Resume -and (Test-Path -LiteralPath $markerPath)) {
            Write-Host "[SKIP] $($Job.Name) already completed."
            continue
        }

        Write-Host ""
        Write-Host "[START] $($Job.Name) at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        if ($DryRun) {
            Write-Host "$Python $($Job.Script) $($Job.Arguments -join ' ')"
            continue
        }

        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        & $Python $Job.Script @($Job.Arguments) 2>&1 | Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
        $timer.Stop()

        if ($exitCode -ne 0) {
            throw "$($Job.Name) failed with exit code $exitCode. See $logPath"
        }

        "Completed=$(Get-Date -Format o)`nElapsed=$($timer.Elapsed)" | Set-Content -LiteralPath $markerPath -Encoding UTF8
        Write-Host "[DONE] $($Job.Name) in $($timer.Elapsed)"
    }

    if ($DryRun) {
        Write-Host ""
        Write-Host "Dry run complete. No training was started."
    }
    else {
        Write-Host ""
        Write-Host "All rebuttal experiments completed successfully."
    }
}
finally {
    if ($sleepStateSet) {
        [void][IdsExecutionState]::SetThreadExecutionState($ES_CONTINUOUS)
    }
}
