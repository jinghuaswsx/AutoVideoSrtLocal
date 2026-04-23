# AutoPush - 本地推送代理

AutoPush 是 AutoVideoSrt 项目的本地中转层。浏览器通过 `127.0.0.1:8787` 访问它，再由它代理调用当前正式入口 `http://172.30.254.14/` 和下游推送服务。

```text
Browser (your computer) -> AutoPush (127.0.0.1:8787)
                            -> AutoVideoSrt OpenAPI (172.30.254.14)
                            -> downstream push service (172.17.254.77:22400)
```

## 需求

- Python 3.10+
- FastAPI + httpx
- 本机可访问：
  - `http://172.30.254.14/`
  - `http://172.17.254.77:22400`

## 启动

Windows 直接双击 `run.bat`。

其它平台：

```bash
cd AutoPush
pip install -r requirements.txt
cp .env.example .env
python main.py
```

启动后打开 `http://127.0.0.1:8787`

## 配置

复制 `.env.example` 后按需调整这些值：

- `AUTOVIDEO_BASE_URL=http://172.30.254.14`
- `AUTOVIDEO_API_KEY=autovideosrt-materials-openapi`
- `PUSH_MEDIAS_TARGET=http://172.17.254.77:22400/dify/shopify/medias`
- `AUTOPUSH_PORT=8787`

## 页面与接口

- `GET /api/config`
- `GET /api/materials`
- `GET /api/materials/{product_code}`
- `GET /api/materials/{product_code}/push-payload?lang=`
- `POST /api/push/medias`

## 目录

- `main.py` - FastAPI 入口
- `backend/settings.py` - 环境变量读取
- `backend/routes.py` - 路由
- `static/` - 前端页面

## 备注

- 改 `.env` 后需要重启 AutoPush
- 这个仓库当前的正式入口不是旧端口地址，默认契约已经切到 `http://172.30.254.14/`
