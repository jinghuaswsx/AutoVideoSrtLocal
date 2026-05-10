# 数据分析移动端业务按钮位置修正

- 日期：2026-05-10
- 范围：`/order-analytics` 数据分析页
- 文档锚点：`AGENTS.md` 文档驱动代码、`docs/superpowers/specs/2026-05-01-mobile-ios-responsive-design.md` 移动端全局壳、`docs/superpowers/specs/2026-05-10-mobile-header-selected-module.md` 移动端头部模块名、`web/static/CLAUDE.md` Ocean Blue 控件约束。

## 背景

数据分析页桌面端在右上角提供两个业务入口：`Payments 导入` 与 `产品盈亏报表`。移动端全局头部已经承担导航菜单、当前模块名、主题切换和退出入口，这两个业务按钮继续显示在全局头部右侧时，会挤在浏览器地址栏下方的最顶端，弱化当前模块头部，也占用安全区附近的横向空间。

## 目标

1. 桌面端保留两个按钮在数据分析页右上角。
2. 移动端不在全局顶栏展示这两个业务按钮。
3. 移动端在数据分析内容区顶部展示同样两个业务入口，用户进入页面后仍能直接访问。
4. 不改变两个按钮的 `data-ppr` 行为、对话框、路由、权限和接口。

## 设计

- 保留现有 `topbar_actions` 内 `.ppr-actions` 作为桌面入口。
- 在 `content` 开头新增 `.ppr-mobile-actions`，复用相同 `data-ppr="open-import"` 与 `data-ppr="open-report"` 按钮。
- CSS 默认隐藏 `.ppr-mobile-actions`；`max-width: 768px` 时显示为内容区顶部的两列操作条。
- `max-width: 768px` 时隐藏顶栏内 `.ppr-actions`，避免业务按钮挤在全局头部最上方。
- 移动端按钮保留图标并显示短文本，避免两个无文字图标含义不清。

## 验收

1. 桌面端 `.topbar` 里仍有 `Payments 导入` 和 `产品盈亏报表` 两个按钮。
2. 移动端 `.topbar .ppr-actions` 被隐藏。
3. 移动端内容区顶部存在 `.ppr-mobile-actions`，含两个同款 `data-ppr` 入口。
4. 点击移动端两个按钮仍打开原有导入 / 报表对话框。
