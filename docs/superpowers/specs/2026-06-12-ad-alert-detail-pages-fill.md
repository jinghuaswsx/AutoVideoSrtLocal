# 广告预警详情页内容验证与补全

## 问题
广告预警模块有 3 个 Level 2/3 详情页路由存在，但数据可能未完全渲染：
- `/ad-alerts/product/<id>`（`ad_alerts_product.html`）— 二级：商品下各语言预警
- `/ad-alerts/product/<id>/country/<lang>`（`ad_alerts_country.html`）— 三级：语种维度趋势
- `/ad-alerts/product/<id>/ad/<ad_code>`（`ad_alerts_ad_detail.html`）— 三级：单条 AD 详情

需要验证这三个页面内容完整，并补全缺失部分。

## 目标
确保从首页卡片 / 详情弹窗 → 商品详情 → 国家详情 → AD 详情的导航链路完整、数据展示正确。

## 要求

### 1. 验证 `/ad-alerts/product/<id>`（ad_alerts_product.html）
- 确认模板中调用了 `ad_alerts.get_product_alert_details(product_id)` 获取数据
- 确认路由 `/product/<int:product_id>` 传入了：`product_id`、`product_code`、`product_name`、`threshold`
- 渲染内容应包含：
  - 商品名和编码（顶栏）
  - 返回 `/ad-alerts/` 的链接
  - 各语言的预警卡片列表（复用 `ad_alerts.html` 中的卡片样式 `oc-ad-alert-card`）
  - 每个卡片展示：语言 badge（含严重度）、ROAS、消耗、活跃花费、预估净盈亏
  - 卡片可点击 → 跳转到 `/ad-alerts/product/<id>/country/<lang>`
- 如果模板中没有这个列表的 JS 渲染逻辑，参照 `ad_alerts.html` 的 `renderList()` + `loadList()` 模式补一个 `loadProductDetail(productId)` 函数，从 `/ad-alerts/api/product-detail/<product_id>` 获取数据并渲染

### 2. 验证 `/ad-alerts/product/<id>/country/<lang>`（ad_alerts_country.html）
- 确认路由传入了 `detail` 对象（`ad_alerts.get_alert_detail()` 的返回值）
- 渲染内容应包含：
  - 趋势 SVG 图（复用 `renderSvg()` 逻辑）
  - 研判结论卡片（严重度、趋势方向、运行阶段、建议文案）
  - 关键指标网格：ROAS、总消耗、购买价值、运行天数、活跃 7 天消耗、预估净盈亏
  - 该语言下所有 AD 列表（从 `detail.ads` 或 `/ad-alerts/api/ad-list` 获取）
  - 每行 AD：国家、AD 名称、花费、购买、ROAS、活跃天数（与详情弹窗中 `renderAdList()` 一致）
- 如果 `ad_alerts_country.html` 中缺少 AD 列表或趋势图，从详情弹窗的代码复制补全

### 3. 验证 `/ad-alerts/product/<id>/ad/<ad_code>`（ad_alerts_ad_detail.html）
- 确认路由传入了 `detail` 对象（`ad_alerts.get_ad_detail_and_trend()` 的返回值）
- 渲染内容应包含：
  - AD 名称、编码、账户信息（顶栏）
  - 关键指标网格：总花费、总购买、ROAS、活跃天数、国家、首次活跃日期、最后活跃日期
  - 趋势 SVG 图（日级别花费/购买/ROAS 趋势）
  - 从 `/ad-alerts/api/ad-detail?product_id=&ad_code=&ad_account_id=` 获取数据的 JS 加载逻辑
- 返回上级入口：链接到 `/ad-alerts/product/<id>/country/<lang>`

### 4. 导航一致性
确保三个页面之间的导航闭环：
- `ad_alerts.html` 卡片 → `/ad-alerts/product/<id>`
- `/ad-alerts/product/<id>` 卡片 → `/ad-alerts/product/<id>/country/<lang>`
- `/ad-alerts/product/<id>/country/<lang>` 中 AD 列表行 → `/ad-alerts/product/<id>/ad/<ad_code>?ad_account_id=xxx`
- 每个页面有清晰的返回按钮

### 5. 空/错误状态
每个页面都要处理：
- 数据未找到 → `abort(404)` 已在路由中实现，确认模板能正常显示 Flask 404 页面
- 数据为空（无预警、无 AD）→ 显示 "暂无 + 相关文案" 而不是空白或 JS 报错
- 加载失败 → catch 中显示 "加载失败"

## 验证
1. `pytest tests/test_ad_alert_routes.py -q` 通过
2. 手动检查（codex 可截图验证）：
   - `/ad-alerts/product/123` → 显示商品名 + 各语言预警卡片
   - 点击某个语言 → `/ad-alerts/product/123/country/en` → 趋势图 + AD 列表
   - 点击某条 AD → `/ad-alerts/product/123/ad/xxx?ad_account_id=yyy` → 单条 AD 详细趋势
   - 每个页面的返回链接可用
3. 打开不存在的商品 ID（如 999999）→ 404
