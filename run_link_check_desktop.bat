@echo off
setlocal EnableDelayedExpansion

cd /d %~dp0

set "ROOT=%~dp0"
set "PORTABLE_EXE=%ROOT%dist\LinkCheckDesktop\LinkCheckDesktop.exe"
set "VENV_DIR=%ROOT%.venv_link_check_runtime"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "READY_FILE=%VENV_DIR%\.ready"

if exist "%PORTABLE_EXE%" (
    echo [LinkCheckDesktop] launching portable exe...
    "%PORTABLE_EXE%"
    set "EXIT_CODE=%ERRORLEVEL%"
    if not "%EXIT_CODE%"=="0" (
        echo [LinkCheckDesktop] portable exe exited with code %EXIT_CODE%
        pause
    )
    exit /b %EXIT_CODE%
)

if not exist "%PYTHON_EXE%" (
    echo [LinkCheckDesktop] runtime venv not found, creating...
    call :resolve_bootstrap_python
    if errorlevel 1 exit /b 1
    !BOOTSTRAP_PYTHON! -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [LinkCheckDesktop] failed to create venv
        pause
        exit /b 1
    )
)

if not exist "%READY_FILE%" (
    echo [LinkCheckDesktop] first run, installing desktop dependencies...
    "%PYTHON_EXE%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo [LinkCheckDesktop] failed to upgrade pip
        pause
        exit /b 1
    )
    "%PYTHON_EXE%" -m pip install -r "%ROOT%link_check_desktop\requirements.txt"
    if errorlevel 1 (
        echo [LinkCheckDesktop] failed to install requirements
        pause
        exit /b 1
    )
    echo ready>"%READY_FILE%"
)

echo [LinkCheckDesktop] launching desktop client...
"%PYTHON_EXE%" -m link_check_desktop.main
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [LinkCheckDesktop] desktop client exited with code %EXIT_CODE%
    pause
)
exit /b %EXIT_CODE%

:resolve_bootstrap_python
where py >nul 2>nul
if %errorlevel%==0 (
    set "BOOTSTRAP_PYTHON=py -3"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    set "BOOTSTRAP_PYTHON=python"
    exit /b 0
)

echo [LinkCheckDesktop] no usable Python found, install Python 3.11+ first
pause
exit /b 1
