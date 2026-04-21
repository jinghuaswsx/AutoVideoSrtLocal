# media_products 新增明空 ID (mk_id) 字段 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按任务清单逐步实施。本 plan 使用 `- [ ]` checkbox 追踪。

**Goal:** 在 `media_products` 表新增 `mk_id` 字段（对应明空系统产品 ID，数字 1–8 位，全局唯一），并提供列表页 inline edit 与编辑模态两个录入入口。

**Architecture:**
- 数据层：`media_products` 加列 + `UNIQUE` 索引；仓库已有自动迁移机制（`appcore/db_migrations.py` 会在服务启动时跑 `db/migrations/*.sql`），放文件即可
- 后端：DAO 层做字段归一化与格式校验，路由层捕获冲突 `IntegrityError` 返回 `409`、格式 `ValueError` 返回 `400`；`_serialize_product` 把 `mk_id` 吐给前端
- 前端：列表表格加一列「明空 ID」支持 inline edit；编辑模态在产品名输入框外层 hero-grid 之前独立一行加 `mk_id` 字段
- 推送管理本次完全不动

**Tech Stack:** Python 3.14 · Flask · PyMySQL · 原生 HTML + JS · pytest（需 MySQL 环境）

---

## Spec 对齐备注

Spec `docs/superpowers/specs/2026-04-21-media-products-mk-id-design.md` 写的编辑模态文件是 `_medias_edit_modal.html`，实际**真正的编辑模态**是 `_medias_edit_detail_modal.html`（id `#edMask` / 标题「编辑产品素材」）。`_medias_edit_modal.html` 只是「添加产品素材」弹窗，本次按 spec §4.3「创建接口本次不动」的决定不修改。Task 5 在 detail modal 里加字段。

## 测试基线说明

项目测试全部依赖 MySQL，Windows 本地环境未启动 MySQL，本地无法跑 `pytest`。每个 task 的验证方式为：
1. 代码级：简单语法/导入检查（`python -c "import ..."`）
2. 集成级：测试发布后在 `http://14.103.220.208:9999/medias/` 手动验证（见 Task 6）

## 文件结构

| 文件 | 动作 | 责任 |
|------|------|------|
| `db/migrations/2026_04_21_add_mk_id_to_media_products.sql` | 新建 | 加列 + 唯一索引 |
| `appcore/medias.py` | 改 `update_product` | 白名单放行 mk_id + 归一化校验 |
| `web/routes/medias.py` | 改 `_serialize_product` + `api_update_product` | 出入参带 mk_id；冲突/格式错误映射 HTTP 状态码 |
| `web/templates/medias_list.html` | 改 `<style>` | 新增 inline edit 样式规则 |
| `web/static/medias.js` | 改 `renderGrid` / `rowHTML` + 新增 inline edit handler + 改 `edSave` + 改 `openEditDetail` 打开时回填 | 列表 inline edit + 编辑模态字段 |
| `web/templates/_medias_edit_detail_modal.html` | 改 | 在产品名输入框上方加 mk_id 字段 |

---

## Task 1 · 新建 DB migration

**Files:**
- Create: `db/migrations/2026_04_21_add_mk_id_to_media_products.sql`

- [ ] **Step 1.1：写 migration SQL**

创建文件 `db/migrations/2026_04_21_add_mk_id_to_media_products.sql`，内容：

```sql
-- 为 media_products 增加明空系统 ID 字段 mk_id
-- 规则：INT UNSIGNED（容纳 1-8 位十进制），允许 NULL（老数据不回填），全局 UNIQUE
ALTER TABLE media_products
  ADD COLUMN mk_id INT UNSIGNED NULL AFTER product_code,
  ADD UNIQUE KEY uk_media_products_mk_id (mk_id);
```

- [ ] **Step 1.2：确认文件被 migration runner 识别**

看 `appcore/db_migrations.py` 行 44：`files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))`，会按字母序扫所有 `.sql`。文件名以 `2026_04_21_` 开头，排在现有 `2026_04_19_*` 之后，排序正确。

运行 sanity 检查（不需要 DB）：

```bash
ls db/migrations/2026_04_21_add_mk_id_to_media_products.sql
```

Expected：文件存在

- [ ] **Step 1.3：commit**

```bash
git add db/migrations/2026_04_21_add_mk_id_to_media_products.sql
git commit -m "feat(medias): 新增 mk_id 字段迁移 (INT UNSIGNED + UNIQUE)"
```

---

## Task 2 · DAO 归一化与白名单

**Files:**
- Modify: `appcore/medias.py:311-329`

- [ ] **Step 2.1：扩展 update_product 的允许白名单**

打开 `appcore/medias.py`，把 `update_product()` 的 `allowed` 集合加入 `"mk_id"`，并在 `keys = [...]` 行之前插入归一化 / 校验。替换以下代码块：

**原来（行 311–329）：**
```python
def update_product(product_id: int, **fields) -> int:
    import json as _json
    allowed = {"name", "color_people", "source", "archived",
               "importance", "trend_score", "selling_points",
               "product_code", "cover_object_key",
               "localized_links_json", "ad_supported_langs",
               "link_check_tasks_json"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return 0
    # localized_links_json：支持 dict 输入，自动序列化为 JSON 字符串
    def _val(k):
        v = fields[k]
        if k in {"localized_links_json", "link_check_tasks_json"} and isinstance(v, dict):
            return _json.dumps(v, ensure_ascii=False)
        return v
    set_sql = ", ".join(f"{k}=%s" for k in keys)
    args = tuple(_val(k) for k in keys) + (product_id,)
    return execute(f"UPDATE media_products SET {set_sql} WHERE id=%s", args)
```

**改成：**
```python
def update_product(product_id: int, **fields) -> int:
    import json as _json
    allowed = {"name", "color_people", "source", "archived",
               "importance", "trend_score", "selling_points",
               "product_code", "cover_object_key",
               "localized_links_json", "ad_supported_langs",
               "link_check_tasks_json",
               "mk_id"}
    # mk_id 归一化：空串 / 全空白 → NULL；否则必须是 1-8 位纯数字
    if "mk_id" in fields:
        v = fields["mk_id"]
        if v is None or (isinstance(v, str) and not v.strip()):
            fields["mk_id"] = None
        else:
            s = str(v).strip()
            if not s.isdigit() or not (1 <= len(s) <= 8):
                raise ValueError("mk_id 必须是 1-8 位数字")
            fields["mk_id"] = int(s)
    keys = [k for k in fields if k in allowed]
    if not keys:
        return 0
    # localized_links_json：支持 dict 输入，自动序列化为 JSON 字符串
    def _val(k):
        v = fields[k]
        if k in {"localized_links_json", "link_check_tasks_json"} and isinstance(v, dict):
            return _json.dumps(v, ensure_ascii=False)
        return v
    set_sql = ", ".join(f"{k}=%s" for k in keys)
    args = tuple(_val(k) for k in keys) + (product_id,)
    return execute(f"UPDATE media_products SET {set_sql} WHERE id=%s", args)
```

- [ ] **Step 2.2：语法检查（无 DB）**

```bash
/c/Python314/python.exe -c "import ast; ast.parse(open('appcore/medias.py', encoding='utf-8').read()); print('ok')"
```

Expected：`ok`

- [ ] **Step 2.3：commit**

```bash
git add appcore/medias.py
git commit -m "feat(medias): DAO 支持 mk_id 字段（白名单+归一化校验）"
```

---

## Task 3 · 后端路由 · 接收 mk_id + 冲突映射 + 序列化

**Files:**
- Modify: `web/routes/medias.py`（`_serialize_product` 行 128–169；`api_update_product` 行 339–397；文件头部导入）

- [ ] **Step 3.1：在 `_serialize_product` 返回体加 mk_id**

在 `web/routes/medias.py:155` 行（`"product_code": p.get("product_code"),`）之后插入一行：

```python
        "mk_id": p.get("mk_id"),
```

最终该段代码：

```python
    return {
        "id": p["id"],
        "name": p["name"],
        "product_code": p.get("product_code"),
        "mk_id": p.get("mk_id"),
        "has_en_cover": has_en_cover,
        ...
    }
```

- [ ] **Step 3.2：文件顶部确保 `pymysql` 已导入**

搜一下 `web/routes/medias.py` 顶部 import 段是否已有 `pymysql`：

```bash
grep -n "^import pymysql\|^from pymysql" web/routes/medias.py
```

**如果没有**，在现有 import 段末尾（`from appcore import medias` 附近）加一行：

```python
import pymysql.err
```

- [ ] **Step 3.3：`api_update_product` 接收 mk_id 并捕获异常**

在 `web/routes/medias.py:339-397`，`api_update_product` 里：

**A.** 在 `update_fields = {"name": name, "product_code": product_code}` 后（约行 361）、`if isinstance(body.get("localized_links"), dict):` 之前，插入：

```python
    # 明空 ID（mk_id）：选填，1-8 位数字，空串代表清除
    if "mk_id" in body:
        update_fields["mk_id"] = body.get("mk_id")
```

**B.** 把 `medias.update_product(pid, **update_fields)`（原行 389）包进 try/except：

**原来：**
```python
    medias.update_product(pid, **update_fields)

    if isinstance(body.get("copywritings"), dict):
```

**改成：**
```python
    try:
        medias.update_product(pid, **update_fields)
    except ValueError as e:
        return jsonify({"error": "mk_id_invalid", "message": str(e)}), 400
    except pymysql.err.IntegrityError as e:
        code = e.args[0] if e.args else None
        if code == 1062 and "uk_media_products_mk_id" in str(e):
            return jsonify({
                "error": "mk_id_conflict",
                "message": "明空 ID 已被其他产品占用",
            }), 409
        raise

    if isinstance(body.get("copywritings"), dict):
```

- [ ] **Step 3.4：语法检查**

```bash
/c/Python314/python.exe -c "import ast; ast.parse(open('web/routes/medias.py', encoding='utf-8').read()); print('ok')"
```

Expected：`ok`

- [ ] **Step 3.5：commit**

```bash
git add web/routes/medias.py
git commit -m "feat(medias): 路由接收 mk_id，冲突 409 / 非法 400，序列化带出"
```

---

## Task 4 · 前端列表 inline edit

**Files:**
- Modify: `web/templates/medias_list.html`（scoped `<style>` 段 — 追加几条规则；表格 colgroup 不在此文件，在 JS `renderGrid` 里）
- Modify: `web/static/medias.js`（`renderGrid` 行 347；`rowHTML` 行 383；事件绑定；新增 `attachMkIdInlineEdit` helper）

- [ ] **Step 4.1：`renderGrid` 表头加列**

打开 `web/static/medias.js`，找到 `renderGrid` 行 347 附近的 `grid.innerHTML = \`...\``，修改 `<colgroup>` 和 `<thead>`：

**原来（行 348–370）：**
```javascript
    grid.innerHTML = `
      <table class="oc-table" style="table-layout:fixed;">
        <colgroup>
          <col style="width:48px">
          <col style="width:96px">
          <col style="width:120px">
          <col style="width:120px">
          <col style="width:60px">
          <col style="width:200px">
          <col style="width:108px">
          <col style="width:160px">
        </colgroup>
        <thead>
          <tr>
            <th>ID</th>
            <th>主图</th>
            <th>产品名称</th>
            <th>产品 ID</th>
            <th>素材数</th>
            <th>语种覆盖</th>
            <th>修改时间</th>
            <th>操作</th>
          </tr>
        </thead>
```

**改成：**
```javascript
    grid.innerHTML = `
      <table class="oc-table" style="table-layout:fixed;">
        <colgroup>
          <col style="width:48px">
          <col style="width:96px">
          <col style="width:120px">
          <col style="width:120px">
          <col style="width:96px">
          <col style="width:60px">
          <col style="width:200px">
          <col style="width:108px">
          <col style="width:160px">
        </colgroup>
        <thead>
          <tr>
            <th>ID</th>
            <th>主图</th>
            <th>产品名称</th>
            <th>产品 ID</th>
            <th>明空 ID</th>
            <th>素材数</th>
            <th>语种覆盖</th>
            <th>修改时间</th>
            <th>操作</th>
          </tr>
        </thead>
```

- [ ] **Step 4.2：`rowHTML` 加单元格**

同一文件 `rowHTML` 行 383。在「产品 ID」单元格（行 394）之后、「素材数」单元格（行 395）之前插入新 `<td>`。

**原来（行 389–402）：**
```javascript
    return `
      <tr${warnCls} data-pid="${p.id}">
        <td class="mono">${p.id}</td>
        <td><div class="oc-thumb-sm">${cover}</div></td>
        <td class="name wrap"><a href="#" data-pid="${p.id}" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</a></td>
        <td class="mono wrap" title="${escapeHtml(p.product_code || '')}">${p.product_code ? `<a href="https://newjoyloo.com/products/${encodeURIComponent(p.product_code)}" target="_blank" rel="noopener noreferrer">${escapeHtml(p.product_code)}</a>` : '<span class="muted">—</span>'}</td>
        <td><span class="oc-pill">${count}</span></td>
        <td>${renderLangBar(p.lang_coverage)}</td>
        <td class="muted">${fmtDate(p.updated_at)}</td>
        <td class="actions">
          <button class="oc-btn sm ghost" data-edit="${p.id}">${icon('edit', 12)}<span>编辑</span></button>
          <button class="bt-row-btn" data-bt-open="${p.id}" data-bt-name="${escapeHtml(p.name)}" title="一键翻译到多语言">🌐 翻译</button>
        </td>
      </tr>`;
```

**改成：**
```javascript
    const mkIdText = (p.mk_id === null || p.mk_id === undefined) ? '' : String(p.mk_id);
    return `
      <tr${warnCls} data-pid="${p.id}">
        <td class="mono">${p.id}</td>
        <td><div class="oc-thumb-sm">${cover}</div></td>
        <td class="name wrap"><a href="#" data-pid="${p.id}" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</a></td>
        <td class="mono wrap" title="${escapeHtml(p.product_code || '')}">${p.product_code ? `<a href="https://newjoyloo.com/products/${encodeURIComponent(p.product_code)}" target="_blank" rel="noopener noreferrer">${escapeHtml(p.product_code)}</a>` : '<span class="muted">—</span>'}</td>
        <td class="mono mk-id-cell" data-pid="${p.id}" data-mkid="${escapeHtml(mkIdText)}" title="点击编辑明空 ID"><span class="mk-id-text">${mkIdText ? escapeHtml(mkIdText) : '<span class="muted">—</span>'}</span></td>
        <td><span class="oc-pill">${count}</span></td>
        <td>${renderLangBar(p.lang_coverage)}</td>
        <td class="muted">${fmtDate(p.updated_at)}</td>
        <td class="actions">
          <button class="oc-btn sm ghost" data-edit="${p.id}">${icon('edit', 12)}<span>编辑</span></button>
          <button class="bt-row-btn" data-bt-open="${p.id}" data-bt-name="${escapeHtml(p.name)}" title="一键翻译到多语言">🌐 翻译</button>
        </td>
      </tr>`;
```

（注意：`escapeHtml(mkIdText)` 单独调一次用于 `data-mkid` 属性值；`<span class="mk-id-text">` 里的内容用条件分支：非空时 `escapeHtml`，空值时用 `<span class="muted">—</span>` 展示占位。这里 mkIdText 只会是数字字符串，`escapeHtml` 是保险。）

- [ ] **Step 4.3：`renderGrid` 事件绑定加 inline edit**

同一 `renderGrid`，行 375–380 的事件绑定段：

**原来：**
```javascript
    grid.querySelectorAll('[data-edit]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); openEdit(+b.dataset.edit); }));
    grid.querySelectorAll('[data-del]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); deleteProduct(+b.dataset.del); }));
    grid.querySelectorAll('tr[data-pid] .name a').forEach(a =>
      a.addEventListener('click', (e) => { e.preventDefault(); openEdit(+a.dataset.pid); }));
```

**改成：**
```javascript
    grid.querySelectorAll('[data-edit]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); openEdit(+b.dataset.edit); }));
    grid.querySelectorAll('[data-del]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); deleteProduct(+b.dataset.del); }));
    grid.querySelectorAll('tr[data-pid] .name a').forEach(a =>
      a.addEventListener('click', (e) => { e.preventDefault(); openEdit(+a.dataset.pid); }));
    grid.querySelectorAll('td.mk-id-cell').forEach(td =>
      td.addEventListener('click', (e) => { e.stopPropagation(); startMkIdInlineEdit(td); }));
```

- [ ] **Step 4.4：新增 `startMkIdInlineEdit` helper 函数**

在 `medias.js` 的 `rowHTML` 函数之后（行 403 之后，`closeAllMenus` 之前）插入：

```javascript
  async function startMkIdInlineEdit(td) {
    if (td.dataset.editing === '1') return;
    td.dataset.editing = '1';
    const pid = +td.dataset.pid;
    const original = td.dataset.mkid || '';
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'numeric';
    input.maxLength = 8;
    input.value = original;
    input.className = 'mk-id-input';
    input.setAttribute('aria-label', '明空 ID');
    td.innerHTML = '';
    td.appendChild(input);
    input.focus();
    input.select();

    let settled = false;

    async function commit() {
      if (settled) return;
      settled = true;
      const raw = input.value.trim();
      if (raw === original) { restore(original); return; }
      if (raw !== '' && !/^\d{1,8}$/.test(raw)) {
        input.classList.add('error');
        input.focus();
        settled = false;
        return;
      }
      input.disabled = true;
      try {
        await fetchJSON('/medias/api/products/' + pid, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mk_id: raw === '' ? null : parseInt(raw, 10) }),
        });
        restore(raw);
      } catch (e) {
        const msg = (e.message || '').toString();
        if (msg.includes('mk_id_conflict') || msg.includes('已被其他产品占用') || msg.includes('已被占用')) {
          alert('明空 ID 已被其他产品占用');
        } else if (msg.includes('mk_id_invalid') || msg.includes('必须是 1-8 位数字')) {
          alert('明空 ID 必须是 1-8 位数字');
        } else {
          alert('保存失败：' + msg);
        }
        input.disabled = false;
        input.classList.add('error');
        input.focus();
        settled = false;
      }
    }

    function restore(value) {
      td.dataset.mkid = value;
      td.dataset.editing = '';
      td.innerHTML = value
        ? `<span class="mk-id-text">${escapeHtml(value)}</span>`
        : `<span class="mk-id-text"><span class="muted">—</span></span>`;
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      else if (e.key === 'Escape') { e.preventDefault(); settled = true; restore(original); }
    });
    input.addEventListener('blur', commit);
  }
```

- [ ] **Step 4.5：加 scoped 样式**

打开 `web/templates/medias_list.html`，找到页面 `<style>` 块结尾（应该在 `<head>` 内的 scoped token 定义之后）。在样式块最后追加：

```css
  /* ---------- mk_id inline edit ---------- */
  .oc-table td.mk-id-cell {
    cursor: pointer;
  }
  .oc-table td.mk-id-cell:hover {
    background: var(--oc-bg-subtle);
  }
  .oc-table td.mk-id-cell .mk-id-text .muted {
    color: var(--oc-fg-subtle);
  }
  .oc-table td.mk-id-cell .mk-id-input {
    width: 100%;
    height: 28px;
    padding: 0 8px;
    font-family: var(--oc-font-mono, ui-monospace, monospace);
    font-size: 13px;
    border: 1px solid var(--oc-border-strong);
    border-radius: 6px;
    background: #fff;
    color: var(--oc-fg);
    outline: none;
    transition: border-color 120ms, box-shadow 120ms;
  }
  .oc-table td.mk-id-cell .mk-id-input:focus {
    border-color: var(--oc-accent);
    box-shadow: 0 0 0 2px var(--oc-accent-ring);
  }
  .oc-table td.mk-id-cell .mk-id-input.error {
    border-color: var(--oc-danger);
    box-shadow: 0 0 0 2px var(--oc-danger-bg);
  }
```

**如果** `medias_list.html` 的 `<style>` 段落结构无法一眼定位，先读该文件首 80 行确认后再追加，避免破坏现有 CSS。

- [ ] **Step 4.6：语法检查**

```bash
/c/Python314/python.exe -c "print('JS/CSS 无本地 lint，跳过。后续靠浏览器 devtools 控制台验证。')"
ls web/static/medias.js web/templates/medias_list.html
```

- [ ] **Step 4.7：commit**

```bash
git add web/static/medias.js web/templates/medias_list.html
git commit -m "feat(medias): 列表页新增明空 ID 列，支持 inline 编辑"
```

---

## Task 5 · 编辑模态字段

**Files:**
- Modify: `web/templates/_medias_edit_detail_modal.html`（在 `.oc-edit-hero-grid`（约行 20）之前插入新 `.oc-field`）
- Modify: `web/static/medias.js`（`edSave` 行 2127；`openEditDetail` 行 839）

- [ ] **Step 5.1：模板加字段**

打开 `web/templates/_medias_edit_detail_modal.html`，找到：

```html
      <section class="oc-section oc-section-card">
        <div class="oc-section-title">必要信息<span class="req">*</span></div>
        <div class="oc-edit-hero-grid">
          <div class="oc-field">
            <label class="oc-label" for="edName">产品名称<span class="req">*</span></label>
            <input id="edName" class="oc-input" maxlength="120">
          </div>
```

在 `<div class="oc-edit-hero-grid">` **之前**插入一行 `mk_id` 字段。改成：

```html
      <section class="oc-section oc-section-card">
        <div class="oc-section-title">必要信息<span class="req">*</span></div>
        <div class="oc-field" style="margin-bottom:var(--oc-sp-3)">
          <label class="oc-label" for="edMkId">明空 ID</label>
          <input id="edMkId" class="oc-input" type="text"
                 inputmode="numeric" maxlength="8"
                 placeholder="选填，1-8 位数字" autocomplete="off">
          <div class="oc-hint">对应明空系统的产品 ID，用于后续推送</div>
        </div>
        <div class="oc-edit-hero-grid">
          <div class="oc-field">
            <label class="oc-label" for="edName">产品名称<span class="req">*</span></label>
            <input id="edName" class="oc-input" maxlength="120">
          </div>
```

（其他行保持不变。注意 `margin-bottom` 用的是页面已有的间距 token；如果该 token 在该作用域找不到，可改成固定 `12px` — 先尝试 token。）

- [ ] **Step 5.2：`openEditDetail` 回填 mk_id**

打开 `web/static/medias.js`，定位 `openEditDetail` 行 839。需要找到其中往 `edName`、`edCode` 赋值的位置（搜索字符串 `edName').value` 或 `edCode').value`），在旁边加上 `edMkId` 回填。

```bash
grep -n "edName').value\|edCode').value" web/static/medias.js | head -5
```

假设赋值段类似：
```javascript
      $('edName').value = product.name || '';
      $('edCode').value = product.product_code || '';
```

在其下加一行：
```javascript
      $('edMkId').value = (product.mk_id === null || product.mk_id === undefined) ? '' : String(product.mk_id);
```

（若 `openEditDetail` 里没有直接对 `edName.value` 赋值，而是通过 `edState.productData.product` 之后在 `renderEdit*` 之类的函数里渲染，按同一模式在对应位置回填。可用 `grep -n "edName" web/static/medias.js` 定位所有赋值点。）

- [ ] **Step 5.3：`edSave` 收集、校验、携带 mk_id**

定位 `edSave` 行 2127，在 `const adSupportedLangs = [...]` 与 `const payload = {` 之间，插入 mk_id 校验与收集；并在 `payload` 里加 `mk_id`。

**原来（行 2154–2164）：**
```javascript
    const adSupportedLangs = [...document.querySelectorAll(
      '#edAdSupportedLangsBox input[name="ad_supported_langs"]:checked'
    )].map(i => i.value).join(',');

    const payload = {
      name,
      product_code: code,
      copywritings: cwDict,
      localized_links: edState.productData.product.localized_links || {},
      ad_supported_langs: adSupportedLangs,
    };
```

**改成：**
```javascript
    const adSupportedLangs = [...document.querySelectorAll(
      '#edAdSupportedLangsBox input[name="ad_supported_langs"]:checked'
    )].map(i => i.value).join(',');

    const mkIdRaw = ($('edMkId').value || '').trim();
    if (mkIdRaw && !/^\d{1,8}$/.test(mkIdRaw)) {
      alert('明空 ID 必须是 1-8 位数字');
      $('edMkId').focus();
      return;
    }

    const payload = {
      name,
      product_code: code,
      mk_id: mkIdRaw === '' ? null : parseInt(mkIdRaw, 10),
      copywritings: cwDict,
      localized_links: edState.productData.product.localized_links || {},
      ad_supported_langs: adSupportedLangs,
    };
```

- [ ] **Step 5.4：catch 块识别 mk_id 冲突**

同一 `edSave`，修改 catch 块（行 2172–2176）：

**原来：**
```javascript
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('edCode').focus(); }
      else alert('保存失败：' + msg);
    }
```

**改成：**
```javascript
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('mk_id_conflict') || msg.includes('明空 ID 已被其他产品占用')) {
        alert('明空 ID 已被其他产品占用');
        $('edMkId').focus();
      } else if (msg.includes('mk_id_invalid')) {
        alert('明空 ID 必须是 1-8 位数字');
        $('edMkId').focus();
      } else if (msg.includes('已被占用')) {
        alert('产品 ID 已被占用');
        $('edCode').focus();
      } else {
        alert('保存失败：' + msg);
      }
    }
```

- [ ] **Step 5.5：commit**

```bash
git add web/templates/_medias_edit_detail_modal.html web/static/medias.js
git commit -m "feat(medias): 编辑模态在产品名上方新增明空 ID 字段"
```

---

## Task 6 · 合并 + 测试发布 + 手动验证

- [ ] **Step 6.1：worktree 里最后自检**

```bash
git log --oneline master..HEAD
git status
/c/Python314/python.exe -c "import ast; ast.parse(open('appcore/medias.py', encoding='utf-8').read()); ast.parse(open('web/routes/medias.py', encoding='utf-8').read()); print('py ok')"
```

Expected：本分支有 5 个 commit，工作区干净，Python 语法 ok。

- [ ] **Step 6.2：合并回 master**

切回主 worktree：
```bash
cd /g/Code/AutoVideoSrt
git checkout master
git merge --no-ff feature/media-products-mk-id -m "merge: media_products 新增明空 ID (mk_id) 字段"
```

- [ ] **Step 6.3：push + 测试发布（走项目记忆里的测试发布流程）**

按 `feedback_test_deploy.md`：说"测试发布"或直接执行。这里我手动执行一次：

```bash
git push origin master
ssh <server> "cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test"
```

（具体命令按 `reference_test_env.md` / `server.md`。）

Expected：服务启动日志里 `[migrations]` 显示 `2026_04_21_add_mk_id_to_media_products.sql applied`；`/medias/` 页面可访问。

- [ ] **Step 6.4：手动测试 case 清单**

在 `http://14.103.220.208:9999/medias/` 里走一遍：

1. **列表显示**：已有产品（如截图里的 319）「明空 ID」列显示 `—`
2. **列表编辑 · 合法写入**：点击 319 行的「明空 ID」→ 输入 `123456` → 回车 → 单元格显示 `123456`
3. **列表编辑 · 清除**：再点击 → 清空 → 回车 → 显示 `—`；后端 `mk_id` 应为 NULL
4. **列表编辑 · 非法数字**：输入 `abc` → 回车 → 输入框红边框，不提交
5. **列表编辑 · 超长**：`maxlength=8` 拦住 9 位输入；如手动粘贴 `123456789` → 前端正则拒绝
6. **列表编辑 · 冲突**：先给 319 设 `111111`，再给另一产品设 `111111` → alert「明空 ID 已被其他产品占用」
7. **列表编辑 · Esc**：点击后 Esc → 恢复原值，不发请求
8. **编辑模态 · 回填**：点击行「编辑」→ modal 打开，产品名上方的「明空 ID」字段显示当前值
9. **编辑模态 · 保存合法值**：改 mk_id 为新值 → 保存 → modal 关闭 → 列表 reload 后新值生效
10. **编辑模态 · 保存冲突**：改成别家占用的值 → 点保存 → alert 冲突 → focus 在 mk_id
11. **编辑模态 · 保存清空**：清空 mk_id → 保存 → 列表显示 `—`
12. **推送管理回归**：打开「推送管理」页，确认没有新报错（本次未改，只是顺手看看没有意外影响）

每个 case 通过就在 checkbox 勾选。任何一项不过就报告并回到对应 task 修正。

- [ ] **Step 6.5：清理 worktree 分支**

全部通过后：

```bash
git worktree remove .worktrees/media-products-mk-id
git branch -d feature/media-products-mk-id
```

- [ ] **Step 6.6：（用户决定）正式发布生产**

测试发布验收 OK 后，由用户决定是否正式发布（走「发布流程」）。

---

## Self-Review

### Spec 覆盖矩阵

| spec 条目 | 对应 task |
|-----------|----------|
| §3.1 migration 文件 | Task 1 |
| §3.2 DAO 白名单 + 归一化 | Task 2 |
| §3.3 序列化带出 | Task 3 Step 3.1 |
| §4.1 更新接口冲突/格式映射 | Task 3 Step 3.3 |
| §4.2 列表接口 | Task 3 Step 3.1（通过 serialize 自动带） |
| §4.3 创建接口不动 | （无对应 task，保持原样 ✓） |
| §5.1–5.4 列表 inline edit + 样式 | Task 4 |
| §6.1–6.2 编辑模态字段 + JS 改造 | Task 5 |
| §7 兼容性 | migration 自动跑（Task 1）+ 白名单向前兼容 |
| §8 非目标（不改推送） | 未触碰 pushes.py / pushes.js ✓ |
| §9 测试策略 | Task 6 Step 6.4 |
| §10 文件清单 | Task 2/3/4/5 覆盖全部 |

### Placeholder 扫查：无 TBD / TODO / "类似 Task N"，所有代码步骤都给出完整代码。

### 类型一致性检查：
- 后端：mk_id 是 `int | None`；API 入参兼容 `int` / `str` / `""` / `None`；DAO 统一归一化为 `int | None`
- 前端：`p.mk_id` 可能为 `null` / `number`，渲染时用三元判空；`payload.mk_id` 统一为 `number | null`
- 错误 code 约定：前后端都用 `mk_id_invalid` / `mk_id_conflict` 字符串

### 歧义检查：
- "前导零"：用户输入 `01234` 会被 `parseInt(..., 10) = 1234` 吞零，落库 1234，回显 1234。Task 6 Step 6.4 case 2 输入的是 `123456`，不触发；如用户实际场景会填前导零，列表的 inline edit 保存后会显示去零值 — 这是预期行为（spec §2 已说明）
- "空值 vs 0"：`null` 代表未设置；`0` 是合法数字（长度 1 通过正则）— 目前 `0` 可被填入。如果业务禁止 0，需要前端 + 后端都加排除。此处保持 spec 原样，允许 0
