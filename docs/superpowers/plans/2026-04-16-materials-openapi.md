# Materials OpenAPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为外部系统新增一个按 `product_code` 查询素材信息的开放接口，使用 `X-API-Key` 校验权限，并返回产品主图、视频和视频封面的 TOS 临时签名下载地址。

**Architecture:** 在 `web/routes` 下新增独立的开放接口 blueprint，与站内登录态 `medias` 路由隔离。接口内部复用现有 `appcore.medias` 查询能力和 `appcore.tos_clients.generate_signed_media_download_url()` 生成签名地址，配置从 `config.py` 读取 `.env` 中的共享 `apikey`。对外交付单独的 Markdown 接口说明文档。

**Tech Stack:** Flask blueprints, pytest, 项目现有 `appcore.medias`, 项目现有 TOS 签名下载封装, `.env` / `.env.example`

---

## File Structure

- Create: `web/routes/openapi_materials.py`
  外部素材开放接口，负责 `apikey` 校验、`product_code` 查找、聚合返回和错误响应。

- Modify: `web/app.py`
  导入并注册新的 `openapi_materials` blueprint。

- Modify: `config.py`
  新增 `OPENAPI_MEDIA_API_KEY` 配置读取。

- Modify: `.env.example`
  增加 `OPENAPI_MEDIA_API_KEY=` 示例项，提醒部署时配置。

- Create: `tests/test_openapi_materials_routes.py`
  覆盖缺失/错误 `apikey`、产品不存在、成功聚合返回、签名地址生成等路由测试。

- Modify: `tests/test_config.py`
  覆盖 `OPENAPI_MEDIA_API_KEY` 默认值读取行为。

- Create: `docs/素材信息获取接口API.md`
  给外部调用方的最终使用文档。

## Task 1: 配置开放接口鉴权键

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `tests/test_config.py`

- [ ] **Step 1: 写配置读取的失败测试**

在 `tests/test_config.py` 追加：

```python
def test_materials_openapi_key_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("OPENAPI_MEDIA_API_KEY", raising=False)

    import importlib
    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.OPENAPI_MEDIA_API_KEY == ""
```

- [ ] **Step 2: 运行测试确认当前失败**

Run: `pytest tests/test_config.py -q -k materials_openapi_key_defaults_to_empty`

Expected: `AttributeError` 或断言失败，因为 `config.py` 里还没有 `OPENAPI_MEDIA_API_KEY`

- [ ] **Step 3: 在配置层增加新变量**

在 `config.py` 的 TOS / OpenAPI 附近新增：

```python
OPENAPI_MEDIA_API_KEY = _env("OPENAPI_MEDIA_API_KEY")
```

同时在 `.env.example` 增加一行占位：

```dotenv
OPENAPI_MEDIA_API_KEY=changeme-materials-openapi-key
```

注意：

- 不要把真实生产密钥提交到 Git
- 用户要求“先默认写死一个 apikey 放到 `.env`”，实现时只改本地/服务器 `.env`，不把真实 key 写进 `.env.example`

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_config.py -q -k materials_openapi_key_defaults_to_empty`

Expected: `1 passed`

- [ ] **Step 5: 提交**

```bash
git add config.py .env.example tests/test_config.py
git commit -m "feat: add materials openapi api key config"
```

## Task 2: 搭建开放接口路由和 apikey 校验

**Files:**
- Create: `web/routes/openapi_materials.py`
- Modify: `web/app.py`
- Test: `tests/test_openapi_materials_routes.py`

- [ ] **Step 1: 先写路由鉴权失败测试**

新建 `tests/test_openapi_materials_routes.py`，先放最小测试：

```python
from web.app import create_app


def _client():
    app = create_app()
    return app.test_client()


def test_materials_openapi_rejects_missing_api_key(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    client = _client()

    response = client.get("/openapi/materials/sonic-lens-refresher")

    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid api key"


def test_materials_openapi_rejects_wrong_api_key(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    client = _client()

    response = client.get(
        "/openapi/materials/sonic-lens-refresher",
        headers={"X-API-Key": "wrong-key"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid api key"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_openapi_materials_routes.py -q -k "missing_api_key or wrong_api_key"`

Expected: `404` 或 `ImportError`，因为开放接口 blueprint 还不存在

- [ ] **Step 3: 新建开放接口 blueprint 并注册**

新建 `web/routes/openapi_materials.py`：

```python
from __future__ import annotations

from flask import Blueprint, jsonify, request

import config

bp = Blueprint("openapi_materials", __name__, url_prefix="/openapi/materials")


def _api_key_valid() -> bool:
    expected = (config.OPENAPI_MEDIA_API_KEY or "").strip()
    provided = (request.headers.get("X-API-Key") or "").strip()
    return bool(expected) and provided == expected


@bp.route("/<product_code>", methods=["GET"])
def get_material(product_code: str):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    return jsonify({"error": "product not found"}), 404
```

在 `web/app.py` 增加：

```python
from web.routes.openapi_materials import bp as openapi_materials_bp
```

并在注册区增加：

```python
app.register_blueprint(openapi_materials_bp)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_openapi_materials_routes.py -q -k "missing_api_key or wrong_api_key"`

Expected: `2 passed`

- [ ] **Step 5: 提交**

```bash
git add web/routes/openapi_materials.py web/app.py tests/test_openapi_materials_routes.py
git commit -m "feat: add materials openapi auth route"
```

## Task 3: 实现按 product_code 聚合素材并生成签名地址

**Files:**
- Modify: `web/routes/openapi_materials.py`
- Test: `tests/test_openapi_materials_routes.py`

- [ ] **Step 1: 写成功聚合返回的失败测试**

在 `tests/test_openapi_materials_routes.py` 追加：

```python
def test_materials_openapi_returns_product_assets(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    client = _client()

    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: {
            "id": 123,
            "name": "Sonic Lens Refresher",
            "product_code": code,
            "archived": 0,
            "created_at": None,
            "updated_at": None,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_covers",
        lambda pid: {
            "en": "1/medias/123/cover_en.jpg",
            "de": "1/medias/123/cover_de.jpg",
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_copywritings",
        lambda pid: [
            {"lang": "en", "title": "Title", "body": "Body", "description": "Desc", "ad_carrier": None, "ad_copy": None, "ad_keywords": None},
            {"lang": "de", "title": "Titel", "body": "Text", "description": "Beschreibung", "ad_carrier": None, "ad_copy": None, "ad_keywords": None},
        ],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_items",
        lambda pid: [
            {
                "id": 456,
                "lang": "en",
                "filename": "demo.mp4",
                "display_name": "demo.mp4",
                "object_key": "1/medias/123/demo.mp4",
                "cover_object_key": "1/medias/123/item_cover.jpg",
                "duration_seconds": 12.3,
                "file_size": 1234567,
                "created_at": None,
            },
            {
                "id": 457,
                "lang": "en",
                "filename": "demo-2.mp4",
                "display_name": "demo-2.mp4",
                "object_key": "1/medias/123/demo-2.mp4",
                "cover_object_key": None,
                "duration_seconds": 8.8,
                "file_size": 7654321,
                "created_at": None,
            },
        ],
    )

    signed_calls = []

    def fake_signed_url(object_key, expires=None):
        signed_calls.append(object_key)
        return f"https://signed.example.com/{object_key}"

    monkeypatch.setattr(
        "web.routes.openapi_materials.tos_clients.generate_signed_media_download_url",
        fake_signed_url,
    )

    response = client.get(
        "/openapi/materials/sonic-lens-refresher",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["product"]["product_code"] == "sonic-lens-refresher"
    assert payload["covers"]["en"]["download_url"] == "https://signed.example.com/1/medias/123/cover_en.jpg"
    assert payload["copywritings"]["en"][0]["title"] == "Title"
    assert payload["items"][0]["video_download_url"] == "https://signed.example.com/1/medias/123/demo.mp4"
    assert payload["items"][0]["video_cover_download_url"] == "https://signed.example.com/1/medias/123/item_cover.jpg"
    assert payload["items"][1]["video_cover_download_url"] is None
    assert signed_calls == [
        "1/medias/123/cover_en.jpg",
        "1/medias/123/cover_de.jpg",
        "1/medias/123/demo.mp4",
        "1/medias/123/item_cover.jpg",
        "1/medias/123/demo-2.mp4",
    ]
```

再补一个不存在测试：

```python
def test_materials_openapi_returns_404_for_missing_product(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    client = _client()

    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: None,
    )

    response = client.get(
        "/openapi/materials/not-found",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "product not found"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_openapi_materials_routes.py -q -k "returns_product_assets or returns_404_for_missing_product"`

Expected: `404` 仍然来自空壳路由，或断言失败，因为还没聚合素材数据

- [ ] **Step 3: 实现聚合序列化和签名地址**

把 `web/routes/openapi_materials.py` 扩成完整实现：

```python
from __future__ import annotations

from collections import defaultdict

from flask import Blueprint, jsonify, request

import config
from appcore import medias, tos_clients

bp = Blueprint("openapi_materials", __name__, url_prefix="/openapi/materials")


def _api_key_valid() -> bool:
    expected = (config.OPENAPI_MEDIA_API_KEY or "").strip()
    provided = (request.headers.get("X-API-Key") or "").strip()
    return bool(expected) and provided == expected


def _serialize_cover_map(covers: dict[str, str]) -> dict[str, dict]:
    payload = {}
    for lang, object_key in covers.items():
        payload[lang] = {
            "object_key": object_key,
            "download_url": tos_clients.generate_signed_media_download_url(object_key),
            "expires_in": config.TOS_SIGNED_URL_EXPIRES,
        }
    return payload


def _group_copywritings(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["lang"]].append({
            "title": row.get("title"),
            "body": row.get("body"),
            "description": row.get("description"),
            "ad_carrier": row.get("ad_carrier"),
            "ad_copy": row.get("ad_copy"),
            "ad_keywords": row.get("ad_keywords"),
        })
    return dict(grouped)


def _serialize_items(rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        items.append({
            "id": row["id"],
            "lang": row.get("lang") or "en",
            "filename": row["filename"],
            "display_name": row.get("display_name") or row["filename"],
            "object_key": row["object_key"],
            "video_download_url": tos_clients.generate_signed_media_download_url(row["object_key"]),
            "cover_object_key": row.get("cover_object_key"),
            "video_cover_download_url": (
                tos_clients.generate_signed_media_download_url(row["cover_object_key"])
                if row.get("cover_object_key") else None
            ),
            "duration_seconds": row.get("duration_seconds"),
            "file_size": row.get("file_size"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        })
    return items


@bp.route("/<product_code>", methods=["GET"])
def get_material(product_code: str):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    product = medias.get_product_by_code((product_code or "").strip().lower())
    if not product:
        return jsonify({"error": "product not found"}), 404

    covers = medias.get_product_covers(product["id"])
    copywritings = medias.list_copywritings(product["id"])
    items = medias.list_items(product["id"])

    return jsonify({
        "product": {
            "id": product["id"],
            "product_code": product.get("product_code"),
            "name": product.get("name"),
            "archived": bool(product.get("archived")),
            "created_at": product["created_at"].isoformat() if product.get("created_at") else None,
            "updated_at": product["updated_at"].isoformat() if product.get("updated_at") else None,
        },
        "covers": _serialize_cover_map(covers),
        "copywritings": _group_copywritings(copywritings),
        "items": _serialize_items(items),
        "expires_in": config.TOS_SIGNED_URL_EXPIRES,
    })
```

- [ ] **Step 4: 运行专项测试确认通过**

Run: `pytest tests/test_openapi_materials_routes.py -q`

Expected: 全部通过

- [ ] **Step 5: 跑回归确保没有带崩配置和应用工厂**

Run: `pytest tests/test_openapi_materials_routes.py tests/test_config.py tests/test_web_routes.py -q -k "materials_openapi or materials_page_contains_aligned_create_modal_layout or materials_page_contains_aligned_edit_modal_layout"`

Expected: `0 failed`

- [ ] **Step 6: 提交**

```bash
git add web/routes/openapi_materials.py tests/test_openapi_materials_routes.py
git commit -m "feat: add materials openapi aggregation endpoint"
```

## Task 4: 交付外部使用文档并补本地 `.env`

**Files:**
- Create: `docs/素材信息获取接口API.md`
- Modify: `.env`（仅本地/部署环境，不提交真实 key）

- [ ] **Step 1: 新建外部接口文档**

写 `docs/素材信息获取接口API.md`，内容至少包含：

```md
# 素材信息获取接口 API

## 接口地址

GET /openapi/materials/<product_code>

## 认证方式

请求头：

X-API-Key: your-api-key

## 成功响应

```json
{
  "product": {},
  "covers": {},
  "copywritings": {},
  "items": [],
  "expires_in": 3600
}
```

## 说明

- `video_download_url` 是视频下载地址
- `video_cover_download_url` 是视频封面下载地址，没有封面时为 `null`
- 所有下载地址都是 TOS 临时签名地址，会过期
```

- [ ] **Step 2: 在本地 `.env` 写入默认 apikey**

只修改本地开发/部署环境 `.env`，不要把真实值提交到 Git：

```dotenv
OPENAPI_MEDIA_API_KEY=<your-api-key>
```

如果部署到线上，同步把服务器 `/opt/autovideosrt/.env` 也加上同一项。

- [ ] **Step 3: 做一次手工烟雾验证**

本地或测试环境执行：

```bash
curl -H "X-API-Key: $OPENAPI_MEDIA_API_KEY" \
  http://127.0.0.1:5000/openapi/materials/sonic-lens-refresher
```

Expected:

- `apikey` 正确时返回 `200` 或 `404`
- `apikey` 错误时返回 `401`

- [ ] **Step 4: 提交文档**

```bash
git add docs/素材信息获取接口API.md
git commit -m "docs: add materials openapi usage guide"
```

## Self-Review Checklist

- [ ] Spec 里的 `product_code` 查询在 Task 2/3 里有实现
- [ ] `X-API-Key` 校验在 Task 1/2 里有配置和测试
- [ ] 产品主图、视频、视频封面签名地址都在 Task 3 里有覆盖
- [ ] `401` / `404` 错误响应在 Task 2/3 里有覆盖
- [ ] 对外文档 `docs/素材信息获取接口API.md` 在 Task 4 里有交付
- [ ] 没有把真实 apikey 写进 Git 跟踪文件
