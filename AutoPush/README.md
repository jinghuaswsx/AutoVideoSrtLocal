# AutoPush — 本地推送代理

**定位**：AutoVideoSrt 项目的**内网子项目**。远程主项目（服务端）由于 CORS 限制无法在浏览器里直接调用内网下游推送服务，因此把「推送」流程拆到本地运行——由你这台能访问内网的电脑提供一个本地 HTTP 服务，浏览器走**同源**访问它，由它**代理**调用上游 AutoVideoSrt OpenAPI 和下游内网推送服务。

```
浏览器 (你的电脑) ──同源─▶ AutoPush (127.0.0.1:8787)
                              ├──▶ AutoVideoSrt OpenAPI  (14.103.220.208:8888)
                              └──▶ 下游推送服务          (172.17.254.77:22400)
```

## 1. 前置要求

- Windows / macOS / Linux 都行（开发环境是 Windows）
- Python 3.10+（FastAPI + httpx）
- 本机能通以下两个地址：
  - `http://14.103.220.208:8888`（AutoVideoSrt 公网）
  - `http://172.17.254.77:22400`（内网推送服务）
- 从 AutoVideoSrt 管理员那里拿一个 `X-API-Key`（就是 `OPENAPI_MEDIA_API_KEY`）

## 2. 启动

### Windows — 双击 `run.bat`

```
AutoPush\run.bat
```

首次会自动 `pip install -r requirements.txt`，并复制 `.env.example` 为 `.env`。启动完成后直接打开浏览器：<http://127.0.0.1:8787>

### 其他系统 / 手动

```bash
cd AutoPush
pip install -r requirements.txt
cp .env.example .env          # 按需修改里面三个变量
python main.py                 # 或者：python -m uvicorn main:app --port 8787
```

## 3. 配置（`.env`）

| 字段 | 说明 | 默认 |
| --- | --- | --- |
| `AUTOVIDEO_BASE_URL` | 上游 AutoVideoSrt 地址 | `http://14.103.220.208:8888` |
| `AUTOVIDEO_API_KEY` | 上游 OpenAPI 鉴权 Key | `autovideosrt-materials-openapi` |
| `PUSH_MEDIAS_TARGET` | 下游推送目标 | `http://172.17.254.77:22400/dify/shopify/medias` |
| `AUTOPUSH_PORT` | 本地监听端口 | `8787` |

## 4. 三个页面

| Tab | 做什么 |
| --- | --- |
| 推送列表 | 从 `GET /openapi/materials` 拉产品清单（分页、按关键词查），每行展示各语种就绪度（主图 / 文案 / 视频数）。选好语种点「去载荷」→ 自动跳到载荷 tab 并预填 product_code + lang |
| 推送创建 | 输入 product_code → 从上游拉完整素材 → 手工编辑一份推送 JSON（右下角实时预览）。**当前版本只做展示，没有推送按钮**，保持和 push-module 原版一致 |
| 推送载荷 | 输入 product_code + lang → 上游生成推送 payload → 视频/封面预览 → 点「推送」走本地代理 POST 到下游 |

## 5. 后端 API（FastAPI，仅供前端调用）

所有接口都在 `127.0.0.1:8787`，无鉴权（本地回环监听已是隔离）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/config` | 返回 upstream / target 两个地址，供页头展示 |
| `GET` | `/api/materials?page=&page_size=&q=&archived=` | 代理上游产品列表 |
| `GET` | `/api/materials/{product_code}` | 代理上游单产品详情 |
| `GET` | `/api/materials/{product_code}/push-payload?lang=` | 代理上游推送载荷生成 |
| `POST` | `/api/push/medias` | 代理 POST 到 `PUSH_MEDIAS_TARGET` |

## 6. 目录结构

```
AutoPush/
├── README.md              # 本文件
├── requirements.txt       # fastapi / uvicorn / httpx / python-dotenv
├── main.py                # FastAPI 入口
├── .env.example           # 环境变量样板
├── run.bat                # Windows 一键启动
├── backend/
│   ├── settings.py        # Settings + get_settings
│   ├── errors.py          # UpstreamServiceError
│   ├── autovideo.py       # AutoVideoService（httpx 客户端）
│   └── routes.py          # FastAPI 路由
└── static/
    ├── index.html         # 3-tab 外壳
    ├── app.css            # Ocean Blue 风格样式
    └── app.js             # 所有前端逻辑（原生 JS，ES module）
```

## 7. 调试小贴士

- 加载失败看 Network tab：状态码 502 = 下游不可达；401 = API Key 错；4xx/5xx 详情看返回 JSON 的 `detail` 字段
- 更改 `.env` 后**重启** AutoPush 生效（`get_settings()` 有 `@lru_cache`）
- 想临时试推送到假地址验证前端，把 `PUSH_MEDIAS_TARGET` 指到 `https://httpbin.org/post`，响应会把你发的 body 原样 echo 回来

## 8. 非本项目负责的事

- 不做鉴权（本地回环监听默认只有你自己访问）
- 不做历史记录持久化（推送响应只在当前页面展示）
- 不写回 AutoVideoSrt（只读）
- 不做自动更新：AutoVideoSrt OpenAPI 接口有变动时，需要同步更新 `backend/autovideo.py`
