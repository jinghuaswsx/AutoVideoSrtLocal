# 产品链接默认域名 — 设计与实施

- 锚点：`docs/superpowers/specs/2026-05-09-product-link-default-domain.md`
- 关联 issue：AUT-20
- 涉及范围：`db/migrations/`、`appcore/product_link_domains.py`、`web/routes/admin.py`、`web/templates/admin_settings.html`、`web/routes/medias/_serializers.py`、`web/static/medias.js`、`tests/`

## 1. 背景与目标

域名管理页（`/admin/settings?tab=domains`）目前只能勾选「启用」与设置排序，**没有「主域名 / 默认域名」概念**。导致两件事：

- 「素材管理 → 编辑产品素材」弹窗中的「产品链接」输入框始终用前端硬编码常量
  `DEFAULT_LINK_DOMAIN = 'newjoyloo.com'`（[`web/static/medias.js`](../../../web/static/medias.js) 第 4006 行）拼默认 URL；改换主域名时要同时改两份硬编码（`appcore/product_link_domains.py` 顶部的 `DEFAULT_LINK_DOMAINS` / `DEFAULT_PRODUCT_LINK_DOMAINS` + 前端 `DEFAULT_LINK_DOMAIN`）。
- 外部下游（`media_detail_from_url.first_product_page_url`、`pushes`、`shopify_image_tasks` 等）取「第一条」域名时只能依赖 `sort_order`，没有显式语义。

本次新增「默认域名」概念：在 `media_link_domains` 上加 `is_default` 标志位，由域名管理页的「设为默认」按钮维护，全系统单一来源；产品编辑弹窗的「产品链接」一行依据该默认域名生成默认 URL。

## 2. 范围

**做：**

- `media_link_domains` 表新增 `is_default TINYINT(1) NOT NULL DEFAULT 0`；新建 schema migration 文件 `db/migrations/2026_05_09_media_link_domains_is_default.sql`。Migration 同时回填：当现有表里没有任何 `is_default=1` 的行时，把 `sort_order` 最低（同分时 `id` 最小）的行置为默认。
- `appcore.product_link_domains` 暴露：
  - `list_domains(...)` 返回行附带 `is_default: bool`。
  - `get_default_domain() -> str | None`：返回默认域名字符串；表中没有显式默认时回退到 `DEFAULT_LINK_DOMAINS[0]`。
  - `set_default_domain(domain_id: int) -> None`：原子地把目标行 `is_default=1` 且 `enabled=1`，其余行 `is_default=0`。空入参或非法 id 直接 no-op。
  - `_enabled_domain_rows(product_id)` 排序调整为「默认域名优先 → 其余按 `sort_order`」，让所有走 `resolve_product_page_url_rows` 的下游（包括 `first_product_page_url`、`pushes`、`shopify_image_tasks`、`medias.py` 推送、`web/services/media_link_check.py`、`media_detail_from_url`）自动用默认域名。
  - `delete_domain` 不必特殊处理：删除默认行后系统进入「无显式默认」状态，由 `get_default_domain` 回退。
- 域名管理页（`web/templates/admin_settings.html` 的 `#domainManagementCard`）改造：
  - 新增「默认」列（位于「域名」列与「排序」列之间）。
  - 当行 `is_default == True`：渲染只读 badge `默认`（蓝色实心 pill）。
  - 当行 `is_default == False`：渲染按钮「设为默认」（次按钮风格，与现有 `.settings-domain-btn` 体系对齐）。点击提交 `domain_action=set_default` + `default_domain_id=<id>`。
  - 「设为默认」按钮在该行 `enabled=False` 时仍可点击；后端会同时将其 `enabled=1`，并把 checkbox 自动更新到选中。
  - 域名管理表删除按钮、保存启用状态、新增域名等既有交互保持不变。
- `web/routes/admin.py` `_handle_product_link_domains_post` 增补 `set_default` 分支调用 `product_link_domains.set_default_domain`。`active_tab == 'domains'` 的 GET 处理保持不变（只是 `list_domains` 现在带 `is_default` 字段）。
- `web/routes/medias/_serializers.py::_serialize_product` 在产品 payload 里新增顶层 `default_link_domain: str`，由 `product_link_domains.get_default_domain()` 提供；`include_product_link_domains` 控制 `product_link_domains` 列表是否注入，但 `default_link_domain` 始终注入（无论是否走 modal 渲染场景，列表页/详情页都需要）。注入失败（例如查询异常）时回退到 `""`，前端继续用旧硬编码。
- `web/static/medias.js`：
  - 把模块顶部的 `const DEFAULT_LINK_DOMAIN = 'newjoyloo.com';` 改为 `let DEFAULT_LINK_DOMAIN = 'newjoyloo.com';`，并在 `edOpenEditDetailModal`/`_loadProductDetail` 等给 `edState.productData` 赋值的入口处用 `product.default_link_domain` 覆盖（仅当字段非空时覆盖；保留全局 fallback）。
  - `edProductLinkDomains()` 返回的列表里把默认域名前移：默认域名行排到第一个；若产品没启用任何域名，仍 fallback 到 `[{domain: DEFAULT_LINK_DOMAIN}]`。
  - `edRenderProductUrl(lang)` 输入框 placeholder 与默认填充值切换为基于动态 `DEFAULT_LINK_DOMAIN`。

**不做：**

- 不改 `media_product_link_domains` schema、不改产品级勾选语义。
- 不动 `media_link_domains.enabled` 与 `is_default` 之外的列。
- 不改 `DEFAULT_LINK_DOMAINS` 的硬编码值（保留为最终兜底，避免 DB 异常时前端无法渲染）。
- 不动产品链接管理弹窗（`edProductLinksMask`）的现有渲染逻辑；它读 `product_link_domains` 数组，默认域名首位排序由后端 `_enabled_domain_rows` 已经处理。
- 不影响 `web/routes/medias/products.py` 现有「产品级启用域名」编辑接口。

## 3. 数据模型

```sql
-- db/migrations/2026_05_09_media_link_domains_is_default.sql
ALTER TABLE media_link_domains
  ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0 AFTER enabled;

UPDATE media_link_domains
   SET is_default = 1
 WHERE id = (
       SELECT id FROM (
         SELECT id FROM media_link_domains
          ORDER BY sort_order ASC, id ASC
          LIMIT 1
       ) AS first_row
     )
   AND NOT EXISTS (
       SELECT 1 FROM (
         SELECT id FROM media_link_domains WHERE is_default = 1
       ) AS existing_default
     );
```

约束：单默认由应用层维护（`set_default_domain` 用一个事务清除其他行后再写入目标行）。MySQL 不易加 partial unique index，且即使数据层多一条 `is_default=1` 也只是退化为「随机选一个」，不会破坏外部接口契约。

## 4. UX 细节

| 行状态 | 「默认」列渲染 | 行操作 |
|--------|--------------|--------|
| `is_default=True`，`enabled=True` | 蓝底白字 badge `默认` | `删除` |
| `is_default=False`，`enabled=True` | 次按钮 `设为默认` | `删除` |
| `is_default=False`，`enabled=False` | 次按钮 `设为默认`（点击会同时启用） | `删除` |
| `is_default=True`，`enabled=False` | badge `默认（已停用）`（红字提示），同时 enabled checkbox 仍未勾选 | `删除` |

- 操作列宽度从 96px 略增到 132px；新增「默认」列宽 132px。
- 点 `设为默认` → 表单 `domain_action=set_default` + `default_domain_id=<id>`，后端 redirect 回同一页 + flash「已切换默认域名」。
- 点 `删除`：保留既有 confirm 文案。删除默认行后下次渲染默认列回到所有按钮态，提示用户重新挑一个。

## 5. 修改顺序与提交清单

1. 改认知文档：本 spec + `CLAUDE.md` 加锚点段落。
2. 改规范文档：本 spec（即此文件）。
3. 改代码：migration → appcore → admin route → admin 模板 → serializer → medias.js。
4. 改测试：扩 `tests/test_db_migration_product_link_domains.py`（新增 migration 文件存在性 + 关键 SQL 片段）+ `tests/test_product_link_domains.py`（`set_default_domain`、`get_default_domain`、`_enabled_domain_rows` 排序）+ `tests/test_settings_product_domains.py`（默认列渲染 + POST `set_default`）+ `tests/test_medias_routes.py`/相关 serializer 测试若已有则补 `default_link_domain`。
5. 不写 `CHANGELOG`（仓库未维护）；commit message 携带 `Docs-anchor: docs/superpowers/specs/2026-05-09-product-link-default-domain.md`。

## 6. 验证清单

- [ ] `pytest tests/test_db_migration_product_link_domains.py tests/test_product_link_domains.py tests/test_settings_product_domains.py -q`
- [ ] dev server 起来，超管账号登录 `/admin/settings?tab=domains`：默认列显示，切换默认会刷新 badge；新增域名后默认列同步生效。
- [ ] 素材管理 → 编辑产品素材弹窗：把默认域名从 `newjoyloo.com` 切到 `omurio.com` 后，产品链接输入框默认值变为 `https://omurio.com/...`；切回不影响产品已自定义的链接。
