# 选品中心分页跳页输入设计

最后更新：2026-05-20

## 背景

选品中心多个列表已经具备分页按钮，但只能通过首页、上一页、页码按钮、下一页或末页逐步导航。运营在页数较多时需要直接输入页码并回车跳转。

现有分页入口分散在三个页面：

- `/xuanpin/mk`：产品库、视频素材库、昨天消耗前100。
- `/xuanpin/meta-hot-posts`：素材库、今日新增、我的收藏夹、产品列表、产品帖子。
- `/xuanpin/tabcut`：视频榜、商品榜。

今日推荐和新品选择当前模板没有分页组件，本次不新增新的分页结构。

## 目标

1. 选品中心所有现有分页组件都增加 `去 [页码] 页` 输入区。
2. 用户在输入框输入页码后按回车，跳转到对应页。
3. 页码输入自动限制在 `1..totalPages` 范围内。
4. 非数字、空值或小于 1 的输入跳到第 1 页；超过总页数跳到最后一页。
5. 同一列表的顶部和底部分页保持一致，任意一处输入都调用同一个加载函数。
6. 不改 `/xuanpin/api/*` 接口，不改分页大小、排序、筛选或权限逻辑。

## 设计

### 明空选品

`web/templates/mk_selection.html` 保留现有三个分页渲染入口：

- 产品库分页：`loadData(page)`。
- 视频素材库分页：`renderMkArchivePager(..., "loadMkLocalMaterialLibrary", page, total)`。
- 昨天消耗前100分页：`renderMkArchivePager(..., "loadMkYesterdayTop100", page, total)`。

新增小型页码输入渲染函数或内联片段，输出 `去 <input type="number"> 页`。输入框带当前页默认值、`min="1"`、`max=totalPages`，回车时读取值、归一化并调用对应 loader。

### Meta 热帖

`renderMetaHotPager(data, loaderName)` 是 Meta 热帖各分页列表的共享入口。本次在该函数末尾追加跳页输入，复用当前 `loaderName`：

- `loadMetaHotPosts`
- `loadTodayNewMaterials`
- `loadFavoriteMetaHotPosts`
- `loadMetaHotProductList`
- `loadCurrentMetaHotProductPosts`

顶部 `mhPagerTop` 和底部 `mhPagerBottom` 渲染同一份跳页输入。

### TABCUT

`renderPager(data)` 追加跳页输入。回车调用 `loadTabcut(normalizedPage)`，沿用当前 `tabcutView`、筛选条件、排序和 page size。

## UI 细节

- 输入框保持紧凑，不打断既有分页按钮布局。
- 文案统一为 `去`、`页`。
- 输入框宽度固定，使用当前页面分页样式体系：
  - 明空页使用 `.oc-pager` 下的输入样式。
  - Meta 热帖使用 `.mh-pager` 下的输入样式。
  - TABCUT 使用 `.tabcut-pager` 下的输入样式。
- 移动端保持可换行或横向滚动的现有分页行为，不新增遮挡式控件。

## 验收

- `/xuanpin/mk` 的产品库、视频素材库、昨天消耗前100 分页都包含跳页输入。
- `/xuanpin/meta-hot-posts` 的共享分页函数包含跳页输入，并对所有调用该函数的列表生效。
- `/xuanpin/tabcut` 的分页包含跳页输入。
- 回车跳转会 clamp 页码，不会发出 0、负数或超过最后页的请求。
- 不访问 Windows 本机 MySQL。

## 验证

```bash
pytest tests/test_mk_selection_routes.py tests/test_meta_hot_posts_routes.py tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py -q
```
