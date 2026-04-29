$ErrorActionPreference = "Stop"

$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptPath
$LogDir = Join-Path $RepoRoot "scratch\meta_realtime_local\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "meta_realtime_local_sync_$Stamp.log"

Push-Location $RepoRoot
try {
  "[$(Get-Date -Format s)] start ADS Power 90 Meta realtime sync" | Out-File -FilePath $LogPath -Encoding utf8
  $Python = "C:\Python314\python.exe"
  if (-not (Test-Path $Python)) {
    $Python = "python"
  }
  $PreviousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $env:NODE_NO_WARNINGS = "1"
  $Output = & $Python (Join-Path $RepoRoot "tools\meta_realtime_local_sync.py") --once 2>&1
  $ExitCode = $LASTEXITCODE
  $ErrorActionPreference = $PreviousErrorActionPreference
  $Output | Out-File -FilePath $LogPath -Append -Encoding utf8
  "[$(Get-Date -Format s)] exit=$ExitCode" | Out-File -FilePath $LogPath -Append -Encoding utf8
  exit $ExitCode
}
catch {
  $_ | Out-File -FilePath $LogPath -Append -Encoding utf8
  "[$(Get-Date -Format s)] exit=1" | Out-File -FilePath $LogPath -Append -Encoding utf8
  exit 1
}
finally {
  Pop-Location
}
