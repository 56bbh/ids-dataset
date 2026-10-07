param(
    [ValidateRange(1, 100)]
    [int]$Runs = 5,

    [ValidateRange(1, 500)]
    [int]$Epochs = 50,

    [ValidateRange(1, 100)]
    [int]$Patience = 8,

    [string]$BatchName = "profile_disjoint_temporal",

    [string]$Python = "python",

    [string]$ProfileData = "",

    [string]$OutputRoot = "",

    [switch]$Resume,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ModelRoot = Join-Path $RepoRoot "src\models"
if (-not $ProfileData) {
    $ProfileData = Join-Path $RepoRoot "data\model_inputs\profile_disjoint"
}
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot "outputs"
}
$ResultRoot = Join-Path $OutputRoot "experiment_results\$BatchName"
$LogRoot = Join-Path $OutputRoot "experiment_logs\$BatchName"
$Suffix = "ratio0p6_T10_S1"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $ProfileData)) {
    throw "Profile-disjoint input directory was not found: $ProfileData"
}

$RequiredInputs = @(
    "X_train_$Suffix.npy", "y_train_$Suffix.npy",
    "X_val_$Suffix.npy", "y_val_$Suffix.npy",
    "X_test_$Suffix.npy", "y_test_$Suffix.npy"
)
foreach ($FileName in $RequiredInputs) {
    $InputPath = Join-Path $ProfileData $FileName
    if (-not (Test-Path -LiteralPath $InputPath)) {
        throw "Required profile-disjoint input was not found: $InputPath"
    }
}

$Jobs = @(
    [PSCustomObject]@{
        Name = "01_lstm_ordered"
        Script = Join-Path $ModelRoot "train_lstm_baseline.py"
        Arguments = @(
            "--data-dir", $ProfileData,
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
        )
    },
    [PSCustomObject]@{
        Name = "02_transformer_ordered"
        Script = Join-Path $ModelRoot "train_transformer_baseline.py"
        Arguments = @(
            "--data-dir", $ProfileData,
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
        )
    }
)

foreach ($Job in $Jobs) {
    if (-not (Test-Path -LiteralPath $Job.Script)) {
        throw "Training script was not found: $($Job.Script)"
    }
}

if (-not ("IdsProfileExecutionState" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IdsProfileExecutionState {
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
}

$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]0x00000001
$sleepStateSet = $false
$previousPythonUnbuffered = $env:PYTHONUNBUFFERED

try {
    Write-Host "Batch: $BatchName"
    Write-Host "Data: $ProfileData"
    Write-Host "Runs per model: $Runs (seeds 42 through $([int](41 + $Runs)))"
    Write-Host "Maximum epochs: $Epochs; patience: $Patience"
    Write-Host "Results: $ResultRoot"
    Write-Host "Logs: $LogRoot"

    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $ResultRoot, $LogRoot | Out-Null
        $env:PYTHONUNBUFFERED = "1"
        $stateResult = [IdsProfileExecutionState]::SetThreadExecutionState(
            $ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED
        )
        $sleepStateSet = $stateResult -ne 0
        if (-not $sleepStateSet) {
            Write-Warning "Windows sleep prevention could not be enabled. Keep the computer awake manually."
        }
    }

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

        "Completed=$(Get-Date -Format o)`nElapsed=$($timer.Elapsed)" |
            Set-Content -LiteralPath $markerPath -Encoding UTF8
        Write-Host "[DONE] $($Job.Name) in $($timer.Elapsed)"
    }

    if ($DryRun) {
        Write-Host ""
        Write-Host "Dry run complete. No training was started and no output directories were created."
    }
    else {
        Write-Host ""
        Write-Host "LSTM and Transformer profile-disjoint experiments completed successfully."
    }
}
finally {
    if ($sleepStateSet) {
        [void][IdsProfileExecutionState]::SetThreadExecutionState($ES_CONTINUOUS)
    }
    $env:PYTHONUNBUFFERED = $previousPythonUnbuffered
}
