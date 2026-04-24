[CmdletBinding()]
param(
    [switch]$SkipLoginPrompt = $true
)

$ErrorActionPreference = "Stop"

function Get-PythonLauncher {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3")
    }

    throw "No available Python launcher was found (py / python)."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logDir = Join-Path $repoRoot "output\shopifyid_dianxiaomi_sync"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "shopifyid-dianxiaomi-sync-scheduled-$timestamp.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location $repoRoot

if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$launcher = @(Get-PythonLauncher)
$command = @($launcher + @("tools\shopifyid_dianxiaomi_sync.py"))
if ($SkipLoginPrompt) {
    $command += "--skip-login-prompt"
}

$header = @(
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] start shopifyid dianxiaomi daily sync",
    "repo_root=$repoRoot",
    "log_path=$logPath",
    "command=$($command -join ' ')",
    ""
)
$header | Out-File -FilePath $logPath -Encoding utf8

$exe = $command[0]
$cmdArgs = @()
if ($command.Count -gt 1) {
    $cmdArgs = $command[1..($command.Count - 1)]
}

$stdoutPath = Join-Path $logDir "shopifyid-dianxiaomi-sync-scheduled-$timestamp.stdout.tmp"
$stderrPath = Join-Path $logDir "shopifyid-dianxiaomi-sync-scheduled-$timestamp.stderr.tmp"

try {
    $process = Start-Process `
        -FilePath $exe `
        -ArgumentList $cmdArgs `
        -WorkingDirectory $repoRoot `
        -Wait `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    if (Test-Path $stdoutPath) {
        Get-Content $stdoutPath | Tee-Object -FilePath $logPath -Append
    }
    if (Test-Path $stderrPath) {
        Get-Content $stderrPath | Tee-Object -FilePath $logPath -Append
    }

    $exitCode = $process.ExitCode
} finally {
    if (Test-Path $stdoutPath) {
        Remove-Item $stdoutPath -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $stderrPath) {
        Remove-Item $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] exit_code=$exitCode" | Tee-Object -FilePath $logPath -Append
exit $exitCode
