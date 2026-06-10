# Meta 热帖移动端顶部控件滚动收起与视频宽度

日期：2026-06-10

## 背景

手机竖屏浏览 `/xuanpin/meta-hot-posts` 素材卡片时，顶部 sticky 区域包含当前页摘要、美国/欧洲 AI 分析按钮、卡片放大按钮和顶部分页。它方便翻页，但用户连续上滑扫视频时会持续占用首屏空间。

同时卡片内视频预览仍使用固定 `267px` 宽度，移动端卡片已经单列展示时，视频区域明显窄于卡片内容区。

## 锚点

- `AGENTS.md#文档驱动代码`：用户新增移动端行为先固化为 spec，再改模板。
- `web/templates/CLAUDE.md#CSRF / 路由守卫`：本次只改前端展示与滚动行为，不新增 mutating 请求。
- `web/static/CLAUDE.md#Ocean Blue 设计系统`：控件颜色和交互沿用现有 Meta 热帖页 token，不引入新色板。
- `2026-05-13-meta-hot-posts-selection-design.md#后台页面`：视频卡片继续延迟加载真实播放器，点击封面后才加载视频或 iframe。
- `2026-05-18-meta-hot-posts-page-summary-design.md#设计`：顶部摘要仍由 `renderMetaHotPageSummary()` 生成。
- `2026-05-19-meta-hot-posts-split-ai-analysis-visibility-design.md#目标`：美国/欧洲 AI 分析显示按钮保留并继续独立控制。
- `2026-05-20-meta-hot-posts-sticky-controls-design.md#设计`：继续使用 `.mh-sticky-controls` 承载顶部摘要、按钮和顶部分页。

## 目标

1. 移动端向下滚动内容（手指上滑）时，`.mh-sticky-controls` 内的顶部摘要、AI 分析按钮、卡片放大按钮和顶部分页整体收起，不遮挡卡片内容。
2. 移动端向上滚动内容（手指下滑）或回到页面顶部时，顶部控件整体恢复显示。
3. 桌面端 sticky 控件保持现状，不新增滚动隐藏行为。
4. 移动端视频/图片预览宽度贴齐卡片内容区宽度，继续保持原来的竖版比例 `267 / 476`。
5. 不改变 API、分页状态、筛选条件、卡片数据结构、AI 分析显示偏好或视频延迟加载逻辑。

## 设计

- `web/templates/meta_hot_posts.html` 顶部增加本 spec 的 Docs-anchor。
- 移动端 CSS：
  - `.mh-sticky-controls` 增加 `transition: transform 160ms ease, opacity 160ms ease`。
  - `.meta-hot-page.mh-mobile-controls-hidden .mh-sticky-controls` 使用 `transform: translateY(calc(-100% - 10px))`、`opacity:0`、`pointer-events:none` 收起。
  - `.mh-media` 在 `max-width:768px` 下设为 `width:100%`、`aspect-ratio:267 / 476`、`margin-left/right:0`。
- JS：
  - 新增 `initMetaHotMobileStickyControls()`，只在 `matchMedia('(max-width: 768px)')` 命中时根据 `window.scrollY` 方向切换 `mh-mobile-controls-hidden`。
  - 向下滚动超过阈值后隐藏；向上滚动、接近顶部、离开移动端 viewport 或当前焦点在 sticky 控件内时显示。
  - 使用 `requestAnimationFrame` 节流滚动处理。

## 验证

```bash
python3 scripts/pytest_related.py --base origin/master --run
pytest tests/test_meta_hot_posts_routes.py tests/test_xuanpin_routes.py -q
python -m compileall web tests -q
git diff --check
```

全量 pytest 默认跳过；本次为模板/CSS/JS 小范围改动，聚焦路由与模板契约测试即可覆盖。
