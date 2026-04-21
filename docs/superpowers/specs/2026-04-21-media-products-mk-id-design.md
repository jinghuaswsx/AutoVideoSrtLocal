# 素材管理 · 产品记录新增「明空 ID」字段（mk_id）

- 日期：2026-04-21
- 作者：Claude（设计）
- 执行交接：后续由 Codex 在独立 worktree 中实施
- 背景：推送管理（pushes）未来需要把素材产品推送到明空系统，明空系统以自身产品 ID 标识产品。本次先打通数据模型与素材管理端的录入/编辑入口，推送端后续 PR 再对接。

## 1. 目标

在 `media_products` 表新增 `mk_id`（明空系统 ID），并提供两个录入入口：
1. **素材管理列表页**：新增「明空 ID」列，支持单元格 inline 编辑
2. **素材编辑模态**：在产品名输入框上方新增「明空 ID」字段

本次**不**动推送管理（`appcore/pushes.py` / `web/static/pushes.js`），推送对接留给后续需求。

## 2. 约束与业务规则

| 项目 | 规则 |
|------|------|
| 数据类型 | 数字，1–8 位十进制正整数 |
| 存储类型 | `INT UNSIGNED`（最大 4,294,967,295，足够覆盖 8 位十进制） |
| 可空性 | `NULL` 允许（老数据不回填、逐步补录） |
| 唯一性 | **全局唯一**（跨用户不允许重复）；`UNIQUE KEY`；MySQL 对多个 `NULL` 不视为冲突 |
| 空值语义 | 前端空串 → 后端 `NULL`；列表显示占位 `—` |
| 校验 | 非空时必须由 1–8 个 `0-9` 字符组成；允许前导零输入但落库前转为 `int`，显示不补零 |

## 3. 数据层

### 3.1 Migration

新建文件：`db/migrations/2026_04_21_add_mk_id_to_media_products.sql`

```sql
ALTER TABLE media_products
  ADD COLUMN mk_id INT UNSIGNED NULL AFTER product_code,
  ADD UNIQUE KEY uk_media_products_mk_id (mk_id);
```

### 3.2 DAO

`appcore/medias.py` 中 `update_product(pid, **fields)` 的允许字段白名单新增 `"mk_id"`；写库前做归一化：

```python
# update_product 内，白名单筛选之后
if "mk_id" in fields:
    v = fields["mk_id"]
    if v is None or (isinstance(v, str) and not v.strip()):
        fields["mk_id"] = None
    else:
        s = str(v).strip()
        if not s.isdigit() or len(s) > 8 or len(s) < 1:
            raise ValueError("mk_id 必须是 1-8 位数字")
        fields["mk_id"] = int(s)
```

DB 的 `1062 Duplicate entry`（唯一冲突）交由上层捕获。

### 3.3 序列化

`web/routes/medias.py · _serialize_product()`（约行 128–169）返回体新增：

```python
"mk_id": row.get("mk_id"),
```

所有返回产品信息的接口（列表 + 详情 + 更新后回显）同步带出。

## 4. 后端路由

### 4.1 更新接口

`web/routes/medias.py · api_update_product`（`PUT /medias/api/products/<pid>`，约行 339–397）：

1. 请求体允许字段加 `mk_id`
2. 调用 `medias.update_product(pid, ..., mk_id=...)`
3. 异常映射：
   - `ValueError("mk_id 必须是 1-8 位数字")` → `HTTP 400 {"error": "mk_id_invalid", "message": "..."}`
   - MySQL `1062` 或包含 `uk_media_products_mk_id` 的 IntegrityError → `HTTP 409 {"error": "mk_id_conflict", "message": "明空 ID 已被其他产品占用"}`

### 4.2 列表接口

`GET /medias/api/products`：无需改动 SQL/参数，`_serialize_product()` 改造后列表自动带出 `mk_id`。

### 4.3 创建接口

**本次不改创建接口**。新建产品时 `mk_id` 一律为 `NULL`，创建后通过列表 inline edit 或编辑模态补录即可。理由：
- 新建产品本身是低频操作
- 避免在两个入口同时处理 mk_id 校验/冲突逻辑，减少本次改动范围

## 5. 前端 · 列表页（inline edit）

相关文件：
- 模板：`web/templates/medias_list.html`
- 脚本：`web/static/medias.js`

### 5.1 列定义

在「产品 ID」（`product_code`）列之后插入新列「明空 ID」。每行单元格结构：

```html
<td class="mk-id-cell" data-pid="{pid}">
  <span class="mk-id-text">{{ mk_id || '—' }}</span>
</td>
```

`data-pid` 存产品 id；`.mk-id-text` 显示值或占位 `—`。

### 5.2 交互

| 触发 | 行为 |
|------|------|
| 单击 `.mk-id-cell` | 把 `<span>` 替换为 `<input class="oc-input oc-input--inline" type="text" inputmode="numeric" maxlength="8">`；预填当前值（占位 `—` 视为空）；`focus()` + `select()` |
| 输入 | 前端不做强拦截；UX 允许输入后再校验 |
| `Enter` 或 `blur` | 提交（见下） |
| `Esc` | 放弃，恢复原值 |

### 5.3 提交流程

1. 取 `input.value.trim()`
2. 本地校验：
   - 空 → 代表清除，将要发 `mk_id: null`
   - 非空 → 必须 `/^\d{1,8}$/`；否则 input 加 `.oc-input--error` 并保持编辑态，不发请求
3. 若新值 == 原值（包括空 → 空），直接取消编辑，不发请求
4. `PUT /medias/api/products/<pid>`，body `{"mk_id": <int|null>}`（不传其他字段）
5. 结果：
   - `200` → 用后端返回值替换 `<span>`，退出编辑态；短暂高亮（`--success-bg` 200ms 过渡）
   - `409 mk_id_conflict` → input 变 `.oc-input--error`、toast「明空 ID 已被占用」，保持编辑态
   - `400 mk_id_invalid` → 同上
   - 其他错误 → toast 错误 message，保持编辑态

### 5.4 样式

沿用 Ocean Blue token，新增 inline-edit 辅助样式（写在 `medias_list.html` 的 scoped style 里）：

```css
.mk-id-cell { cursor: pointer; }
.mk-id-cell .oc-input--inline { height: 28px; width: 100px; padding: 0 8px; }
.oc-input--error { border-color: var(--danger); box-shadow: 0 0 0 2px var(--danger-bg); }
```

禁止引入 emoji / 紫色 / 重阴影。

## 6. 前端 · 编辑模态

相关文件：
- 模板：`web/templates/_medias_edit_modal.html`
- 脚本：`web/static/medias.js` 的 `save()`（约行 694–728）、打开模态的入口（约行 839 `openEditDetail()` / 新建入口）

### 6.1 DOM 插入

在 `#mName`（产品名输入，约行 15）的外层 `.oc-field` **之前** 插入：

```html
<label class="oc-field">
  <span class="oc-field-label">明空 ID</span>
  <input id="mMkId" class="oc-input" type="text"
         inputmode="numeric" maxlength="8"
         placeholder="选填，1-8 位数字">
  <span class="oc-field-hint">对应明空系统的产品 ID，用于推送</span>
</label>
```

### 6.2 JS 改动

- **打开模态**：回填 `document.getElementById('mMkId').value = (product.mk_id ?? '').toString()`
- **save()**：
  ```js
  const mkIdRaw = document.getElementById('mMkId').value.trim();
  if (mkIdRaw && !/^\d{1,8}$/.test(mkIdRaw)) {
    // 标红 + 聚焦 + 中断 save
    return;
  }
  body.mk_id = mkIdRaw === '' ? null : parseInt(mkIdRaw, 10);
  ```
- **409 处理**：给 `#mMkId` 加 `.oc-input--error`、focus、toast「明空 ID 已被占用」

## 7. 兼容性与迁移影响

- 老数据 `mk_id` 全为 `NULL`，不影响现有功能
- `media_products` 表在生产上已存在；migration 只加列与唯一索引，锁表时间短（小表）
- 序列化返回体多一个字段，前端旧缓存不会报错（忽略未知字段）
- CSRF：medias 蓝图已 `csrf.exempt`，inline edit 无需额外 token 处理

## 8. 非目标 / 超出范围

- ❌ 推送管理的 UI 与逻辑（本次不改 `appcore/pushes.py`、`web/static/pushes.js`、`web/routes/pushes.py`）
- ❌ 批量导入 / 批量回填 mk_id
- ❌ mk_id 与明空系统在线校验（仅本地格式+唯一校验）
- ❌ 素材管理「新增产品」弹窗的字段调整（创建时 mk_id 一律为 NULL，创建后再补录）

## 9. 测试策略

| 层级 | 验证点 |
|------|--------|
| Migration | 在测试环境（`/opt/autovideosrt-test`，端口 9999，库 `auto_video_test`）跑一次迁移；列和唯一索引已建；旧产品查询正常 |
| 后端 | 手动 curl / httpx 验证：①合法数字保存成功；②空串 → NULL；③非数字 400；④9 位数字 400；⑤同一数字给另一产品 409 |
| 前端列表 | 点击单元格进入编辑；空值显示 `—`；非数字红框不提交；冲突 toast；成功高亮 |
| 前端模态 | 打开已有产品回填正确；保存后列表回显一致；冲突红框 + toast |
| 回归 | 素材列表、素材上传、文案、link check 等不受影响 |

## 10. 文件清单

| 文件 | 动作 |
|------|------|
| `db/migrations/2026_04_21_add_mk_id_to_media_products.sql` | 新建 |
| `appcore/medias.py` | 修改 `update_product` 白名单 + 归一化 |
| `web/routes/medias.py` | 修改 `_serialize_product`、`api_update_product` 异常映射 |
| `web/templates/medias_list.html` | 新增「明空 ID」列 + scoped 样式 |
| `web/static/medias.js` | 列渲染、inline edit 行为、模态回填与 `save()` 改造 |
| `web/templates/_medias_edit_modal.html` | 产品名上方插入 mk_id 字段 |

## 11. 风险

- **唯一索引冲突**（老数据空值 NULL 不影响，但同时并发写两条相同 mk_id 会 409）→ 前端已处理冲突反馈
- **前端 inline edit 误触**（点到单元格就进入编辑）→ 通过点击目标限制为 `.mk-id-cell`、输入框 Esc 可退出，体感接受
- **Ocean Blue 视觉一致性**：样式严格走 token，实施时由实施方自检一遍紫色 / 硬编码

## 12. 工作流

1. 本 spec 用户审阅通过后 → 进入 `superpowers:writing-plans` 产出详细实施计划
2. 开独立 worktree（分支 `feature/media-products-mk-id`）
3. 交由 Codex 实施
4. 按测试策略验证
5. 合并回 master → push → 走测试发布流程
