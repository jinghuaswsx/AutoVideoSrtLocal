# web/static/

前端静态资源：Ocean Blue 设计系统、medias.js（素材管理弹窗）、CSRF 约束。

## CSRF（必读）
- `web` 蓝图（含 `medias`、`order_analytics` 等）的 POST/PUT/DELETE 路由都开了 CSRF 保护。
- 前端 fetch / axios 必须带 `X-CSRFToken` header，token 从 `layout.html` `<meta name="csrf-token" content="{{ csrf_token() }}">` 读。
- `medias.js` / 弹窗类前端代码新增 mutating 请求时，先确认是否复用了已有的请求封装（含 token 注入），避免散落手写 fetch。

## medias 蓝图 url_prefix
- 蓝图前缀是 `/medias`。前端表面看是 `/api/products/...`，实际真路径是 `/medias/api/products/...`。新增路由别拼错。

## Ocean Blue 设计系统（管理后台视觉基调）

视觉基调：**深海蓝侧栏 + 白色主区 + 海洋蓝品牌色 + 大圆角卡片**。
**硬性约束：全程零紫色。** 所有 hue 限定 `200–240`（cyan 到 pure blue），禁止 `260+` 的蓝紫/靛蓝。

### 禁用清单（严格执行）
- 任何紫色：violet / indigo / purple / magenta / pink / lavender；OKLCH hue 必须在 200-240
- 紫蓝渐变、紫粉渐变、彩虹渐变
- Glassmorphism 毛玻璃作主效果、Neumorphism、3D 拟物
- 大面积深色（除侧栏外）
- 过度动画 / 鼠标跟随光晕 / 滚动视差 / infinite 动画 / bounce
- 居中英雄区 + 渐变 blob 的营销站套路
- 纯黑 `#000` / 纯白 `#fff` 作文字/背景
- emoji 出现在按钮 / 表格单元格 / 表单 label

### Tokens（必走 CSS 变量，禁止硬编码）
完整 token 集（颜色 / 字号 / 间距 / 圆角 / 阴影 / 动效）见 `web/static/css/tokens.css`（如不存在则集中定义在 `layout.html` `:root`）。改色板时只动 token，不要在组件内硬编码 hex。

关键 token：`--accent` `--accent-hover` `--cyan` `--bg` `--bg-muted` `--border` `--border-strong` `--fg` `--fg-muted` `--{warning|success|danger|info}{-bg|-fg}` `--chart-1..5` `--space-1..10` `--radius{-sm|-md|-lg|-xl|-full}` `--shadow{-xs|-sm|-lg}` `--ease` `--ease-out` `--duration` `--sidebar-w=224px` `--header-h=56px`。

### 组件红线
- **按钮**：主 `--accent` 底白字 `--radius`；次 白底 `--border-strong` 描边；危险 `--danger` 底；高度 sm=28 / default=32 / lg=36
- **卡片**：白底 + `1px solid --border` + `--radius-lg`，默认无 shadow，仅 hover / 浮层 `--shadow-sm`
- **输入框**：h=32，边框 `--border-strong`，focus 换 `--accent` + 2px `--accent-ring`
- **侧栏**：项高 36，padding `--space-3`，`--radius-md`；激活 `--sidebar-bg-active`
- **图标**：Lucide（1.5px stroke）inline SVG；尺寸 14/16/18/20/24
- **4/8 网格**：margin/padding 走 `--space-*`，禁止 `13px` `17px`

### Motion
- hover/focus 120ms，只动 color/background/border
- 展开/折叠 180ms `--ease-out`
- 弹窗入场 280ms `--ease`，opacity 0→1 + translateY(4→0)
- 只 transition `opacity / transform / *-color`

### 三态必做
empty / loading / error 三态都要有；响应式：侧栏 < 1024 折叠为抽屉，主内容 < 768 单列。

### 自检清单
- [ ] 有任何紫色/靛蓝？（hue ≤ 240）
- [ ] 颜色/尺寸全走 token？
- [ ] empty / loading / error 三态齐全？
- [ ] Tab / focus / Esc 键盘可达？
- [ ] 与现有页面密度 / 圆角 / 按钮风格一致？

## 中文排版
- 行高：正文 `--leading`（1.55），标题 `--leading-tight`（1.3）
- 数字 / 代码用 `--font-mono`
- UI label 半角标点，正文全角
