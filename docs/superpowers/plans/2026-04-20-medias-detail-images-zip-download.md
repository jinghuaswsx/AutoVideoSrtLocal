# Medias Detail Images ZIP Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在素材管理编辑页为当前语种的商品详情图新增“一键打包下载”能力，后端打包 ZIP 并直接下载。

**Architecture:** 后端在 `web/routes/medias.py` 新增一个同步下载路由，按当前语种查询详情图、从 TOS 下载到临时文件、重命名写入 ZIP 并作为附件响应。前端在编辑弹窗里新增按钮并根据当前详情图数量控制禁用状态，点击后直接命中该路由。

**Tech Stack:** Flask, Python `zipfile`/`tempfile`/`io`, 现有 `tos_clients`, 原生前端 JS, pytest

---

### Task 1: 后端 ZIP 下载路由

**Files:**
- Modify: `web/routes/medias.py`
- Test: `tests/test_medias_routes.py`

- [ ] **Step 1: 写失败测试**

```python
def test_detail_images_download_zip_returns_sorted_archive(authed_client_no_db, monkeypatch):
    import io
    import zipfile
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "product_code": "demo-item"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 21, "product_id": pid, "lang": lang, "sort_order": 0, "object_key": "1/medias/1/a.webp"},
            {"id": 22, "product_id": pid, "lang": lang, "sort_order": 1, "object_key": "1/medias/1/b.jpg"},
        ],
    )

    def fake_download(object_key, local_path):
        with open(local_path, "wb") as fh:
            fh.write(b"BYTES-" + object_key.encode())

    monkeypatch.setattr(r.tos_clients, "download_media_file", fake_download)

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    assert zf.namelist() == [
        "demo-item_en_detail-images/01.webp",
        "demo-item_en_detail-images/02.jpg",
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_medias_routes.py -k download_zip -q`
Expected: FAIL，提示路由不存在或断言不满足

- [ ] **Step 3: 写最小实现**

```python
@bp.route("/api/products/<int:pid>/detail-images/download-zip", methods=["GET"])
@login_required
def api_detail_images_download_zip(pid: int):
    ...
```

实现要点：

- 用 `request.args.get("lang")` 取语种，默认 `en`
- 用 `medias.list_detail_images(pid, lang)` 取排序后的详情图
- 用 `tempfile.TemporaryDirectory()` 存临时下载文件
- 用 `zipfile.ZipFile` 写 ZIP
- ZIP 内目录名与下载名统一为 `{product_code or f'product-{pid}'}_{lang}_detail-images`
- 每张图片按顺序命名为 `01.ext`

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_medias_routes.py -k download_zip -q`
Expected: PASS

- [ ] **Step 5: 补空结果测试**

```python
def test_detail_images_download_zip_404_when_empty(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "product_code": "demo-item"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: [])

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en")
    assert resp.status_code == 404
```

- [ ] **Step 6: 跑后端测试全集**

Run: `pytest tests/test_medias_routes.py -q`
Expected: PASS

### Task 2: 编辑弹窗按钮与前端联动

**Files:**
- Modify: `web/templates/_medias_edit_detail_modal.html`
- Modify: `web/static/medias.js`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: 写模板/脚本失败测试**

```python
def test_medias_edit_modal_contains_detail_image_zip_download_button():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="edDetailImagesDownloadZipBtn"' in template
    assert "detail-images/download-zip" in scripts
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_web_routes.py -k detail_image_zip_download_button -q`
Expected: FAIL，提示按钮或脚本未找到

- [ ] **Step 3: 写最小前端实现**

```html
<button type="button" class="oc-btn ghost sm" id="edDetailImagesDownloadZipBtn" disabled>
  <svg width="14" height="14"><use href="#ic-download"/></svg>
  <span>一键打包下载</span>
</button>
```

```javascript
function edSyncDetailImagesDownloadZipButton() {
  const btn = $('edDetailImagesDownloadZipBtn');
  const pid = edState.productData && edState.productData.product && edState.productData.product.id;
  const items = edDetailImagesCtrl && edDetailImagesCtrl.items ? edDetailImagesCtrl.items() : [];
  btn.disabled = !pid || !items.length;
}
```

再在详情图面板刷新后调用同步函数，并在按钮点击时跳转到：

```javascript
`/medias/api/products/${pid}/detail-images/download-zip?lang=${encodeURIComponent(lang)}`
```

- [ ] **Step 4: 跑前端结构测试确认通过**

Run: `pytest tests/test_web_routes.py -k detail_image_zip_download_button -q`
Expected: PASS

- [ ] **Step 5: 跑相关组合测试**

Run: `pytest tests/test_web_routes.py -k "detail_image or link_check_controls" -q`
Expected: PASS

### Task 3: 最终回归

**Files:**
- Modify: `web/routes/medias.py`
- Modify: `web/static/medias.js`
- Modify: `web/templates/_medias_edit_detail_modal.html`
- Modify: `tests/test_medias_routes.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: 运行本功能相关测试**

Run: `pytest tests/test_medias_routes.py tests/test_web_routes.py -q`
Expected: PASS

- [ ] **Step 2: 自查交互行为**

检查点：

- 编辑弹窗详情图区域能看到 `一键打包下载`
- 当前语种无详情图时按钮禁用
- 当前语种有详情图时按钮可点击
- 下载 URL 带当前语种参数

- [ ] **Step 3: 提交**

```bash
git add web/routes/medias.py web/static/medias.js web/templates/_medias_edit_detail_modal.html tests/test_medias_routes.py tests/test_web_routes.py docs/superpowers/specs/2026-04-20-medias-detail-images-zip-download-design.md docs/superpowers/plans/2026-04-20-medias-detail-images-zip-download.md
git commit -m "feat(medias): add detail image zip download"
```
