# 素材管理 - 项目负责人重新分配 设计稿

- 日期：2026-04-24
- 涉及模块：素材管理（medias）
- 状态：Draft，待 writing-plans 转化为实施计划

## 1. 背景与目标

素材管理列表当前的「负责人」列是只读文本，展示 `media_products.user_id` 对应用户的中文名（`users.xingming`，为空则 fallback `username`）。当项目实际归属人需要变更时，运维目前只能手动改数据库。

本次目标：**让管理员（`role='admin'`）在列表上直接把一个项目的归属人换成另一个用户，并让该项目下所有素材数据的用户关联同步迁移。**

## 2. 需求边界（已对齐）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 关联关系语义 | **同步全改**：`media_products.user_id` + 该项目所有 `media_items.user_id` + 所有 `media_raw_sources.user_id` 一起变更为新负责人 |
| 2 | 物理文件路径 | **不迁移**：已有 object_key 保留原 user_id 前缀，OSS 物理路径不动。新上传文件用新负责人 uid 作前缀，同项目下出现「混合前缀」属正常现象 |
| 3 | 下拉用户范围 | 所有 `is_active=1` 的用户（admin 和 user 角色均在列） |
| 4 | 编辑入口 | **仅列表 inline 编辑**：点击「负责人」单元格 → 下拉 → 选完即保存，与 mk_id / 上架状态 inline edit 一致 |
| 5 | 权限 | **严格 admin-only**：仅 `role='admin'` 能触发下拉，后端强校验，非 admin 请求 → 403。普通用户看到的仍是只读文本 |
| 6 | 交互 | **不加 confirm 对话框**：选完即生效，toast 提示「已转交给 xxx」 |

## 3. 现状速记

- `media_products.user_id`：项目归属人，`list_products` 通过 `LEFT JOIN users u ON u.id = p.user_id` 取 `COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)` 作 `owner_name`
- `media_items.user_id`、`media_raw_sources.user_id`：当前作「上传者」语义，本次决策 A 后将等同于「项目归属人」
- `media_copywritings` / `media_product_covers` / `media_product_detail_images` / `media_raw_source_translations`：只持有 `product_id`，不直接存 `user_id`，随项目自动迁移
- `users` 表：`id / username / xingming / role / is_active`
- `api_list_products` 调 `list_products(None, ...)`，**所有登录用户看到的列表都是全量**，无需 scope 切换
- 现有 `update_product()` 不允许改 `user_id`（`allowed` 字段集不含 owner）
- admin 判定：`getattr(current_user, "role", "") == "admin"`
- 现有 inline edit 范例：`web/static/medias.js` 的 mk-id-cell、listing-status-cell

## 4. 架构设计

### 4.1 后端 —— `appcore/medias.py`

新增两个纯数据层函数：

```python
def list_active_users() -> list[dict]:
    """返回 [{id, display_name}]，is_active=1，按 display_name 升序。"""
    ...

def update_product_owner(product_id: int, new_user_id: int) -> None:
    """单事务内把项目归属人改为 new_user_id，并同步 items / raw_sources。

    - 校验 new_user_id 在 users 表且 is_active=1，否则 ValueError
    - 只更新未软删除的行（deleted_at IS NULL）
    - 3 条 UPDATE 全部成功才 commit；中途异常则 rollback
    """
```

三条 UPDATE：

1. `UPDATE media_products SET user_id=%s WHERE id=%s AND deleted_at IS NULL`
2. `UPDATE media_items SET user_id=%s WHERE product_id=%s AND deleted_at IS NULL`
3. `UPDATE media_raw_sources SET user_id=%s WHERE product_id=%s AND deleted_at IS NULL`

**不复用 `update_product()`**：归属变更是独立事务动作，不和其他字段编辑混用，避免 allowed 白名单被扩得过杂。

### 4.2 后端 —— `web/routes/medias.py`

新增两条路由，均需 admin-only 装饰器（参考 `prompt_library._is_admin` / `pushes._is_admin` 写法）：

- **`GET /medias/api/users/active`**
  - 非 admin → 403
  - 返回 `{"users": [{id, display_name}, ...]}`

- **`PATCH /medias/api/products/<int:pid>/owner`**
  - 非 admin → 403
  - 请求体 `{"user_id": int}`
  - 校验：`pid` 存在且未软删 → 404；`user_id` 非法或用户 `is_active=0` → 400
  - 调 `medias.update_product_owner(pid, new_user_id)`
  - 返回 `{"owner_name": "..."}`（即时重算，前端直接渲染）

**附带小改动**：`_serialize_product` 结果里补一个 `"user_id": p.get("user_id")` 字段，供前端 inline edit 时带上当前 uid（用于下拉默认选中）。

### 4.3 前端 —— `web/static/medias.js`

1. **`rowHTML(p)`**：把「负责人」`<td>` 改造为：

```html
<td class="wrap owner-cell" data-pid="${p.id}" data-owner-uid="${p.user_id ?? ''}" title="${escapeHtml(ownerName)}">
  ${ownerName ? escapeHtml(ownerName) : '<span class="muted">—</span>'}
</td>
```

2. **`paint(items)`**：紧挨 mk-id-cell / listing-status-cell 的绑定行，追加：

```js
if (window.IS_ADMIN) {
  grid.querySelectorAll('td.owner-cell').forEach(td =>
    td.addEventListener('click', (e) => { e.stopPropagation(); startOwnerInlineEdit(td); }));
}
```

3. **新增 `startOwnerInlineEdit(td)`**：
   - 防重入（`td.dataset.editing === '1'` 直接 return）
   - 首次点击时懒加载：`GET /medias/api/users/active` → 缓存至 module-scope `_activeUsersCache`
   - 替换 td 内容为 `<select>`：默认选中 `data-owner-uid`，下拉项来自缓存
   - `change` 事件：`PATCH /medias/api/products/${pid}/owner` `{user_id: +select.value}`
   - 成功：用响应里的 `owner_name` 更新 td innerText、`data-owner-uid`、`title`，toast 提示「已转交给 xxx」
   - 失败：恢复原文本 + 红底 toast 报错
   - ESC / blur 恢复原文本

4. **toast 复用现有工具**（实施时 grep `medias.js` 里已有的 notify/toast 函数名）

### 4.4 前端 —— `web/templates/layout.html`（按需）

确认 `window.IS_ADMIN` 是否已由全局布局注入（grep 验证）。若无则在 layout 中加：

```html
<script>window.IS_ADMIN = {{ 'true' if current_user.role == 'admin' else 'false' }};</script>
```

### 4.5 视觉规范

- inline edit 的 `<select>` 高度贴近行高（32-36px）
- 所有颜色 / 圆角 / 间距走 CSS token，不硬编码
- focus 用 `--accent-ring`；hue 严格 200-240，禁紫
- 非 admin 的 `.owner-cell` 不加 cursor:pointer，保持纯文本观感

## 5. 数据一致性 & 事务

- `update_product_owner` 必须在单个 DB 连接 + 显式事务里跑 3 条 UPDATE
- 任一 UPDATE 异常 → rollback 全部（否则会出现 products 已换、items 还指向旧人的错乱）
- 软删除行不更新：保留历史溯源，防止被意外改回

## 6. 权限

- 前端：`window.IS_ADMIN=false` 时 `.owner-cell` 不注册 click listener，UI 层看不到下拉
- 后端：两条路由均走 admin 装饰器，非 admin 请求一律 403（无法通过直接 curl 绕过）

## 7. 测试计划

### 7.1 单元测试 `tests/test_appcore_medias.py` 新增

- `list_active_users`：只返回 `is_active=1`、中文名优先、`xingming` 空时 fallback `username`、按 display_name 升序
- `update_product_owner`：
  - 建项目（owner=A）+ 2 个 items + 1 个 raw_source，换人到 B，3 张表的 user_id 都为 B
  - 事务性：mock 第 2 条 UPDATE 抛异常，第 1 条应已 rollback
  - 软删除的行不被更新：建个 `deleted_at IS NOT NULL` 的 item，换人后其 user_id 仍为 A
  - 非法 new_user_id（不存在 / `is_active=0`）→ ValueError

### 7.2 路由测试 `tests/test_medias_routes.py` 新增

- admin `PATCH /medias/api/products/<pid>/owner` 返回 200 + 新 owner_name
- admin `GET /medias/api/users/active` 返回用户列表
- 普通用户访问两条路由 → 403
- PATCH 不存在的 pid → 404
- PATCH body 缺 `user_id` / 非数字 / 指向 `is_active=0` 用户 → 400

### 7.3 手测清单

- admin 登录：负责人单元格 hover 光标变手型，点击出下拉，选人后 toast 弹出 + 单元格变新名
- 普通用户登录：负责人单元格纯文本，点击无反应
- 换完后刷新页面，负责人列显示新名字（验证持久化）
- 直接查 DB：`media_products` / `media_items` / `media_raw_sources` 该项目相关行的 `user_id` 均为新负责人 id（验证三张表同步生效）
- 用 DevTools 拿普通用户 session cookie 手动 curl PATCH → 403

## 8. 涉及文件清单

- `appcore/medias.py` — 新增 `list_active_users` / `update_product_owner`
- `web/routes/medias.py` — 新增 2 条路由；`_serialize_product` 补 `user_id` 字段
- `web/static/medias.js` — `rowHTML` 加 class/dataset、`paint` 注册 listener、新增 `startOwnerInlineEdit` + 活跃用户缓存
- `web/templates/layout.html` — 若尚未注入 `window.IS_ADMIN`，补一行
- `tests/test_appcore_medias.py` — 新增 unit 测试
- `tests/test_medias_routes.py` — 新增 route 测试

## 9. 明确不做

- 不加数据库 migration（复用现有列）
- 不迁移物理文件 / OSS object_key 改名
- 不在编辑详情弹窗里加字段
- 不加二次确认弹窗
- 不改 `update_product()` 的 allowed 字段集
- 不改 `api_list_products` 的权限/过滤逻辑（现状已是全量，符合需求）

## 10. 回滚策略

- 代码回滚：回退所有涉及文件到改动前版本即可
- 数据回滚：若误改归属人，admin 再点一次改回；无需额外脚本
