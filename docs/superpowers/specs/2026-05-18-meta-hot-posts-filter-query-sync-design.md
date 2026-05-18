# Meta 热帖筛选 URL 同步设计

日期：2026-05-18

## 背景

Meta 热帖素材库的筛选工具栏已经把筛选项传给 `/xuanpin/api/meta-hot-posts`，但浏览器页面地址没有同步这些筛选参数。用户点击“筛选”后刷新页面，筛选控件回到默认值，列表也按默认条件加载。

## 口径

- 只作用于 Meta 热帖“素材库”筛选工具栏。
- 点击“筛选”、分页、重置时，同步页面地址栏的 GET 查询参数。
- 页面刷新时，从 GET 查询参数恢复筛选控件值，并按查询参数里的 `page` 加载素材库。
- API 请求仍使用现有参数名：`category`、`mark_status`、`min_price`、`max_price`、`min_interactions`、`min_comments`、`created_from`、`created_to`、`page`、`page_size`。
- 页面地址栏只保留用户可理解的筛选项和非首页 `page`，不暴露固定的 `page_size`。

## 验收

- 访问 `/xuanpin/meta-hot-posts?category=Kitchenware&mark_status=ok&page=3` 时，筛选控件恢复对应值，首轮列表加载第 3 页。
- 点击“筛选”后，地址栏包含当前非空筛选项。
- 点击“重置”后，筛选项清空，地址栏查询参数同步清空。
