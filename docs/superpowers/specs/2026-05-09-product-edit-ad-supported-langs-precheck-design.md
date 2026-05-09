# 素材管理产品编辑页 — 顶部国家勾选前置校验

- 锚点：`docs/superpowers/specs/2026-05-09-product-edit-ad-supported-langs-precheck-design.md`
- 关联 issue：[AUT-22](https://multica) — 素材管理产品编辑页：勾选顶部国家前校验该国启用链接均已检测且可访问
- 涉及范围：素材管理「编辑产品素材」弹窗顶部 `ad_supported_langs` 复选框、`web/static/medias.js`、`web/templates/_medias_edit_detail_modal.html`、`web/static/medias.css`、`web/services/media_product_mutations.py`、`web/routes/medias/products.py`

## 1. 背景与目标

「编辑产品素材」弹窗顶部有一组语言/国家复选框（`edAdSupportedLangsBox` → `ad_supported_langs` 字段，排除 `en`），勾选代表「该产品支持向这个国家投流广告」。当前缺乏前置校验：

- 勾选某国家时，没人验证该产品在对应语种下"启用域名"都能正常访问；坏链接的产品被推到投流后才暴露。
- 后端 `PUT /medias/api/products/<pid>` 不检查 `ad_supported_langs` 与可用性数据是否一致，绕过前端就能把"勾了的、但链接挂了"的状态写库。

本 spec 加一道前置校验：**手动勾选** `ad_supported_langs` 中的某 lang 时，必须满足"该产品该 lang 下启用域名 ≥ 1 且全部 `media_product_link_availability.ok=1`"；保存时后端再守一次门，但只校验本次相对存量"新增"的 lang，避免老产品因为后续链接挂掉永远存不进去。

## 2. 范围

**做：**

- 前端：拦截 `#edAdSupportedLangsBox` 复选框 click，**勾选** 路径自动跑一次 `POST /medias/api/products/<pid>/link-availability/<lang>`；全部 `ok=true` 才真正勾上，否则保持未勾选并弹失败 modal；空启用域名场景弹专门的"请先启用域名"提示。
- 前端：取消勾选 (`unchecked`) **不**触发校验，直接放行。
- 前端：失败 modal 提供"去产品链接管理"按钮，切 `edState.activeLang = lang` 后打开既有 `edProductLinksMask` 弹窗，方便用户处理。
- 后端：`build_product_update_response` 在 `ad_supported_langs` 入库前，对**新增** lang 集合（`new - old`）做同样校验；任一 lang 不通过就 422 + `{"error":"ad_supported_langs_precheck_failed","issues":[...]}`，不写库不刷文案。
- 复用现有 `appcore/link_availability.py` 探测 + 持久化模块；**不新增**表，不改 schema。

**不做：**

- 不校验"老的、已勾选"的 lang。运营改产品名等无关字段不会被卡。
- 不在保存路径自动重新探测链接（保存时只读 `media_product_link_availability` 缓存）。
- 不动 `media_link_domains` / `media_product_link_domains` schema。
- 不引入新依赖。

## 3. UX

### 3.1 触发与状态

复选框 `<input type="checkbox" name="ad_supported_langs">` 的 click 事件被前端拦截：

| 用户动作 | 前端逻辑 |
|----------|----------|
| 点未勾选的复选框（uncheck → check） | `preventDefault()` + 跑 `POST link-availability/<lang>` + 等结果 |
| 点已勾选的复选框（check → uncheck） | 不拦截，直接放行 |
| 探测飞行中 | 复选框 `disabled`，旁边显示 `检测中…` |
| 全部 `ok=true` | 真正把 checkbox 置为 `checked` |
| 启用域名为 0 | 弹"请先启用域名"提示，保持未勾选 |
| 任一 `ok=false` 或 `not_checked` | 弹失败 modal 列出每条 (domain, HTTP, error)，保持未勾选 |
| 网络异常 / 5xx | toast 报错，保持未勾选 |

### 3.2 失败 modal `edAdLangPrecheckMask`

```
┌── 国家勾选校验失败 — 德语 (de) ────────────────[×]─┐
│                                                   │
│  以下域名的链接不满足"全部检测通过 + 可正常访问"   │
│  的要求，无法勾选该国家：                          │
│                                                   │
│  newjoyloo.com   HTTP 404   http 404              │
│    https://newjoyloo.com/de/products/sonic-lens   │
│  omurio.com      超时        timeout              │
│    https://omurio.com/de/products/sonic-lens      │
│                                                   │
│  说明：勾选某国家前，必须保证该语种下"已启用的域名" │
│  全部 HTTP 可访问。请去产品链接管理处理后重试。     │
│                                                   │
│  [取消]                       [去产品链接管理]    │
└───────────────────────────────────────────────────┘
```

空域名场景共用同一 modal，但内容只有一行说明：

```
该语种当前未启用任何域名，无法勾选该国家。
请先去【产品链接管理】启用至少一个域名。
```

按钮：`[取消]` 关闭 modal；`[去产品链接管理]` 切 `edState.activeLang = lang` 并打开 `edProductLinksMask`。

### 3.3 保存路径

保存请求 (`PUT /medias/api/products/<pid>`) 收到 422 + `error=="ad_supported_langs_precheck_failed"` 时，前端弹 toast：

```
保存失败：以下国家不满足前置条件
- 德语 (de): newjoyloo.com — http 404
- 法语 (fr): 该语种未启用任何域名
请处理后重试。
```

前端不自动取消勾选状态（用户的勾选意图保留），由用户决定重新点取消或去处理链接。

## 4. 后端

### 4.1 模块改动 — `web/services/media_product_mutations.py`

新增异常 + 校验 helper：

```python
class AdSupportedLangsPrecheckError(Exception):
    """Raised when ad_supported_langs has newly-added langs not satisfying
    'enabled domain ≥ 1 and all media_product_link_availability.ok=1'."""

    def __init__(self, issues: list[dict]):
        super().__init__("ad_supported_langs_precheck_failed")
        self.issues = issues
```

`build_product_update_response` 在 `ad_supported_langs` clean 之后、`update_product_fn` 之前插入新 step：

```python
# 计算新增 lang
old_set = set(_split_csv(product.get("ad_supported_langs")))
new_set = set(_split_csv(update_fields.get("ad_supported_langs")))
added = sorted(new_set - old_set)

if added:
    issues = _check_ad_lang_precheck_issues(
        product=product,
        langs=added,
        list_enabled_domain_rows_fn=list_enabled_domain_rows_fn,  # injected
        list_link_availability_fn=list_link_availability_fn,      # injected
    )
    if issues:
        return ProductMutationResponse(
            {"error": "ad_supported_langs_precheck_failed", "issues": issues},
            422,
        )
```

`_check_ad_lang_precheck_issues` 实现：

```python
def _check_ad_lang_precheck_issues(*, product, langs, list_enabled_domain_rows_fn, list_link_availability_fn):
    issues = []
    pid = int(product.get("id") or 0)
    for lang in langs:
        rows = list_enabled_domain_rows_fn(product, lang) or []
        if not rows:
            issues.append({"lang": lang, "reason": "no_enabled_domains"})
            continue
        avail = list_link_availability_fn(pid, lang) or []
        avail_by_domain = {item["domain"]: item for item in avail}
        domain_issues = []
        for row in rows:
            domain = row["domain"]
            entry = avail_by_domain.get(domain)
            if entry is None or entry.get("checked_at") in (None, ""):
                domain_issues.append({"domain": domain, "reason": "not_checked"})
                continue
            if not entry.get("ok"):
                err = entry.get("error") or (
                    f"http {entry['http_status']}"
                    if entry.get("http_status")
                    else "unavailable"
                )
                domain_issues.append({"domain": domain, "reason": err})
        if domain_issues:
            issues.append({"lang": lang, "domains": domain_issues})
    return issues
```

注入项默认值（routes 层装配）：

- `list_enabled_domain_rows_fn = product_link_domains.resolve_product_page_url_rows`
- `list_link_availability_fn = link_availability.list_results`

### 4.2 Route — `web/routes/medias/products.py`

`_build_product_update_response` 装配新增两个注入项；422 自然落到 `_product_mutation_flask_response`，不需要新代码路径。

### 4.3 校验语义说明

- "启用域名" = `product_link_domains.resolve_product_page_url_rows(product, lang)` 当前的返回（已经过滤了产品级 enabled + 全局级 enabled，跟产品链接管理弹窗 / 链接可用性探测同源）
- "全部检测通过" = 上述每个 domain 在 `media_product_link_availability` 里都有非空 `checked_at` 行且 `ok=1`
- 至少一个 domain 没探测过 / `ok=0` → 整个 lang 不通过

## 5. 前端

### 5.1 模板（`web/templates/_medias_edit_detail_modal.html`）

在产品链接管理弹窗 `edProductLinksMask` 下面追加：

```html
<!-- 顶部国家勾选前置校验失败弹窗（spec: docs/superpowers/specs/2026-05-09-product-edit-ad-supported-langs-precheck-design.md） -->
<div class="oc-modal-mask oc" id="edAdLangPrecheckMask" hidden>
  <div class="oc-modal oc-modal-narrow oc-ad-lang-precheck-modal" role="dialog" aria-modal="true" aria-labelledby="edAdLangPrecheckTitle">
    <div class="oc-modal-head">
      <h3 id="edAdLangPrecheckTitle">国家勾选校验失败</h3>
      <button class="oc-icon-btn" id="edAdLangPrecheckClose" title="关闭" aria-label="关闭">
        <svg width="16" height="16"><use href="#ic-close"/></svg>
      </button>
    </div>
    <div class="oc-modal-body oc-ad-lang-precheck-body">
      <p id="edAdLangPrecheckHint" class="oc-hint"></p>
      <div id="edAdLangPrecheckList" class="oc-ad-lang-precheck-list"></div>
      <p class="oc-hint">说明：勾选某国家前，必须保证该语种下「已启用的域名」全部 HTTP 可访问。</p>
    </div>
    <div class="oc-modal-foot">
      <button class="oc-btn ghost" id="edAdLangPrecheckCancelBtn">取消</button>
      <button class="oc-btn primary" id="edAdLangPrecheckJumpBtn">去产品链接管理</button>
    </div>
  </div>
</div>
```

### 5.2 JS（`web/static/medias.js`）

新增状态 + 函数：

```js
edState.adLangPrecheck = { lang: '', kind: '' /* 'failed' | 'empty' | 'error' */, issues: [], pending: false };

function edSetupAdLangCheckboxGuard()       // 在 edRenderAdSupportedLangs 末尾挂 click 监听（事件委托）
function edHandleAdLangCheckboxClick(ev)    // 阻止默认 toggle，跑校验，分支处理
async function edRunAdLangPrecheck(lang)    // POST /medias/api/products/<pid>/link-availability/<lang>
function edOpenAdLangPrecheckModal({lang, kind, issues})
function edCloseAdLangPrecheckModal()
function edJumpFromPrecheckToProductLinks() // 切 activeLang + 关闭 precheck modal + 打开 product links modal
function edHandleSaveErrorAdLangPrecheck(payload) // 收到 422 时弹 toast
```

事件接线：

- 委托方式：`#edAdSupportedLangsBox` 的 `click` capture phase 监听 `input[name="ad_supported_langs"]`；调用 `edHandleAdLangCheckboxClick`
- `#edAdLangPrecheckClose` / `#edAdLangPrecheckCancelBtn` → `edCloseAdLangPrecheckModal`
- `#edAdLangPrecheckJumpBtn` → `edJumpFromPrecheckToProductLinks`
- `edSavePayload` 拿到 `422` + `error == "ad_supported_langs_precheck_failed"` 时调 `edHandleSaveErrorAdLangPrecheck`

### 5.3 CSS（`web/static/medias.css`）

新增 selector：

```css
.oc-ad-lang-precheck-body { display:flex; flex-direction:column; gap:var(--oc-sp-3); padding:var(--oc-sp-5); }
.oc-ad-lang-precheck-list { display:flex; flex-direction:column; gap:var(--oc-sp-2); }
.oc-ad-lang-precheck-row { display:grid; grid-template-columns:1fr auto auto; gap:var(--oc-sp-3); padding:var(--oc-sp-3); border:1px solid var(--oc-border); border-radius:var(--oc-radius-md); background:var(--oc-bg-subtle); }
.oc-ad-lang-precheck-row .url { grid-column:1/-1; font-family:var(--oc-font-mono); font-size:var(--oc-text-xs); color:var(--oc-fg-muted); word-break:break-all; }
.oc-ad-lang-precheck-row .badge.danger { background:var(--oc-danger-bg); color:var(--oc-danger-fg); }
.oc-ad-lang-precheck-row .badge.warning { background:var(--oc-warning-bg); color:var(--oc-warning-fg); }
.oc-ad-lang-precheck-empty { padding:var(--oc-sp-3); text-align:center; color:var(--oc-fg-muted); }
```

颜色与圆角全部走 `--oc-*` 变量；不引入新颜色。

## 6. 测试

### 6.1 后端单测

新增 `tests/test_media_product_mutations_ad_lang_precheck.py`：

| 用例 | 期望 |
|------|------|
| 老 lang 已坏，新增 lang 不在 body | 不触发校验，直接通过（200） |
| 新增 lang，启用域名 = 0 | 422 + issue `reason="no_enabled_domains"` |
| 新增 lang，启用域名 = 2 全部 ok | 200，update_product_fn 被调用 |
| 新增 lang，启用域名 = 2 但 1 个 not_checked | 422 + issue 含该 domain reason="not_checked" |
| 新增 lang，启用域名 = 2 但 1 个 ok=false | 422 + issue 含该 domain reason="http 404" |
| body 里 ad_supported_langs ⊊ 老的（仅取消勾选） | 不触发校验，通过 |
| body 里 ad_supported_langs == 老的 | 不触发校验，通过 |
| 多个新增 lang 都失败 | 422，issues 含全部失败 lang |
| 新增 lang 但前端没传 ad_supported_langs key | 校验跳过 |

### 6.2 路由层冒烟（用 `tests/test_medias_routes.py` 的既有 fixture）

新增 `tests/test_medias_routes_ad_lang_precheck.py`：

- 未登录 PUT /api/products/<pid> → 302
- 登录后传新增 lang + mock service 返回 issues → 422 + 正确 JSON

### 6.3 前端冒烟（dev server 5090，prod .env，admin/709709@）

1. 进任一商品「编辑产品素材」弹窗
2. 找一个未勾选国家 + 已启用域名 + 全部 ok → 点击勾选成功
3. 故意把链接弄坏 → 弹失败 modal，HTTP 状态显示
4. 没启用域名的国家 → 弹"请先启用域名"
5. 取消已勾选 → 不触发，直接成功
6. 老产品（已勾选 + 链接坏）改产品名保存 → 通过（C 方案兜底）
7. 老产品手动勾新国家但链接坏 → 422 + toast

### 6.4 命令

```bash
pytest tests/test_media_product_mutations_ad_lang_precheck.py \
       tests/test_medias_routes_ad_lang_precheck.py \
       tests/test_media_product_mutations_service.py \
       tests/test_appcore_link_availability.py \
       tests/test_medias_link_availability_routes.py -q
```

## 7. 兼容 / 回滚

- 不改 schema、不引入新依赖
- 回滚 = 把 service 中 `_check_ad_lang_precheck_issues` 调用注释掉 + 前端摘 `edSetupAdLangCheckboxGuard` 监听
- 老数据兼容：C 方案保证老产品已勾选的 lang 即使链接已坏也不会卡保存

## 8. 变更记录

| 日期 | 变更 | 备注 |
|------|------|------|
| 2026-05-09 | 初版 | issue AUT-22；A（点击自动检测）+ B（空域名阻断）+ Q3.C（仅校验新增 lang） |
