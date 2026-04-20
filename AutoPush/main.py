"""AutoPush FastAPI 入口。

运行方式：
  双击 run.bat，或者在本目录执行：
    python -m uvicorn main:app --host 127.0.0.1 --port 8787 --reload

然后浏览器访问 http://127.0.0.1:8787
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.routes import api
from backend.settings import get_settings


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AutoPush", version="0.1.0")

    app.include_router(api)

    # 暴露运行时配置给前端（只读、非敏感字段）
    @app.get("/api/config")
    async def _config() -> dict:
        return {
            "autovideoBaseUrl": settings.autovideo_base_url,
            "pushMediasTarget": settings.push_medias_target,
        }

    # 挂载静态目录；/ 直接 serve index.html
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def _index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False,
    )
