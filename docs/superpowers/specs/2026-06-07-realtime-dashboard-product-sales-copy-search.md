# 实时大盘产品销量名称与 code 操作

## 背景

数据分析「实时大盘」的「产品销量」子 tab 当前把中文产品名和商品 product code 拼在同一行展示，例如：

```text
隐形眼镜清洗器 · sonic-lens-refresher-rjc
```

运营查看销量后经常需要分别复制中文产品名、复制 product code，或跳到「素材管理」按 product code 查找产品。当前同一行文本不利于移动端阅读，也缺少行内操作入口。

## 目标

1. 仅调整「数据分析 → 实时大盘 → 产品销量」表格的「产品」列展示。
2. 中文产品名与 product code 分两行显示。
3. 每一行文本后跟一个复制图标按钮，分别复制中文产品名与 product code。
4. product code 的复制按钮后再跟一个放大镜图标，点击打开 `/medias/?q=<product_code>`，让素材管理按该 code 自动搜索。

## 设计

- 后端接口不变，继续使用 `product_sales_stats` 中的 `product_name`、`product_code`、`product_id` 等字段。
- 前端新增产品销量专用单元格渲染函数，替代原来的 `product_name + " · " + product_code` 文本拼接。
- 复制按钮使用 icon-only 按钮样式和浏览器 `navigator.clipboard`，并保留 `document.execCommand("copy")` fallback，以兼容非 secure context。
- 素材管理跳转沿用仓库既有搜索约定：`/medias/?q=<product_code>`。
- 不改「新品投放分析」的产品销量表格，避免扩大本次实时大盘需求范围。

## 验证

- 静态回归：`tests/test_order_analytics_template_layout.py` 校验实时大盘产品销量渲染调用、复制按钮属性、素材管理搜索链接与图标样式。
- 相关测试：`pytest tests/test_order_analytics_template_layout.py -q`。

## Docs-anchor

- `AGENTS.md`
- `appcore/order_analytics/CLAUDE.md`
- `web/static/CLAUDE.md`
- `docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`
- `docs/superpowers/specs/2026-05-10-mobile-realtime-table-alignment.md`
