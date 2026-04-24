[CmdletBinding()]
param(
    [string]$ServerHost = "172.30.254.14",
    [string]$User = "root",
    [string]$KeyPath = "C:\Users\admin\.ssh\CC.pem",
    [int]$NoVncPort = 6081,
    [int]$CdpPort = 9223,
    [switch]$NoOpenBrowser
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
    "-L", "$NoVncPort`:127.0.0.1:6081",
    "-L", "$CdpPort`:127.0.0.1:9223",
    "$User@$ServerHost"
)

Write-Host "Opening MK selection SSH tunnel..." -ForegroundColor Green
Write-Host "noVNC URL: http://127.0.0.1:$NoVncPort/vnc.html"
Write-Host "CDP URL:   http://127.0.0.1:$CdpPort/json/version"
Write-Host "Keep this window open while using the remote browser."

if (-not $NoOpenBrowser) {
    $openCommand = "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:$NoVncPort/vnc.html'"
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile",
        "-Command",
        $openCommand
    ) | Out-Null
}

& $sshExe @args
