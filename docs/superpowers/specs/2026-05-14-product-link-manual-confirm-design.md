# 产品链接可用性人工确认设计

- 锚点：`docs/superpowers/specs/2026-05-14-product-link-manual-confirm-design.md`
- 关联锚点：`docs/superpowers/specs/2026-05-09-product-link-management-modal.md`
- 关联锚点：`docs/superpowers/specs/2026-05-09-product-edit-ad-supported-langs-precheck-design.md`
- 关联锚点：`docs/superpowers/specs/2026-04-25-shopify-image-task-center-design.md`
- 涉及范围：素材管理「产品链接管理」弹窗、`web/static/medias.js`、`web/services/media_link_check.py`、`appcore/link_availability.py`

## 背景

产品链接管理弹窗里的 HTTP 可用性探测由服务器发起。部分店铺链接在浏览器里实际可访问，但服务器探测可能因网络、反爬、超时、地区线路或 HEAD/GET 差异返回异常。现有流程只能重新检查，无法让运营在确认链接真实正常后把该域名放行。

顶部国家勾选前置校验和素材推送准入会读取 `media_product_link_availability` 的结果。对于启用域名，只有该语种下每个域名都有非空 `checked_at` 且 `ok=1`，才满足后续推送条件。

## 目标

在产品链接管理弹窗中，为每一个链接行追加 `确认链接正常` 按钮。运营确认某一条链接实际正常后，系统把该产品、语种、域名的可用性缓存写为人工确认正常，使该域名满足后续国家勾选和推送前置条件。

按钮必须按链接行渲染，每个启用域名都有自己的按钮；点击只确认当前行的 `domain + lang`，不能只确认第一条，也不能一次性误改其他域名。

## 行为

- `确认链接正常` 出现在每个产品链接行的 URL 后侧或同一行操作区，和复制按钮、重新检查按钮保持一致的密度。
- 点击后调用既有链接可用性接口的人工确认分支：`POST /medias/api/products/<pid>/link-availability/<lang>`，body 为 `{"domain":"<domain>","manual_confirm":true}`。
- 后端只接受当前产品该语种已启用的域名；未知域名仍返回 404，非法域名仍返回 400。
- 人工确认会 upsert `media_product_link_availability`：
  - `http_status = 200`
  - `ok = 1`
  - `error = "manual_confirmed"`
  - `elapsed_ms = 0`
  - `checked_at = NOW()`
  - `link_url` 使用当前解析出的真实 URL
- 返回 payload 仍是该语种当前启用域名的 `items` 列表，方便前端刷新整块弹窗。
- UI 标签中，`error="manual_confirmed"` 且 `ok=true` 的行显示为 `人工确认正常`，同时仍满足 `链接正常` 语义。

## 不做

- 不新增表结构。
- 不改变服务器自动探测的判定规则。
- 不绕过 Shopify 图片确认状态。非英语推送仍要求 Shopify 图片状态 `replace_status=confirmed` 且 `link_status=normal`。
- 不提供批量人工确认，避免一次误放多个域名。

## 验证

- 后端单测覆盖 `manual_confirm_result()` 写入格式。
- 路由服务单测覆盖 `manual_confirm=true` 时只确认请求里的单个域名，并返回该语种全部启用域名列表。
- 前端字符串测试覆盖每个链接行渲染 `data-product-links-action="confirm-link"`，且 action 使用当前行 `data-domain`。
- 回归现有产品链接可用性、国家勾选前置校验、Shopify 图片按钮测试。
