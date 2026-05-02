# Mobile iOS Responsive Adaptation — Implementation Plan

- 配套 spec：[docs/superpowers/specs/2026-05-01-mobile-ios-responsive-design.md](../specs/2026-05-01-mobile-ios-responsive-design.md)
- 分支：`feature/mobile-ios-responsive`

## Phase 1 — 全局壳

### 1.1 新增 `web/static/css/mobile.css`
全局 `@media` 补丁文件。承载下列规则：
- 表单输入字号兜底 `font-size: max(16px, 1em)`（防 iOS 自动缩放）
- 按钮 / 链接 / 抽屉项触控热区 `min-height: 44px`（仅在移动模式下覆盖）
- 表格兜底：`.main-content table:not([data-no-mobile-scroll])` 包装 `display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%`
- 模态在 `<480px` `inset: 0; border-radius: 0; max-height: none; max-width: none; height: 100dvh; width: 100vw`
- 抽屉在 `<768px` 改为 `width: min(86vw, 320px)` 或全宽
- `.btn` / `.oc-btn` / `.tc-btn` / `.tools-tab` 等常见交互类在 `<768px` 提高到 36–40 高（保持文字大小）
- 取消桌面 hover 副作用：`@media (hover: none) { .oc-card:hover { box-shadow: none; } ... }`
- 通用 safe-area：sticky / fixed bottom 元素加 `padding-bottom: max(<原值>, env(safe-area-inset-bottom))`

### 1.2 改 `web/templates/layout.html`
- 顶部 meta 改为 `<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">`
- 加 `<meta name="theme-color" content="#1e3a8a">`（dark 用 `#0a0f1c`）
- `<head>` 内 link 新建的 `mobile.css`：`<link rel="stylesheet" href="{{ url_for('static', filename='css/mobile.css') }}">`
- 在 layout `<style>` 末尾追加 `@media (max-width: 768px)` 段：
  - `.sidebar` → `transform: translateX(-100%); transition: transform 0.28s var(--mb-ease, ease)`
  - `.sidebar.open` → `transform: translateX(0)`
  - `.main-wrap` → `margin-left: 0; max-width: 100vw`
  - `.topbar` → `padding: 0 12px; height: 52px; gap: 8px`
  - `.topbar-title` → `flex: 1; min-width: 0; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis`
  - `.topbar-user`、`.topbar-logout-btn` → `display: none`（移到抽屉脚部）
  - 顶栏新增 `#sidebar-toggle` 按钮显示
  - `.main-content` → `padding: 16px 14px 32px`
  - 新增 `.sidebar-backdrop`：`position: fixed; inset: 0; background: rgba(15, 23, 42, .45); z-index: 99; opacity: 0; pointer-events: none; transition: opacity .2s`，open 状态启用
  - body `.sidebar-open` → `overflow: hidden`
- 在 sidebar 上方加 hamburger 按钮 + backdrop 节点
- 在 layout 末尾 `<script>` 段加抽屉控制 JS（不依赖 jQuery）：
  - 点击 hamburger 切换 `sidebar.classList.toggle('open')` 与 `body.classList.toggle('sidebar-open')`
  - 点击 backdrop / 按 ESC / 点击侧栏链接 → 关闭
  - `window.matchMedia('(min-width: 768px)')` change 时强制清除 open 状态（避免桌面态残留）

### 1.3 改 `web/templates/login.html`
- viewport meta 同步加 `viewport-fit=cover`
- 末尾加 `@media (max-width: 480px) { body { padding: 16px; } .card { width: 100%; max-width: 420px; padding: 32px 22px 28px; border-radius: 14px; } input { font-size: 16px; } .btn { font-size: 16px; padding: 14px; } }`

**验证**：浏览器 dev tools 切到 iPhone 14 Pro，验证 layout 抽屉能开能关、点 backdrop 能关、桌面态无回归。

---

## Phase 2 — 高频查看页

### 2.1 `web/static/pushes.css`
现状：表格列多，已有 768px 段把 toolbar 堆叠 + drawer 全宽。补：
- `<480px`：表格 `<thead>` 隐藏；`<tr>` 改 `display:block; padding:12px; border:1px solid var(--oc-border); border-radius:var(--oc-r-lg); margin-bottom:12px`
- `<td>` 改 `display:flex; justify-content:space-between; gap:12px; padding:6px 0; border:none`，并用 `::before { content: attr(data-label); ... }` 显示行标签
- 模板侧给 `<td>` 加 `data-label`（在 pushes.js 的渲染处补）；如成本太高，退而求其次：`<480px` 下 `.push-table { display: block; overflow-x: auto }`，保留桌面式横滚

实施选 B（表格内部由 JS 渲染、改起来风险高）：仅做横滚 + 紧凑列宽；toolbar 已堆叠保留。

### 2.2 `web/templates/order_analytics.html`
- inline `<style>` 末尾加 `@media (max-width: 768px)`：
  - `.oa-tabs-topbar` 已 `overflow-x: auto`，OK；最大宽度去掉 `max-width: 58vw` 改 `max-width: 100%`
  - KPI 卡片 grid（如 `grid-template-columns: repeat(4, 1fr)`）改单列或 2 列
  - 各 `<table>` 包裹 `display:block; overflow-x:auto`（兜底）
  - 顶栏标题 + tabs 拼成的 `topbar-title`：移动端把 tabs 移到 `.main-content` 下方第一行，避免 56px 顶栏挤爆

### 2.3 `web/templates/tasks_list.html`
- `.tc-filters` 横向变堆叠：`flex-direction: column; align-items: stretch; gap: 8px`
- `.tc-input` 宽度 100%
- `#tcDetailDrawer` 在 `<768px` 改 `width: 100vw; max-width: 100vw`
- 创建弹窗 / reason 弹窗在 `<480px` 全屏：`width:100vw; height:100dvh; transform: none; top:0; left:0; border-radius:0`
- 表格走兜底横滚

### 2.4 `web/templates/medias_list.html`
- `.oc-card` 网格改 `<480px` 2 列：`.oc-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px }`
- `.oc-toolbar` 紧凑：`flex-wrap: wrap`，搜索框 `min-width: 0; flex: 1 1 100%`
- 详情/编辑模态依赖 `oc-modal`，在 mobile.css 全局规则里已经全屏化

### 2.5 `web/templates/index.html`（任务工作台）
- 工作台是单页多步骤 + 大量内部组件，在 `<768px` 给整个 `.workbench-container` 加 `overflow-x: auto`，子元素内部布局保留桌面假设；不主动塌陷，避免造成新 bug

**验证**：每改完一页，dev tools 模拟 iPhone 16 截图验证；顺手桌面 1280×800 截图确认无回归。

---

## Phase 3 — 二级查看页

### 3.1 视频翻译列表 / 详情
对象：`multi_translate_list/detail`、`*_translate_list/detail`、`omni_translate_list/detail`、`copywriting_list/detail`、`text_translate_list/detail`、`bulk_translate_list/detail`、`subtitle_removal_list/detail`、`image_translate_list/detail`、`video_creation_list/detail`、`video_review_list/detail`、`raw_video_pool_list`。

不逐一个个看：
- 列表页：在 mobile.css 加通用规则 `.main-content table { display: block; overflow-x: auto }`（pushes.css 自己已处理则无影响）
- 详情页：在 mobile.css 加 `.main-content > .detail-shell, .main-content [class*="-detail-"] { padding: 12px; }`（保守通用规则）

只针对每个页头拥挤的 `topbar-title`（很多含 tabs 或子标题）保证不溢出：mobile.css 全局加 `.topbar-title { flex: 1; min-width: 0 } .topbar-title > * { min-width: 0 }`。

### 3.2 `admin_ai_billing.html` / `admin_usage.html`
inline style 加 `@media (max-width: 768px)`：
- 筛选堆叠
- 数字大字号在小屏缩 30%
- 表格横滚

### 3.3 `scheduled_tasks.html` / `user_settings.html` / `admin_settings.html` / `admin_users.html`
mobile.css 通用规则 + 各页内 `<style>` 加少量 `@media`：
- 表单 grid 改单列
- 操作按钮换行

### 3.4 `voice_library.html` / `prompt_library.html`
PC-only 工作台。允许横滚不强求塌陷。

---

## Phase 4 — 回归测试

### 4.1 webapp-testing 跑 iPhone 16 viewport
脚本 `tests/manual/mobile_smoke.py`（新建，可丢弃）：
1. 启 Playwright iPhone 14 Pro Max（接近 iPhone 16 Pro Max viewport）
2. 使用 `testuser.md` 中的管理员凭据登录
3. 依次访问：
   - `/`
   - `/pushes`（点 hamburger 打开抽屉、关闭）
   - `/order-analytics`
   - `/tasks/`
   - `/medias`
   - `/multi-translate`
   - `/admin/ai-usage` 或 `/my-ai-usage`
4. 每页截屏到 `tests/manual/mobile_smoke_artifacts/<page>.png`
5. 控制台抓 console error，输出报告

### 4.2 真机/Safari 旁路验证
浏览器 dev tools 是模拟，关键剩余问题 100vh / safe-area 必须实机看。当前阶段先用模拟器拿到 95% 信心，部署后用 iPhone 真机最终验。

### 4.3 修 bug 循环
每个 fail 页：
- 截图对照
- 定位元素
- 改 inline `<style>` 或 mobile.css
- 重跑那一页

---

## Phase 5 — 出货

按 CLAUDE.md 硬规则：worktree 完成后的固定收尾顺序——
1. 提交合并代码：在 worktree commit → 切回主 worktree → merge 到 master
2. push origin/master
3. 部署到 LocalServer：`ssh root@172.30.254.14 'cd /opt/autovideosrt && git pull && systemctl restart autovideosrt-web'`
4. healthcheck：curl `/login` 200 + 抽样 `/pushes`
5. cleanup：`git worktree remove ...` + `git branch -d feature/mobile-ios-responsive`

部署前 commit 拆分计划：
- `feat(mobile): add responsive shell with drawer sidebar` (layout.html + mobile.css + login.html)
- `feat(mobile): adapt high-frequency views (pushes/order/tasks/medias)`
- `feat(mobile): adapt secondary views (translate/billing/settings)`
- `chore(docs): mobile responsive spec & plan`

## Phase 排期

| 阶段 | 估计 |
|------|------|
| Phase 1 壳 | ~30 min |
| Phase 2 高频 | ~45 min |
| Phase 3 二级 | ~30 min |
| Phase 4 测试修复 | ~45 min |
| Phase 5 出货 | ~15 min |
| **合计** | **~2.5 h** |
