[CmdletBinding()]
param(
    [string]$TaskName = "AutoVideoSrtLocal-ShopifyIdDianxiaomiSyncDaily",
    [string]$StartTime = "12:11"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runnerPath = Join-Path $scriptDir "shopifyid_dianxiaomi_sync_daily.ps1"

if (-not (Test-Path $runnerPath)) {
    throw "Daily sync script was not found: $runnerPath"
}

try {
    $dailyAt = [datetime]::ParseExact($StartTime, "HH:mm", $null)
} catch {
    throw "StartTime must use HH:mm format, for example 12:11."
}

$powershellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$runnerPath`""

$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Daily -At $dailyAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$description = "Run the Dianxiaomi Shopify ID sync every day at 12:11 and backfill empty media_products.shopifyid values."

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host "Windows scheduled task registered:" -ForegroundColor Green
$task | Select-Object TaskName, State, TaskPath | Format-Table -AutoSize
$info | Select-Object LastRunTime, NextRunTime, LastTaskResult | Format-List
