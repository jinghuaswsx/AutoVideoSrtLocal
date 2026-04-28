[CmdletBinding()]
param(
    [string]$ServerHost = "172.30.254.14",
    [string]$User = "root",
    [string]$KeyPath = "C:\Users\admin\.ssh\CC.pem",
    [int]$CdpPort = 9223
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $KeyPath)) {
    throw "SSH key was not found: $KeyPath"
}

$sshExe = (Get-Command ssh.exe -ErrorAction Stop).Source
$args = @(
    "-i", $KeyPath,
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=60",
    "-N",
    "-T",
    "-L", "$CdpPort`:127.0.0.1:9223",
    "$User@$ServerHost"
)

Write-Host "Opening SSH tunnel for MK selection browser CDP..." -ForegroundColor Green
Write-Host "CDP URL: http://127.0.0.1:$CdpPort/json/version"
Write-Host "Use Sunlogin to view the actual browser window on the cjh desktop."
Write-Host "Keep this window open while using the remote browser."

& $sshExe @args
