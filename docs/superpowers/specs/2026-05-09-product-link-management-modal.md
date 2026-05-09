# 产品链接管理弹窗 — 设计与实施

- 锚点：`docs/superpowers/specs/2026-05-09-product-link-management-modal.md`
- 关联 issue：AUT-15
- 涉及范围：素材管理「编辑产品素材」弹窗、`web/static/medias.js`、`appcore/product_link_domains.py`、新增 `appcore/link_availability.py`、新增表 `media_product_link_availability`

## 1. 背景与目标

素材管理产品的「编辑产品素材」弹窗（`web/templates/_medias_edit_detail_modal.html`）只在「产品链接」一行铺单一 URL 输入框；非英语 tab 下方还会渲染一个内联的 `edShopifyImageStatus` 面板，展示该语种每个启用域名的 Shopify 换图 / 链接确认状态 + `确认图片正常 / 重新排队换图 / 标记链接不可用` 操作按钮。

随着该产品启用的域名增多，这个内联面板会非常拥挤，而且：

- 英语 tab 没有任何「多域名 + 链接可用性」的入口
- 内联面板与上面的输入框 / 复制按钮共享一行视觉，密度过高
- 没有「直接探测 HTTP 状态码（404 / 403 等）确认链接是否可访问」的入口

本次新增「产品链接管理」按钮 + 弹窗，把多域名状态、Shopify 换图操作、HTTP 可用性探测三件事聚成一个独立的弹窗，所有语种 tab 都可以打开。

## 2. 范围

**做：**

- 在「产品链接」一行下面新增按钮 `产品链接管理`，所有语种 tab 都渲染。
- 新弹窗 `edProductLinksMask`，按行展示该产品「当前启用的每个域名 × 当前语种」的链接状态。
- 行内集成既有 Shopify 图片确认 / 重排队 / 标记不可用按钮（仅非英语生效，跟既有接口对齐）。
- 新增轻量 HTTP 可用性探测：HEAD/GET 请求，跟随重定向，返回最终 HTTP 状态码 + 是否可达 + 错误描述；持久化到新表 `media_product_link_availability`，每 (product_id, lang, domain) 一行最新结果。
- 弹窗内 `重新检查可用性`（行内）+ `全部重新检查可用性`（弹窗顶部）两种入口。
- 拿掉 `edShopifyImageStatus` 内联面板的渲染（非英语 tab 下方那块），所有信息都进新弹窗。
- 数据接口：新增 `POST /medias/api/products/<pid>/link-availability/<lang>` 触发探测、`GET /medias/api/products/<pid>/link-availability/<lang>` 拉取最新结果。

**不做：**

- 不改 `media_link_domains` / `media_product_link_domains` / `media_products.localized_links_json` 的 schema。
- 不改既有 `查看结果` (`edLinkCheckMask`) 的链接检测 + 图片分析流程。
- 不在弹窗里提供「启用 / 停用域名」的勾选编辑（域名启停归域名管理页 / 现有 `api_set_product_link_domains` 路由）。
- 不引入新的依赖（HTTP 探测使用 stdlib `urllib.request`，避免引入 `httpx`）。

## 3. UX

### 3.1 入口

`web/templates/_medias_edit_detail_modal.html` 第 44–57 行的「产品链接」字段块下面新增：

```html
<div class="oc-product-links-entry">
  <button type="button" class="oc-btn ghost sm" id="edProductLinksOpenBtn">
    <svg width="14" height="14"><use href="#ic-link"/></svg>
    <span>产品链接管理</span>
  </button>
  <span class="oc-hint">查看该产品所有启用域名 + 检查链接可访问性 + Shopify 换图状态</span>
</div>
```

按钮在所有语种 tab 都显示，状态由当前 `activeLang` 控制弹窗内容。

### 3.2 弹窗骨架（`edProductLinksMask`）

```
┌── 产品链接管理 — <产品名> · <语言> ───────────────────────────[×]──┐
│                                                                  │
│  当前展示的是「<语言>」tab 下该产品**已启用的全部域名**的链接状态。     │
│                                                                  │
│  [全部重新检查可用性]                              最近探测：xx 秒前 │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ [newjoyloo.com]  HTTP 200  链接正常                        │  │
│  │ https://newjoyloo.com/de/products/sonic-lens-refresher-rjc │  │
│  │ 探测于 2026-05-09 14:32                                    │  │
│  │                                                             │  │
│  │ Shopify: 人工确认完成                                       │  │
│  │ [复制链接] [重新检查可用性] [确认图片正常] [重新排队换图]    │  │
│  │ [标记链接不可用]                                           │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ [omurio.com]  HTTP 404  链接失联                           │  │
│  │ ... 同上 ...                                               │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│                                            [关闭]                │
└──────────────────────────────────────────────────────────────────┘
```

英语 tab 下：

- 不渲染 `Shopify:` 段、不渲染 `确认图片正常 / 重新排队换图 / 标记链接不可用` 三个按钮（这些 API 要求 `lang ≠ en`）。
- 顶部说明改为「英语为源语言，仅检测链接可访问性」。

空 / 加载 / 错误三态：

- 空：当前语种下该产品**没有启用任何域名** → 居中提示「该产品当前未启用任何域名，去「域名管理」启用至少一个」。
- 加载：探测请求飞行时按钮 disable，行内 Badge 显示 `检测中…`，置灰；最近一次结果继续显示。
- 错误：探测请求 5xx / 网络错误 → 顶部 toast `产品链接可用性检查失败：<msg>`，行内 Badge 退回上一次结果。

## 4. 后端

### 4.1 表 `media_product_link_availability`

新增迁移 `db/migrations/2026_05_09_media_product_link_availability.sql`：

```sql
CREATE TABLE IF NOT EXISTS media_product_link_availability (
  product_id INT NOT NULL,
  lang VARCHAR(8) NOT NULL,
  domain VARCHAR(255) NOT NULL,
  link_url VARCHAR(1024) NOT NULL,
  http_status SMALLINT DEFAULT NULL,
  ok TINYINT(1) NOT NULL DEFAULT 0,
  error VARCHAR(255) DEFAULT NULL,
  elapsed_ms INT DEFAULT NULL,
  checked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, lang, domain),
  KEY idx_media_product_link_avail_product_lang (product_id, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Product link HTTP availability cache (per product × lang × domain).';
```

**判定规则：**

- `http_status` 取最终响应（跟随重定向后）状态码；2xx / 3xx → `ok=1`，4xx / 5xx / 网络错误 / 超时 → `ok=0`。
- `error` 仅在 `ok=0` 时填，例如 `"timeout"` / `"http 404"` / `"http 403"` / `"network: <msg>"`。
- 同 `(product_id, lang, domain)` upsert，保留最新一次。

### 4.2 模块 `appcore/link_availability.py`

```python
def probe(url: str, *, timeout: float = 5.0, max_redirects: int = 5) -> dict
def upsert_result(product_id: int, lang: str, domain: str, link_url: str, result: dict) -> None
def list_results(product_id: int, lang: str) -> list[dict]
def list_results_for_domain(product_id: int, lang: str, domain: str) -> dict | None
def probe_and_record(product_id: int, lang: str, rows: list[dict]) -> list[dict]
```

`probe()` 用 stdlib `urllib.request`：

- 默认 `Method=HEAD`；如果服务端 405 或返回 0 字节 Content-Length，回退一次 `GET` 并立即关闭连接（不读 body）。
- 5s 超时；最多跟 5 次 302。
- User-Agent: `Mozilla/5.0 (compatible; AutoVideoSrt-LinkAvailability/1.0)`。
- 网络异常 / 超时统一返回 `{"http_status": None, "ok": False, "error": <type>:<msg>, "elapsed_ms": <int>}`。

`probe_and_record(...)` 内部并行 `concurrent.futures.ThreadPoolExecutor(max_workers=8)`，对一组 `[{"domain", "url"}, ...]` 并行打探，最后逐条 `upsert_result` 并返回最新结果列表。

### 4.3 路由 `web/routes/medias/link_check.py`（沿用同一文件）

```python
@bp.route("/api/products/<int:pid>/link-availability/<lang>", methods=["POST"])
@login_required
def api_product_link_availability_run(pid: int, lang: str):
    # body: {"domain": "..."} 可选；缺省探测全部启用域名
    ...

@bp.route("/api/products/<int:pid>/link-availability/<lang>", methods=["GET"])
@login_required
def api_product_link_availability_get(pid: int, lang: str):
    # 返回该产品该语种下已启用域名的最新可用性结果
    ...
```

`POST` 返回最新结果列表（同步，5s 超时 × 并行）；`GET` 仅读最近持久化结果不重新探测。

返回结构：

```json
{
  "product_id": 123,
  "lang": "de",
  "items": [
    {
      "domain": "newjoyloo.com",
      "link_url": "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc",
      "http_status": 200,
      "ok": true,
      "error": null,
      "elapsed_ms": 432,
      "checked_at": "2026-05-09T14:32:00Z"
    },
    {
      "domain": "omurio.com",
      "link_url": "https://omurio.com/de/products/sonic-lens-refresher-rjc",
      "http_status": 404,
      "ok": false,
      "error": "http 404",
      "elapsed_ms": 218,
      "checked_at": "2026-05-09T14:32:00Z"
    }
  ]
}
```

无启用域名时返回 `items: []`，HTTP 200。

`build_product_link_availability_run_response` / `build_product_link_availability_get_response` 拆到 `web/services/media_link_check.py`，跟现有 link-check 服务并列，方便单测。

### 4.4 鉴权

沿用既有 medias 蓝图 `@login_required` 守卫；通过 `_can_access_product` 复用产品归属校验，跟 `api_product_link_check_create` 一致。

## 5. 前端

### 5.1 模板（`web/templates/_medias_edit_detail_modal.html`）

- 在「产品链接」字段块（第 44–57 行）末尾、`</div>` 闭合前补一个 `oc-product-links-entry`（按钮 + hint）。
- 在 `edLinkCheckMask` 之后追加新弹窗 `edProductLinksMask`，结构上由 `oc-modal` + 顶部 head + body（操作条 + 列表容器 `edProductLinksList`）+ 底部「关闭」按钮组成。
- 顶部备注一句注释指明入口位置。

### 5.2 JS（`web/static/medias.js`）

新增函数 / 状态：

```js
edState.productLinksModal = { lang: '', items: [], loading: false, error: '' };

function edOpenProductLinksModal() { ... }
function edCloseProductLinksModal() { ... }
function edRenderProductLinksModal() { ... }
function edFetchProductLinkAvailability(force=false) { ... }
function edRunProductLinkAvailability(domain=null) { ... }
function edHandleProductLinksAction(action, domain) { ... }
```

事件接线（沿用 `setupHandlers` 模式）：

- `#edProductLinksOpenBtn` → `edOpenProductLinksModal`
- 弹窗内 `data-product-links-action` 属性的按钮统一委托到 `edHandleProductLinksAction`，action 取值 `recheck-one / recheck-all / copy / shopify-confirm / shopify-requeue / shopify-unavailable`
- `#edProductLinksClose` / `#edProductLinksDoneBtn` → `edCloseProductLinksModal`
- 切换 `edState.activeLang` 时若弹窗在打开状态，自动拉新数据

`edRenderShopifyImageStatus(lang)` 改为始终隐藏 `edShopifyImageStatus` 面板（保留函数体里的 `shopify_image_status` 数据 map 处理，避免影响现有缓存逻辑）。改写规则：移除原有渲染 HTML，仅 `box.hidden = true; box.innerHTML = '';`。

Shopify 图片确认 / 重排队 / 标记不可用三个动作复用现有 `edApplyShopifyImageAction`。在新弹窗里点完，回到 `edFetchProductLinkAvailability(true)` 重新拉一次状态。

### 5.3 CSS（`web/static/medias.css`）

新增 selector 走现有 `oc-link-check-*` token 风格：

```css
.oc-product-links-entry { ... }
.oc-product-links-modal-body { ... }
.oc-product-links-row { ... }
.oc-product-links-row-meta { ... }
.oc-product-links-row-actions { ... }
.oc-product-links-empty { ... }
```

颜色与圆角全部走 `--oc-*` 变量；不引入新颜色。HTTP 状态 Badge 重用 `oc-link-check-badge` + `info / success / warning / danger` kind。

## 6. 验证

### 6.1 后端单测

- `tests/test_appcore_link_availability.py`：
  - `probe()` 200 / 301→200 / 404 / 403 / 5xx / 超时 / 网络错误 各分支
  - `probe()` HEAD 405 → fallback GET
  - `upsert_result()` 第一次 INSERT、再次 UPDATE 同行
  - `probe_and_record()` 多域名并发 + 顺序无关
- `tests/test_medias_link_availability_routes.py`：
  - 路由未登录 302
  - 登录后 POST 无 body → 走全部启用域名
  - 登录后 POST 带 `domain` → 仅探测单域名
  - GET 返回历史结果 + 已启用域名为空时 `items: []`
- `tests/test_db_migration_media_product_link_availability.py`：迁移文件包含 `CREATE TABLE` + 主键 + 索引

### 6.2 前端冒烟（dev server）

- 起 `python -m web.app` 端口 5090，prod `.env` + prod 库
- 用 `admin/709709@` 登录
- 进任一商品的「编辑产品素材」弹窗
- 验证：
  - 英语 tab 下，「产品链接」行下面有 `产品链接管理` 按钮；点开弹窗能看到该产品启用的全部域名 + URL；点「全部重新检查可用性」能拿到 HTTP status + 失败的标红
  - 切换到德语 tab，弹窗内容随之刷新，多了 Shopify 状态那一行 + 三个 Shopify 操作按钮
  - 非英语 tab 下「产品链接」输入框下面**不再**有内联状态面板
  - `查看结果` 按钮（链接检测 + 图片分析）行为不变
- HTTP 冒烟：
  - 未登录访问 `/medias/api/products/1/link-availability/de` → 302
  - 登录后 GET → 200 / JSON 含 `items`

### 6.3 pytest 命令

```bash
pytest tests/test_appcore_link_availability.py \
       tests/test_medias_link_availability_routes.py \
       tests/test_db_migration_media_product_link_availability.py \
       tests/test_db_migration_product_link_domains.py \
       tests/test_appcore_medias_link_check_bootstrap.py \
       tests/test_link_check_bootstrap_routes.py -q
```

## 7. 兼容 / 回滚

- 拿掉 `edShopifyImageStatus` 内联面板渲染**不影响数据写入路径**；`edApplyShopifyImageAction` / `shopify_image_status` 数据结构不变，只是不在主表单下方铺一行。
- 回滚 = 把 `edRenderShopifyImageStatus` 恢复为原渲染 + 把按钮入口从模板里去掉；后端表保留也不影响其他模块。
- 新表 `media_product_link_availability` 仅给本弹窗使用，没有跨链路依赖。

## 8. 变更记录

| 日期 | 变更 | 备注 |
|------|------|------|
| 2026-05-09 | 初版 | issue AUT-15，按 (B) + 合并 Shopify 操作 + 新增 HTTP 可用性探测 |
