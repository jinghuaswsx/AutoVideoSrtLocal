# 产品售价与用户运费批量填充

日期：2026-06-12

## 背景

素材管理 ROAS 口径已经以产品级售价和用户支付运费作为独立站收入基础。当前生产库中仍有一批产品缺少 `standalone_price`、`tk_sale_price` 或 `standalone_shipping_fee`，导致产品级保本 ROAS 无法稳定展示。

运营确认本次先补齐所有产品的三个基础数字：

- 实际售价：用于 `standalone_price`。
- 产品售价：用于 `tk_sale_price`。
- 用户运费：用于 `standalone_shipping_fee`。

后续用户运费统一按 `$7` 填充；ROAS 计算中空运费也继续按 `$7` 兜底，保持与现有素材 ROAS 口径一致。

## 上位锚点

- [素材管理 ROAS 维护设计](2026-04-28-material-roas-design.md)：独立站收入为售价 + 用户支付运费，空运费按 `$7` 兜底。
- [产品链接管理弹窗](2026-05-09-product-link-management-modal.md)：英语链接也纳入产品链接管理。
- [产品链接默认域名](2026-05-09-product-link-default-domain.md)：系统可由产品启用域名解析英语产品页。
- [产品链接 GET 参数污染修复](2026-06-09-product-link-query-param-repair-design.md)：产品页 URL 入库和解析时去除 query/fragment。

## 填充规则

### 价格范围

- 产品售价必须在 `$10` 到 `$50` 之间，含边界。
- 用户支付运费必须在 `$5` 到 `$20` 之间，含边界。
- 本次统一写入用户支付运费 `$7`，天然落在允许范围内。
- 第一个 SKU 价格低于 `$10` 或高于 `$50` 时跳过该产品，并在报告中记录 `out_of_range`，不做截断、不写边界值。
- 2026-06-13 用户确认例外：首轮剩余的 7 个 `out_of_range` 产品也按英语链接第一个 SKU 实际价格写入，不截断到边界；本例外只用于完成本次历史数据补齐，后续批量工具默认仍按 `$10` 到 `$50` 范围跳过越界价格。

### 价格来源

访问产品英语链接，解析公开 Shopify 产品 JSON：

- 优先访问 `media_products.product_link`，当它是有效 `/products/` 链接时视为产品原始英语链接。
- 再访问 `appcore.product_link_domains.resolve_product_page_url_rows(product, "en")` 解析出的英语链接，按默认域名优先顺序尝试。
- 每个候选链接依次尝试 `.js` 和 `.json` 产品 JSON。
- 取返回 `variants` 列表中的第一条 variant 作为“第一个 SKU”。
- 第一个 SKU 的 `price` 作为产品定价，写入 `standalone_price` 和 `tk_sale_price`。

### 写库语义

默认只补空值：

- `standalone_price = COALESCE(standalone_price, first_sku_price)`
- `tk_sale_price = COALESCE(tk_sale_price, first_sku_price)`
- `standalone_shipping_fee = COALESCE(standalone_shipping_fee, 7.00)`

只有显式 `--force` 时才覆盖已有非空值。

本次用户要求“完成所有产品”的当前售价和统一用户运费，因此正式全量执行使用 `--force --apply`：让 `standalone_price` 与 `tk_sale_price` 都等于英语链接第一个 SKU 的当前价格，并把 `standalone_shipping_fee` 统一为 `$7`。默认只补空值模式保留为安全 dry-run / 分批排查能力。

## 工具要求

新增一次性批处理工具：

```bash
.venv/bin/python tools/product_price_shipping_fill.py --dry-run
.venv/bin/python tools/product_price_shipping_fill.py --limit 50 --offset 0 --dry-run
.venv/bin/python tools/product_price_shipping_fill.py --apply
```

要求：

- 默认 dry-run；必须带 `--apply` 才写库。
- 支持 `--limit`、`--offset`、`--product-id`，便于分批和失败复跑。
- 支持 `--force` 覆盖已有值。
- 支持低并发和单产品短超时，避免公开 Shopify 接口或外部域名卡死全量任务。
- 输出 JSON summary，包含扫描数、候选数、写入数、跳过原因分布和失败样例。
- 网络失败、无 variants、无价格、价格越界都只记录并跳过，不影响其它产品继续处理。

## 非目标

- 不新增数据库字段。
- 不修改素材管理 UI。
- 不改变 `appcore/product_roas.py` 的 ROAS 计算公式。
- 不改变订单利润、SKU 订单保本 ROAS、Shopify 订单历史回填逻辑。
- 不注册定时任务；本次是一次性数据补齐工具。

## 验证

- 单元测试覆盖链接优先级、价格范围、默认 COALESCE 写库、`--force` 覆盖、dry-run 不写库。
- 运行 focused tests，不跑全量 pytest。
- 正式写库前必须先运行 dry-run，确认 `out_of_range`、`no_variants`、`fetch_error` 等跳过项数量。
- 正式写库后复查 `media_products` 中三个字段的缺失数量，并抽样核对写入价格来源。
