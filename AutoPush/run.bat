@echo off
cd /d %~dp0

REM 首次运行自动 pip install
if not exist .venv_marker (
    echo [AutoPush] 首次运行，安装依赖 ...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [AutoPush] pip install 失败，请手动排查
        pause
        exit /b 1
    )
    echo installed > .venv_marker
)

REM 未发现 .env 时提示从 .env.example 复制
if not exist .env (
    echo [AutoPush] 未找到 .env，已复制 .env.example 为默认配置
    copy .env.example .env >nul
)

echo [AutoPush] 启动服务 http://127.0.0.1:8787
python main.py
pause
