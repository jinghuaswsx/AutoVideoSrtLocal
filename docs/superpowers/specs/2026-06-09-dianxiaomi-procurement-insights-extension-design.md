# 店小秘采购洞察 Chrome 插件首版设计

日期：2026-06-09

## 背景

运营在店小秘后台进入“店小秘云仓”的“采购建议”或“缺货建议”页面时，需要快速判断当前商品是否值得采购、采购数量是否需要放大或收缩。现有 AutoVideoSrtLocal 已经同步店小秘订单、店小秘云仓 SKU、Meta 广告消耗和素材产品投放状态，但这些数据散落在后台页面中，无法直接叠加到店小秘采购页面。

首版目标是在 Chrome 插件里读取当前店小秘页面可见的 SKU / 商品线索，请求 AutoVideoSrtLocal 后端，显示最小决策数据。
2026-06-10 追加目标：运营点击“一键生成采购订单”并出现“生成采购单”弹窗后，插件必须把采购洞察显示在弹窗右侧到屏幕最右边的区域，面板顶部和高度贴齐弹窗，并优先从弹窗内商品行确定性锁定产品。

## 事实来源

- `AGENTS.md`：新代码必须先有文档锚点；工具放在 `tools/`；涉及数据分析接口需遵守权限和数据质量口径。
- `docs/analytics-data-quality-guardrails.md`：数据分析类 JSON 顶层应带 `data_quality`，前端缺失时不得默认为 ok。
- `docs/superpowers/specs/2026-04-28-dianxiaomi-order-import-design.md`：店小秘订单明细表 `dianxiaomi_order_lines` 是订单事实来源，产品匹配可使用 `product_id`、`product_display_sku`、`product_sku`、`product_sub_sku`。
- `docs/superpowers/specs/2026-05-28-medias-product-ad-status-cache-design.md`：产品级真实 ROAS、广告消耗和投放状态优先读取 `media_product_ad_summary_cache`。
- `docs/superpowers/specs/2026-06-06-medias-product-video-workbench-design.md`：产品订单窗口数据和语种广告订单汇总可复用 `appcore.media_product_ad_orders_report.get_product_ad_orders_report`。
- `docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md`：SKU 订单保本 ROAS 和云仓 SKU 口径已存在，首版不重新计算。
- `docs/superpowers/specs/2026-06-05-xmyc-retirement-dianxiaomi-yuncang-design.md`：采购价来源统一为店小秘云仓 `dianxiaomi_yuncang_skus`，不再读取 xmyc。

## 目标

1. 在 `tools/dianxiaomi_procurement_insights/` 下新增 Chrome MV3 插件。
2. 插件注入 `dianxiaomi.com` 页面，显示一个可折叠的采购洞察面板。
3. 插件首版用通用 DOM 规则读取当前页面或当前鼠标所在表格行里的 SKU、商品 SKU、SKU 编码、商品名、商品 code 等线索。
4. 后端提供登录保护的只读接口，按线索匹配素材产品，并返回采购辅助数据。
5. 首版核心展示：
   - 是否在投：投放中 / 已停投 / 从未投放。
   - 今天、昨天、最近 7 天订单量。
   - 产品整体真实 ROAS。
6. 响应带匹配证据与 `data_quality`，方便明天按真实页面继续调试。
7. 弹窗模式核心展示：
   - 总消耗、总 ROAS、总订单量作为主核心指标，字体约为普通指标 2 倍、蓝色加粗。
   - 今天、昨天、7 天、30 天按表格展示订单、消耗、ROAS；每行订单量为核心，蓝色加粗。
   - 弹窗可见时，展示区域贴着弹窗右侧，高度与弹窗一致；弹窗不可见时才回退为原右侧浮动紧凑面板。

## 非目标

- 首版不改店小秘自动同步、订单导入、Meta 同步、缓存刷新定时任务。
- 首版不新增数据库表或迁移。
- 首版不在插件里计算 ROAS 或订单量；插件只采集线索和展示后端结果。
- 首版不承诺精确解析所有“采购建议 / 缺货建议”页面的私有 DOM；但“生成采购单”弹窗是已确认入口，必须提供页面专用选择器与通用兜底规则。
- 首版不绕过 AutoVideoSrtLocal 登录权限；未登录时插件提示用户先登录后台。

## 后端接口

新增蓝图：

```text
GET /dianxiaomi-procurement-insights/api/insights
GET /dianxiaomi-procurement-insights/api/health
```

权限：

- `@login_required`
- `@permission_required("data_analytics")`

请求参数：

| 参数 | 说明 |
| --- | --- |
| `sku` / `skus` | 店小秘云仓 SKU 或页面抽取出的候选 SKU，`skus` 支持逗号分隔 |
| `product_sku` | 店小秘商品 SKU |
| `sku_code` | 店小秘 SKU 编码 / 商家编码 |
| `shopify_product_id` | Shopify product ID，如页面可见 |
| `product_code` | 素材库 product code / 商品 handle，如页面可见 |
| `product_name` | 页面可见商品名，用作低置信度兜底 |
| `page_url` | 当前店小秘页面 URL，用于调试证据 |

匹配顺序：

1. `product_id` 显式参数，仅用于调试。
2. `shopify_product_id` 匹配 `media_product_shopify_ids.shopify_product_id` 或 `media_products.shopifyid`。
3. SKU 精确匹配 `media_product_skus.dianxiaomi_sku`、`dianxiaomi_product_sku`、`dianxiaomi_sku_code`、`shopify_sku`。
4. SKU 精确匹配历史订单行 `dianxiaomi_order_lines.product_display_sku`、`product_sku`、`product_sub_sku`。
5. `product_code` 精确匹配 `media_products.product_code`。
6. `product_name` 只作为最后低置信度兜底，并在响应中标记匹配方式。

返回核心结构：

```json
{
  "ok": true,
  "matched": true,
  "product": {
    "id": 123,
    "name": "Demo",
    "product_code": "demo-rjc",
    "match_method": "media_product_skus.dianxiaomi_sku",
    "match_confidence": "high"
  },
  "summary": {
    "delivery_status": "active",
    "delivery_label": "投放中",
    "orders": {"today": 1, "yesterday": 2, "last_7d": 8, "last_30d": 22},
    "total_orders": 42,
    "true_roas": 2.31,
    "ad_spend_usd": 120.5,
    "total_revenue_usd": 278.3,
    "periods": {
      "today": {"label": "今天", "orders": 1, "ad_spend_usd": 12.3, "roas": 1.23},
      "yesterday": {"label": "昨天", "orders": 2, "ad_spend_usd": 23.4, "roas": 2.34},
      "last_7d": {"label": "7天", "orders": 8, "ad_spend_usd": 88.8, "roas": 2.1},
      "last_30d": {"label": "30天", "orders": 22, "ad_spend_usd": 320.5, "roas": 1.9}
    },
    "computed_at": "2026-06-09T12:00:00"
  },
  "markets": [],
  "data_quality": {"status": "ok"}
}
```

## 插件交互

- 插件默认在店小秘页面右侧显示紧凑面板。
- 面板有刷新按钮，刷新时读取当前鼠标所在行；如果没有行，则读取页面可见文本的候选线索。
- “生成采购单”弹窗可见时，插件优先读取弹窗内的 SKU、商品中文名、商品图片与商品行文本，并用这些线索请求后端；弹窗线索优先级高于鼠标所在行和整页扫描。
- “生成采购单”弹窗可见时，插件面板进入弹窗锚定模式：左边贴弹窗右侧，右边贴近视口右侧，顶部与弹窗顶部一致，高度与弹窗一致，内容区内部滚动。
- 面板保存后端地址，默认指向生产环境 `http://172.16.254.106`。
- 后端未登录或权限不足时，面板显示需要登录后台。
- 插件 popup 提供后端地址保存、测试连接和打开后台入口。

## 数据口径

- 投放状态、真实 ROAS、总收入、总广告消耗：优先读取 `appcore.media_product_ad_status_cache.get_product_ad_summary_cache`。
- 订单窗口：读取 `appcore.media_product_order_stats.get_product_order_stats`。
- 总订单量与今天 / 昨天 / 7 天 / 30 天的订单、消耗、ROAS：读取 `appcore.media_product_ad_orders_report.get_product_ad_orders_report` 的 `total` 行；如果订单窗口服务已有更新但广告订单报告缺少对应订单字段，可用 `media_product_order_stats` 订单数补齐。
- 市场/语种明细：读取 `appcore.media_product_ad_orders_report.get_product_ad_orders_report`，首版按语种市场组展示，不承诺等价于 Meta 精确 geo breakdown。
- 市场/语种明细中的投放状态必须与素材管理页同源，优先读取 `appcore.media_product_ad_status_cache.get_product_lang_ad_summary_cache` 的 `delivery_status`；订单、消耗、ROAS 仍读取广告订单报告。这样 `today_spend=0` 但近 7 天仍有活跃消耗的市场不会被误判为已停投。
- `data_quality`：无匹配、缓存缺失、低置信度匹配均返回 warning。

## 验证

- 服务层测试覆盖 SKU 匹配、product code 兜底、无匹配响应、汇总数据组装。
- 路由测试覆盖登录后 GET 接口返回 JSON。
- 插件静态测试覆盖 manifest host permissions、content script、background fetch 入口、弹窗优先线索采集、弹窗锚定布局和周期表格关键 CSS。
- 聚焦验证优先运行：

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

若 selector 无法覆盖新文件，运行：

```bash
pytest tests/test_dianxiaomi_procurement_insights.py tests/test_media_product_ad_orders_report.py tests/test_media_product_ad_status_cache.py -q
```

## 发布接入

首版插件按 `docs/superpowers/specs/2026-06-09-chrome-extension-tool-release-standard.md` 发布：

- release setting key：`dianxiaomi_procurement_insights_extension_release`
- 线上 zip：`/static/downloads/tools/DianxiaomiProcurementInsights-chrome-<version>.zip`
- 素材管理页下载按钮放在“下载自动换图工具”左侧
- 版本号来自 `tools/dianxiaomi_procurement_insights/version.py` 与 `chrome_ext/manifest.json`，二者必须一致
- 发布脚本：`scripts/build_chrome_extension_release.py --release-standard-read --tool dianxiaomi_procurement_insights --version <version>`

## 2026-06-10 1.1.1 弹窗自动锚定修订

- 用户点击“生成采购单 / 一键生成采购订单”后，插件必须在弹窗出现后自动进入右侧锚定模式；不能依赖用户手动刷新插件面板。
- 内容脚本保留 MutationObserver / resize / scroll 触发，同时增加低频定位心跳，确保店小秘弹窗动画、遮罩层切换或异步渲染完成后，面板仍会贴到弹窗右侧并保持同高。
- 已匹配产品且存在 `product_code` 时，产品标题行右侧显示两个跳转按钮：`产品中心` 打开 `http://172.16.254.106/medias/?q=<product_code>`，`订单中心` 打开 `http://172.16.254.106/order-analytics/dxm-orders-view/order-trend/<product_code>`。

## 2026-06-11 V1.2 数字短横线 SKU 修订

- 店小秘云仓弹窗中 `0427-16411412`、`0427-16415934` 这类纯数字加短横线的商品 SKU，必须作为 `skus` 线索传给后端；不能因为没有英文字母而被前端过滤。
- 验收样例：弹窗商品“免打孔壁挂拖把架”应通过上述 SKU 匹配到素材库产品 `drill-free-wall-mop-holder-rjc`，展示其真实消耗、ROAS 和订单数据。
- 店小秘“仓库 SKU 列表”存在多种 SKU 形态，插件前端候选提取必须同时兼容：长数字 SKU（如 `46081368686765`）、数字短横线 SKU（如 `0311-17187365`）、数字短横线带短后缀（如 `0514-16428715-2`）、长数字带短横线后缀（如 `45807908847785-1`、`159464308921378826-99`）、字母前缀数字 SKU（如 `YI21513591334`、`HP2472035`），以及多段字母数字 SKU（如 `PM1999041-RED-L`、`DG250315033-heiSkoda-1`、`2305171755-2p-Lada`）。
- 前端兼容应优先读取弹窗或商品行的行首 SKU，并过滤明显不是 SKU 的尺寸、单位、日期和价格噪声（如 `3XL`、`100cm`、`2026-06-11`、`10.50`），避免扩大误匹配面。
