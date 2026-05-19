# Meta 热帖分析中文输出适配

日期：2026-05-18

## 背景

Meta 热帖已有两组视频 AI 分析：

- 美国搬运 / 可抄分析：`meta_hot_posts.video_copyability`，结果写入 `meta_hot_post_video_copyability_analyses`。
- 欧洲投放适配分析：`meta_hot_posts.europe_fit`，结果写入 `meta_hot_post_europe_assessments`。

存量美国搬运分析曾返回英文 `summary`，因此已增加 `summary_zh` 回填任务。后续新分析不应再依赖二次翻译作为主要路径，模型分析结果本身应直接提供中文运营解读，前端优先展示中文字段。

## 目标

- 后续美国搬运分析继续保留英文 `summary` 兼容字段，同时强制返回中文 `summary_zh`。
- 美国搬运分析的 `winning_angles`、`copy_notes`、`risk_notes` 后续直接返回简体中文，便于回填任务或审计读取完整中文解读。
- 后续欧洲分析的 `strengths`、`risks`、`required_changes`、`reasoning` 直接返回简体中文。
- 保留结构化推荐枚举和分数字段，避免破坏现有排序、筛选、看板逻辑。
- 前端保持美国分析 `summary_zh || summary` 的兜底展示；欧洲 Top50 直接展示新分析落库的中文列表字段。

## 非目标

- 不在本次变更中重跑或重写已完成的欧洲历史英文分析。
- 不改变美国 Top50 / 欧洲 Top50 的排序规则。
- 不改变 Gemini provider、模型、调度间隔或队列优先级。
- 不停止正在执行的美国 `summary_zh` 存量回填长任务。

## 行为设计

### 美国搬运分析

`appcore/meta_hot_posts/video_copyability.py` 的响应 schema 已包含 `summary_zh`。后续要求：

- `summary`: 保留英文一句话摘要，用于兼容旧字段、导出或排查。
- `summary_zh`: 必填的简体中文运营解读，1 到 3 句。
- `winning_angles`、`copy_notes`、`risk_notes`: 返回简体中文数组；Meta、Facebook、Instagram、Reels、SKU、ROAS 等术语保留原术语。

如果模型异常缺失 `summary_zh`，存储层仍会把该条标记为 `summary_zh_status='pending'`，交由既有回填任务补齐。

### 欧洲适配分析

`appcore/meta_hot_posts/europe_fit.py` 的响应 schema 保持字段名不变，字段内容语言改为：

- `best_countries`: 国家名或国家代码可保留英文。
- `strengths`: 简体中文优势点。
- `risks`: 简体中文风险点。
- `required_changes`: 简体中文需要调整项。
- `reasoning`: 简体中文综合判断，1 到 3 句。

`recommendation` 仍使用 `direct_reuse`、`adapt_before_use`、`not_recommended`，避免影响前端标签映射。

## 验证

- 测试美国 prompt/schema 要求 `summary_zh` 且中文列表字段。
- 测试欧洲 system/prompt/schema 要求中文输出，但推荐枚举保持英文。
- 跑 Meta 热帖相关单测，确认服务 hydrate、路由模板和存储兼容现有字段。
- 发布后不停止当前回填任务，只确认 Web 服务 active 且根路由 302。
