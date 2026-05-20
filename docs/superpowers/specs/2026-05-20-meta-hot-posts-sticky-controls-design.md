# Meta 热帖顶部操作条吸顶设计

日期：2026-05-20

## 背景

`/xuanpin/meta-hot-posts` 的素材卡片列表较长，运营上下滑动时，卡片区上方的当前页摘要、AI 分析显示开关、卡片放大按钮和顶部分页控件会离开视口。用户需要频繁回到列表顶部才能切换分析显示、调整卡片放大或翻页，操作成本高。

## 目标

- 将卡片区上方的状态摘要、AI 分析显示按钮、卡片放大按钮和顶部分页控件固定在页面顶部栏下方。
- 固定区域只作用于 Meta 热帖页内部，不影响选品中心一级 Tab、Meta 子 Tab、工具按钮和筛选工具栏。
- 保留底部分页控件，长列表底部仍可直接翻页。
- 保留现有分页、AI 分析显示、卡片放大、收藏夹排序和产品列表逻辑。

## 非目标

- 不改 `/xuanpin/api/*` 接口。
- 不调整筛选项、排序、分页口径或卡片数据结构。
- 不把筛选工具栏一起固定，避免占用过多纵向空间。
- 不新增桌面端之外的独立浮动复制工具条。

## 设计

- 在 `web/templates/meta_hot_posts.html` 中，把现有 `.mh-status` 和 `#mhPagerTop` 包进新的 `.mh-sticky-controls` 容器。
- `.mh-sticky-controls` 使用 `position:sticky`，桌面端 `top:68px`，移动端 `top:60px`，位于共享 `.topbar` 下方并保留少量间距。
- 固定区域使用不透明背景、边框、8px 圆角和合适的 `z-index`，避免滚动内容从底下透出；颜色沿用 Meta 热帖页当前的绿色/浅灰视觉，不引入新色板。
- `.mh-status` 在 sticky 容器内取消外侧底部 margin，`#mhPagerTop` 仍由现有 `renderMetaHotPager()` 更新，不复制分页状态。
- 移动端保持现有分页横向滚动和隐藏卡片放大按钮的行为；sticky 容器允许内容换行，避免按钮和分页重叠。

## 验收

- 向下滚动 `/xuanpin/meta-hot-posts#library` 时，当前页摘要、美国/欧洲 AI 分析按钮、卡片放大按钮和顶部分页仍停留在视口顶部栏下方。
- 顶部固定区域不遮挡已有 `.topbar`，也不覆盖卡片首行内容。
- 点击顶部分页、AI 分析显示按钮和卡片放大按钮仍调用现有函数。
- 底部分页仍存在。
- 产品列表、收藏夹、今日新增和 Top50 子 Tab 不因 sticky 容器产生空白或重叠。

## 验证

- `pytest tests/test_meta_hot_posts_routes.py tests/test_xuanpin_routes.py -q`
- 未登录访问 `/xuanpin/meta-hot-posts` 返回 302。
- 登录后人工滚动确认顶部操作条吸顶，按钮和分页可点击。
