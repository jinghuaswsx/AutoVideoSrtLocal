# Meta 热帖 AI 分析卡片手动翻译中文

日期：2026-05-19

## 背景

Meta 热帖卡片中已有两类 AI 分析卡片：

- 美国 AI 分析卡片：来自 `meta_hot_post_video_copyability_analyses`，展示分数、推荐标签和视频可抄分析摘要。
- 欧洲 AI 分析卡片：来自 `meta_hot_post_europe_assessments`，展示欧洲投放评估、适合国家、优势、风险和需要调整项。

历史数据中仍存在英文分析内容。批量回填能处理存量，但运营在页面刷素材时也需要对单个卡片即时翻译，并把翻译结果存档，避免下次重复请求模型。

## 目标

- 美国 AI 分析卡片和欧洲 AI 分析卡片都增加 `翻译中文` 按钮。
- 按钮只出现在对应 AI 分析结果卡片内部，不影响底部“美国AI分析”“欧洲AI分析”弹窗按钮。
- 点击底部“美国AI分析”打开的结果弹窗里，摘要区域右上角也增加 `翻译中文` 按钮，方便直接在弹窗内触发翻译。
- 点击按钮后调用 OpenRouter 通道的 `google/gemini-3.1-flash-lite` 大模型，把当前卡片的对应 AI 分析内容翻译成简体中文。
- 翻译成功后立即更新当前前端卡片显示，并把中文结果写入数据库缓存字段。
- 如果对应中文缓存已存在，接口直接返回缓存内容，不重复调用模型。
- 失败时保持原有英文/已有内容不变，按钮恢复可点击，并在页面状态区显示失败信息。

## 翻译范围

### 美国 AI 分析卡片

翻译图中美国 AI 分析卡片红框内的分析正文，也就是当前卡片展示的美国可抄分析摘要：

- `summary` 英文摘要。
- 如 `analysis_json` 中存在 `winning_angles`、`copy_notes`、`risk_notes`，可作为上下文一起输入模型，帮助翻译更准确。

存档字段：

- `meta_hot_post_video_copyability_analyses.summary_zh`
- `summary_zh_status='done'`
- `summary_zh_error=NULL`
- `summary_zh_translated_at=NOW()`

前端展示继续使用 `summary_zh || summary`，翻译后当前卡片立即显示中文摘要。

### 欧洲 AI 分析卡片

翻译图中欧洲 AI 分析卡片红框内的结构化内容：

- `strengths_json`
- `risks_json`
- `required_changes_json`
- `reasoning`

同时可把 `recommendation`、`best_countries_json` 作为上下文输入模型，但不要求翻译适合国家代码/国家名。

存档字段：

- `meta_hot_post_europe_assessments.strengths_zh_json`
- `risks_zh_json`
- `required_changes_zh_json`
- `reasoning_zh`
- `zh_status='done'`
- `zh_error=NULL`
- `zh_translated_at=NOW()`

前端展示继续使用中文缓存优先：

- `europe_fit_strengths_zh || europe_fit_strengths`
- `europe_fit_risks_zh || europe_fit_risks`
- `europe_fit_required_changes_zh || europe_fit_required_changes`
- `europe_fit_reasoning_zh || europe_fit_reasoning`

## LLM 通道

手动翻译使用 OpenRouter：

- provider：`openrouter`
- model：`google/gemini-3.1-flash-lite`
- temperature：`0.0`
- max tokens：美国摘要 `512`，欧洲结构化翻译 `700`

现有批量回填任务仍可保留当前节奏和限流策略；本需求只新增单卡片即时翻译入口，不启动新的后台批量任务。

## 接口设计

新增接口：

```text
POST /xuanpin/api/meta-hot-posts/<post_id>/ai-analysis/<mode>/translate-zh
```

其中 `mode` 支持：

- `us_copyability`
- `europe_translation`

返回：

```json
{
  "ok": true,
  "mode": "us_copyability",
  "cached": false,
  "item": {"id": 123},
  "result": {}
}
```

`item` 使用现有 `_hydrate_item()` 输出结构，前端直接用它刷新当前卡片。接口必须保持 `@login_required` 和 Meta 热帖权限校验。

## 前端行为

- `copyabilityBlock(row)` 在美国 AI 分析卡片标题行右侧渲染 `翻译中文`。
- `renderEuropeFitPanel(row)` 在欧洲 AI 分析卡片标题行右侧渲染 `翻译中文`。
- `renderAiSummarySection()` 在美国 AI 分析结果弹窗的“摘要”标题行右侧渲染 `翻译中文`，复用同一接口。
- 点击按钮时禁用该按钮，显示处理中状态，调用新增接口。
- 成功后用返回的 `item` 更新 `mhItemsById` 和当前 DOM 卡片；如果当前 AI 分析弹窗展示的是同一帖子和同一 mode，同时重绘弹窗结果区，让摘要立即变为中文。
- 如果该卡片已经有中文缓存，按钮仍可存在；点击后接口返回缓存并刷新显示，不产生额外模型调用。

## 非目标

- 不改变美国/欧洲 AI 分析本身的重新分析逻辑。
- 不改变用户维度的“显示美国AI分析 / 显示欧洲AI分析”设置。
- 不新增数据库字段；复用既有中文缓存字段。
- 不把整张帖子文案或商品卡片内容纳入本按钮翻译范围。
- 不并发批量翻译整页卡片，避免触发 OpenRouter 429。

## 验证

- 翻译模块测试覆盖 OpenRouter `google/gemini-3.1-flash-lite` 调用参数。
- Service 测试覆盖：
  - 美国已有中文缓存时直接返回缓存。
  - 美国无中文缓存时调用翻译并写库。
  - 欧洲已有中文缓存时直接返回缓存。
  - 欧洲无中文缓存时调用翻译并写库。
- Route 测试覆盖新增接口、权限和 mode 分发。
- Template 测试覆盖两个 AI 分析卡片内都有 `翻译中文` 按钮、美国 AI 分析弹窗摘要区也有 `翻译中文` 按钮，以及按钮调用新增接口。
- 发布前跑 Meta 热帖相关单测；发布后确认测试/生产服务 active，未登录入口 302，登录页面 200。
