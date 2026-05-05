@echo off
REM Install Caddy gateway to G:\gateway\
REM Pre-requisite: caddy.exe downloaded somewhere on this host (default location:
REM G:\subtitle\downloads\caddy\caddy.exe — created by the subtitle bootstrap).
REM Override with: install_caddy.bat <path-to-caddy.exe>
setlocal
set "GW=G:\gateway"
set "SRC=%~1"
if "%SRC%"=="" set "SRC=G:\subtitle\downloads\caddy\caddy.exe"

if not exist "%SRC%" (
    echo [ERR] caddy.exe not found at %SRC%
    echo Download caddy_*_windows_amd64.zip from https://github.com/caddyserver/caddy/releases
    echo and pass the path:  install_caddy.bat C:\path\to\caddy.exe
    exit /b 1
)

mkdir "%GW%" 2>nul
mkdir "%GW%\logs" 2>nul

copy /Y "%SRC%" "%GW%\caddy.exe" >nul
copy /Y "%~dp0Caddyfile" "%GW%\Caddyfile" >nul
copy /Y "%~dp0start.bat" "%GW%\start.bat" >nul

REM Validate config
"%GW%\caddy.exe" validate --config "%GW%\Caddyfile"
if errorlevel 1 (
    echo [ERR] Caddyfile validation failed
    exit /b 1
)

REM Firewall (best effort; needs admin)
net session >nul 2>&1
if %errorlevel% equ 0 (
    netsh advfirewall firewall show rule name="GPU-Gateway-80" >nul 2>&1
    if errorlevel 1 (
        echo [INFO] Adding firewall rule GPU-Gateway-80
        netsh advfirewall firewall add rule name="GPU-Gateway-80" dir=in action=allow protocol=TCP localport=80 >nul
    )
) else (
    echo [WARN] Not admin — skipped firewall rule. Run once as admin:
    echo        netsh advfirewall firewall add rule name="GPU-Gateway-80" dir=in action=allow protocol=TCP localport=80
)

echo [OK] Gateway installed to %GW%
echo       Run G:\gateway\start.bat to bring it up.
endlocal
