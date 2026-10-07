param(
    [ValidateRange(1, 100)]
    [int]$Runs = 5,

    [ValidateRange(1, 500)]
    [int]$Epochs = 50,

    [ValidateRange(1, 100)]
    [int]$Patience = 8,

    [string]$DataBatchName = "profile_disjoint_stability_20261007",

    [string]$ResultBatchName = "profile_disjoint_split_stability",

    [string]$Python = "python",

    [string]$PartitionsRoot = "",

    [string]$OutputRoot = "",

    [switch]$Resume,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $PartitionsRoot) {
    $PartitionsRoot = Join-Path $RepoRoot "data\model_inputs\$DataBatchName"
}
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot "outputs"
}
$ResultRoot = Join-Path $OutputRoot "experiment_results\$ResultBatchName"
$LogRoot = Join-Path $OutputRoot "experiment_logs\$ResultBatchName"
$TrainScript = Join-Path $RepoRoot "src\models\train_resnet1d_baseline.py"
$AggregateScript = Join-Path $RepoRoot "src\analysis\aggregate_profile_disjoint_stability.py"
$Suffix = "ratio0p6_T10_S1"

foreach ($RequiredPath in @($Python, $PartitionsRoot, $TrainScript, $AggregateScript)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required path was not found: $RequiredPath"
    }
}

$PartitionDirs = @(
    Get-ChildItem -LiteralPath $PartitionsRoot -Directory |
        Where-Object { $_.Name -match '^partition_\d+_seed\d+$' } |
        Sort-Object Name
)
if ($PartitionDirs.Count -lt 3 -or $PartitionDirs.Count -gt 5) {
    throw "Expected 3-5 partition directories under $PartitionsRoot; found $($PartitionDirs.Count)."
}

$Jobs = @()
foreach ($PartitionDir in $PartitionDirs) {
    $DataDir = Join-Path $PartitionDir.FullName "model_input"
    foreach ($FileName in @(
        "X_train_$Suffix.npy", "y_train_$Suffix.npy",
        "X_val_$Suffix.npy", "y_val_$Suffix.npy",
        "X_test_$Suffix.npy", "y_test_$Suffix.npy"
    )) {
        $InputPath = Join-Path $DataDir $FileName
        if (-not (Test-Path -LiteralPath $InputPath)) {
            throw "Required model input was not found: $InputPath"
        }
    }

    $Jobs += [PSCustomObject]@{
        Name = $PartitionDir.Name
        Arguments = @(
            "--data-dir", $DataDir,
            "--suffix", $Suffix,
            "--output-dir", (Join-Path $ResultRoot $PartitionDir.Name),
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
        )
    }
}

if (-not ("IdsSplitStabilityExecutionState" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IdsSplitStabilityExecutionState {
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
    Write-Host "Data partitions: $PartitionsRoot"
    Write-Host "Partitions: $($PartitionDirs.Count)"
    Write-Host "Runs per partition: $Runs (model seeds 42 through $([int](41 + $Runs)))"
    Write-Host "Maximum epochs: $Epochs; patience: $Patience"
    Write-Host "Results: $ResultRoot"
    Write-Host "Logs: $LogRoot"

    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $ResultRoot, $LogRoot | Out-Null
        $env:PYTHONUNBUFFERED = "1"
        $stateResult = [IdsSplitStabilityExecutionState]::SetThreadExecutionState(
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
            Write-Host "$Python $TrainScript $($Job.Arguments -join ' ')"
            continue
        }

        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        & $Python $TrainScript @($Job.Arguments) 2>&1 | Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
        $timer.Stop()
        if ($exitCode -ne 0) {
            throw "$($Job.Name) failed with exit code $exitCode. See $logPath"
        }

        "Completed=$(Get-Date -Format o)`nElapsed=$($timer.Elapsed)" |
            Set-Content -LiteralPath $markerPath -Encoding UTF8
        Write-Host "[DONE] $($Job.Name) in $($timer.Elapsed)"
    }

    $AggregateArgs = @(
        "--partitions-root", $PartitionsRoot,
        "--results-root", $ResultRoot,
        "--output-dir", (Join-Path $ResultRoot "summary"),
        "--include-existing"
    )
    Write-Host ""
    Write-Host "[SUMMARY] Aggregating model-seed and split-selection variance"
    if ($DryRun) {
        Write-Host "$Python $AggregateScript $($AggregateArgs -join ' ')"
        Write-Host ""
        Write-Host "Dry run complete. No training or aggregation was started."
    }
    else {
        & $Python $AggregateScript @AggregateArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Aggregation failed with exit code $LASTEXITCODE"
        }
        Write-Host ""
        Write-Host "All profile-disjoint split-stability experiments completed successfully."
    }
}
finally {
    if ($sleepStateSet) {
        [void][IdsSplitStabilityExecutionState]::SetThreadExecutionState($ES_CONTINUOUS)
    }
    $env:PYTHONUNBUFFERED = $previousPythonUnbuffered
}
