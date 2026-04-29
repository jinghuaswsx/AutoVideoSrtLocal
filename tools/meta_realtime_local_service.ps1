param(
  [ValidateSet("start", "stop", "restart", "status")]
  [string]$Action = "status"
)

$ErrorActionPreference = "Stop"

$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptPath
$Pythonw = "C:\Python314\pythonw.exe"
if (-not (Test-Path $Pythonw)) {
  $Pythonw = "pythonw.exe"
}

$ServiceScript = Join-Path $RepoRoot "tools\meta_realtime_local_daemon.py"
$StateDir = Join-Path $RepoRoot "scratch\meta_realtime_local"
$PidPath = Join-Path $StateDir "service.pid"
$LogPath = Join-Path $StateDir "logs\meta_realtime_local_service.log"

function Get-ServiceProcess {
  if (-not (Test-Path $PidPath)) { return $null }
  $pidValue = (Get-Content -Raw $PidPath).Trim()
  if (-not $pidValue) { return $null }
  return Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
}

function Start-LocalService {
  New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
  $existing = Get-ServiceProcess
  if ($existing) {
    Write-Output "running pid=$($existing.Id)"
    return
  }
  Start-Process -FilePath $Pythonw -ArgumentList @($ServiceScript, "--service") -WorkingDirectory $RepoRoot -WindowStyle Hidden
  Start-Sleep -Seconds 2
  $started = Get-ServiceProcess
  if ($started) {
    Write-Output "started pid=$($started.Id)"
  } else {
    throw "service did not start"
  }
}

function Stop-LocalService {
  $existing = Get-ServiceProcess
  if (-not $existing) {
    Write-Output "stopped"
    return
  }
  Stop-Process -Id $existing.Id -Force
  Start-Sleep -Seconds 1
  Write-Output "stopped pid=$($existing.Id)"
}

function Show-LocalStatus {
  $existing = Get-ServiceProcess
  if ($existing) {
    Write-Output "running pid=$($existing.Id) started=$($existing.StartTime)"
  } else {
    Write-Output "stopped"
  }
  if (Test-Path $LogPath) {
    Get-Content -Tail 8 $LogPath
  }
}

switch ($Action) {
  "start" { Start-LocalService }
  "stop" { Stop-LocalService }
  "restart" { Stop-LocalService; Start-LocalService }
  "status" { Show-LocalStatus }
}
