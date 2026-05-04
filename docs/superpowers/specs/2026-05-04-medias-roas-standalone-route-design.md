# 素材管理：单产品 ROAS 独立路由设计

- 日期：2026-05-04
- 关联代码：[medias_list.html:2435-2515](../../../web/templates/medias_list.html#L2435-L2515)、[medias.js:494-655](../../../web/static/medias.js#L494-L655)、[web/routes/medias/products.py](../../../web/routes/medias/products.py)、[appcore/product_roas.py](../../../appcore/product_roas.py)

## 1. 背景与目标

当前「素材管理」列表页里，单条产品的「ROAS」按钮触发模态弹窗（[medias_list.html:2435](../../../web/templates/medias_list.html#L2435)），表单填完后点「计算 ROAS」+「保存」两步走，保存目标是 `PUT /medias/api/products/<pid>`。

存在两个需求未被满足：

1. **没有可分享/可深链/可独立刷新的 ROAS 页面**——所有操作必须先进列表页再开弹窗。
2. **手动「计算 ROAS」+「保存」两步流程繁琐**——用户希望「数据填齐就自动算、自动入库」。

本设计目标：

- 新增独立路由 `GET /medias/<int:pid>/roas` 渲染整页的 ROAS 编辑页（侧栏 + 顶栏 + 整页内容）。
- 模态保留，模态右上角加胶囊按钮「在新页面打开」，点击 `target="_blank"` 跳到独立路由页。
- 把「填字段→自动算 ROAS→自动 PUT 入库」做成默认行为，**模态和独立页共用同一套自动保存机制**；旧的「计算 ROAS」按钮保留作兜底（手动重算 + 同步保存）。
- 抽出共享 partial + JS 控制器，避免模态/独立页双份维护。

## 2. 设计决策（与用户对齐结果）

| 决策点 | 结果 |
| ----- | ---- |
| 模态去留 | 保留模态；模态新增胶囊按钮「在新页面打开」，链接到独立路由 |
| 独立页字段范围 | 1:1 复用模态全部字段：独立站信息（左列）+ TK 可选项 + 平均运费计算器（右列） |
| 保存交互 | 自动保存，留在当前页；不再有「关闭弹窗」概念 |
| 自动计算 + 自动保存适用范围 | **模态 + 独立页都生效**（避免两边行为不一致） |
| 触发时机/粒度 | 输入实时 → debounce **600ms** → 整表 PUT `/medias/api/products/<pid>`；前端在保存前先尝试 `calculateRoasBreakEven`，能算就把 `effective_*` / `estimated_roas` / `actual_roas` 字段一起带上，不能算就只 PUT 用户填写的字段 |
| 兜底按钮 | 保留「计算 ROAS」按钮：点击 = 立即重算 + 立即保存（绕过 debounce） |
| 状态反馈 | **页面顶部固定状态条（横向 banner）**，三态：`保存中…` / `已保存 ✓ HH:MM:SS` / `保存失败 ↻`（点击 ↻ 重试） |
| 跳转按钮形态 | 胶囊按钮（pill），文案「在新页面打开」+ 图标，`target="_blank" rel="noopener"` |
| 实现策略 | **方案 A**：抽出 Jinja partial `_roas_form.html` + JS 类 `RoasFormController`，模态和独立页都复用 |

## 3. URL 与后端路由

### 3.1 新增视图

在 [web/routes/medias/products.py](../../../web/routes/medias/products.py)（或新建 `web/routes/medias/roas.py`，挂在同一个 `bp` 蓝图下）添加：

```python
@bp.route("/<int:pid>/roas")
@login_required
def roas_page(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)
    return render_template(
        "medias/roas.html",
        product=_serialize_product(product),
        roas_rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
    )
```

- 权限：`@login_required` + 复用 `_can_access_product`（与 `/medias/cover/<pid>` 一致）。
- 产品不存在 / 无权限 → `abort(404)`。
- 序列化函数 `_serialize_product` 已经在 [_serializers.py](../../../web/routes/medias/_serializers.py) 中，复用即可。
- 汇率从 `product_roas.get_configured_rmb_per_usd()` 取。

### 3.2 复用现有保存端点

`PUT /medias/api/products/<int:pid>` ([products.py:237-328](../../../web/routes/medias/products.py)) **不改动**，只通过 `_ROAS_PRODUCT_FIELDS` 白名单过滤可写字段——本次自动保存提交的就是这个白名单的子集。

> 计算出的 `estimated_roas` / `actual_roas` / `effective_basis` / `effective_roas` 是**前端展示派生字段**，不入 `media_products` 表（已有的 `appcore/product_roas.calculate_break_even_roas` 也是按需即时计算）。所以 PUT body 只需要原始输入字段。

### 3.3 URL 风格对齐

- 现有 `/medias/cover/<pid>` 是 `<verb>/<id>`，本次反过来用 `/<id>/<resource>` 与用户指定的 `/medias/6/roas` 对齐。两种风格仓库内并存可接受；以本次为准的新风格。

## 4. 前端架构

### 4.1 模板拆分

- 把 [medias_list.html:2443-2507](../../../web/templates/medias_list.html#L2443-L2507) 内 `<div class="oc-modal-body"> ... </div>` 这一段（产品卡 + 表单两栏）抽到新 partial：

  ```
  web/templates/medias/_roas_form.html   {# 共享内容：产品卡 + 表单 #}
  ```

- 模态保持外壳（mask + modal-head + modal-foot），中间用 `{% include "medias/_roas_form.html" %}`。
- 模态 head 增加胶囊按钮「在新页面打开」（`<a class="oc-btn pill" target="_blank" rel="noopener" href="...">`），通过 `data-roas-pill-href` 在 `openRoasModal` 时动态写入。
- 独立路由用新模板 `web/templates/medias/roas.html`：
  ```
  {% extends "layout.html" %}
  {% block title %}独立站保本 ROAS - {{ product.product_code }}{% endblock %}
  {% block page_title %}独立站保本 ROAS{% endblock %}
  {% block extra_style %}
    {# 复用 medias_list.html 的 .oc page-scoped tokens——抽出到 _roas_styles.html partial 共享 #}
    {% include "medias/_roas_styles.html" %}
  {% endblock %}
  {% block content %}
    <div class="oc oc-roas-page">
      <div class="oc-roas-status-bar" data-roas-status></div>     {# 顶部固定状态条 #}
      <header class="oc-roas-page-head">
        <a class="oc-btn ghost" href="/medias">← 返回素材管理</a>
        <h1>独立站保本 ROAS</h1>
      </header>
      {% include "medias/_roas_form.html" %}
    </div>
  {% endblock %}
  {% block scripts %}
    <script src="{{ url_for('static', filename='roas_form.js') }}"></script>
    <script>
      new RoasFormController(
        document.querySelector('.oc-roas-page'),
        { productId: {{ product.id }}, statusBarEl: document.querySelector('[data-roas-status]') }
      ).fillFromProduct({{ product | tojson }});
    </script>
  {% endblock %}
  ```

- **基模板** = `layout.html`（仓库实际名，无下划线前缀，参见 [medias_list.html:1](../../../web/templates/medias_list.html#L1)）。
- **CSS 复用**：`medias_list.html` 的 `.oc { --oc-* }` token 定义在 `extra_style` block（行 6-50+），实施时把 ROAS 用到的 token 抽到 `web/templates/medias/_roas_styles.html`，列表页和独立页都 include；外层包 `<div class="oc">` 让所有 token 生效。

### 4.2 JS 控制器

抽 `RoasFormController` 类（建议新文件 [web/static/roas_form.js](../../../web/static/roas_form.js)，模态和独立页都引用）：

```js
class RoasFormController {
  constructor(rootEl, { productId, statusBarEl, onAfterSave }) { ... }
  bind() { /* 监听 input/change，debounce 600ms 触发 save() */ }
  fillFromProduct(product) { /* setRoasFieldValues 等价 */ }
  collectPayload() { /* collectRoasPayload 等价 */ }
  computeRoas() { /* calculateRoasBreakEven 等价 */ }
  renderResult() { /* renderRoasResult 等价 */ }
  async save({ immediate }) {
    this._setStatus('saving');
    try {
      const payload = this.collectPayload();
      await fetchJSON('/medias/api/products/' + this.productId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      this._setStatus('saved');
      if (this.onAfterSave) this.onAfterSave(payload);
    } catch (e) {
      this._setStatus('error', e.message);
    }
  }
  _setStatus(state, msg) { /* 写状态条 banner */ }
}
```

- [medias.js:604-655](../../../web/static/medias.js#L604-L655) 的 `openRoasModal` / `saveRoas` 改为构造 `RoasFormController` 并复用，不再保留独立的 `saveRoas`。
- 「计算 ROAS」按钮 → `controller.save({ immediate: true })`，绕过 debounce + 立刻刷新结果。
- 模态关闭时控制器不销毁（保留 `loadList()` 刷新行为，可放到 `onAfterSave` 回调里只在模态场景下注入）。
- 独立页里 `onAfterSave` 不调用 `loadList()`（独立页没有列表）。

### 4.3 状态条 UI

```html
<div class="oc-roas-status-bar" data-state="idle">
  <span class="oc-roas-status-text">尚未编辑</span>
  <button hidden class="oc-roas-status-retry">重试</button>
</div>
```

CSS（OKLCH，遵循 design system）：

| 状态 | 背景 | 文案颜色 | 文案 |
|----|------|--------|------|
| idle | 透明 | `--fg-subtle` | 「尚未编辑」 |
| saving | `--info-bg` | `--accent` | 「保存中…」 |
| saved | `--success-bg` | `--success-fg` | 「已保存 ✓ HH:MM:SS」 |
| error | `--danger-bg` | `--danger-fg` | 「保存失败：xxx」+「重试」按钮 |

模态里同样需要这条状态条（顶部 modal-head 和 form 之间）；独立页里贴在 layout 顶部、面包屑上方，position 用 sticky。

### 4.4 自动保存语义

- 防抖 600ms：用户停止输入 600ms 后触发 `save({ immediate: false })`。
- 任何时候只允许一个 in-flight 请求；如果触发新一次保存时上一次还在飞，记录 pending payload，上一次返回后立即用最新 payload 再触发一次（last-write-wins）。
- 失败：状态条显示 error + 重试按钮；不阻塞继续编辑（继续编辑会自动重试）。
- 网络错误（offline）和 4xx/5xx 同样走 error 路径，错误消息透传。
- ROAS 计算：每次 collectPayload 后调 `computeRoas`，把派生值渲染到结果区；**派生值不放入 PUT body**（保持 `_ROAS_PRODUCT_FIELDS` 白名单不动）。

## 5. 模态新增「在新页面打开」按钮

- 位置：模态 `<div class="oc-modal-head">`，在「关闭」按钮左侧。
- 元素：
  ```html
  <a id="roasOpenInPage"
     class="oc-btn pill ghost"
     target="_blank" rel="noopener"
     href="#">
    <svg width="14" height="14"><use href="#ic-external-link"/></svg>
    在新页面打开
  </a>
  ```
- 在 `openRoasModal(product)` 中赋值 `href = '/medias/' + product.id + '/roas'`。
- 胶囊样式（`.oc-btn.pill`）：高度 28px，左右 padding 12px，`border-radius: var(--radius-full)`。

## 6. 页面布局（独立路由）

```
┌────────┬──────────────────────────────────────────────────┐
│ 侧栏   │  [顶部状态条 sticky]                              │
│        ├──────────────────────────────────────────────────┤
│ 素材   │  ← 返回素材管理       独立站保本 ROAS             │
│ 管理▼  ├──────────────────────────────────────────────────┤
│        │  {% include "medias/_roas_form.html" %}           │
│        │   • 产品卡（缩略图 + ID + 中/英文名 + 当前采用）  │
│        │   • 表单两栏：独立站信息 / TK 可选项 + 运费计算器│
│        │   • 计算结果卡 +「计算 ROAS」兜底按钮             │
└────────┴──────────────────────────────────────────────────┘
```

- 侧栏：复用现有素材管理 layout，「素材管理」一项保持 active。
- 移动端（< 1024px）：两栏堆叠为单列；状态条仍 sticky；侧栏抽屉化（按现有 layout 行为）。**禁止改动 768px 以上的 PC 布局**（按 CLAUDE.md 移动版铁律）。
- 不放「保存」「取消」按钮——保存全靠自动保存。

## 7. 测试计划

### 7.1 后端

- 在 [tests/test_product_roas.py](../../../tests/test_product_roas.py) 或新建 `tests/test_medias_roas_route.py`：
  - `GET /medias/<pid>/roas` 200，包含字段 input。
  - 不存在 pid → 404。
  - 未登录 → redirect 到 login。
  - 无 `_can_access_product` 权限 → 404。
  - `PUT /medias/api/products/<pid>` 已有覆盖，确保 `_ROAS_PRODUCT_FIELDS` 子集 PUT 仍然 OK。

### 7.2 前端 / 集成

- [tests/test_material_roas_frontend.py](../../../tests/test_material_roas_frontend.py) 扩展：
  - 模板渲染 partial 后字段齐全。
  - 模态 head 包含胶囊跳转链接 `<a target="_blank" href="/medias/<pid>/roas">`。
- 独立页 webapp-testing（Playwright + testuser.md）冒烟（实施时跑，不进 unit test）：
  - 打开 `/medias/6/roas` → 看到产品卡 + 表单。
  - 改字段 → 等 ~1s → 状态条变「已保存 ✓」→ 重新加载页面字段保留。
  - 故意断网（chrome devtools） → 状态条变「保存失败」+ 重试按钮。
  - 模态点「在新页面打开」 → 新标签打开 `/medias/<pid>/roas`。
  - PC 端列表页和模态行为不破坏（CLAUDE.md 铁律）。

## 8. 改动清单（后续 plan 拆分用）

- [ ] 后端：新增 `roas_page` 视图 + 路由 `/medias/<int:pid>/roas`
- [ ] 模板：抽 `web/templates/medias/_roas_form.html` partial（产品卡 + 表单两栏）
- [ ] 模板：抽 `web/templates/medias/_roas_styles.html` partial（`.oc` page-scoped tokens + ROAS 专属样式），列表页和独立页都 include
- [ ] 模板：新建 `web/templates/medias/roas.html`（继承素材管理 layout）
- [ ] 模板：模态 head 增加胶囊「在新页面打开」按钮
- [ ] 模板：模态和独立页都嵌入「顶部状态条」
- [ ] JS：新建 `web/static/roas_form.js`，导出 `RoasFormController`
- [ ] JS：[medias.js:494-655](../../../web/static/medias.js#L494-L655) 改造 `openRoasModal/saveRoas` → 复用 `RoasFormController`
- [ ] 模板：模态 footer 改造——**删除**「保存」按钮 + `#roasSaveMsg`（保存提示由顶部状态条接管）；**保留**「关闭」按钮（原 `#roasCancelBtn` 重命名为「关闭」并保留功能）；「计算 ROAS」按钮仍在结果区作兜底
- [ ] CSS：新增 `.oc-btn.pill`、`.oc-roas-status-bar` 样式（走 token，禁紫色）
- [ ] 测试：后端路由、partial 渲染单元测试
- [ ] 测试：webapp-testing 自动化冒烟

## 9. 风险与缓解

| 风险 | 缓解 |
|----|------|
| 抽 partial 时破坏现有模态 | partial 抽出后用 webapp-testing 跑一次模态原行为冒烟（开模态、填字段、点计算 ROAS、保存）确认无回归 |
| 自动保存触发频繁导致后端压力 | 600ms debounce + last-write-wins 单飞机制；后端 PUT 已有，未改 SQL 路径 |
| 前端 last-write-wins 漏触发 | 控制器单元测试覆盖：连续 5 次输入应只发 1 次请求；in-flight 时新输入应排队，飞回后立即重发 |
| 独立路由权限/数据穿越 | 路由内 `_can_access_product` 校验，与 cover 路由一致 |
| 模态和独立页双份状态条 / 跳转按钮在小屏幕样式不一致 | 仅 < 1024px 单列堆叠；> 1024px 不动 PC 布局；CSS 严格包在 `@media (max-width: 1024px)` 内 |

## 10. 不在范围内（YAGNI）

- 不做产品上下/左右切换（独立页只是单产品）。
- 不做 ROAS 历史版本、变更审计。
- 不改保本 ROAS 计算公式 / 汇率配置 UI。
- 不做权限分级（沿用现有 `_can_access_product`）。
- 不做移动端原生 App 路由（仅响应式 web）。
