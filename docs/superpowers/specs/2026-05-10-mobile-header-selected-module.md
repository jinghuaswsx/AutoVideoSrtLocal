# 移动端头部显示当前模块

## 背景

移动端全局壳由 `docs/superpowers/specs/2026-05-01-mobile-ios-responsive-design.md` 定义，当前 `web/templates/layout.html` 在移动顶栏左侧固定显示 `🎬 AutoVideoSrt`。用户在手机上进入「数据分析」等页面时，头部仍显示主站品牌，无法直接看出当前所在模块。

左侧导航已经在每个页面给当前模块菜单项加 `active`，其中包含模块图标和模块名。这是当前模块显示的事实来源。

## 目标

- 移动端顶栏左侧显示当前左侧菜单 `active` 项的图标和模块名。
- 当前页面没有 active 菜单时，继续显示 `🎬 AutoVideoSrt` 作为兜底。
- 桌面端保持现状：仍显示页面 `page_title`，不显示移动品牌块。
- 不新增后端路由、权限、业务逻辑或接口。

## 设计

在 `layout.html` 内保留现有 `.topbar-mobile-brand` 元素作为兜底，然后用一个小的页内脚本读取 `.sidebar-nav a.active`：

- 复制 active 菜单内 `.nav-icon` 的文本到移动头部图标。
- 从 active 菜单中移除 `.nav-icon`、`.sidebar-group-caret` 后提取剩余文本，写入移动头部模块名。
- 若 active 菜单有 `href`，同步到移动头部链接，避免头部显示模块名但点击回首页。
- 若页面没有 active 菜单、图标为空或标题为空，不覆盖兜底。

移动端 CSS 给模块名增加单行省略，防止长模块名挤压主题和退出按钮。

## 验证

- 新增静态模板测试，检查移动头部具备可更新的图标/文字节点、脚本读取 active 菜单并同步文本、图标和链接。
- 运行相关 layout/menu 测试。
- 如启动 dev server，移动 viewport 访问 `/order-analytics` 时头部应显示 `📊 数据分析`。
