# Meta 热帖 AI 分析显示开关

日期：2026-05-19

> 后续修订：本总开关设计已被 `docs/superpowers/specs/2026-05-19-meta-hot-posts-split-ai-analysis-visibility-design.md` 取代。当前线上目标是美国/欧洲 AI 分析独立按钮、用户级持久化、默认隐藏。

## 背景

Meta 热帖卡片目前会直接展示两类 AI 分析信息：

- 美国搬运分析：`video_copyability.summary_zh || summary`。
- 欧洲适配分析：`europe_fit_*_zh || europe_fit_*`。

运营浏览素材时有时需要只看视频、文案、商品基础信息和标注按钮，不希望 AI 分析块占用卡片空间。页面需要一个轻量开关控制卡片内 AI 分析内容的显示与隐藏。

## 目标

- 在 `/xuanpin/meta-hot-posts` 页面工具区增加一个 toggle。
- toggle 两个状态文案固定为：`显示AI分析`、`关闭AI分析`。
- 默认状态为 `显示AI分析`，即默认展示中文优先的 AI 分析内容。
- 点击 `关闭AI分析` 后隐藏卡片中的美国搬运分析块和欧洲评估块。
- 点击 `显示AI分析` 后恢复显示。
- 使用 `localStorage` 记住用户选择。

## 非目标

- 不改变接口返回字段。
- 不改变 AI 分析、翻译回填、排序或筛选逻辑。
- 不隐藏商品基础信息块，包括商品图、类目、价格、SKU 和商品分析状态。

## 实现计划

- `web/templates/meta_hot_posts.html` 增加工具区 toggle。
- 增加 `mhShowAiAnalysis` 状态、`applyMetaHotAiAnalysisVisibility()`、`toggleMetaHotAiAnalysis()`、`restoreMetaHotAiAnalysisVisibility()`。
- `copyabilityBlock(row)` 和 `renderEuropeFitPanel(row)` 在关闭状态下返回空字符串。
- 页面初始化时从 `localStorage.mhShowAiAnalysis` 恢复状态。
- `tests/test_meta_hot_posts_routes.py` 覆盖按钮文案、localStorage key、函数名和两个 AI block 的隐藏条件。

## 验证

- `pytest tests/test_meta_hot_posts_routes.py -q`
- 相关 Meta 热帖回归测试。
- 发布生产后确认服务 `active`，根路由返回 `302`。
