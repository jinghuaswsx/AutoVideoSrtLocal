# 选品中心一级路由设计

## 背景

明空选品、新品选择、TABCUT 都属于“选品中心”，但历史上页面分散在 `/medias/*` 与 `/new-product-review/*` 下。TABCUT 上线后继续挂在 `/medias/tabcut-selection` 会让导航、权限和后续扩展边界变得混乱。

## 目标

- 新增一级路由 `/xuanpin` 作为选品中心入口。
- 页面入口统一为 `/xuanpin/mk`、`/xuanpin/new-products`、`/xuanpin/tabcut`。
- API 新增同域别名 `/xuanpin/api/*`，旧 API 路径继续可用，避免已有调用断开。
- 旧页面路径做 302 兼容跳转，用户地址栏最终落到 `/xuanpin/*`。

## 设计

- 新增 `web.routes.xuanpin` 蓝图，`url_prefix="/xuanpin"`。
- 页面路由只负责权限和渲染/委托，不复制业务逻辑。
- 明空 API 委托 `web.routes.medias` 已有构建函数；TABCUT API 委托 `appcore.tabcut_selection.service`；新品 API 委托 `web.routes.new_product_review` 已有处理函数。
- 模板中的 tab 链接与 fetch 地址全部改为 `/xuanpin/*`。
- 旧页面 `/medias/mk-selection`、`/medias/tabcut-selection`、`/new-product-review/` 返回 302；旧 API 不跳转。

## 验收

- `/xuanpin/mk`、`/xuanpin/new-products`、`/xuanpin/tabcut` 管理员可访问，普通用户禁止访问。
- `/xuanpin/meta-hot-posts` 管理员或拥有 `meta_hot_posts` 权限的分析用户可访问；分析用户登录首页应落到 `/xuanpin/meta-hot-posts`，不能先跳到 admin-only 的 `/xuanpin/mk`。
- 旧页面地址自动跳到新地址。
- `/xuanpin/api/*` 与旧 API 行为一致。
- 左侧“选品中心”菜单按当前用户可访问的第一个子页决定默认链接：有 `mk_selection` 时链接到 `/xuanpin/mk`，仅有 `meta_hot_posts` 时链接到 `/xuanpin/meta-hot-posts`；新旧相关页面都能高亮。
