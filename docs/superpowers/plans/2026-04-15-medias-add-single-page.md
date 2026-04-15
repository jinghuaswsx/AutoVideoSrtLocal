# 素材单页添加/编辑弹窗 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"添加/编辑产品素材"弹窗从三 Tab 改成单页；新增"产品 ID (slug)"与"独立封面图"两个必填字段；视频上传改为每次一个且保存时至少 1 条；移除色号/代言人与来源的 UI 入口。

**Architecture:** 数据库加 `product_code` + `cover_object_key`；复用 TOS 流程新增封面 bootstrap/complete；新增 `/medias/cover/<pid>` 代理；前端弹窗结构重写为垂直堆叠；保存逻辑集中在 PUT 路径上做服务端硬校验。

**Tech Stack:** Flask + Jinja2 + 原生 JS + MySQL + 火山 TOS（对象存储），pytest。

> 相关设计：[2026-04-15-medias-add-single-page-design.md](../specs/2026-04-15-medias-add-single-page-design.md)

> **与设计稿的一处调整**：`cover_url` **不落库**，改为在产品序列化阶段从 `cover_object_key` 计算得出 `/medias/cover/<pid>`。原因：图片地址由代理路由提供，DB 冗余一份易不一致。

---

## 文件清单

- **新建**
  - `db/migrations/2026_04_15_medias_add_product_code_and_cover.sql`
- **修改**
  - `appcore/medias.py` — `create_product` / `update_product` / 新增 `get_product_by_code`
  - `web/routes/medias.py` — 新增字段读写、封面 bootstrap/complete、`/cover/<pid>` 代理、保存硬校验、`_serialize_product` 扩展
  - `web/templates/_medias_edit_modal.html` — 去 Tab，单页结构
  - `web/static/medias.js` — 状态/交互全面改写
  - `web/templates/medias_list.html` — 不改结构，仅间接受益于后端 `cover_url` 的优先级调整
  - `tests/test_appcore_medias.py` — 追加 DAO 测试
  - `tests/test_web_routes.py` — 追加路由测试（如已覆盖 medias 路由）

---

## Task 1 — 数据库迁移：新增 `product_code` 与 `cover_object_key`

**Files:**
- Create: `db/migrations/2026_04_15_medias_add_product_code_and_cover.sql`

- [ ] **Step 1: 写迁移 SQL**

```sql
-- db/migrations/2026_04_15_medias_add_product_code_and_cover.sql
ALTER TABLE media_products
  ADD COLUMN product_code VARCHAR(64) NULL AFTER name,
  ADD COLUMN cover_object_key VARCHAR(255) NULL AFTER source,
  ADD UNIQUE KEY uk_media_products_product_code (product_code);
```

- [ ] **Step 2: 在本地 MySQL 应用迁移**

Run:
```bash
mysql -u <user> -p <db_name> < db/migrations/2026_04_15_medias_add_product_code_and_cover.sql
```
Expected: 无错误返回。

- [ ] **Step 3: 验证列已创建**

Run:
```bash
mysql -u <user> -p -e "DESCRIBE media_products;" <db_name>
```
Expected: 出现 `product_code varchar(64) YES UNI NULL` 与 `cover_object_key varchar(255) YES NULL`。

- [ ] **Step 4: 提交**

```bash
git add db/migrations/2026_04_15_medias_add_product_code_and_cover.sql
git commit -m "feat(medias-db): 新增 product_code 唯一列与 cover_object_key"
```

---

## Task 2 — DAO：`create_product` / `update_product` 支持新字段

**Files:**
- Modify: `appcore/medias.py:9-14, 50-58`

- [ ] **Step 1: 在 `tests/test_appcore_medias.py` 末尾追加失败用例**

```python
def test_create_product_with_code_and_cover(user_id):
    pid = medias.create_product(
        user_id, "带编码的产品",
        product_code="abc-product-01",
        cover_object_key="covers/1/x.jpg",
    )
    try:
        p = medias.get_product(pid)
        assert p["product_code"] == "abc-product-01"
        assert p["cover_object_key"] == "covers/1/x.jpg"
    finally:
        medias.soft_delete_product(pid)


def test_update_product_sets_code_and_cover(user_id):
    pid = medias.create_product(user_id, "待更新产品")
    try:
        medias.update_product(
            pid,
            product_code="updated-slug",
            cover_object_key="covers/1/new.jpg",
        )
        p = medias.get_product(pid)
        assert p["product_code"] == "updated-slug"
        assert p["cover_object_key"] == "covers/1/new.jpg"
    finally:
        medias.soft_delete_product(pid)


def test_get_product_by_code(user_id):
    pid = medias.create_product(user_id, "可查编码", product_code="query-code-1")
    try:
        p = medias.get_product_by_code("query-code-1")
        assert p and p["id"] == pid
        assert medias.get_product_by_code("nope-xxxx") is None
    finally:
        medias.soft_delete_product(pid)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_appcore_medias.py -v -k "code_and_cover or get_product_by_code"`
Expected: 三个用例 FAIL（`create_product` 不认 kwargs / `get_product_by_code` 不存在）。

- [ ] **Step 3: 修改 `appcore/medias.py` — 扩展 `create_product`**

把 `create_product` 改成：

```python
def create_product(user_id: int, name: str, color_people: str | None = None,
                   source: str | None = None, product_code: str | None = None,
                   cover_object_key: str | None = None) -> int:
    return execute(
        "INSERT INTO media_products "
        "(user_id, name, product_code, color_people, source, cover_object_key) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (user_id, name, product_code, color_people, source, cover_object_key),
    )
```

- [ ] **Step 4: 扩展 `update_product` 白名单**

把 `update_product` 里 `allowed` 改成：

```python
allowed = {"name", "color_people", "source", "archived",
           "importance", "trend_score", "selling_points",
           "product_code", "cover_object_key"}
```

- [ ] **Step 5: 新增 `get_product_by_code`**

在 `get_product` 下方追加：

```python
def get_product_by_code(code: str) -> dict | None:
    return query_one(
        "SELECT * FROM media_products WHERE product_code=%s AND deleted_at IS NULL",
        (code,),
    )
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `pytest tests/test_appcore_medias.py -v`
Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add appcore/medias.py tests/test_appcore_medias.py
git commit -m "feat(medias-dao): create/update_product 支持 product_code 与 cover_object_key"
```

---

## Task 3 — 路由：产品序列化与 `GET /api/products/<pid>` 暴露新字段

**Files:**
- Modify: `web/routes/medias.py:32-44, 109-119`

- [ ] **Step 1: 修改 `_serialize_product`**

把它改成：

```python
def _serialize_product(p: dict, items_count: int | None = None,
                       cover_item_id: int | None = None) -> dict:
    cover_url = None
    if p.get("cover_object_key"):
        cover_url = f"/medias/cover/{p['id']}"
    elif cover_item_id:
        cover_url = f"/medias/thumb/{cover_item_id}"
    return {
        "id": p["id"],
        "name": p["name"],
        "product_code": p.get("product_code"),
        "color_people": p.get("color_people"),
        "source": p.get("source"),
        "archived": bool(p.get("archived")),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
        "items_count": items_count,
        "cover_thumbnail_url": cover_url,
    }
```

> `cover_thumbnail_url` 字段名保留以免列表卡片渲染改动，只是语义变为"优先产品封面，否则首个 item 缩略图"。

- [ ] **Step 2: 启动本地服务器手工测试**

Run:
```bash
python web.py   # 或项目约定的启动命令
```
打开 `/medias/`，确认现有产品列表仍能正常渲染（无报错）。

- [ ] **Step 3: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias-api): 序列化暴露 product_code 与优先封面"
```

---

## Task 4 — 路由：`POST /api/products` 与 `PUT /api/products/<pid>` 接收新字段 + 校验

**Files:**
- Modify: `web/routes/medias.py:94-137`

- [ ] **Step 1: 新增 slug 校验常量**

在 `bp = Blueprint(...)` 之后、`_is_admin` 之前加：

```python
import re

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


def _validate_product_code(code: str) -> tuple[bool, str | None]:
    if not code:
        return False, "产品 ID 必填"
    if not _SLUG_RE.match(code):
        return False, "产品 ID 只能使用小写字母、数字和连字符，长度 3-64，且首尾不能是连字符"
    return True, None
```

- [ ] **Step 2: 重写 `api_create_product`**

```python
@bp.route("/api/products", methods=["POST"])
@login_required
def api_create_product():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    product_code = (body.get("product_code") or "").strip().lower() or None
    if product_code is not None:
        ok, err = _validate_product_code(product_code)
        if not ok:
            return jsonify({"error": err}), 400
        if medias.get_product_by_code(product_code):
            return jsonify({"error": "产品 ID 已被占用"}), 409
    pid = medias.create_product(
        current_user.id, name,
        product_code=product_code,
    )
    return jsonify({"id": pid}), 201
```

> 创建阶段 `product_code` 可选（首次打开弹窗还未填），保存阶段强校验；用户在弹窗里必然先填后触发 upload，此处若已传入则强校验格式与唯一性。

- [ ] **Step 3: 重写 `api_update_product`（保存入口，硬校验集中于此）**

```python
@bp.route("/api/products/<int:pid>", methods=["PUT"])
@login_required
def api_update_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}

    name = (body.get("name") or "").strip() or p["name"]
    product_code = (body.get("product_code") or "").strip().lower()
    ok, err = _validate_product_code(product_code)
    if not ok:
        return jsonify({"error": err}), 400
    exist = medias.get_product_by_code(product_code)
    if exist and exist["id"] != pid:
        return jsonify({"error": "产品 ID 已被占用"}), 409

    if not p.get("cover_object_key") and not body.get("cover_object_key"):
        return jsonify({"error": "封面图必填"}), 400

    items = medias.list_items(pid)
    if not items:
        return jsonify({"error": "至少需要 1 条视频素材"}), 400

    update_fields = {"name": name, "product_code": product_code}
    if body.get("cover_object_key"):
        update_fields["cover_object_key"] = body["cover_object_key"]
    medias.update_product(pid, **update_fields)

    if isinstance(body.get("copywritings"), list):
        medias.replace_copywritings(pid, body["copywritings"])
    return jsonify({"ok": True})
```

- [ ] **Step 4: 手工冒烟测试**

Run 本地服务器，用浏览器 DevTools Console：

```javascript
await fetch('/medias/api/products', {method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({name:'test-plan', product_code:'Bad Code'})}).then(r=>[r.status, r.json()]).then(console.log)
```
Expected: `[400, {error: '产品 ID 只能使用小写字母…'}]`

```javascript
await fetch('/medias/api/products', {method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({name:'test-plan', product_code:'valid-code-1'})}).then(r=>r.json()).then(console.log)
```
Expected: `{id: <n>}`

继续重复上一条：Expected `[409, {error:'产品 ID 已被占用'}]`。

PUT 到刚刚的 pid，不带 cover_object_key 且无 item：Expected `[400, {error:'封面图必填'}]`（注意：此产品 cover_object_key 可能仍为 NULL，先被封面校验拦住）。

清理：`DELETE /medias/api/products/<pid>`。

- [ ] **Step 5: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias-api): POST/PUT 校验 product_code 与保存必填"
```

---

## Task 5 — 路由：封面 bootstrap / complete / 代理

**Files:**
- Modify: `web/routes/medias.py`（追加 3 个路由；复用 TOS helper）

- [ ] **Step 1: 在 `tos_clients` 使用处查看 `build_media_object_key` 签名，确认前缀约定**

阅读 `appcore/tos_clients.py:194-199`：现有规则 `{user_id}/medias/{product_id}/{date}_{uuid}_{filename}`。

我们复用同一工具，把封面直接接在视频 object key 里也 OK——但为可读性约定：封面 key 文件名前加 `cover_` 前缀（不需要改 tos_clients，调用处 filename 传 `cover_<原名>`）。

- [ ] **Step 2: 追加封面 bootstrap 路由**

在 `api_item_bootstrap` 函数之后加：

```python
@bp.route("/api/products/<int:pid>/cover/bootstrap", methods=["POST"])
@login_required
def api_cover_bootstrap(pid: int):
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    filename = os.path.basename((body.get("filename") or "cover.jpg").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = tos_clients.build_media_object_key(
        current_user.id, pid, f"cover_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": tos_clients.generate_signed_media_upload_url(object_key),
        "expires_in": TOS_SIGNED_URL_EXPIRES,
    })
```

- [ ] **Step 3: 追加封面 complete 路由**

在上面之后加：

```python
@bp.route("/api/products/<int:pid>/cover/complete", methods=["POST"])
@login_required
def api_cover_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return jsonify({"error": "object_key required"}), 400
    if not tos_clients.media_object_exists(object_key):
        return jsonify({"error": "对象不存在"}), 400

    # 旧封面（如存在）删除 TOS 对象
    old_key = p.get("cover_object_key")
    if old_key and old_key != object_key:
        try:
            tos_clients.delete_media_object(old_key)
        except Exception:
            pass

    medias.update_product(pid, cover_object_key=object_key)

    # 下载到本地缓存（与 thumbs 同目录），供代理路由直出
    try:
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover{ext}"
        tos_clients.download_media_file(object_key, str(local))
    except Exception:
        pass

    return jsonify({"ok": True, "cover_url": f"/medias/cover/{pid}"})
```

- [ ] **Step 4: 追加封面代理路由**

在 `thumb(item_id)` 附近追加：

```python
@bp.route("/cover/<int:pid>")
@login_required
def cover(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    if not p.get("cover_object_key"):
        abort(404)
    product_dir = THUMB_DIR / str(pid)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        f = product_dir / f"cover{ext}"
        if f.exists():
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            return send_file(str(f), mimetype=mime)
    # 本地缓存缺失：重新下一次
    try:
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(p["cover_object_key"]).suffix or ".jpg"
        local = product_dir / f"cover{ext}"
        tos_clients.download_media_file(p["cover_object_key"], str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)
```

- [ ] **Step 5: 手工测试（需 TOS 已配置）**

本地启动，浏览器 DevTools：
```javascript
const pid = <新建的 pid>;
const boot = await fetch(`/medias/api/products/${pid}/cover/bootstrap`,
  {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename:'x.jpg'})}).then(r=>r.json());
console.log(boot);
```
Expected: 返回 `object_key`、`upload_url`。

- [ ] **Step 6: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias-api): 新增封面 bootstrap/complete 与代理路由"
```

---

## Task 6 — 弹窗模板：去 Tab，改为单页

**Files:**
- Modify: `web/templates/_medias_edit_modal.html`（整文件重写）
- Modify: `web/templates/medias_list.html:164-224`（只加 `.oc-cover-*` 样式）

- [ ] **Step 1: 追加 `.oc-cover-dropzone` 与 `.oc-cover-preview` 样式**

在 `medias_list.html` 的 `.oc-modal-foot { … }` 规则之前插入（紧跟 `.oc-upload-row.err` 所在段）：

```css
/* Cover image */
.oc-cover-wrap { display:flex; gap:var(--oc-sp-3); align-items:flex-start; }
.oc-cover-dropzone { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:var(--oc-sp-2); width:260px; height:148px; border:1.5px dashed var(--oc-border-strong); border-radius:var(--oc-r-md); background:var(--oc-bg-subtle); color:var(--oc-fg-muted); cursor:pointer; text-align:center; transition:all var(--oc-dur-fast) var(--oc-ease); }
.oc-cover-dropzone:hover, .oc-cover-dropzone.drag { border-color:var(--oc-accent); background:var(--oc-accent-subtle); color:var(--oc-accent); }
.oc-cover-dropzone .dz-icon { width:32px; height:32px; border-radius:50%; background:var(--oc-accent-subtle); color:var(--oc-accent); display:flex; align-items:center; justify-content:center; }
.oc-cover-preview { position:relative; width:260px; height:148px; border:1px solid var(--oc-border); border-radius:var(--oc-r-md); overflow:hidden; background:var(--oc-bg-muted); }
.oc-cover-preview img { width:100%; height:100%; object-fit:cover; display:block; }
.oc-cover-preview .actions { position:absolute; inset:auto 0 0 0; display:flex; gap:var(--oc-sp-2); padding:var(--oc-sp-2); background:linear-gradient(to top, oklch(22% 0.02 235 / 0.72), transparent); opacity:0; transition:opacity var(--oc-dur-fast) var(--oc-ease); }
.oc-cover-preview:hover .actions { opacity:1; }
.oc-cover-preview .actions .oc-btn { height:26px; font-size:12px; padding:0 10px; }

.oc-section { display:flex; flex-direction:column; gap:var(--oc-sp-3); margin-bottom:var(--oc-sp-6); }
.oc-section:last-child { margin-bottom:0; }
.oc-section-title { display:flex; align-items:center; gap:var(--oc-sp-2); font-size:13px; font-weight:600; color:var(--oc-fg); }
.oc-section-title .req { color:var(--oc-danger); }
.oc-section-title .count { min-width:18px; height:18px; padding:0 6px; border-radius:9px; background:var(--oc-bg-muted); color:var(--oc-fg-muted); font-size:11px; display:inline-flex; align-items:center; justify-content:center; font-variant-numeric:tabular-nums; }
.oc-section-title .optional { color:var(--oc-fg-subtle); font-weight:400; font-size:12px; margin-left:var(--oc-sp-1); }

.oc-hint { font-size:11px; color:var(--oc-fg-subtle); margin-top:-4px; }
```

- [ ] **Step 2: 整个重写 `_medias_edit_modal.html`**

```html
<div class="oc-modal-mask oc" id="editMask" hidden>
  <div class="oc-modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
    <div class="oc-modal-head">
      <h3 id="modalTitle">编辑产品素材</h3>
      <button class="oc-icon-btn" id="modalClose" title="关闭" aria-label="关闭">
        <svg width="16" height="16"><use href="#ic-close"/></svg>
      </button>
    </div>

    <div class="oc-modal-body">

      <!-- 基本信息 -->
      <section class="oc-section">
        <div class="oc-row">
          <div class="oc-field" style="margin-bottom:0">
            <label class="oc-label" for="mName">产品名称<span class="req">*</span></label>
            <input id="mName" class="oc-input" maxlength="120" placeholder="例如：兰蔻小黑瓶精华">
          </div>
          <div class="oc-field" style="margin-bottom:0">
            <label class="oc-label" for="mCode">产品 ID<span class="req">*</span></label>
            <input id="mCode" class="oc-input" maxlength="64" placeholder="sonic-lens-refresher" autocomplete="off">
            <div class="oc-hint">小写字母、数字和连字符，3–64 字符，如 <code>sonic-lens-refresher</code></div>
          </div>
        </div>
      </section>

      <!-- 封面图 -->
      <section class="oc-section">
        <div class="oc-section-title">封面图<span class="req">*</span></div>
        <div class="oc-cover-wrap">
          <div id="coverDropzone" class="oc-cover-dropzone" tabindex="0" role="button" aria-label="上传封面图" hidden>
            <div class="dz-icon"><svg width="16" height="16"><use href="#ic-upload"/></svg></div>
            <div class="dz-title">点击或拖拽上传封面图</div>
            <div class="dz-hint">JPG / PNG / WebP</div>
          </div>
          <div id="coverPreview" class="oc-cover-preview" hidden>
            <img id="coverImg" alt="封面">
            <div class="actions">
              <button type="button" class="oc-btn ghost sm" id="coverReplace">更换</button>
            </div>
          </div>
          <input type="file" id="coverInput" accept="image/*" hidden>
        </div>
      </section>

      <!-- 视频素材 -->
      <section class="oc-section">
        <div class="oc-section-title">
          <span>视频素材<span class="req">*</span></span>
          <span class="count" id="itemsBadge">0</span>
        </div>
        <div class="oc-dropzone" id="dropzone" tabindex="0" role="button" aria-label="上传视频素材">
          <div class="dz-icon"><svg width="18" height="18"><use href="#ic-upload"/></svg></div>
          <div class="dz-title">点击或拖拽上传 1 个视频素材</div>
          <div class="dz-hint">每次操作仅接受 1 个文件；支持 mp4 / mov / webm / mkv，可多次上传</div>
          <input type="file" id="fileInput" accept="video/*" hidden>
        </div>
        <div id="uploadProgress" class="oc-upload-list"></div>
        <div id="itemsGrid" class="oc-items-grid"></div>
      </section>

      <!-- 文案 -->
      <section class="oc-section">
        <div class="oc-section-title">
          <span>文案</span>
          <span class="optional">(可选)</span>
          <span class="count" id="cwBadge">0</span>
        </div>
        <div id="cwList" class="oc-cw-list"></div>
        <button class="oc-add-btn" type="button" id="cwAddBtn">
          <svg width="14" height="14"><use href="#ic-plus"/></svg>
          <span>添加文案条目</span>
        </button>
      </section>

    </div>

    <div class="oc-modal-foot">
      <button class="oc-btn ghost" id="cancelBtn">取消</button>
      <button class="oc-btn primary" id="saveBtn">
        <svg width="14" height="14"><use href="#ic-check"/></svg>
        <span>保存</span>
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 提交**

```bash
git add web/templates/_medias_edit_modal.html web/templates/medias_list.html
git commit -m "feat(medias-ui): 弹窗单页结构 + 封面上传样式"
```

---

## Task 7 — 前端脚本：状态、封面上传、单视频上传、保存校验

**Files:**
- Modify: `web/static/medias.js`（整块改写 Modal / Items / Save 片段）

- [ ] **Step 1: 删除 `switchTab` 与所有调用点**

删除 `medias.js:152-155` 的 `switchTab` 函数；删除 `openEdit` 中 `switchTab('basic');`、`openCreate` 中 `switchTab('basic');`、`ensureProductIdForUpload` 里 `switchTab('basic');`、`save()` 中 `switchTab('basic');`、以及最底部 `document.querySelectorAll('.oc-tab').forEach(...)` 这一整句。

- [ ] **Step 2: 改写 `openCreate`**

```javascript
function openCreate() {
  state.current = { product: null, copywritings: [], items: [] };
  $('modalTitle').textContent = '添加产品素材';
  $('mName').value = '';
  $('mCode').value = '';
  setCover(null);
  renderCopywritings([]);
  renderItems([]);
  $('uploadProgress').innerHTML = '';
  showModal();
  setTimeout(() => $('mName').focus(), 80);
}
```

- [ ] **Step 3: 改写 `openEdit`**

```javascript
async function openEdit(pid) {
  try {
    const data = await fetchJSON('/medias/api/products/' + pid);
    state.current = data;
    $('modalTitle').textContent = '编辑产品素材';
    $('mName').value = data.product.name || '';
    $('mCode').value = data.product.product_code || '';
    setCover(data.product.cover_object_key ? `/medias/cover/${pid}?_=${Date.now()}` : null);
    renderCopywritings(data.copywritings);
    renderItems(data.items);
    $('uploadProgress').innerHTML = '';
    showModal();
  } catch (e) {
    alert('加载失败：' + (e.message || e));
  }
}
```

- [ ] **Step 4: 新增封面状态管理**

在 `// ---------- Items ----------` 之前插入：

```javascript
// ---------- Cover ----------
const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/;

function setCover(url) {
  const dz = $('coverDropzone');
  const pv = $('coverPreview');
  if (url) {
    $('coverImg').src = url;
    pv.hidden = false; dz.hidden = true;
  } else {
    $('coverImg').removeAttribute('src');
    pv.hidden = true; dz.hidden = false;
  }
}

async function uploadCover(file) {
  if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
  if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
  const pid = await ensureProductIdForUpload();
  if (!pid) return;
  try {
    const boot = await fetchJSON(`/medias/api/products/${pid}/cover/bootstrap`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name }),
    });
    const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
    if (!putRes.ok) throw new Error('TOS 上传失败');
    const done = await fetchJSON(`/medias/api/products/${pid}/cover/complete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ object_key: boot.object_key }),
    });
    state.current.product.cover_object_key = boot.object_key;
    setCover(done.cover_url + `?_=${Date.now()}`);
  } catch (e) {
    alert('封面上传失败：' + (e.message || ''));
  }
}
```

- [ ] **Step 5: 改写 `ensureProductIdForUpload`（强校验 name + product_code）**

```javascript
async function ensureProductIdForUpload() {
  if (state.current && state.current.product && state.current.product.id) return state.current.product.id;
  const name = $('mName').value.trim();
  const code = $('mCode').value.trim().toLowerCase();
  if (!name) { alert('请先填写产品名称'); $('mName').focus(); return null; }
  if (!SLUG_RE.test(code)) { alert('请先填写合法的产品 ID（小写字母/数字/连字符，3–64）'); $('mCode').focus(); return null; }
  try {
    const res = await fetchJSON('/medias/api/products', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, product_code: code }),
    });
    const full = await fetchJSON('/medias/api/products/' + res.id);
    state.current = full;
    $('modalTitle').textContent = '编辑产品素材';
    return res.id;
  } catch (e) {
    const msg = (e.message || '').toString();
    if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('mCode').focus(); }
    else alert('创建失败：' + msg);
    return null;
  }
}
```

- [ ] **Step 6: 改写 `uploadFiles` → `uploadVideo`（单文件）**

把原 `uploadFiles(files)` 改为：

```javascript
async function uploadVideo(file) {
  if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
  const pid = await ensureProductIdForUpload();
  if (!pid) return;
  const box = $('uploadProgress');
  const row = document.createElement('div');
  row.className = 'oc-upload-row';
  row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>上传中…</span>`;
  box.appendChild(row);
  try {
    const boot = await fetchJSON(`/medias/api/products/${pid}/items/bootstrap`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name }),
    });
    const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
    if (!putRes.ok) throw new Error('TOS 上传失败');
    await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ object_key: boot.object_key, filename: file.name, file_size: file.size }),
    });
    row.className = 'oc-upload-row ok';
    row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>完成</span>`;
  } catch (e) {
    row.className = 'oc-upload-row err';
    row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>失败：${escapeHtml(e.message || '')}</span>`;
  }
  const full = await fetchJSON('/medias/api/products/' + pid);
  state.current = full;
  renderItems(full.items);
  loadList();
}
```

- [ ] **Step 7: 改写 `save()`（前端硬校验 3 必填项 + 409 处理）**

```javascript
async function save() {
  const name = $('mName').value.trim();
  const code = $('mCode').value.trim().toLowerCase();
  if (!name) { alert('产品名称必填'); $('mName').focus(); return; }
  if (!SLUG_RE.test(code)) { alert('产品 ID 必填且需合法（小写字母/数字/连字符，3–64）'); $('mCode').focus(); return; }
  if (!state.current || !state.current.product || !state.current.product.cover_object_key) {
    alert('请上传封面图'); return;
  }
  if (!document.querySelectorAll('.oc-item').length) {
    alert('请至少上传 1 条视频素材'); return;
  }
  const pid = state.current.product.id;
  try {
    await fetchJSON('/medias/api/products/' + pid, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, product_code: code,
        copywritings: collectCopywritings(),
      }),
    });
    hideModal();
    loadList();
  } catch (e) {
    const msg = (e.message || '').toString();
    if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('mCode').focus(); }
    else alert('保存失败：' + msg);
  }
}
```

> 注意：`save` 不再依赖 `ensureProductIdForUpload`——因为进入 save 阶段一定已经通过封面/视频上传创建过产品。

- [ ] **Step 8: 事件绑定**

在 `DOMContentLoaded` 里，`const dz = $('dropzone'); …` 之上插入封面事件：

```javascript
const cdz = $('coverDropzone');
cdz.addEventListener('click', () => $('coverInput').click());
cdz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('coverInput').click(); } });
cdz.addEventListener('dragover', (e) => { e.preventDefault(); cdz.classList.add('drag'); });
cdz.addEventListener('dragleave', () => cdz.classList.remove('drag'));
cdz.addEventListener('drop', (e) => {
  e.preventDefault(); cdz.classList.remove('drag');
  const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
  if (f) uploadCover(f);
});
$('coverReplace').addEventListener('click', () => $('coverInput').click());
$('coverInput').addEventListener('change', (e) => {
  const f = e.target.files[0]; e.target.value = '';
  if (f) uploadCover(f);
});
```

把原 dropzone 的 drop/change 逻辑改为"只取第一个文件"：

```javascript
dz.addEventListener('drop', (e) => {
  e.preventDefault(); dz.classList.remove('drag');
  const file = [...(e.dataTransfer.files || [])]
    .find(f => f.type.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/i.test(f.name));
  if (file) uploadVideo(file);
});
$('fileInput').addEventListener('change', (e) => {
  const file = e.target.files[0]; e.target.value = '';
  if (file) uploadVideo(file);
});
```

- [ ] **Step 9: 手工验收（浏览器）**

启动本地服务器，操作如下并人工核对：

1. 点击"添加产品素材"，弹窗单页展示四个区块：基本信息 / 封面 / 视频 / 文案。
2. 不填任何字段点"保存"→ 提示"产品名称必填"。
3. 填 name，产品 ID 填 `BAD_ID` → 点保存 → 提示 slug 非法。
4. 产品 ID 改为 `plan-test-001`，点击封面 dropzone 上传一张 JPG → 预览出现。
5. 点击视频 dropzone 上传一个 mp4 → 进度行→完成→缩略图出现。
6. 拖拽 2 个视频同时进来 → 只上传第 1 个。
7. 保存 → 列表里新卡出现，封面为刚上传的图（不是视频抽帧）。
8. 用同一 `plan-test-001` 重新创建 → 保存时 409 提示"产品 ID 已被占用"。
9. 编辑已创建产品 → 表单正确回填 name、product_code、封面。
10. DevTools 面板 → Elements 检查弹窗无紫色 token 引用（Ctrl+F 搜 `violet|indigo|purple`）。

- [ ] **Step 10: 提交**

```bash
git add web/static/medias.js
git commit -m "feat(medias-ui): 单页交互 + 封面上传 + 单视频上传 + 保存校验"
```

---

## Task 8 — 路由集成测试（补强）

**Files:**
- Modify: `tests/test_web_routes.py`（如已有 medias 路由测试则追加；否则新建 `tests/test_medias_routes.py`）

- [ ] **Step 1: 先 grep 现有 medias 路由测试**

Run: `grep -n "medias" tests/test_web_routes.py`
Expected: 判断是否已覆盖 `/medias/api/products`。若未覆盖则新建文件。

- [ ] **Step 2: 追加 slug 校验与 409 冲突用例**

在合适文件里追加（以 flask test client，具体 fixture 遵循仓库 `tests/conftest.py` 约定）：

```python
def test_create_product_rejects_bad_slug(logged_in_client):
    rv = logged_in_client.post("/medias/api/products",
        json={"name": "t", "product_code": "Bad_Slug"})
    assert rv.status_code == 400
    assert "产品 ID" in rv.get_json()["error"]


def test_create_product_rejects_duplicate_slug(logged_in_client):
    rv1 = logged_in_client.post("/medias/api/products",
        json={"name": "t1", "product_code": "dup-test"})
    assert rv1.status_code == 201
    rv2 = logged_in_client.post("/medias/api/products",
        json={"name": "t2", "product_code": "dup-test"})
    assert rv2.status_code == 409


def test_put_product_requires_cover_and_items(logged_in_client):
    rv = logged_in_client.post("/medias/api/products",
        json={"name": "t", "product_code": "save-guard-1"})
    pid = rv.get_json()["id"]
    rv2 = logged_in_client.put(f"/medias/api/products/{pid}",
        json={"name": "t", "product_code": "save-guard-1"})
    assert rv2.status_code == 400
    assert "封面图" in rv2.get_json()["error"]
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/test_web_routes.py -v -k "slug or duplicate or cover_and_items"`
Expected: 三个用例 PASS。

> 如仓库当前没有 `logged_in_client` fixture，用 conftest 提供的等价物（遵循已有 medias DAO 测试约定）；若 conftest 无法提供已登录客户端，则把这三个用例退化为 `appcore/medias.py` 层的单元测试（校验 slug 正则 + 唯一约束异常）。

- [ ] **Step 4: 提交**

```bash
git add tests/
git commit -m "test(medias): 覆盖 slug 校验、唯一冲突与保存必填"
```

---

## Task 9 — 端到端清理 + 列表回归 + 最终提交

- [ ] **Step 1: 冒烟 + 回归列表页**

浏览器访问 `/medias/`，确认：
- 现有（无 product_code / 无 cover 的）老产品仍正常展示（cover 回落首个视频缩略图）。
- 新建的产品显示自定义封面。
- 搜索、分页、归档、删除按钮均正常。

- [ ] **Step 2: 确认无遗留紫色**

Run:
```bash
grep -rEn "(violet|indigo|purple|magenta|lavender)" web/templates/_medias_edit_modal.html web/static/medias.js web/templates/medias_list.html
```
Expected: 空输出。

- [ ] **Step 3: 跑完整测试套件**

Run: `pytest tests/test_appcore_medias.py tests/test_web_routes.py -v`
Expected: 全部 PASS。

- [ ] **Step 4: 触发发布流程（按项目约定）**

按项目 `CLAUDE.md` 中 "发布流程"：commit + push + SSH pull + restart。**由用户确认再执行**。

---

## Self-Review 检查

- [x] 规格第 2 节字段表：Task 1/2 覆盖 DB；Task 4/5/7 覆盖路由与前端。
- [x] 规格第 3 节迁移：Task 1 完成（仅加 `product_code` 与 `cover_object_key`，不加 `cover_url` 列——与 spec 里"cover_url TEXT"有出入，已在计划顶部注明调整）。
- [x] 规格第 4 节后端：Task 2/3/4/5。
- [x] 规格第 5 节前端：Task 6/7。
- [x] 规格第 6 节三态与键盘：Task 7 Step 8/9 手工验收覆盖。
- [x] 规格第 7 节验收清单：Task 7 Step 9 + Task 8 + Task 9。
- [x] 无 TBD / TODO / 抽象描述；所有代码步骤包含具体代码块。
- [x] 命名一致：`product_code` / `cover_object_key` / `SLUG_RE` / `uploadCover` / `uploadVideo` 全程一致。
