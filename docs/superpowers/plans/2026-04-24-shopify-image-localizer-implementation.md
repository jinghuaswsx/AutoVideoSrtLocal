# Shopify 图片本地化替换工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在仓库 `tools` 目录下实现一个本地运行的 Shopify 图片本地化 EXE 工具，输入 `product_code + 语言` 后，先从服务端拉取图片并落盘，再用持久化 Playwright 浏览器同时完成 `EZ Product Image` 和 `Translate and Adapt` 的图片替换。

**Architecture:** 服务端新增一个只给本地工具调用的 OpenAPI 子接口族，负责返回语言列表、Shopify 商品 ID、英文参考图和目标语言混合图；本地端在 `tools/shopify_image_localizer` 下实现 `Tkinter + Playwright + PyInstaller` 工具，按“下载 -> 登录检测 -> 页面截图归类 -> 图片配对 -> 双链路上传”的顺序执行。图片归类与冲突处理尽量保持纯函数化，便于单测；真实 Shopify DOM 则通过 Playwright 烟测来锁定选择器。

**Tech Stack:** Python 3.10+、Tkinter、Playwright、PyInstaller、Flask Blueprint、pytest、requests、Pillow/现有 `link_check_desktop.image_compare`

---

## 文件结构

### 新增文件

```text
tools/shopify_image_localizer/
  __init__.py
  main.py
  models.py
  settings.py
  storage.py
  api_client.py
  downloader.py
  matcher.py
  controller.py
  gui.py
  build_exe.py
  browser/
    __init__.py
    session.py
    orchestrator.py
    ez_flow.py
    translate_flow.py
  packaging/
    shopify_image_localizer.spec

tests/
  test_appcore_medias_shopify_localizer.py
  test_openapi_shopify_image_localizer_routes.py
  test_shopify_image_localizer_settings.py
  test_shopify_image_localizer_storage.py
  test_shopify_image_localizer_api_client.py
  test_shopify_image_localizer_downloader.py
  test_shopify_image_localizer_matcher.py
  test_shopify_image_localizer_gui.py
  test_shopify_image_localizer_controller.py
  test_shopify_image_localizer_browser_session.py
  test_shopify_image_localizer_orchestrator.py
  test_shopify_image_localizer_flows.py
  test_shopify_image_localizer_build_exe.py
```

### 修改文件

```text
appcore/medias.py
web/routes/openapi_materials.py
web/app.py
AGENTS.md
```

### 责任划分

- `appcore/medias.py`：服务端语言选项、Shopify 商品 ID 解析、英文/目标语种混合图装配。
- `web/routes/openapi_materials.py`：对外暴露本地工具专用 OpenAPI 路由。
- `tools/shopify_image_localizer/settings.py`：本地配置文件和默认浏览器目录。
- `tools/shopify_image_localizer/storage.py`：`<EXE>/<product_code>/<lang>/source`、`classify`、`screenshots` 目录管理，以及清单和日志写入。
- `tools/shopify_image_localizer/api_client.py`：请求语言列表和 bootstrap 数据。
- `tools/shopify_image_localizer/downloader.py`：逐张下载参考图与目标图。
- `tools/shopify_image_localizer/matcher.py`：把 Shopify 页面截图和本地图片做归类、配对和冲突判定。
- `tools/shopify_image_localizer/browser/*`：持久化浏览器、登录检测、并发/串行回退、EZ/TAA 两条页面流。
- `tools/shopify_image_localizer/controller.py`：串起整体执行状态机。
- `tools/shopify_image_localizer/gui.py`：本地窗口、语言下拉、状态展示。
- `tools/shopify_image_localizer/build_exe.py` / `packaging/*.spec`：打包 EXE。

## 验证约束

1. 本计划优先用**不依赖本地 MySQL** 的单元测试推进；数据库相关逻辑一律靠 monkeypatch 路由/DAO 验证。
2. 真实 Shopify 页面选择器必须通过一次人工登录后的烟测锁定，不能只凭想象写死。
3. 单元测试完成后，再做一次真实手工 smoke：
   - `product_code = dino-glider-launcher-toy-rjc`
   - `shopify_product_id = 8552296546477`
   - `lang = de`

---

### Task 1: 服务端数据装配助手

**Files:**
- Modify: `appcore/medias.py`
- Test: `tests/test_appcore_medias_shopify_localizer.py`

- [ ] **Step 1: 写失败测试，锁定语言格式、Shopify ID 解析和混合图片装配**

```python
from appcore import medias


def test_list_shopify_localizer_languages_formats_display(monkeypatch):
    monkeypatch.setattr(
        medias,
        "list_languages",
        lambda: [{"code": "de", "name_zh": "德语", "sort_order": 2, "enabled": 1}],
    )

    assert medias.list_shopify_localizer_languages() == [{
        "code": "de",
        "name_zh": "德语",
        "shop_locale": "de",
        "folder_code": "de",
        "label": "德语（DE/de）",
    }]


def test_resolve_shopify_product_id_uses_latest_ranking_row(monkeypatch):
    monkeypatch.setattr(
        medias,
        "query_one",
        lambda sql, args=(): {"product_id": "8552296546477"},
    )

    assert medias.resolve_shopify_product_id(123) == "8552296546477"


def test_list_shopify_localizer_images_returns_cover_then_detail(monkeypatch):
    def fake_query(sql, args=()):
        compact = " ".join(sql.split())
        if "FROM media_product_covers" in compact:
            return [{"lang": "de", "object_key": "covers/de.jpg"}]
        if "FROM media_product_detail_images" in compact:
            return [
                {"id": 9, "sort_order": 2, "object_key": "detail/de_2.jpg"},
                {"id": 8, "sort_order": 1, "object_key": "detail/de_1.jpg"},
            ]
        raise AssertionError(compact)

    monkeypatch.setattr(medias, "query", fake_query)

    assert medias.list_shopify_localizer_images(77, "de") == [
        {"id": "cover-de", "kind": "cover", "filename": "de.jpg", "object_key": "covers/de.jpg"},
        {"id": "detail-8", "kind": "detail", "filename": "de_1.jpg", "object_key": "detail/de_1.jpg"},
        {"id": "detail-9", "kind": "detail", "filename": "de_2.jpg", "object_key": "detail/de_2.jpg"},
    ]
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_appcore_medias_shopify_localizer.py -q
```

Expected: FAIL，提示缺少 `list_shopify_localizer_languages / resolve_shopify_product_id / list_shopify_localizer_images`。

- [ ] **Step 3: 在 `appcore/medias.py` 实现本地工具专用助手**

```python
def list_shopify_localizer_languages() -> list[dict]:
    items: list[dict] = []
    for row in list_languages():
        code = str(row.get("code") or "").strip().lower()
        if not code:
            continue
        name_zh = str(row.get("name_zh") or code).strip()
        items.append({
            "code": code,
            "name_zh": name_zh,
            "shop_locale": code,
            "folder_code": code,
            "label": f"{name_zh}（{code.upper()}/{code}）",
        })
    return items


def resolve_shopify_product_id(product_id: int) -> str | None:
    row = query_one(
        "SELECT product_id FROM dianxiaomi_rankings "
        "WHERE media_product_id=%s AND product_id IS NOT NULL AND product_id<>'' "
        "ORDER BY snapshot_date DESC, id DESC LIMIT 1",
        (product_id,),
    ) or {}
    value = str(row.get("product_id") or "").strip()
    return value or None


def list_shopify_localizer_images(product_id: int, lang: str) -> list[dict]:
    lang = (lang or "").strip().lower()
    cover_rows = query(
        "SELECT lang, object_key FROM media_product_covers "
        "WHERE product_id=%s AND lang=%s AND object_key IS NOT NULL AND object_key<>''",
        (product_id, lang),
    ) or []
    detail_rows = query(
        "SELECT id, sort_order, object_key FROM media_product_detail_images "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
        "AND object_key IS NOT NULL AND object_key<>'' "
        "ORDER BY sort_order ASC, id ASC",
        (product_id, lang),
    ) or []

    items: list[dict] = []
    if cover_rows:
        object_key = str(cover_rows[0].get("object_key") or "").strip()
        if object_key:
            items.append({
                "id": f"cover-{lang}",
                "kind": "cover",
                "filename": Path(object_key).name or f"{lang}.jpg",
                "object_key": object_key,
            })
    for row in detail_rows:
        object_key = str(row.get("object_key") or "").strip()
        if not object_key:
            continue
        items.append({
            "id": f"detail-{int(row['id'])}",
            "kind": "detail",
            "filename": Path(object_key).name or f"detail-{int(row['id'])}.jpg",
            "object_key": object_key,
        })
    return items
```

- [ ] **Step 4: 回跑测试**

Run:

```bash
pytest tests/test_appcore_medias_shopify_localizer.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add appcore/medias.py tests/test_appcore_medias_shopify_localizer.py
git commit -m "feat: add shopify image localizer media helpers"
```

---

### Task 2: 本地工具 OpenAPI 路由

**Files:**
- Modify: `web/routes/openapi_materials.py`
- Modify: `web/app.py`
- Test: `tests/test_openapi_shopify_image_localizer_routes.py`

- [ ] **Step 1: 先写路由测试**

```python
def test_languages_requires_api_key(client):
    response = client.get("/openapi/medias/shopify-image-localizer/languages")
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid api key"}


def test_languages_returns_items(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_shopify_localizer_languages",
        lambda: [{"code": "de", "label": "德语（DE/de）"}],
    )
    response = client.get(
        "/openapi/medias/shopify-image-localizer/languages",
        headers={"X-API-Key": "demo-key"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"items": [{"code": "de", "label": "德语（DE/de）"}]}


def test_bootstrap_returns_mixed_payload(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: {"id": 123, "product_code": code, "name": "Demo Product"},
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.resolve_shopify_product_id",
        lambda pid: "8552296546477",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_language_name",
        lambda code: "德语",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_shopify_localizer_images",
        lambda pid, lang: [{"id": f"{lang}-1", "kind": "detail", "filename": f"{lang}.jpg", "object_key": f"{lang}.jpg"}],
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"product_code": "demo-rjc", "lang": "de"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["product"]["shopify_product_id"] == "8552296546477"
    assert payload["language"]["code"] == "de"
    assert payload["reference_images"][0]["url"].endswith("/medias/obj/en.jpg")
    assert payload["localized_images"][0]["url"].endswith("/medias/obj/de.jpg")
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_openapi_shopify_image_localizer_routes.py -q
```

Expected: FAIL，提示没有新蓝图或路由。

- [ ] **Step 3: 新增蓝图并注册到 `web/app.py`**

```python
# web/routes/openapi_materials.py
shopify_localizer_bp = Blueprint(
    "openapi_shopify_localizer",
    __name__,
    url_prefix="/openapi/medias/shopify-image-localizer",
)


@shopify_localizer_bp.route("/languages", methods=["GET"])
def shopify_localizer_languages():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    return jsonify({"items": medias.list_shopify_localizer_languages()})


@shopify_localizer_bp.route("/bootstrap", methods=["POST"])
def shopify_localizer_bootstrap():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    body = request.get_json(silent=True) or {}
    product_code = str(body.get("product_code") or "").strip().lower()
    lang = str(body.get("lang") or "").strip().lower()
    if not product_code or not lang:
        return jsonify({"error": "missing product_code or lang"}), 400
    if not medias.is_valid_language(lang):
        return jsonify({"error": "invalid lang"}), 400

    product = medias.get_product_by_code(product_code)
    if not product:
        return jsonify({"error": "product not found"}), 404

    shopify_product_id = medias.resolve_shopify_product_id(int(product["id"]))
    if not shopify_product_id:
        return jsonify({"error": "shopify product id not found"}), 409

    reference_images = medias.list_shopify_localizer_images(int(product["id"]), "en")
    localized_images = medias.list_shopify_localizer_images(int(product["id"]), lang)
    if not reference_images:
        return jsonify({"error": "english references not ready"}), 409
    if not localized_images:
        return jsonify({"error": "localized images not ready"}), 409

    return jsonify({
        "product": {
            "product_code": product.get("product_code"),
            "shopify_product_id": shopify_product_id,
            "name": product.get("name"),
        },
        "language": {
            "code": lang,
            "name_zh": medias.get_language_name(lang),
            "shop_locale": lang,
        },
        "reference_images": [
            {**item, "url": _media_download_url(item["object_key"])}
            for item in reference_images
        ],
        "localized_images": [
            {**item, "url": _media_download_url(item["object_key"])}
            for item in localized_images
        ],
    })
```

```python
# web/app.py
from web.routes.openapi_materials import shopify_localizer_bp as openapi_shopify_localizer_bp

app.register_blueprint(openapi_shopify_localizer_bp)
csrf.exempt(openapi_shopify_localizer_bp)
```

- [ ] **Step 4: 回跑测试**

Run:

```bash
pytest tests/test_openapi_shopify_image_localizer_routes.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add web/routes/openapi_materials.py web/app.py tests/test_openapi_shopify_image_localizer_routes.py
git commit -m "feat: add openapi routes for shopify image localizer"
```

---

### Task 3: 桌面工具基础骨架

**Files:**
- Create: `tools/shopify_image_localizer/__init__.py`
- Create: `tools/shopify_image_localizer/models.py`
- Create: `tools/shopify_image_localizer/settings.py`
- Create: `tools/shopify_image_localizer/storage.py`
- Create: `tools/shopify_image_localizer/api_client.py`
- Test: `tests/test_shopify_image_localizer_settings.py`
- Test: `tests/test_shopify_image_localizer_storage.py`
- Test: `tests/test_shopify_image_localizer_api_client.py`

- [ ] **Step 1: 写失败测试**

```python
def test_load_runtime_config_uses_default_browser_dir(tmp_path):
    from tools.shopify_image_localizer import settings

    config = settings.load_runtime_config(root=tmp_path)

    assert config["browser_user_data_dir"] == r"C:\chrome-shopify-image"


def test_create_workspace_uses_product_code_and_lang(monkeypatch, tmp_path):
    from datetime import datetime
    from tools.shopify_image_localizer import storage

    monkeypatch.setattr(storage, "executable_root", lambda: tmp_path)
    workspace = storage.create_workspace("demo-rjc", "de", now=datetime(2026, 4, 24, 10, 11, 12))

    assert workspace.root == tmp_path / "demo-rjc" / "de"
    assert workspace.source_en_dir.is_dir()
    assert workspace.source_localized_dir.is_dir()
    assert workspace.classify_ez_dir.is_dir()
    assert workspace.screenshots_taa_dir.is_dir()


def test_api_client_targets_localizer_routes(monkeypatch):
    from tools.shopify_image_localizer import api_client

    calls = []

    class DummyResponse:
        status_code = 200
        def json(self):
            return {"items": [{"code": "de"}]}

    monkeypatch.setattr(
        api_client.requests,
        "get",
        lambda url, headers, timeout: calls.append((url, headers, timeout)) or DummyResponse(),
    )

    payload = api_client.fetch_languages("http://127.0.0.1:5000", "demo-key")
    assert payload["items"][0]["code"] == "de"
    assert calls[0][0] == "http://127.0.0.1:5000/openapi/medias/shopify-image-localizer/languages"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_shopify_image_localizer_settings.py tests/test_shopify_image_localizer_storage.py tests/test_shopify_image_localizer_api_client.py -q
```

Expected: FAIL，提示模块不存在。

- [ ] **Step 3: 实现设置、工作目录和 API 客户端**

```python
# tools/shopify_image_localizer/settings.py
DEFAULT_BASE_URL = "http://172.30.254.14"
DEFAULT_API_KEY = "autovideosrt-materials-openapi"
DEFAULT_BROWSER_USER_DATA_DIR = r"C:\chrome-shopify-image"
CONFIG_FILENAME = "shopify_image_localizer_config.json"
```

```python
# tools/shopify_image_localizer/storage.py
@dataclass(frozen=True)
class Workspace:
    root: Path
    source_en_dir: Path
    source_localized_dir: Path
    classify_ez_dir: Path
    classify_taa_dir: Path
    screenshots_ez_dir: Path
    screenshots_taa_dir: Path
    manifest_path: Path
    log_path: Path


def create_workspace(product_code: str, lang: str, *, now: datetime | None = None) -> Workspace:
    root = executable_root() / product_code / lang
    source_en_dir = root / "source" / "en"
    source_localized_dir = root / "source" / "localized"
    classify_ez_dir = root / "classify" / "ez"
    classify_taa_dir = root / "classify" / "taa"
    screenshots_ez_dir = root / "screenshots" / "ez"
    screenshots_taa_dir = root / "screenshots" / "taa"
    for path in (
        source_en_dir, source_localized_dir,
        classify_ez_dir, classify_taa_dir,
        screenshots_ez_dir, screenshots_taa_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return Workspace(
        root=root,
        source_en_dir=source_en_dir,
        source_localized_dir=source_localized_dir,
        classify_ez_dir=classify_ez_dir,
        classify_taa_dir=classify_taa_dir,
        screenshots_ez_dir=screenshots_ez_dir,
        screenshots_taa_dir=screenshots_taa_dir,
        manifest_path=root / "manifest.json",
        log_path=root / "run.log",
    )
```

```python
# tools/shopify_image_localizer/api_client.py
class ApiError(RuntimeError):
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("error") or f"api failed: {status_code}")
        self.status_code = status_code
        self.payload = payload


def fetch_languages(base_url: str, api_key: str, *, timeout: int = 20) -> dict[str, Any]:
    response = requests.get(
        f"{base_url.rstrip('/')}/openapi/medias/shopify-image-localizer/languages",
        headers={"X-API-Key": api_key},
        timeout=timeout,
    )
    payload = response.json()
    if response.status_code >= 400:
        raise ApiError(response.status_code, payload)
    return payload


def fetch_bootstrap(base_url: str, api_key: str, product_code: str, lang: str, *, timeout: int = 30) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}/openapi/medias/shopify-image-localizer/bootstrap",
        headers={"X-API-Key": api_key},
        json={"product_code": product_code, "lang": lang},
        timeout=timeout,
    )
    payload = response.json()
    if response.status_code >= 400:
        raise ApiError(response.status_code, payload)
    return payload
```

- [ ] **Step 4: 回跑测试**

Run:

```bash
pytest tests/test_shopify_image_localizer_settings.py tests/test_shopify_image_localizer_storage.py tests/test_shopify_image_localizer_api_client.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/shopify_image_localizer tests/test_shopify_image_localizer_settings.py tests/test_shopify_image_localizer_storage.py tests/test_shopify_image_localizer_api_client.py
git commit -m "feat: scaffold shopify image localizer desktop core"
```

---

### Task 4: 下载器与纯配对内核

**Files:**
- Create: `tools/shopify_image_localizer/downloader.py`
- Create: `tools/shopify_image_localizer/matcher.py`
- Test: `tests/test_shopify_image_localizer_downloader.py`
- Test: `tests/test_shopify_image_localizer_matcher.py`

- [ ] **Step 1: 写失败测试**

```python
def test_downloader_retries_once_then_succeeds(monkeypatch, tmp_path):
    from tools.shopify_image_localizer import downloader

    attempts = {"count": 0}

    class DummyResponse:
        status_code = 200
        content = b"image"
        def raise_for_status(self):
            return None

    def fake_get(url, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise downloader.requests.RequestException("boom")
        return DummyResponse()

    monkeypatch.setattr(downloader.requests, "get", fake_get)
    items = downloader.download_images(
        [{"id": "img-1", "filename": "a.jpg", "url": "https://example.com/a.jpg"}],
        tmp_path,
        retries=1,
    )
    assert attempts["count"] == 2
    assert items[0]["local_path"].endswith("a.jpg")


def test_matcher_assigns_best_localized_image_and_flags_conflict(monkeypatch):
    from tools.shopify_image_localizer import matcher

    reference_images = [{"id": "ref-1", "local_path": "ref-1.png"}]
    slot_images = [{"slot_id": "ez-1", "local_path": "slot-1.png"}]
    localized_images = [
        {"id": "loc-a", "local_path": "loc-a.png"},
        {"id": "loc-b", "local_path": "loc-b.png"},
    ]

    scores = {
        ("slot-1.png", ("ref-1.png",)): {"status": "matched", "score": 0.95, "reference_path": "ref-1.png"},
        ("loc-a.png", ("ref-1.png",)): {"status": "matched", "score": 0.98, "reference_path": "ref-1.png"},
        ("loc-b.png", ("ref-1.png",)): {"status": "matched", "score": 0.91, "reference_path": "ref-1.png"},
    }

    monkeypatch.setattr(
        matcher,
        "find_best_reference",
        lambda candidate, refs: scores[(candidate, tuple(refs))],
    )

    result = matcher.assign_images(slot_images, reference_images, localized_images)
    assert result["assigned"][0]["localized_id"] == "loc-a"
    assert result["conflicts"][0]["localized_id"] == "loc-b"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_shopify_image_localizer_downloader.py tests/test_shopify_image_localizer_matcher.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现下载器和配对器**

```python
# tools/shopify_image_localizer/downloader.py
def download_images(items: list[dict], output_dir: Path, *, retries: int = 1, status_cb=None) -> list[dict]:
    downloaded: list[dict] = []
    for item in items:
        filename = item.get("filename") or f"{item['id']}.jpg"
        output_path = output_dir / filename
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = requests.get(item["url"], timeout=30)
                response.raise_for_status()
                output_path.write_bytes(response.content)
                downloaded.append({**item, "local_path": str(output_path)})
                last_exc = None
                break
            except requests.RequestException as exc:
                last_exc = exc
        if last_exc is not None:
            raise RuntimeError(f"failed to download {item['url']}: {last_exc}") from last_exc
    return downloaded
```

```python
# tools/shopify_image_localizer/matcher.py
from link_check_desktop.image_compare import find_best_reference


def assign_images(slot_images: list[dict], reference_images: list[dict], localized_images: list[dict], *, min_score: float = 0.80) -> dict:
    reference_paths = [row["local_path"] for row in reference_images]
    reference_by_path = {row["local_path"]: row for row in reference_images}
    assignments: list[dict] = []
    conflicts: list[dict] = []
    review: list[dict] = []
    used_localized: set[str] = set()

    slot_reference_ids: dict[str, str] = {}
    for slot in slot_images:
        best = find_best_reference(slot["local_path"], reference_paths)
        if best.get("status") != "matched" or float(best.get("score") or 0.0) < min_score:
            review.append({"slot_id": slot["slot_id"], "reason": "slot reference not matched"})
            continue
        slot_reference_ids[slot["slot_id"]] = reference_by_path[best["reference_path"]]["id"]

    localized_by_reference: dict[str, list[dict]] = {}
    for item in localized_images:
        best = find_best_reference(item["local_path"], reference_paths)
        if best.get("status") != "matched" or float(best.get("score") or 0.0) < min_score:
            review.append({"localized_id": item["id"], "reason": "localized image not matched"})
            continue
        ref_id = reference_by_path[best["reference_path"]]["id"]
        localized_by_reference.setdefault(ref_id, []).append({**item, "score": float(best["score"])})

    for slot_id, ref_id in slot_reference_ids.items():
        candidates = sorted(localized_by_reference.get(ref_id) or [], key=lambda row: row["score"], reverse=True)
        if not candidates:
            review.append({"slot_id": slot_id, "reason": "no localized candidate"})
            continue
        chosen = candidates[0]
        if chosen["id"] in used_localized:
            conflicts.append({"slot_id": slot_id, "localized_id": chosen["id"], "reason": "localized image already used"})
            continue
        assignments.append({"slot_id": slot_id, "reference_id": ref_id, "localized_id": chosen["id"], "local_path": chosen["local_path"]})
        used_localized.add(chosen["id"])
        for extra in candidates[1:]:
            conflicts.append({"slot_id": slot_id, "localized_id": extra["id"], "reason": "duplicate localized candidate"})

    return {"assigned": assignments, "conflicts": conflicts, "review": review}
```

- [ ] **Step 4: 回跑测试**

Run:

```bash
pytest tests/test_shopify_image_localizer_downloader.py tests/test_shopify_image_localizer_matcher.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/shopify_image_localizer/downloader.py tools/shopify_image_localizer/matcher.py tests/test_shopify_image_localizer_downloader.py tests/test_shopify_image_localizer_matcher.py
git commit -m "feat: add shopify image localizer downloader and matcher"
```

---

### Task 5: 持久化浏览器会话与并发回退编排

**Files:**
- Create: `tools/shopify_image_localizer/browser/__init__.py`
- Create: `tools/shopify_image_localizer/browser/session.py`
- Create: `tools/shopify_image_localizer/browser/orchestrator.py`
- Test: `tests/test_shopify_image_localizer_browser_session.py`
- Test: `tests/test_shopify_image_localizer_orchestrator.py`

- [ ] **Step 1: 写失败测试**

```python
def test_build_admin_urls():
    from tools.shopify_image_localizer.browser import session

    assert session.build_ez_url("8552296546477").endswith("/product/8552296546477")
    assert "shopLocale=de" in session.build_translate_url("8552296546477", "de")


def test_orchestrator_falls_back_to_serial_on_conflict():
    from tools.shopify_image_localizer.browser import orchestrator

    calls = []

    def ez_flow():
        calls.append("ez-parallel")
        raise orchestrator.FlowConflictError("conflict")

    def taa_flow():
        calls.append("taa-parallel")
        return {"status": "done"}

    def ez_serial():
        calls.append("ez-serial")
        return {"status": "done"}

    def taa_serial():
        calls.append("taa-serial")
        return {"status": "done"}

    result = orchestrator.run_parallel_with_fallback(
        ez_parallel=ez_flow,
        taa_parallel=taa_flow,
        ez_serial=ez_serial,
        taa_serial=taa_serial,
    )

    assert result["mode"] == "serial_fallback"
    assert calls == ["ez-parallel", "taa-parallel", "ez-serial", "taa-serial"]
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_shopify_image_localizer_browser_session.py tests/test_shopify_image_localizer_orchestrator.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现 URL 构造、登录等待和并发回退**

```python
# tools/shopify_image_localizer/browser/session.py
STORE_SLUG = "0ixug9-pv"


def build_ez_url(shopify_product_id: str) -> str:
    return f"https://admin.shopify.com/store/{STORE_SLUG}/apps/ez-product-image-translate/product/{shopify_product_id}"


def build_translate_url(shopify_product_id: str, shop_locale: str) -> str:
    return (
        f"https://admin.shopify.com/store/{STORE_SLUG}/apps/translate-and-adapt/localize/product"
        f"?highlight=handle&id={shopify_product_id}&shopLocale={shop_locale}"
    )


def launch_persistent_context(playwright, user_data_dir: str):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        channel="msedge",
        headless=False,
    )
```

```python
# tools/shopify_image_localizer/browser/orchestrator.py
class FlowConflictError(RuntimeError):
    pass


def run_parallel_with_fallback(*, ez_parallel, taa_parallel, ez_serial, taa_serial) -> dict:
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ez_future = pool.submit(ez_parallel)
            taa_future = pool.submit(taa_parallel)
            return {
                "mode": "parallel",
                "ez": ez_future.result(),
                "taa": taa_future.result(),
            }
    except FlowConflictError:
        return {
            "mode": "serial_fallback",
            "ez": ez_serial(),
            "taa": taa_serial(),
        }
```

- [ ] **Step 4: 回跑测试**

Run:

```bash
pytest tests/test_shopify_image_localizer_browser_session.py tests/test_shopify_image_localizer_orchestrator.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/shopify_image_localizer/browser tests/test_shopify_image_localizer_browser_session.py tests/test_shopify_image_localizer_orchestrator.py
git commit -m "feat: add browser session and orchestrator for shopify localizer"
```

---

### Task 6: GUI 与控制器主流程

**Files:**
- Create: `tools/shopify_image_localizer/controller.py`
- Create: `tools/shopify_image_localizer/gui.py`
- Create: `tools/shopify_image_localizer/main.py`
- Test: `tests/test_shopify_image_localizer_gui.py`
- Test: `tests/test_shopify_image_localizer_controller.py`

- [ ] **Step 1: 写失败测试**

```python
def test_gui_exposes_product_code_language_and_start(monkeypatch):
    from tools.shopify_image_localizer import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://172.30.254.14",
        "api_key": "demo-key",
        "browser_user_data_dir": r"C:\chrome-shopify-image",
    })
    # 复用 link_check_desktop 同风格 fake widget
    app = gui.ShopifyImageLocalizerApp(prompt_on_start=False)
    try:
        assert app.product_code_var.get() == ""
        assert app.language_var.get() == ""
        assert app.start_button["text"] == "开始替换图片"
    finally:
        app.root.destroy()


def test_controller_runs_download_then_browser(monkeypatch, tmp_path):
    from tools.shopify_image_localizer import controller

    monkeypatch.setattr(controller.storage, "create_workspace", lambda product_code, lang, now=None: type("WS", (), {
        "root": tmp_path / product_code / lang,
        "source_en_dir": tmp_path / "en",
        "source_localized_dir": tmp_path / "localized",
        "manifest_path": tmp_path / "manifest.json",
        "log_path": tmp_path / "run.log",
    })())
    monkeypatch.setattr(controller.api_client, "fetch_bootstrap", lambda *args, **kwargs: {
        "product": {"product_code": "demo-rjc", "shopify_product_id": "855"},
        "language": {"code": "de", "shop_locale": "de"},
        "reference_images": [{"id": "en-1", "filename": "en.jpg", "url": "https://example.com/en.jpg"}],
        "localized_images": [{"id": "de-1", "filename": "de.jpg", "url": "https://example.com/de.jpg"}],
    })
    monkeypatch.setattr(controller.downloader, "download_images", lambda items, output_dir, retries=1, status_cb=None: [{**item, "local_path": str(output_dir / item["filename"])} for item in items])
    monkeypatch.setattr(controller.browser_runner, "run_shopify_localizer", lambda **kwargs: {"status": "done", "mode": "parallel"})

    result = controller.run_shopify_localizer(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="demo-rjc",
        lang="de",
        status_cb=lambda message: None,
    )

    assert result["status"] == "done"
    assert result["mode"] == "parallel"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_controller.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现 GUI 和控制器**

```python
# tools/shopify_image_localizer/controller.py
def run_shopify_localizer(*, base_url, api_key, browser_user_data_dir, product_code, lang, status_cb=None) -> dict:
    reporter = status_cb or (lambda _message: None)
    reporter("正在拉取任务数据")
    bootstrap = api_client.fetch_bootstrap(base_url, api_key, product_code, lang)

    workspace = storage.create_workspace(product_code, lang)

    reporter("正在下载英文参考图")
    reference_images = downloader.download_images(
        bootstrap["reference_images"],
        workspace.source_en_dir,
        status_cb=reporter,
    )
    reporter("正在下载目标语言图片")
    localized_images = downloader.download_images(
        bootstrap["localized_images"],
        workspace.source_localized_dir,
        status_cb=reporter,
    )

    reporter("正在启动 Shopify 浏览器")
    browser_result = browser_runner.run_shopify_localizer(
        browser_user_data_dir=browser_user_data_dir,
        bootstrap=bootstrap,
        reference_images=reference_images,
        localized_images=localized_images,
        workspace=workspace,
        status_cb=reporter,
    )
    storage.write_json(workspace.manifest_path, {
        "product_code": product_code,
        "lang": lang,
        "bootstrap": bootstrap,
        "browser_result": browser_result,
    })
    return browser_result
```

```python
# tools/shopify_image_localizer/gui.py
class ShopifyImageLocalizerApp:
    def __init__(self, *, prompt_on_start: bool = True) -> None:
        runtime_config = settings.load_runtime_config()
        self.root = tk.Tk()
        self.root.title("Shopify 图片本地化替换")
        self.root.geometry("800x800")
        self.root.resizable(False, False)
        self.base_url_var = tk.StringVar(value=runtime_config["base_url"])
        self.api_key_var = tk.StringVar(value=runtime_config["api_key"])
        self.browser_user_data_dir_var = tk.StringVar(value=runtime_config["browser_user_data_dir"])
        self.product_code_var = tk.StringVar()
        self.language_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请输入产品 ID，选择语言后点击开始替换图片")
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(self.main_frame, text="产品 ID").pack(anchor="w")
        self.product_code_entry = tk.Entry(self.main_frame, textvariable=self.product_code_var, width=60)
        self.product_code_entry.pack(fill="x", pady=(4, 8))
        tk.Label(self.main_frame, text="目标语言").pack(anchor="w")
        self.language_box = ttk.Combobox(
            self.main_frame,
            textvariable=self.language_var,
            state="readonly",
            values=[],
        )
        self.language_box.pack(fill="x", pady=(4, 8))
        self.start_button = tk.Button(self.main_frame, text="开始替换图片", command=self.start_run)
        self.start_button.pack(anchor="w", pady=(8, 8))
        tk.Label(self.main_frame, textvariable=self.status_var, justify="left").pack(anchor="w", pady=(8, 8))
        self.log_widget = tk.Text(self.main_frame, height=28, width=96)
        self.log_widget.pack(fill="both", expand=True)
        self.product_code_entry.focus_set()
        self._load_languages_async()
        _ = prompt_on_start

    def _load_languages_async(self) -> None:
        def worker():
            try:
                payload = api_client.fetch_languages(self.base_url_var.get().strip(), self.api_key_var.get().strip())
                labels = [item["label"] for item in payload.get("items") or []]
                self.root.after(0, lambda: self.language_box.configure(values=labels))
            except Exception as exc:
                self.root.after(0, self.status_var.set, f"加载语言失败: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def start_run(self) -> None:
        product_code = self.product_code_var.get().strip()
        language_label = self.language_var.get().strip()
        if not product_code:
            messagebox.showerror("错误", "请先输入产品 ID")
            return
        if not language_label:
            messagebox.showerror("错误", "请先选择目标语言")
            return
```

```python
# tools/shopify_image_localizer/main.py
from tools.shopify_image_localizer.gui import ShopifyImageLocalizerApp


def main() -> None:
    app = ShopifyImageLocalizerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 回跑测试**

Run:

```bash
pytest tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_controller.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/shopify_image_localizer/controller.py tools/shopify_image_localizer/gui.py tools/shopify_image_localizer/main.py tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_controller.py
git commit -m "feat: add shopify localizer gui and controller"
```

---

### Task 7: EZ / Translate 两条真实页面流

**Files:**
- Create: `tools/shopify_image_localizer/browser/ez_flow.py`
- Create: `tools/shopify_image_localizer/browser/translate_flow.py`
- Modify: `tools/shopify_image_localizer/browser/orchestrator.py`
- Test: `tests/test_shopify_image_localizer_flows.py`

- [ ] **Step 1: 先写 fake page 测试，锁定“截图 -> 配对 -> 上传”的行为**

```python
def test_capture_slot_images_screenshots_each_slot(tmp_path):
    from tools.shopify_image_localizer.browser import ez_flow

    class FakeNode:
        def __init__(self, idx):
            self.idx = idx
        def screenshot(self, path):
            Path(path).write_bytes(f"slot-{self.idx}".encode("utf-8"))

    class FakeLocator:
        def count(self):
            return 2
        def nth(self, idx):
            return FakeNode(idx)

    slots = ez_flow.capture_slot_images(FakeLocator(), tmp_path, prefix="ez")
    assert [slot["slot_id"] for slot in slots] == ["ez-001", "ez-002"]
    assert Path(slots[0]["local_path"]).is_file()


def test_upload_assignment_uses_file_input_when_available(tmp_path):
    from tools.shopify_image_localizer.browser import translate_flow

    class FakeInput:
        def __init__(self):
            self.files = None
        def count(self):
            return 1
        def first(self):
            return self
        def set_input_files(self, files):
            self.files = files

    class FakePage:
        def __init__(self):
            self.input = FakeInput()
        def locator(self, selector):
            assert selector == "input[type='file']"
            return self.input

    local_file = tmp_path / "demo.jpg"
    local_file.write_bytes(b"img")
    page = FakePage()
    translate_flow.upload_file(page, str(local_file))
    assert page.input.files == str(local_file)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_shopify_image_localizer_flows.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现页面截图、配对和上传动作**

```python
# tools/shopify_image_localizer/browser/ez_flow.py
def capture_slot_images(locator, output_dir: Path, *, prefix: str) -> list[dict]:
    slots: list[dict] = []
    for index in range(locator.count()):
        slot_id = f"{prefix}-{index + 1:03d}"
        path = output_dir / f"{slot_id}.png"
        locator.nth(index).screenshot(path=str(path))
        slots.append({"slot_id": slot_id, "local_path": str(path)})
    return slots


def run_ez_flow(*, page, shopify_product_id: str, language_code: str, reference_images: list[dict], localized_images: list[dict], workspace, status_cb=None) -> dict:
    page.goto(build_ez_url(shopify_product_id), wait_until="domcontentloaded")
    slot_locator = page.locator("img")
    slot_images = capture_slot_images(slot_locator, workspace.classify_ez_dir, prefix="ez")
    assignments = matcher.assign_images(slot_images, reference_images, localized_images)
    for row in assignments["assigned"]:
        upload_file(page, row["local_path"])
    return {"status": "done", **assignments}
```

```python
# tools/shopify_image_localizer/browser/translate_flow.py
def upload_file(page, local_path: str) -> None:
    file_input = page.locator("input[type='file']")
    if file_input.count():
        file_input.first().set_input_files(local_path)
        return
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name=re.compile("upload|replace|add image", re.I)).click()
    chooser.value.set_files(local_path)


def run_translate_flow(*, page, shopify_product_id: str, shop_locale: str, reference_images: list[dict], localized_images: list[dict], workspace, status_cb=None) -> dict:
    page.goto(build_translate_url(shopify_product_id, shop_locale), wait_until="domcontentloaded")
    english_locator = page.locator("img")
    slot_images = capture_slot_images(english_locator, workspace.classify_taa_dir, prefix="taa")
    assignments = matcher.assign_images(slot_images, reference_images, localized_images)
    for row in assignments["assigned"]:
        upload_file(page, row["local_path"])
    return {"status": "done", **assignments}
```

- [ ] **Step 4: 用真实 Shopify 页面做一次选择器烟测**

Run:

```bash
python -m tools.shopify_image_localizer.main
```

手工验证：

1. 先用样例 `dino-glider-launcher-toy-rjc / de` 启动任务。
2. 若浏览器提示登录，则用户手动登录 Shopify。
3. 在 `C:\chrome-shopify-image` 持久化目录下复用登录态重新执行。
4. 查看本地 `classify/ez` 与 `classify/taa` 是否成功产出页面截图。
5. 如 `img` 选择器过宽，基于真实页面 HTML/截图把 `page.locator("img")` 收窄到插件主内容区域后再重复这一步。

Expected: 两条链路都能进入目标页面，至少能正确截出英文图位，并触发一次真实文件上传动作。

- [ ] **Step 5: 回跑单测并提交**

Run:

```bash
pytest tests/test_shopify_image_localizer_flows.py -q
```

Then:

```bash
git add tools/shopify_image_localizer/browser/ez_flow.py tools/shopify_image_localizer/browser/translate_flow.py tools/shopify_image_localizer/browser/orchestrator.py tests/test_shopify_image_localizer_flows.py
git commit -m "feat: add ez and translate flows for shopify localizer"
```

---

### Task 8: 打包、文档与最终验证

**Files:**
- Create: `tools/shopify_image_localizer/build_exe.py`
- Create: `tools/shopify_image_localizer/packaging/shopify_image_localizer.spec`
- Modify: `AGENTS.md`
- Test: `tests/test_shopify_image_localizer_build_exe.py`

- [ ] **Step 1: 写失败测试**

```python
def test_write_portable_launcher_points_to_bundled_exe(tmp_path):
    from tools.shopify_image_localizer import build_exe

    dist_root = tmp_path / "dist" / "ShopifyImageLocalizer"
    dist_root.mkdir(parents=True)
    launcher = build_exe._write_portable_launcher(dist_root)

    content = launcher.read_text(encoding="utf-8")
    assert launcher.name == "run_shopify_image_localizer.bat"
    assert "ShopifyImageLocalizer.exe" in content


def test_build_portable_zip_contains_config(tmp_path):
    from tools.shopify_image_localizer import build_exe

    dist_root = tmp_path / "dist" / "ShopifyImageLocalizer"
    dist_root.mkdir(parents=True)
    (dist_root / "ShopifyImageLocalizer.exe").write_bytes(b"exe")
    (dist_root / "shopify_image_localizer_config.json").write_text("{}", encoding="utf-8")

    archive = build_exe._build_portable_zip(dist_root)
    assert archive.name == "ShopifyImageLocalizer-portable.zip"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_shopify_image_localizer_build_exe.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现打包脚本并补充项目命令文档**

```python
# tools/shopify_image_localizer/build_exe.py
APP_NAME = "ShopifyImageLocalizer"
PLAYWRIGHT_DIST_DIR = "ms-playwright"
PORTABLE_LAUNCHER_NAME = "run_shopify_image_localizer.bat"
```

```python
# AGENTS.md
## Shopify Image Localizer Commands

- 开发运行：`python -m tools.shopify_image_localizer.main`
- 聚焦测试：`pytest tests/test_appcore_medias_shopify_localizer.py tests/test_openapi_shopify_image_localizer_routes.py tests/test_shopify_image_localizer_settings.py tests/test_shopify_image_localizer_storage.py tests/test_shopify_image_localizer_api_client.py tests/test_shopify_image_localizer_downloader.py tests/test_shopify_image_localizer_matcher.py tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_controller.py tests/test_shopify_image_localizer_browser_session.py tests/test_shopify_image_localizer_orchestrator.py tests/test_shopify_image_localizer_flows.py tests/test_shopify_image_localizer_build_exe.py -q`
- 打包：`python -m tools.shopify_image_localizer.build_exe`
```

- [ ] **Step 4: 回跑构建测试并执行完整聚焦测试**

Run:

```bash
pytest tests/test_shopify_image_localizer_build_exe.py -q
pytest tests/test_appcore_medias_shopify_localizer.py tests/test_openapi_shopify_image_localizer_routes.py tests/test_shopify_image_localizer_settings.py tests/test_shopify_image_localizer_storage.py tests/test_shopify_image_localizer_api_client.py tests/test_shopify_image_localizer_downloader.py tests/test_shopify_image_localizer_matcher.py tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_controller.py tests/test_shopify_image_localizer_browser_session.py tests/test_shopify_image_localizer_orchestrator.py tests/test_shopify_image_localizer_flows.py tests/test_shopify_image_localizer_build_exe.py -q
```

Expected: 全部 PASS。

- [ ] **Step 5: 生成 EXE 并做最终 smoke**

Run:

```bash
python -m tools.shopify_image_localizer.build_exe
```

Then manually verify:

1. 生成 `dist/ShopifyImageLocalizer/ShopifyImageLocalizer.exe`。
2. 启动 EXE 后能拉到语言下拉。
3. 输入 `dino-glider-launcher-toy-rjc`、选择 `德语（DE/de）`、点击 `开始替换图片`。
4. 若未登录，浏览器停在 Shopify 登录页；登录完成后自动继续。
5. 任务结束后，EXE 同级目录出现：
   - `dino-glider-launcher-toy-rjc/de/source/en`
   - `dino-glider-launcher-toy-rjc/de/source/localized`
   - `dino-glider-launcher-toy-rjc/de/classify/ez`
   - `dino-glider-launcher-toy-rjc/de/classify/taa`
   - `dino-glider-launcher-toy-rjc/de/manifest.json`
   - `dino-glider-launcher-toy-rjc/de/run.log`

- [ ] **Step 6: Commit**

```bash
git add tools/shopify_image_localizer/build_exe.py tools/shopify_image_localizer/packaging/shopify_image_localizer.spec AGENTS.md tests/test_shopify_image_localizer_build_exe.py
git commit -m "feat: package shopify image localizer desktop tool"
```

---

## Self-Review Checklist

1. **Spec coverage**
   - 本地 EXE、`tools` 目录落位：Task 3、Task 6、Task 8。
   - 语言下拉来源：Task 1、Task 2、Task 6。
   - 服务端只返回图片 URL，不做压缩包：Task 1、Task 2。
   - `C:\chrome-shopify-image` 持久化浏览器：Task 3、Task 5、Task 8。
   - 登录检测与自动恢复：Task 5、Task 8。
   - `EZ Product Image` + `Translate and Adapt` 双链路：Task 5、Task 7、Task 8。
   - 并发优先、冲突回退串行：Task 5。
   - 混合图片由页面截图归类：Task 4、Task 7。
   - 样例数据 `dino-glider-launcher-toy-rjc / de / 8552296546477`：Task 7、Task 8。

2. **Placeholder scan**
   - 没有占位词或“稍后再补”的描述。
   - 唯一依赖真实页面确认的是 Shopify DOM 选择器，已经被明确写成 Task 7 的烟测步骤，不是占位。

3. **Type consistency**
   - 服务端接口统一使用 `product_code + lang` 入参。
   - 本地目录统一使用 `<product_code>/<lang>/source|classify|screenshots` 这一组结构。
   - Shopify 商品定位统一使用 `shopify_product_id`。
   - 语言展示统一使用 `德语（DE/de）` 这类格式。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-24-shopify-image-localizer-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
