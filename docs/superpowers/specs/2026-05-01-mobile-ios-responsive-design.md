# Mobile iOS Responsive Adaptation — Design Spec

- 日期：2026-05-01
- 分支：`feature/mobile-ios-responsive`
- 目标设备：iPhone 16（393×852）、iPhone 15 Pro Max（430×932）。覆盖到 iPhone SE（375×667）以下不主动保证。
- 设计语言基线：项目已有的 Ocean Blue Admin（OKLCH，hue ∈ 200–240）。

## 1. 目标与不目标

### 目标
- 用户在 iPhone Safari 上能顺畅完成「查看 + 轻量操作」级别的工作流：登录、看推送状态、看订单/数据分析、看任务进度、浏览素材列表、看 API 账单、看视频翻译进度。
- 视觉与现有 Ocean Blue 系统连续，不引入新色板、不破坏既有桌面布局、不让任何页面在 iPhone 上「无法访问」。

### 不目标（YAGNI）
- 不做原生 App，不引入 PWA Manifest 之外的离线能力。
- 不重写任何业务逻辑、API、模板结构。
- 不为「只在 PC 才有意义」的工作台（视频创作 Workbench、翻译实验室批量编辑、声音库批量上传等）做完美移动适配，但保证页面不崩、文字可读、能横向滚动。
- 不做 iPad 专属优化（≥768px 沿用桌面布局即可）。
- 不动现有亮/暗主题切换逻辑。

## 2. 现状速查

| 维度 | 现状 |
|------|------|
| 框架 | Flask + Jinja2，70+ 模板大多 `extends "layout.html"`，登录页 `login.html` 独立 |
| 壳模板 | `web/templates/layout.html`：220px 固定侧栏 + 56px sticky 顶栏 + `main-wrap` `margin-left:220px` + `max-width:calc(100vw - 220px)` |
| 设计变量 | layout.html 用旧紫色变量；多数业务页面已有自己的 OKLCH token 命名空间（`--oc-*`、`--tc-*`、`--tools-*`、`--oa-*`、…）走 hue 200–240 |
| 已有响应式 | `@media` 出现 52 处 / 25 个文件，主要在 pushes.css / medias_list / 各模态层。覆盖零散，缺整体壳层适配 |
| 顶部交互 | 顶栏：标题（block）+ 主题切换 + 用户胶囊 + 退出按钮，PC-only 排布 |

## 3. 适配策略

### 3.1 三层改造，自上而下

1. **L1 全局壳（layout.html + 一个新 mobile.css）**：把 220px 侧栏在 `<768px` 折叠为左滑抽屉；顶栏左侧加 hamburger；`main-wrap` `margin-left:0`、`padding:16px`。
2. **L2 全局补丁（mobile.css）**：给所有页面常见的 `<table>`、`.toolbar/filters`、`<input>/<select>`、`.modal/.drawer`、`.btn` 等模式加 `@media` 补丁——表格横滚 + 表单堆叠 + 输入框 16px + 按钮 44pt 热区 + 弹窗全屏。
3. **L3 关键页面专改**：登录页、推送、数据分析、任务中心、素材管理、API 账单、视频翻译列表/详情——按各自结构在原文件里追加 `@media (max-width: 768px)` 段。

L1 + L2 是"地毯式覆盖"，保证未列入 L3 的页面也"不崩、能用"。L3 是为高频移动场景做精修。

### 3.2 断点

```
≥1024px : 桌面（保留 PC 现状）
768–1023 : 桌面（保留，但允许侧栏折叠为图标栏；先不做，YAGNI）
<768px  : 移动模式 — 侧栏抽屉化、顶栏紧凑、主内容单列
<480px  : 紧凑模式 — 表格→卡片关键页特殊改造、密度再 -20%
```

iPhone 16 / 15 Pro Max 全部落在 <480px。

### 3.3 移动壳

```
┌─────────────────────────────────────┐
│ ☰  AutoVideoSrt   …   🌗  👤        │  顶栏 52px，sticky
├─────────────────────────────────────┤
│                                       │
│           主内容（单列）              │
│                                       │
│                                       │
└─────────────────────────────────────┘
```

- 顶栏：`52px`，`padding: 0 12px`，元素：hamburger 按钮（28×28，触控热区 44×44）→ 应用名/页标题（截断）→ 弹性占位 → 主题切换按钮（图标，28×28）→ 用户头像（28×28，点击展开退出菜单）。
- 「退出登录」按钮（桌面顶栏的红色胶囊）在移动模式下隐藏，移到 hamburger 抽屉脚部（沿用现有 sidebar-footer）。
- hamburger 触发左侧滑入抽屉，宽度 `min(280px, 86vw)`，遮罩 `oklch(22% 0.02 235 / 0.45)`，点遮罩或导航项后关闭。
- 抽屉滚动隔离 `overscroll-behavior: contain`。

### 3.4 iOS 关键调优

- `<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">`
- `<meta name="theme-color" content="#1e3a8a" media="(prefers-color-scheme: light)">` + dark 等价。让 Safari 顶栏底色匹配应用品牌色。
- `body { -webkit-tap-highlight-color: transparent; }` 取消默认蓝灰高亮。
- `input, textarea, select { font-size: max(16px, 1em); }` 防 iOS Safari 自动放大缩放。
- `safe-area-inset-bottom` 给 sticky/fixed 元素：抽屉、模态、底部按钮。
- `touch-action: manipulation` 给主要可交互元素，去除 300ms 双击延迟。
- 取消桌面 hover-only 提示在移动端的副作用：`@media (hover: none) { ... }` 包裹必要的还原。

### 3.5 表格塌陷策略

通用规则（`mobile.css`）：所有 `<table>` 在 `<480px` 用包裹元素 `overflow-x: auto; -webkit-overflow-scrolling: touch`。这是兜底。

关键页特殊塌陷（在原页面 CSS 里加 `@media`）：
- **`pushes_list`**：表格 → 卡片堆叠（缩略图左 + 文字块右 + 状态徽章 + 操作按钮一行）。
- **`tasks_list`**：表格 → 卡片堆叠（任务名 + 状态徽章 + 创建时间 + 操作）。
- **`medias_list`**：已是卡片网格，调成 `grid-template-columns: repeat(2, 1fr)` 即可。
- **`order_analytics`**：tabs 已可水平滚（`oa-tabs-topbar overflow-x:auto`），各表格走兜底横滚；KPI 卡片 grid 改单列。
- **`*_translate_list / *_translate_detail`**：表格走兜底横滚；详情页两栏布局 → 单列。

## 4. 实施分阶段

### Phase 1 — 移动壳（layout.html + mobile.css + login.html）

文件：
- `web/templates/layout.html`：注入 viewport-fit + theme-color；hamburger 按钮 + JS 抽屉切换；移动模式 CSS 直接写 inline `<style>`（保持壳样式靠近壳结构）。
- `web/static/css/mobile.css`（**新文件**）：全局 `@media (max-width: 768px)` / `(max-width: 480px)` 补丁。
- `web/templates/login.html`：内联追加 `@media` 把 `.card` 在小屏全宽化。

验证：在 iPhone 16 viewport 截图，确认壳能用、抽屉能开能关。

### Phase 2 — 高频查看页（登录后即用）

按使用频率改：
1. `pushes_list` — 表格塌陷为卡片，工具栏堆叠
2. `tasks_list` — 表格塌陷为卡片，弹窗全屏
3. `order_analytics` — KPI 卡单列、tab 滚动条隐藏、表格横滚
4. `medias_list` — 卡片网格 2 列，工具栏堆叠
5. `index.html / _task_workbench` — 工作台允许横滚不强求塌陷

### Phase 3 — 二级查看页

`*_translate_list / *_translate_detail`、`admin_ai_billing / admin_usage`、`scheduled_tasks`、`user_settings`、`admin_settings`、`subtitle_removal_list / detail`、`bulk_translate_list / detail`、`projects` 等。

要求：
- 页头标题不溢出
- 工具栏/筛选器堆叠
- 表格走兜底横滚不破版
- 模态在小屏全屏化（多数已用 `--oc-modal` 已生效；个别页面单独修）

### Phase 4 — 回归测试

用 webapp-testing skill（Playwright）在 iPhone viewport 下截屏 + 操作下列页：
- 登录 → 进首页
- 推送管理列表（卡片渲染、抽屉打开、推送弹窗）
- 数据分析（tabs 滚动、表格横滚）
- 任务中心（卡片渲染、详情抽屉、创建弹窗）
- 素材管理（2 列卡片、筛选堆叠、详情弹窗）
- API 账单（金额/图表）
- 视频翻译列表（任一语种）+ 详情

每个页 fail/异常截图归档；逐一修。

### Phase 5 — 出货

- 在 worktree commit
- merge 回 master（按 CLAUDE.md 硬规则，结构性改动走 worktree）
- push origin/master
- 部署到 LocalServer 并 healthcheck
- cleanup worktree + 删除分支

## 5. 文件清单

新增：
- `web/static/css/mobile.css`
- `docs/superpowers/specs/2026-05-01-mobile-ios-responsive-design.md`
- `docs/superpowers/plans/2026-05-01-mobile-ios-responsive.md`

修改：
- `web/templates/layout.html`（壳 + hamburger + meta）
- `web/templates/login.html`（小屏适配）
- 高频页面的 `{% block extra_style %}` 末尾加 `@media`：
  - `web/static/pushes.css`（已有 768px 段，扩展卡片塌陷）
  - `web/templates/order_analytics.html`
  - `web/templates/tasks_list.html`
  - `web/templates/medias_list.html`
  - `web/templates/admin_ai_billing.html`、`admin_usage.html`
  - `web/templates/multi_translate_list.html`、相应 detail
  - 其他二级页按需

## 6. 测试计划

设备 viewport：
- iPhone 16：393×852（DPR 3）
- iPhone 15 Pro Max：430×932（DPR 3）
- 兜底 iPhone SE：375×667

webapp-testing 在 Playwright iPhone 14 Pro / iPhone 14 Pro Max 设备 emulation 下：
1. 启动 dev server (`python main.py` 或 `python -m web`)
2. 使用 `testuser.md` 中的管理员凭据登录
3. 逐一访问 7 个核心页，截图 + 点击关键交互（hamburger / 卡片 / 抽屉 / 模态）
4. 输出失败页清单，回到对应模板修，再跑一轮直到 0 fail

## 7. 风险

- 70+ 模板 inline style，存在没采样到的页面在 mobile.css 全局规则下崩。**对策**：mobile.css 用低特异性选择器（不 `!important`），且只动 layout 关键属性；逐页跑回归。
- iOS Safari 100vh bug（地址栏高度变化）。**对策**：所有高度引用 `100dvh`（fallback `100vh`），抽屉用 `position: fixed; inset: 0`。
- 抽屉的 `position: fixed` 与现有 `oc-modal-mask z-index: 200` 冲突。**对策**：抽屉用 `z-index: 150`，模态保持 200，模态打开时抽屉仍可见也不破事。

## 8. 验收

- 在 iPhone 16 / 15 Pro Max 真机或 viewport 模拟下，下列 7 个页面"可顺畅使用"：
  1. login → index
  2. pushes_list（含推送弹窗、推送历史抽屉）
  3. order_analytics（实时大盘、订单分析）
  4. tasks_list（含详情抽屉、创建弹窗）
  5. medias_list（含编辑模态）
  6. admin_ai_billing / my-ai-usage
  7. multi_translate_list + detail
- 桌面端无视觉/交互回归。
- 不引入紫色（hue ∈ 200–240，绿/黄/红状态色除外）。
