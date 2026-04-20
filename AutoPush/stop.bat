@echo off
setlocal

REM 读取端口（默认 8787）；支持从 .env 读自定义端口
set PORT=8787
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
        if /i "%%A"=="AUTOPUSH_PORT" set PORT=%%B
    )
)

REM 找到监听 :%PORT% 的 PID 并 kill。-ano 输出最后一列是 PID。
REM findstr 的 "LISTENING" 过滤掉 TIME_WAIT / ESTABLISHED 之类不该误杀的连接
set FOUND=0
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo [AutoPush] stop PID %%P on port %PORT%
    taskkill /F /PID %%P
    set FOUND=1
)

if "%FOUND%"=="0" (
    echo [AutoPush] 端口 %PORT% 上没有发现监听进程
)

endlocal
pause
