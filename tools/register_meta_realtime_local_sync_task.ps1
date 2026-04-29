$ErrorActionPreference = "Stop"

$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptPath
$Runner = Join-Path $RepoRoot "tools\meta_realtime_local_sync.ps1"
$TaskName = "AutoVideoSrt Meta Realtime Local Sync"

if (-not (Test-Path -LiteralPath $Runner)) {
  throw "Runner script not found: $Runner"
}

$TaskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Runner`""

& schtasks.exe /Create `
  /TN $TaskName `
  /SC MINUTE `
  /MO 20 `
  /TR $TaskCommand `
  /ST 00:00 `
  /F | Out-Null

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
