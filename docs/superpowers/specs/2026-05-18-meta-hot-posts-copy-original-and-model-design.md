# Meta 热帖文案原文保留与模型绑定设计

## 背景

`/xuanpin/meta-hot-posts` 的工具栏目前同时暴露同步、商品分析、文案翻译、视频本地化、欧洲评估、视频分析和可抄 Top 50 等手动执行按钮。运营界面只需要保留排查类入口，后台定时任务继续负责自动处理。

Meta 热帖文案翻译已经把上游英文 `message_html` 保存在本地，并把中文翻译缓存到 `message_zh_html`。服务端 hydrate 时会优先把中文缓存作为 `message_html` 返回，同时把英文原文放到 `message_source_html`。本次要求是在卡片上继续默认展示中文翻译，并提供“显示原文案”按钮，方便查看原始英文。

当前 Meta 热帖文案翻译的运行 use case 是 `meta_hot_posts.translate_message`，默认已经是 `openrouter / google/gemini-3.1-flash-lite`。为避免已有 DB binding 或相邻文案翻译入口回退到 Gemini 3 Flash，本次也把共享的 `title_translate.generate` 与遗留 `copywriting_translate.generate` 固定到同一 OpenRouter Flash-Lite 模型。

## Scope

1. Meta 热帖页面工具栏只保留“类目分析提示词”和“商品分析失败记录”。
2. 视频卡片默认显示中文翻译；存在中文翻译与英文原文时，在文案后显示“显示原文案”按钮。
3. 点击按钮后切换为原始英文文案，按钮文字变为“显示翻译文案”；再次点击恢复中文翻译。
4. 服务端响应显式带出 `message_is_translated`，前端不靠字符串猜测翻译状态。
5. `meta_hot_posts.translate_message`、`title_translate.generate`、`copywriting_translate.generate` 的启用 DB binding 和代码默认值统一为 `openrouter / google/gemini-3.1-flash-lite`。

## Non-Goals

- 不删除后端 API；定时任务、管理员后台或后续脚本仍可调用这些能力。
- 不改变 Meta 热帖翻译 prompt、调度频率、重试策略或中文缓存表结构。
- 不改变视频本地化、欧洲评估、美国 Top50 子 tab 的读取能力。

## Verification

- `tests/test_meta_hot_posts_routes.py` 覆盖工具栏按钮移除和卡片原文按钮脚本。
- `tests/test_meta_hot_posts_service.py` 覆盖 `message_source_html` 与 `message_is_translated`。
- `tests/test_llm_use_cases_registry.py` 覆盖相关文案翻译 use case 默认模型。
- migration smoke test 覆盖 DB binding 强制更新到 OpenRouter Flash-Lite。
