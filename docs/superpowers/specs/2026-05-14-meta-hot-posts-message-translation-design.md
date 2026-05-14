# Meta Hot Posts Message Translation

## Scope

Meta 热帖卡片的视频下方文案继续保留上游原文 `message_html`，新增中文缓存 `message_zh_html`。列表接口优先返回中文缓存；缓存缺失时仍回退原文，避免页面空白。

## Data Flow

`meta_hot_posts.message_html` 是上游英文原文。同步任务更新原文字段时，如果原文变化，会清空中文缓存并把翻译状态重置为 `pending`。

`meta_hot_posts_translate_messages_tick` 每 10 分钟扫描待翻译记录，每轮最多 50 条，调用 `meta_hot_posts.translate_message` LLM 用例，把纯文本原文翻译为简体中文，再转成安全 HTML 存入 `message_zh_html`。

## UI

`/xuanpin/meta-hot-posts` 仍渲染 `row.message_html`。服务端 hydrate 时会把已完成的 `message_zh_html` 覆盖到该字段，同时把原始英文保留到 `message_source_html` 便于后续排查。

页面保留自动定时处理，并新增「翻译文案」按钮用于手动触发一轮翻译。

## Verification

覆盖点：

- translation helper 提取 HTML 文本、调用 LLM、清洗输出为安全 HTML。
- store 查询、写入、重置翻译状态。
- scheduler 定时翻译与 stale run 接管。
- route/template 暴露手动触发入口。
