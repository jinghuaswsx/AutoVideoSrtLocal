# 选品中心顶部 Tab 统一设计

最后更新：2026-05-18

## 背景

选品中心已有五个一级页面：明空选品、Meta 热帖、TABCUT、今日推荐、新品选择。当前明空选品和新品选择使用 `oc-page-tabs / oc-page-tab`，其余页面使用各自的 `mh-*`、`tabcut-*`、`tr-*` Tab 样式。用户反馈同一个选品中心内按钮风格和点击后的加载体验不一致，希望统一到点击 Meta 热帖按钮后的显示风格和页面加载逻辑。

## 目标

- 五个选品中心一级页面顶部 Tab 使用同一套 DOM 结构和 CSS 类。
- 统一后的视觉以 Meta 热帖页当前顶部 Tab 为基准：圆角胶囊容器、白/浅灰背景、活动项为绿色实底。
- 点击 Tab 仍然跳转到对应 `/xuanpin/*` 页面，由目标页面保留现有初始化加载逻辑。
- 每个页面只声明当前 active 项，避免五份模板继续复制不同样式。

## 非目标

- 不改 `/xuanpin/api/*` 接口。
- 不改权限逻辑或默认入口选择。
- 不把五个页面改成单页内 Ajax 切换。
- 不改 Meta 热帖页内部子 Tab、筛选器、卡片列表和分页逻辑。

## 设计

- 新增共享模板片段 `web/templates/_xuanpin_tabs.html` 和样式片段 `web/templates/_xuanpin_tabs_style.html`。
- 片段接收 `active` 值，渲染五个一级入口：
  - `mk` → `/xuanpin/mk`
  - `meta-hot-posts` → `/xuanpin/meta-hot-posts`
  - `tabcut` → `/xuanpin/tabcut`
  - `today-recommendations` → `/xuanpin/today-recommendations`
  - `new-products` → `/xuanpin/new-products`
- 导航片段使用统一类名 `xuanpin-tabs / xuanpin-tab`，并保持 `role="tablist"`、`role="tab"`、`aria-selected`。
- 样式片段由五个页面在 `extra_style` 中 include，导航片段由内容区 include。样式值以 Meta 热帖页现状为基准，保持 Ocean Blue 规则中的非紫色调。
- 明空选品、新品选择、TABCUT、今日推荐删除各自顶部 Tab 样式和 markup；Meta 热帖页改用共享片段，但保留页面初始化 `initMetaHotSubtabFromHash()`，所以点击 Meta 热帖后的默认加载仍为素材库，hash 仍可进入子 tab。

## 验收

- `/xuanpin/mk`、`/xuanpin/meta-hot-posts`、`/xuanpin/tabcut`、`/xuanpin/today-recommendations`、`/xuanpin/new-products` 的顶部 Tab markup 一致。
- 明空选品、新品选择不再出现 `oc-page-tabs / oc-page-tab`。
- TABCUT 不再出现 `tabcut-tabs / tabcut-tab-link`。
- 今日推荐不再出现 `tr-tabs / tr-tab`。
- Meta 热帖页不再维护独立 `mh-tabs / mh-tab` 作为一级 Tab。
- 相关路由测试通过。

## 验证

- `pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py tests/test_meta_hot_posts_routes.py tests/test_tabcut_selection_routes.py -q`
- 如需人工验收，登录后依次打开五个 `/xuanpin/*` 页面，观察顶部 Tab 样式一致，点击后页面正常按目标页自身逻辑加载。
